from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.services.internal.calibration import calibration_metrics, fit_isotonic_calibration


def _map_query_to_categories(query: str, selected_suggestion: str | None) -> dict[str, Any]:
    try:
        from src.services.mapping import map_query_to_categories
    except Exception as exc:  # pragma: no cover - import guard for CLI usability
        raise RuntimeError(
            "Mapping runtime dependencies are unavailable. Install project dependencies or use labels with raw_confidence+is_correct fields."
        ) from exc

    return map_query_to_categories(query, selected_suggestion=selected_suggestion, max_cards=3)


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _derive_label(entry: dict[str, Any], top_category_id: str, breadcrumb: str) -> int | None:
    if "is_correct" in entry:
        return 1 if bool(entry.get("is_correct")) else 0

    expected_category_id = _as_text(entry.get("expected_category_id")).strip()
    if expected_category_id:
        return 1 if top_category_id == expected_category_id else 0

    expected_any = [
        _as_text(token).strip().lower()
        for token in (entry.get("expected_any") or [])
        if _as_text(token).strip()
    ]
    expected_all = [
        _as_text(token).strip().lower()
        for token in (entry.get("expected_all") or [])
        if _as_text(token).strip()
    ]
    banned_any = [
        _as_text(token).strip().lower()
        for token in (entry.get("banned_any") or [])
        if _as_text(token).strip()
    ]

    if not expected_any and not expected_all and not banned_any:
        return None

    text = breadcrumb.lower()
    if expected_any and not any(token in text for token in expected_any):
        return 0
    if expected_all and not all(token in text for token in expected_all):
        return 0
    if banned_any and any(token in text for token in banned_any):
        return 0
    return 1


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train isotonic confidence calibration for mapping")
    parser.add_argument("--labels", default="config/mapping_calibration_labels.jsonl")
    parser.add_argument("--output", default="config/mapping_confidence_calibration.json")
    parser.add_argument("--max-samples", type=int, default=0, help="0 means all")
    parser.add_argument("--metrics-bins", type=int, default=10)
    parser.add_argument("--output-mode", choices=["summary", "full"], default="summary")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    labels_path = Path(args.labels)
    output_path = Path(args.output)

    entries = _load_jsonl(labels_path)
    if args.max_samples and args.max_samples > 0:
        entries = entries[: args.max_samples]

    if not entries:
        print(json.dumps({"error": "No labels found", "labels": str(labels_path)}, indent=2, ensure_ascii=True))
        raise SystemExit(1)

    scores: list[float] = []
    labels: list[int] = []
    sample_rows: list[dict[str, Any]] = []
    skipped = 0

    for entry in entries:
        if "raw_confidence" in entry and "is_correct" in entry:
            raw_conf = float(entry.get("raw_confidence") or 0.0)
            label = 1 if bool(entry.get("is_correct")) else 0
            scores.append(raw_conf)
            labels.append(label)
            sample_rows.append(
                {
                    "query": _as_text(entry.get("query")),
                    "top_category_id": _as_text(entry.get("expected_category_id")),
                    "top_breadcrumb": _as_text(entry.get("expected_any")),
                    "raw_confidence": round(raw_conf, 4),
                    "label": label,
                    "source": "direct",
                }
            )
            continue

        query = _as_text(entry.get("query")).strip()
        if not query:
            skipped += 1
            continue

        selected = _as_text(entry.get("selected_suggestion")).strip() or None
        payload = _map_query_to_categories(query, selected_suggestion=selected)
        top = payload.get("top_category") or {}

        top_category_id = _as_text(top.get("product_category_id"))
        top_breadcrumb = _as_text(top.get("breadcrumb"))
        raw_confidence = float(top.get("confidence_raw") or top.get("confidence") or payload.get("confidence") or 0.0)

        label = _derive_label(entry, top_category_id=top_category_id, breadcrumb=top_breadcrumb)
        if label is None:
            skipped += 1
            continue

        scores.append(raw_confidence)
        labels.append(label)
        sample_rows.append(
            {
                "query": query,
                "top_category_id": top_category_id,
                "top_breadcrumb": top_breadcrumb,
                "raw_confidence": round(raw_confidence, 4),
                "label": int(label),
                "source": "query",
            }
        )

    if not scores:
        print(
            json.dumps(
                {
                    "error": "No training samples produced",
                    "labels": str(labels_path),
                    "skipped": skipped,
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        raise SystemExit(1)

    model = fit_isotonic_calibration(list(zip(scores, labels)))
    metrics = calibration_metrics(scores=scores, labels=labels, model=model, bins=max(2, int(args.metrics_bins)))
    positives = int(sum(labels))
    negatives = int(len(labels) - positives)

    warnings: list[str] = []
    if len(scores) < 50:
        warnings.append("low_sample_count: recommended >= 50 labeled rows for stable calibration")
    if positives == 0 or negatives == 0:
        warnings.append("single_class_labels: both positive and negative labels are required")
    elif min(positives, negatives) < 10:
        warnings.append("class_imbalance: recommended >= 10 samples per class")

    payload = {
        **model,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "source_labels": str(labels_path),
        "metrics": metrics,
        "class_balance": {
            "positives": positives,
            "negatives": negatives,
        },
        "warnings": warnings,
        "skipped_rows": skipped,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    summary = {
        "labels": str(labels_path),
        "output": str(output_path),
        "samples_used": len(scores),
        "positives": positives,
        "negatives": negatives,
        "metrics": metrics,
        "warnings": warnings,
    }

    if args.output_mode == "full":
        summary["sample_rows"] = sample_rows[: min(30, len(sample_rows))]
        summary["model_preview"] = {
            "breakpoints": payload.get("breakpoints", [])[:15],
            "values": payload.get("values", [])[:15],
        }

    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
