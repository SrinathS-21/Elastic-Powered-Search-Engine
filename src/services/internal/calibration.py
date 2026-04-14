from __future__ import annotations

from typing import Any


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def _safe_label(value: Any) -> int:
    return 1 if bool(value) else 0


def fit_isotonic_calibration(samples: list[tuple[float, int]]) -> dict[str, Any]:
    if not samples:
        raise ValueError("at least one sample is required")

    ordered = sorted((clamp(float(score), 0.0, 1.0), _safe_label(label)) for score, label in samples)
    blocks: list[dict[str, float]] = []

    for score, label in ordered:
        blocks.append(
            {
                "left": score,
                "right": score,
                "sum_y": float(label),
                "weight": 1.0,
            }
        )

        while len(blocks) >= 2:
            prev = blocks[-2]
            curr = blocks[-1]
            prev_mean = prev["sum_y"] / prev["weight"]
            curr_mean = curr["sum_y"] / curr["weight"]
            if prev_mean <= curr_mean:
                break

            merged = {
                "left": prev["left"],
                "right": curr["right"],
                "sum_y": prev["sum_y"] + curr["sum_y"],
                "weight": prev["weight"] + curr["weight"],
            }
            blocks[-2:] = [merged]

    breakpoints: list[float] = []
    values: list[float] = []
    for block in blocks:
        breakpoints.append(clamp(float(block["right"]), 0.0, 1.0))
        values.append(clamp(float(block["sum_y"] / block["weight"]), 0.0, 1.0))

    return {
        "version": 1,
        "kind": "isotonic_step",
        "breakpoints": breakpoints,
        "values": values,
        "sample_count": len(samples),
        "positive_count": sum(label for _score, label in ordered),
    }


def apply_isotonic_calibration(score: float, model: dict[str, Any]) -> float:
    breakpoints = model.get("breakpoints") or []
    values = model.get("values") or []
    if not breakpoints or len(breakpoints) != len(values):
        return clamp(float(score), 0.0, 1.0)

    x = clamp(float(score), 0.0, 1.0)
    for idx, right in enumerate(breakpoints):
        if x <= float(right):
            return clamp(float(values[idx]), 0.0, 1.0)
    return clamp(float(values[-1]), 0.0, 1.0)


def is_calibration_model_valid(model: dict[str, Any]) -> bool:
    breakpoints = model.get("breakpoints")
    values = model.get("values")
    if not isinstance(breakpoints, list) or not isinstance(values, list):
        return False
    if not breakpoints or len(breakpoints) != len(values):
        return False

    prev = -1.0
    for right in breakpoints:
        value = float(right)
        if value < prev:
            return False
        prev = value
    return True


def _brier_score(scores: list[float], labels: list[int]) -> float:
    if not scores:
        return 0.0
    total = 0.0
    for score, label in zip(scores, labels):
        total += (clamp(float(score), 0.0, 1.0) - float(_safe_label(label))) ** 2
    return total / len(scores)


def _ece_score(scores: list[float], labels: list[int], bins: int) -> float:
    if not scores:
        return 0.0

    total = len(scores)
    bins = max(2, int(bins))
    error = 0.0

    for idx in range(bins):
        left = idx / bins
        right = (idx + 1) / bins
        if idx == bins - 1:
            members = [
                i
                for i, score in enumerate(scores)
                if clamp(float(score), 0.0, 1.0) >= left and clamp(float(score), 0.0, 1.0) <= right
            ]
        else:
            members = [
                i
                for i, score in enumerate(scores)
                if clamp(float(score), 0.0, 1.0) >= left and clamp(float(score), 0.0, 1.0) < right
            ]

        if not members:
            continue

        avg_conf = sum(scores[i] for i in members) / len(members)
        avg_acc = sum(labels[i] for i in members) / len(members)
        error += abs(avg_conf - avg_acc) * (len(members) / total)

    return error


def calibration_metrics(scores: list[float], labels: list[int], model: dict[str, Any], bins: int = 10) -> dict[str, float]:
    raw_scores = [clamp(float(score), 0.0, 1.0) for score in scores]
    safe_labels = [_safe_label(label) for label in labels]
    calibrated_scores = [apply_isotonic_calibration(score, model) for score in raw_scores]

    return {
        "brier_raw": round(_brier_score(raw_scores, safe_labels), 6),
        "brier_calibrated": round(_brier_score(calibrated_scores, safe_labels), 6),
        "ece_raw": round(_ece_score(raw_scores, safe_labels, bins=bins), 6),
        "ece_calibrated": round(_ece_score(calibrated_scores, safe_labels, bins=bins), 6),
    }
