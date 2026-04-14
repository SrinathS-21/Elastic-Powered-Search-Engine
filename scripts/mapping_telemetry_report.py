from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return float(ordered[index])


def _load_rows(path: Path, last: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    if last > 0 and len(lines) > last:
        lines = lines[-last:]

    rows: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _default_path() -> Path:
    env = os.getenv("MAPPING_TELEMETRY_FILE", "")
    if env.strip():
        return Path(env)
    return Path("logs") / "mapping_telemetry.jsonl"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize mapping telemetry and suggest threshold tuning windows")
    parser.add_argument("--file", default=str(_default_path()))
    parser.add_argument("--last", type=int, default=1000, help="Analyze only last N rows (0 for all)")
    parser.add_argument("--output", choices=["summary", "full"], default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.file)
    rows = _load_rows(path=path, last=max(0, int(args.last)))

    if not rows:
        print(json.dumps({"file": str(path), "rows": 0, "message": "No telemetry rows found"}, indent=2, ensure_ascii=True))
        raise SystemExit(1)

    decisions = Counter(str(row.get("decision") or "unknown") for row in rows)
    phase3_enabled = sum(1 for row in rows if bool(row.get("phase3_active")))
    semantic_used = sum(1 for row in rows if bool(row.get("semantic_used")))
    fallback_used = sum(1 for row in rows if bool(row.get("product_fallback_used")))
    model_used = sum(1 for row in rows if bool(row.get("top_confidence_model_used")))

    alert_counts: Counter[str] = Counter()
    for row in rows:
        for alert in row.get("alerts") or []:
            alert_counts[str(alert)] += 1

    confidences = [float(row.get("confidence") or 0.0) for row in rows]
    margins = [float(row.get("margin") or 0.0) for row in rows]

    p25_conf = _percentile(confidences, 25)
    p50_conf = _percentile(confidences, 50)
    p75_conf = _percentile(confidences, 75)
    p90_conf = _percentile(confidences, 90)

    p25_margin = _percentile(margins, 25)
    p50_margin = _percentile(margins, 50)
    p75_margin = _percentile(margins, 75)

    # Heuristic tuning window based on observed distribution.
    suggested_confirm = max(0.35, min(0.62, round(p50_conf, 3)))
    suggested_auto_map = max(suggested_confirm + 0.12, min(0.82, round(p90_conf, 3)))
    suggested_auto_map_margin = max(0.08, min(0.18, round(p75_margin, 3)))
    suggested_low_conf_alert = max(0.22, min(0.40, round(p25_conf, 3)))

    summary = {
        "file": str(path),
        "rows": len(rows),
        "phase3_enabled_pct": round((phase3_enabled / len(rows)) * 100.0, 1),
        "semantic_used_pct": round((semantic_used / len(rows)) * 100.0, 1),
        "product_fallback_used_pct": round((fallback_used / len(rows)) * 100.0, 1),
        "learned_confidence_model_used_pct": round((model_used / len(rows)) * 100.0, 1),
        "decision_counts": dict(decisions),
        "alerts": dict(alert_counts),
        "confidence_distribution": {
            "p25": round(p25_conf, 4),
            "p50": round(p50_conf, 4),
            "p75": round(p75_conf, 4),
            "p90": round(p90_conf, 4),
        },
        "margin_distribution": {
            "p25": round(p25_margin, 4),
            "p50": round(p50_margin, 4),
            "p75": round(p75_margin, 4),
        },
        "suggested_threshold_window": {
            "confirm": round(suggested_confirm, 3),
            "auto_map": round(suggested_auto_map, 3),
            "auto_map_margin": round(suggested_auto_map_margin, 3),
            "low_confidence_alert": round(suggested_low_conf_alert, 3),
        },
    }

    if args.output == "full":
        sample = rows[-min(5, len(rows)) :]
        summary["sample_rows"] = sample

    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
