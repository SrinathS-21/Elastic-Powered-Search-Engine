from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return float(ordered[index])


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _rate(count: int, total: int) -> float:
    return (count / total) if total > 0 else 0.0


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    decisions: dict[str, int] = {}
    alerts: dict[str, int] = {}

    phase3_active = 0
    confidences: list[float] = []
    margins: list[float] = []

    for row in rows:
        decision = str(row.get("decision") or "unknown")
        decisions[decision] = decisions.get(decision, 0) + 1

        for alert in row.get("alerts") or []:
            key = str(alert)
            alerts[key] = alerts.get(key, 0) + 1

        if bool(row.get("phase3_active")):
            phase3_active += 1

        confidences.append(float(row.get("confidence") or 0.0))
        margins.append(float(row.get("margin") or 0.0))

    return {
        "rows": total,
        "decision_counts": decisions,
        "alert_counts": alerts,
        "auto_map_rate": _rate(decisions.get("auto_map", 0), total),
        "confirm_rate": _rate(decisions.get("confirm", 0), total),
        "options_rate": _rate(decisions.get("options", 0), total),
        "low_confidence_rate": _rate(alerts.get("low_confidence", 0), total),
        "low_margin_rate": _rate(alerts.get("low_margin", 0), total),
        "product_vote_dominant_rate": _rate(alerts.get("product_vote_dominant", 0), total),
        "phase3_enabled_pct": round(_rate(phase3_active, total) * 100.0, 1),
        "confidence_p50": _percentile(confidences, 50),
        "confidence_p90": _percentile(confidences, 90),
        "margin_p50": _percentile(margins, 50),
    }


def _check(name: str, ok: bool, value: Any, threshold: Any, detail: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "status": "pass" if ok else "fail",
        "value": value,
        "threshold": threshold,
        "detail": detail,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Production observability guard for mapping telemetry")
    parser.add_argument("--telemetry", required=True)
    parser.add_argument("--baseline", default="")

    parser.add_argument("--min-rows", type=int, default=50)
    parser.add_argument("--min-auto-map-rate", type=float, default=0.20)
    parser.add_argument("--max-options-rate", type=float, default=0.55)
    parser.add_argument("--max-low-confidence-rate", type=float, default=0.25)
    parser.add_argument("--max-low-margin-rate", type=float, default=0.75)
    parser.add_argument("--max-product-vote-dominant-rate", type=float, default=0.20)

    parser.add_argument("--expected-canary-percent", type=float, default=-1.0)
    parser.add_argument("--canary-tolerance", type=float, default=7.0)

    parser.add_argument("--max-alert-rate-delta", type=float, default=0.12)
    parser.add_argument("--max-auto-map-rate-delta", type=float, default=0.15)
    parser.add_argument("--max-options-rate-delta", type=float, default=0.12)

    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    telemetry_path = Path(args.telemetry)
    rows = _load_jsonl(telemetry_path)
    metrics = _metrics(rows)

    checks: list[dict[str, Any]] = []
    checks.append(_check("min_rows", metrics["rows"] >= args.min_rows, metrics["rows"], args.min_rows))
    checks.append(
        _check(
            "min_auto_map_rate",
            metrics["auto_map_rate"] >= args.min_auto_map_rate,
            round(metrics["auto_map_rate"], 4),
            args.min_auto_map_rate,
        )
    )
    checks.append(
        _check(
            "max_options_rate",
            metrics["options_rate"] <= args.max_options_rate,
            round(metrics["options_rate"], 4),
            args.max_options_rate,
        )
    )
    checks.append(
        _check(
            "max_low_confidence_rate",
            metrics["low_confidence_rate"] <= args.max_low_confidence_rate,
            round(metrics["low_confidence_rate"], 4),
            args.max_low_confidence_rate,
        )
    )
    checks.append(
        _check(
            "max_low_margin_rate",
            metrics["low_margin_rate"] <= args.max_low_margin_rate,
            round(metrics["low_margin_rate"], 4),
            args.max_low_margin_rate,
        )
    )
    checks.append(
        _check(
            "max_product_vote_dominant_rate",
            metrics["product_vote_dominant_rate"] <= args.max_product_vote_dominant_rate,
            round(metrics["product_vote_dominant_rate"], 4),
            args.max_product_vote_dominant_rate,
        )
    )

    if args.expected_canary_percent >= 0:
        low = args.expected_canary_percent - args.canary_tolerance
        high = args.expected_canary_percent + args.canary_tolerance
        checks.append(
            _check(
                "expected_canary_percent",
                low <= metrics["phase3_enabled_pct"] <= high,
                metrics["phase3_enabled_pct"],
                {"expected": args.expected_canary_percent, "tolerance": args.canary_tolerance},
            )
        )

    baseline_metrics: dict[str, Any] | None = None
    if args.baseline:
        baseline_rows = _load_jsonl(Path(args.baseline))
        baseline_metrics = _metrics(baseline_rows)
        checks.append(
            _check(
                "delta_auto_map_rate",
                abs(metrics["auto_map_rate"] - baseline_metrics["auto_map_rate"]) <= args.max_auto_map_rate_delta,
                round(metrics["auto_map_rate"] - baseline_metrics["auto_map_rate"], 4),
                args.max_auto_map_rate_delta,
            )
        )
        checks.append(
            _check(
                "delta_options_rate",
                abs(metrics["options_rate"] - baseline_metrics["options_rate"]) <= args.max_options_rate_delta,
                round(metrics["options_rate"] - baseline_metrics["options_rate"], 4),
                args.max_options_rate_delta,
            )
        )

        alert_delta = max(
            abs(metrics["low_confidence_rate"] - baseline_metrics["low_confidence_rate"]),
            abs(metrics["low_margin_rate"] - baseline_metrics["low_margin_rate"]),
            abs(metrics["product_vote_dominant_rate"] - baseline_metrics["product_vote_dominant_rate"]),
        )
        checks.append(
            _check(
                "delta_alert_rates",
                alert_delta <= args.max_alert_rate_delta,
                round(alert_delta, 4),
                args.max_alert_rate_delta,
            )
        )

    failures = [item for item in checks if item["status"] == "fail"]

    report: dict[str, Any] = {
        "telemetry": str(telemetry_path),
        "metrics": metrics,
        "checks": checks,
        "pass": len(failures) == 0,
        "failure_count": len(failures),
    }

    if baseline_metrics is not None:
        report["baseline"] = str(args.baseline)
        report["baseline_metrics"] = baseline_metrics

    if args.output == "summary":
        report.pop("checks", None)
        if baseline_metrics is None:
            report.pop("baseline_metrics", None)

    print(json.dumps(report, indent=2, ensure_ascii=True))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
