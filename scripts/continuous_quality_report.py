from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            payload = json.loads(path.read_text(encoding=encoding))
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return None


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


def _telemetry_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    if total == 0:
        return {
            "rows": 0,
            "auto_map_rate": 0.0,
            "confirm_rate": 0.0,
            "options_rate": 0.0,
            "low_confidence_rate": 0.0,
            "low_margin_rate": 0.0,
            "product_vote_dominant_rate": 0.0,
        }

    decision_counts = {"auto_map": 0, "confirm": 0, "options": 0}
    alert_counts = {"low_confidence": 0, "low_margin": 0, "product_vote_dominant": 0}

    for row in rows:
        decision = str(row.get("decision") or "")
        if decision in decision_counts:
            decision_counts[decision] += 1

        alerts = row.get("alerts") or []
        for key in alert_counts:
            if key in alerts:
                alert_counts[key] += 1

    return {
        "rows": total,
        "auto_map_rate": _rate(decision_counts["auto_map"], total),
        "confirm_rate": _rate(decision_counts["confirm"], total),
        "options_rate": _rate(decision_counts["options"], total),
        "low_confidence_rate": _rate(alert_counts["low_confidence"], total),
        "low_margin_rate": _rate(alert_counts["low_margin"], total),
        "product_vote_dominant_rate": _rate(alert_counts["product_vote_dominant"], total),
    }


def _calibration_summary(model: dict[str, Any] | None) -> dict[str, Any]:
    if not model:
        return {
            "exists": False,
            "sample_count": 0,
            "positive_count": 0,
            "warnings": ["missing_model"],
            "age_days": None,
        }

    trained_at = str(model.get("trained_at") or "")
    age_days: float | None = None
    if trained_at:
        try:
            dt = datetime.fromisoformat(trained_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = round((datetime.now(timezone.utc) - dt).total_seconds() / 86400.0, 2)
        except Exception:
            age_days = None

    warnings = [str(item) for item in (model.get("warnings") or [])]
    if int(model.get("sample_count") or 0) < 50:
        warnings.append("calibration_samples_below_50")

    positives = int(model.get("positive_count") or 0)
    negatives = int(model.get("sample_count") or 0) - positives
    if positives < 10 or negatives < 10:
        warnings.append("calibration_class_balance_weak")

    return {
        "exists": True,
        "sample_count": int(model.get("sample_count") or 0),
        "positive_count": positives,
        "negative_count": negatives,
        "warnings": sorted(set(warnings)),
        "age_days": age_days,
        "metrics": model.get("metrics") or {},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Continuous quality loop status report")
    parser.add_argument("--telemetry", default="logs/mapping_telemetry_phase3_complete.jsonl")
    parser.add_argument("--regression", default="")
    parser.add_argument("--calibration-model", default="config/mapping_confidence_calibration.json")
    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    telemetry_path = Path(args.telemetry)
    rows = _load_jsonl(telemetry_path)
    telemetry = _telemetry_summary(rows)

    regression_report = _load_json(Path(args.regression)) if args.regression else None
    regression_failures = int((regression_report or {}).get("failure_count") or 0)

    calibration = _calibration_summary(_load_json(Path(args.calibration_model)))

    next_actions: list[str] = []
    if regression_failures > 0:
        next_actions.append("fix_regression_failures_before_rollout")

    if telemetry["rows"] < 100:
        next_actions.append("collect_more_telemetry_rows_for_stable_monitoring")
    if telemetry["low_confidence_rate"] > 0.20:
        next_actions.append("review_low_confidence_queries_and_adjust_thresholds")
    if telemetry["low_margin_rate"] > 0.65:
        next_actions.append("review_top2_margin_queries_for_ambiguity")
    if telemetry["product_vote_dominant_rate"] > 0.15:
        next_actions.append("audit_product_vote_dominance_and_guardrails")

    if calibration["warnings"]:
        next_actions.append("expand_calibration_labels_and_retrain_model")

    cadence = {
        "weekly": [
            "run_relevance_regression",
            "run_observability_guard",
            "review_top_alerting_queries",
        ],
        "monthly": [
            "refresh_label_dataset",
            "retrain_confidence_calibration",
            "trend_decision_mix_and_alert_rates",
        ],
        "quarterly": [
            "synonym_governance_full_review",
            "threshold_window_recalibration",
            "coverage_expansion_for_new_query_patterns",
        ],
    }

    report: dict[str, Any] = {
        "telemetry_file": str(telemetry_path),
        "telemetry": telemetry,
        "regression_failure_count": regression_failures,
        "calibration": calibration,
        "next_actions": next_actions,
        "cadence": cadence,
        "overall_status": "attention_required" if next_actions else "healthy",
    }

    if args.output == "summary":
        report.pop("cadence", None)

    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
