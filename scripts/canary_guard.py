from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
    raise ValueError(f"Unable to decode JSON file: {path}")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
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


def _metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    total = len(rows)
    decisions = {"auto_map": 0, "confirm": 0, "options": 0}
    alerts = {"low_confidence": 0, "low_margin": 0, "product_vote_dominant": 0}

    confidence_values: list[float] = []

    for row in rows:
        decision = str(row.get("decision") or "")
        if decision in decisions:
            decisions[decision] += 1

        for key in alerts:
            if key in (row.get("alerts") or []):
                alerts[key] += 1

        confidence_values.append(float(row.get("confidence") or 0.0))

    confidence_values.sort()
    p50 = confidence_values[len(confidence_values) // 2] if confidence_values else 0.0

    return {
        "rows": float(total),
        "auto_map_rate": _rate(decisions["auto_map"], total),
        "confirm_rate": _rate(decisions["confirm"], total),
        "options_rate": _rate(decisions["options"], total),
        "low_confidence_rate": _rate(alerts["low_confidence"], total),
        "low_margin_rate": _rate(alerts["low_margin"], total),
        "product_vote_dominant_rate": _rate(alerts["product_vote_dominant"], total),
        "confidence_p50": p50,
    }


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "status": "pass" if ok else "fail", "detail": detail}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canary rollout guard: compare canary vs baseline quality profile")
    parser.add_argument("--baseline-telemetry", required=True)
    parser.add_argument("--canary-telemetry", required=True)
    parser.add_argument("--baseline-regression", default="")
    parser.add_argument("--canary-regression", default="")

    parser.add_argument("--min-rows", type=int, default=50)
    parser.add_argument("--max-regression-failure-delta", type=int, default=0)
    parser.add_argument("--max-alert-rate-delta", type=float, default=0.10)
    parser.add_argument("--max-decision-rate-delta", type=float, default=0.12)
    parser.add_argument("--max-confidence-p50-delta", type=float, default=0.20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    baseline_rows = _load_jsonl(Path(args.baseline_telemetry))
    canary_rows = _load_jsonl(Path(args.canary_telemetry))

    baseline = _metrics(baseline_rows)
    canary = _metrics(canary_rows)

    checks: list[dict[str, Any]] = []
    checks.append(_check("baseline_min_rows", baseline["rows"] >= args.min_rows, f"rows={int(baseline['rows'])}"))
    checks.append(_check("canary_min_rows", canary["rows"] >= args.min_rows, f"rows={int(canary['rows'])}"))

    if args.baseline_regression and args.canary_regression:
        baseline_reg = _load_json(Path(args.baseline_regression))
        canary_reg = _load_json(Path(args.canary_regression))
        baseline_failures = int(baseline_reg.get("failure_count") or 0)
        canary_failures = int(canary_reg.get("failure_count") or 0)
        checks.append(
            _check(
                "regression_failure_delta",
                (canary_failures - baseline_failures) <= args.max_regression_failure_delta,
                f"baseline={baseline_failures}, canary={canary_failures}, max_delta={args.max_regression_failure_delta}",
            )
        )

    max_alert_delta = max(
        abs(canary["low_confidence_rate"] - baseline["low_confidence_rate"]),
        abs(canary["low_margin_rate"] - baseline["low_margin_rate"]),
        abs(canary["product_vote_dominant_rate"] - baseline["product_vote_dominant_rate"]),
    )
    checks.append(
        _check(
            "alert_rate_delta",
            max_alert_delta <= args.max_alert_rate_delta,
            f"max_alert_delta={max_alert_delta:.4f}, threshold={args.max_alert_rate_delta:.4f}",
        )
    )

    max_decision_delta = max(
        abs(canary["auto_map_rate"] - baseline["auto_map_rate"]),
        abs(canary["confirm_rate"] - baseline["confirm_rate"]),
        abs(canary["options_rate"] - baseline["options_rate"]),
    )
    checks.append(
        _check(
            "decision_rate_delta",
            max_decision_delta <= args.max_decision_rate_delta,
            f"max_decision_delta={max_decision_delta:.4f}, threshold={args.max_decision_rate_delta:.4f}",
        )
    )

    confidence_delta = abs(canary["confidence_p50"] - baseline["confidence_p50"])
    checks.append(
        _check(
            "confidence_p50_delta",
            confidence_delta <= args.max_confidence_p50_delta,
            f"confidence_p50_delta={confidence_delta:.4f}, threshold={args.max_confidence_p50_delta:.4f}",
        )
    )

    failed = [item for item in checks if item["status"] == "fail"]

    if any(item["name"] == "regression_failure_delta" and item["status"] == "fail" for item in checks):
        action = "rollback"
    elif failed:
        action = "hold"
    else:
        action = "promote"

    report = {
        "action": action,
        "baseline_telemetry": args.baseline_telemetry,
        "canary_telemetry": args.canary_telemetry,
        "baseline_metrics": baseline,
        "canary_metrics": canary,
        "checks": checks,
        "failed_checks": len(failed),
    }

    print(json.dumps(report, indent=2, ensure_ascii=True))

    if action == "promote":
        raise SystemExit(0)
    if action == "hold":
        raise SystemExit(2)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
