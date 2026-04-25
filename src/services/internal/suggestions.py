"""Keyword suggestion generation from aggregated terms.

Generates search suggestions with term aggregation, demographic/gender token
filtering, and ranking by frequency and relevance.
"""

from __future__ import annotations

from collections.abc import Mapping
import time
from typing import Any

from src.core.clients import es
from src.core.config import (
    ES_REQUEST_TIMEOUT_SEC,
    HEAD_TERMS_HARD_CAP,
    HEAD_TERMS_PER_DOC_LIMIT,
    INDEX_NAME,
    KEYWORD_INDEX,
    KEYWORD_SUGGEST_DOCS,
    LONG_TAIL_TERMS_PER_DOC_LIMIT,
    SAMPLE_KEYWORD_MAP,
    VARIANT_TERMS_PER_DOC_LIMIT,
)
from src.core.logger import log
from src.services.internal.common import as_text, trim_terms
from src.services.internal.query_text import (
    build_query_context,
    canonical_token_list,
    has_strong_term_evidence,
    is_noisy_suggestion_term,
    suggestion_rank_features,
    token_list,
)


DEMOGRAPHIC_TOKENS = {
    "women", "woman", "ladies", "lady", "men", "man", "male", "female",
    "boys", "boy", "girls", "girl", "kids", "kid", "children", "child", "unisex",
}

_WARNING_RATE_LIMIT_SECONDS = 60.0
_LAST_WARNING_AT: dict[str, float] = {}
_INDEX_EXISTS_CACHE_TTL_SECONDS = 20.0
_INDEX_EXISTS_CACHE: dict[str, tuple[float, bool]] = {}


def _warn_rate_limited(key: str, message: str, *args: Any) -> None:
    now = time.monotonic()
    last = _LAST_WARNING_AT.get(key, 0.0)
    if now - last < _WARNING_RATE_LIMIT_SECONDS:
        return
    _LAST_WARNING_AT[key] = now
    log.warning(message, *args)


def _cached_index_exists(index_name: str, context: str) -> bool:
    cache_key = f"suggestions.index_exists.{context}"
    now = time.monotonic()
    cached = _INDEX_EXISTS_CACHE.get(cache_key)
    if cached and (now - cached[0]) <= _INDEX_EXISTS_CACHE_TTL_SECONDS:
        return cached[1]

    try:
        exists = bool(es.indices.exists(index=index_name))
    except Exception as exc:
        _warn_rate_limited(
            cache_key,
            "%s availability check failed: %s",
            context,
            exc,
        )
        exists = False

    _INDEX_EXISTS_CACHE[cache_key] = (now, exists)
    return exists


def _normalize_search_response(response: Any) -> dict[str, Any] | None:
    if isinstance(response, Mapping):
        return dict(response)

    body = getattr(response, "body", None)
    if isinstance(body, Mapping):
        return dict(body)

    return None


def _safe_search(context: str, **kwargs: Any) -> dict[str, Any] | None:
    try:
        response = es.search(**kwargs)
    except Exception as exc:
        _warn_rate_limited(
            f"suggestions.search.{context}",
            "Suggestion query failed (%s): %s",
            context,
            exc,
        )
        return None

    return _normalize_search_response(response)


def _suggestion_anchor_requirements(context: dict[str, Any]) -> tuple[set[str], set[str], bool]:
    canonical_intent = [
        as_text(token).lower().strip()
        for token in (context.get("canonical_intent_tokens") or [])
        if as_text(token).strip()
    ]
    anchor_tokens = [
        as_text(token).lower().strip()
        for token in (context.get("anchor_tokens") or [])
        if as_text(token).strip()
    ]
    canonical_anchor_tokens = canonical_token_list(" ".join(anchor_tokens))
    if not canonical_anchor_tokens:
        canonical_anchor_tokens = canonical_intent[-2:] if len(canonical_intent) >= 2 else canonical_intent[:]

    required_domain = {token for token in canonical_anchor_tokens if token and token not in DEMOGRAPHIC_TOKENS}
    required_demographic = {token for token in canonical_anchor_tokens if token in DEMOGRAPHIC_TOKENS}
    strict_mode = bool(len(canonical_intent) >= 3 and required_domain)
    return required_domain, required_demographic, strict_mode


def _merge_suggestion_candidate(
    bucket: dict[str, dict],
    term: str,
    query: str,
    score: float,
    frequency: int,
    source_priority: int,
) -> None:
    value = as_text(term)
    if len(value) < 2:
        return
    if is_noisy_suggestion_term(value):
        return

    key = value.lower()
    features = suggestion_rank_features(value, query)
    candidate = {
        "value": value,
        "stage": features["stage"],
        "token_coverage": features["token_coverage"],
        "first_pos": features["first_pos"],
        "first_token_mismatch": features["first_token_mismatch"],
        "starts_numeric": features["starts_numeric"],
        "length_delta": features["length_delta"],
        "score": float(score),
        "frequency": int(frequency),
        "source_priority": int(source_priority),
    }

    existing = bucket.get(key)
    if existing is None:
        bucket[key] = candidate
        return

    existing_key = (
        existing["stage"],
        -existing["token_coverage"],
        existing["first_token_mismatch"],
        existing["starts_numeric"],
        existing["first_pos"],
        existing["length_delta"],
        -existing["source_priority"],
        -existing["frequency"],
        -existing["score"],
        len(existing["value"]),
    )
    candidate_key = (
        candidate["stage"],
        -candidate["token_coverage"],
        candidate["first_token_mismatch"],
        candidate["starts_numeric"],
        candidate["first_pos"],
        candidate["length_delta"],
        -candidate["source_priority"],
        -candidate["frequency"],
        -candidate["score"],
        len(candidate["value"]),
    )
    if candidate_key < existing_key:
        bucket[key] = candidate


def fetch_keyword_suggestions(query: str, limit: int = 12) -> list[str]:
    raw_query = query.strip()
    context = build_query_context(raw_query)
    normalized_query = context["normalized_query"]
    intent_query = context["intent_query"]
    if not normalized_query:
        return sorted(SAMPLE_KEYWORD_MAP.keys())[:limit]

    query_tokens = context["raw_tokens"]
    ranked_query_tokens = context["intent_tokens"]
    significant_query_tokens = context["intent_tokens"]
    query_ends_with_noise_token = context["ends_with_noise"]
    required_domain_anchors, required_demographic_anchors, strict_anchor_mode = _suggestion_anchor_requirements(context)

    ranked: dict[str, dict] = {}

    keyword_index_available = _cached_index_exists(KEYWORD_INDEX, "keyword_index")

    if keyword_index_available:
        should_clauses: list[dict[str, Any]] = [
            {"match_phrase": {"keyword_name": {"query": intent_query, "boost": 12.0}}},
            {"match_phrase_prefix": {"keyword_name": {"query": intent_query, "boost": 9.0}}},
            {"match": {"keyword_name": {"query": intent_query, "operator": "and", "boost": 7.0}}},
            {"match": {"keyword_name.stem": {"query": intent_query, "operator": "and", "boost": 5.4}}},
            {"match": {"variant_terms": {"query": intent_query, "operator": "and", "boost": 4.0}}},
            {"match": {"variant_terms.stem": {"query": intent_query, "operator": "and", "boost": 3.8}}},
            {"match": {"long_tail_terms": {"query": intent_query, "operator": "and", "boost": 1.8}}},
            {"match": {"long_tail_terms.stem": {"query": intent_query, "operator": "and", "boost": 1.5}}},
            {"match": {"head_terms": {"query": intent_query, "boost": 0.6}}},
            {"match": {"keyword_name": {"query": intent_query, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.8}}},
        ]

        for phrase in context["phrase_candidates"][:4]:
            should_clauses.append({"match_phrase": {"keyword_name": {"query": phrase, "boost": 10.5}}})
            should_clauses.append({"match_phrase": {"variant_terms": {"query": phrase, "boost": 3.8}}})

        if intent_query != normalized_query:
            should_clauses.append(
                {"match": {"keyword_name": {"query": normalized_query, "operator": "and", "boost": 2.0}}}
            )

        response = _safe_search(
            "keyword_index",
            index=KEYWORD_INDEX,
            size=max(limit * 3, KEYWORD_SUGGEST_DOCS),
            _source=["keyword_name", "head_terms", "variant_terms", "long_tail_terms", "product_count", "category_count"],
            query={
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
            request_timeout=ES_REQUEST_TIMEOUT_SEC,
        )

        if response:
            for hit in response.get("hits", {}).get("hits", []):
                source = hit.get("_source", {})
                doc_score = float(hit.get("_score") or 0.0)
                doc_frequency = int(source.get("product_count") or 0)

                _merge_suggestion_candidate(
                    ranked,
                    source.get("keyword_name", ""),
                    intent_query,
                    score=doc_score,
                    frequency=doc_frequency,
                    source_priority=6,
                )

                for candidate in trim_terms(source.get("variant_terms") or [], VARIANT_TERMS_PER_DOC_LIMIT):
                    if not has_strong_term_evidence(candidate, normalized_query, ranked_query_tokens):
                        continue
                    if suggestion_rank_features(candidate, normalized_query)["stage"] > 4:
                        continue
                    _merge_suggestion_candidate(
                        ranked,
                        candidate,
                        intent_query,
                        score=doc_score,
                        frequency=doc_frequency,
                        source_priority=5,
                    )

                for candidate in trim_terms(source.get("long_tail_terms") or [], LONG_TAIL_TERMS_PER_DOC_LIMIT):
                    if not has_strong_term_evidence(candidate, normalized_query, ranked_query_tokens):
                        continue
                    if suggestion_rank_features(candidate, normalized_query)["stage"] > 4:
                        continue
                    _merge_suggestion_candidate(
                        ranked,
                        candidate,
                        intent_query,
                        score=doc_score,
                        frequency=doc_frequency,
                        source_priority=3,
                    )

                head_terms = source.get("head_terms") or []
                if len(head_terms) <= HEAD_TERMS_HARD_CAP:
                    for candidate in trim_terms(head_terms, HEAD_TERMS_PER_DOC_LIMIT):
                        if not has_strong_term_evidence(candidate, normalized_query, ranked_query_tokens):
                            continue
                        if suggestion_rank_features(candidate, normalized_query)["stage"] > 4:
                            continue
                        _merge_suggestion_candidate(
                            ranked,
                            candidate,
                            intent_query,
                            score=doc_score,
                            frequency=doc_frequency,
                            source_priority=2,
                        )

    product_index_available = _cached_index_exists(INDEX_NAME, "product_index")

    # Product index fallback is expensive on remote clusters.
    # Use it only when keyword-index scoring produced no candidates.
    if not ranked and product_index_available:
        response = _safe_search(
            "product_index",
            index=INDEX_NAME,
            size=max(6, limit),
            _source=["productName", "suggest_text"],
            query={
                "bool": {
                    "should": [
                        {"match_phrase": {"productName": {"query": normalized_query, "boost": 8.0}}},
                        {"match_phrase_prefix": {"productName": {"query": normalized_query, "boost": 6.0}}},
                        {
                            "multi_match": {
                                "query": normalized_query,
                                "type": "bool_prefix",
                                "fields": [
                                    "productName_autocomplete",
                                    "productName_autocomplete._2gram",
                                    "productName_autocomplete._3gram",
                                ],
                                "boost": 5.8,
                            }
                        },
                        {"match": {"productName.stem": {"query": normalized_query, "operator": "and", "boost": 4.0}}},
                        {"match": {"search_text": {"query": normalized_query, "operator": "and", "boost": 2.0}}},
                        {"match": {"suggest_text": {"query": normalized_query, "operator": "and", "boost": 2.8}}},
                        {"match": {"suggest_text.stem": {"query": normalized_query, "operator": "and", "boost": 2.1}}},
                        {"match": {"productName.ngram": {"query": normalized_query, "operator": "and", "boost": 3.0}}},
                        {"match": {"productName": {"query": normalized_query, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.8}}},
                        {"match": {"productName": {"query": intent_query, "operator": "and", "boost": 2.0}}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            request_timeout=ES_REQUEST_TIMEOUT_SEC,
        )
        if response:
            for hit in response.get("hits", {}).get("hits", []):
                name = as_text(hit.get("_source", {}).get("productName"))
                if not name:
                    continue
                _merge_suggestion_candidate(
                    ranked,
                    name,
                    intent_query,
                    score=float(hit.get("_score") or 0.0),
                    frequency=0,
                    source_priority=1,
                )

    if not ranked:
        fallback = [key for key in SAMPLE_KEYWORD_MAP if intent_query in key.lower() or normalized_query in key.lower()]
        return fallback[:limit]

    ordered = sorted(
        ranked.values(),
        key=lambda item: (
            item["stage"],
            -item["token_coverage"],
            item["first_token_mismatch"],
            item["starts_numeric"],
            item["first_pos"],
            item["length_delta"],
            -item["source_priority"],
            -item["frequency"],
            -item["score"],
            len(item["value"]),
            item["value"].lower(),
        ),
    )

    if len(query_tokens) >= 2:
        denoised: list[dict] = []
        for item in ordered:
            term_tokens = token_list(item["value"])
            if item["stage"] >= 5 and term_tokens and term_tokens[0] != query_tokens[0]:
                continue
            denoised.append(item)
        ordered = denoised

    if query_ends_with_noise_token and significant_query_tokens:
        prefix_filtered: list[dict] = []
        prefix_len = len(significant_query_tokens)
        for item in ordered:
            term_tokens = token_list(item["value"])
            if term_tokens[:prefix_len] != significant_query_tokens:
                continue
            prefix_filtered.append(item)
        if prefix_filtered:
            ordered = prefix_filtered

    if required_domain_anchors:
        anchor_filtered: list[dict] = []
        for item in ordered:
            candidate_tokens = set(canonical_token_list(item["value"]))
            if not candidate_tokens:
                continue
            if not (required_domain_anchors & candidate_tokens):
                continue
            if strict_anchor_mode and required_demographic_anchors and not (required_demographic_anchors & candidate_tokens):
                continue
            anchor_filtered.append(item)

        if len(anchor_filtered) >= max(4, limit // 2):
            ordered = anchor_filtered

    strong = [item for item in ordered if item["stage"] <= 4]
    chosen = strong[:limit] if len(strong) >= limit else ordered[:limit]
    return [item["value"] for item in chosen]
