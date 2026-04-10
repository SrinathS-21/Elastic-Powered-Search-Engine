from __future__ import annotations

from typing import Any

try:
    from ...core.clients import es
    from ...core.config import (
        HEAD_TERMS_HARD_CAP,
        HEAD_TERMS_PER_DOC_LIMIT,
        INDEX_NAME,
        KEYWORD_INDEX,
        KEYWORD_SUGGEST_DOCS,
        LONG_TAIL_TERMS_PER_DOC_LIMIT,
        SAMPLE_KEYWORD_MAP,
        VARIANT_TERMS_PER_DOC_LIMIT,
    )
    from .common import as_text, trim_terms
    from .query_text import (
        build_query_context,
        has_strong_term_evidence,
        is_noisy_suggestion_term,
        suggestion_rank_features,
        token_list,
    )
except ImportError:
    from core.clients import es
    from core.config import (
        HEAD_TERMS_HARD_CAP,
        HEAD_TERMS_PER_DOC_LIMIT,
        INDEX_NAME,
        KEYWORD_INDEX,
        KEYWORD_SUGGEST_DOCS,
        LONG_TAIL_TERMS_PER_DOC_LIMIT,
        SAMPLE_KEYWORD_MAP,
        VARIANT_TERMS_PER_DOC_LIMIT,
    )
    from services.internal.common import as_text, trim_terms
    from services.internal.query_text import (
        build_query_context,
        has_strong_term_evidence,
        is_noisy_suggestion_term,
        suggestion_rank_features,
        token_list,
    )


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
    anchor_token = context["anchor_tokens"][-1] if len(context["anchor_tokens"]) >= 2 else ""

    ranked: dict[str, dict] = {}

    try:
        if es.indices.exists(index=KEYWORD_INDEX):
            should_clauses: list[dict[str, Any]] = [
                {"match_phrase": {"keyword_name": {"query": intent_query, "boost": 12.0}}},
                {"match_phrase_prefix": {"keyword_name": {"query": intent_query, "boost": 9.0}}},
                {"match": {"keyword_name": {"query": intent_query, "operator": "and", "boost": 7.0}}},
                {"match": {"keyword_name.stem": {"query": intent_query, "operator": "and", "boost": 5.4}}},
                {"match_phrase_prefix": {"variant_terms": {"query": intent_query, "boost": 5.0}}},
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

            response = es.search(
                index=KEYWORD_INDEX,
                size=max(limit * 3, KEYWORD_SUGGEST_DOCS),
                _source=["keyword_name", "head_terms", "variant_terms", "long_tail_terms", "product_count", "category_count"],
                query={
                    "bool": {
                        "should": should_clauses,
                        "minimum_should_match": 1,
                    }
                },
            )

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

        if len(ranked) < limit and es.indices.exists(index=INDEX_NAME):
            response = es.search(
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
            )
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
    except Exception:
        pass

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

    if anchor_token:
        anchor_filtered = [item for item in ordered if anchor_token in token_list(item["value"])]
        if len(anchor_filtered) >= max(4, limit // 2):
            ordered = anchor_filtered

    strong = [item for item in ordered if item["stage"] <= 4]
    chosen = strong[:limit] if len(strong) >= limit else ordered[:limit]
    return [item["value"] for item in chosen]
