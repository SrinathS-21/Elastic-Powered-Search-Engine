from __future__ import annotations

from typing import Any

try:
    from ...core.clients import es
    from ...core.config import SAMPLE_KEYWORD_MAP
except ImportError:
    from core.clients import es
    from core.config import SAMPLE_KEYWORD_MAP


def as_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def trim_terms(values: list[Any], max_terms: int) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidate = as_text(value)
        if len(candidate) < 2:
            continue
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(candidate)
        if len(terms) >= max_terms:
            break
    return terms


def index_exists(index_name: str) -> bool:
    try:
        return bool(es.indices.exists(index=index_name))
    except Exception:
        return False


def index_doc_count(index_name: str, known_exists: bool | None = None) -> int:
    exists = known_exists if known_exists is not None else index_exists(index_name)
    if not exists:
        return 0
    try:
        return int(es.count(index=index_name)["count"])
    except Exception:
        return 0


def sample_hierarchy_cards(keyword: str, max_cards: int = 3) -> list[dict]:
    paths = SAMPLE_KEYWORD_MAP.get(keyword.lower(), [])
    cards: list[dict] = []
    for idx, path in enumerate(paths[:max_cards]):
        cards.append(
            {
                "breadcrumb": path,
                "count": max(1, 10 - idx * 2),
                "correlation_pct": max(35.0, 88.0 - idx * 14),
                "avg_token_coverage": max(0.35, 0.9 - idx * 0.18),
                "ranking_basis": {
                    "exact_hits": max(0, 3 - idx),
                    "prefix_hits": 1,
                    "token_and_hits": 1,
                    "semantic_hits": 0,
                },
                "sample_products": ["Sample mapping"],
            }
        )
    return cards
