"""Synonym expansion and token coverage analysis.

Expands queries with synonyms and analyzes token coverage for improved search recall.
"""

from __future__ import annotations

import math
from functools import lru_cache

from src.core.synonym_data import load_synonym_map
from src.services.internal.query_text import canonical_token_list

DEFAULT_BENCHMARK_QUERIES = [
    "stainless steel pipe",
    "ms channel",
    "industrial valve",
    "safety gloves",
    "ro water purifier",
    "led flood light",
    "air compressor",
    "welding machine",
    "pvc pipe",
    "electrical cable",
    "bolt and nut",
    "hydraulic pump",
    "gear motor",
    "packing tape",
    "bearing",
    "conveyor belt",
    "sheet metal",
    "industrial fan",
    "power supply",
    "drill machine",
]

COMPACT_BENCHMARK_QUERIES = DEFAULT_BENCHMARK_QUERIES[:10]


@lru_cache(maxsize=1)
def _synonym_map() -> dict[str, str]:
    return load_synonym_map()


def synonym_source_tokens() -> set[str]:
    return set(_synonym_map().keys())


def expand_synonyms(query: str) -> str | None:
    synonym_map = _synonym_map()
    if not synonym_map:
        return None
    words = query.lower().split()
    expanded = [synonym_map.get(w, w) for w in words]
    result = " ".join(expanded)
    return result if result != query.lower() else None


def tokenize_for_match(text: str) -> set[str]:
    if not text:
        return set()
    return {token for token in canonical_token_list(text) if len(token) >= 2}


def token_coverage(query: str, text: str) -> float:
    query_tokens = tokenize_for_match(query)
    if not query_tokens:
        return 0.0
    text_tokens = tokenize_for_match(text)
    if not text_tokens:
        return 0.0
    matched = len(query_tokens & text_tokens)
    return matched / len(query_tokens)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil((pct / 100) * len(ordered)) - 1))
    return float(ordered[idx])
