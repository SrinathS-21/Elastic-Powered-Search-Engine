from __future__ import annotations

import math
from typing import Any

try:
    from ..core.clients import es
    from ..core.config import (
        AUTO_MAP_CONFIDENCE as CONFIG_AUTO_MAP_CONFIDENCE,
        AUTO_MAP_MARGIN as CONFIG_AUTO_MAP_MARGIN,
        CONFIRM_MAP_CONFIDENCE as CONFIG_CONFIRM_MAP_CONFIDENCE,
        HEAD_TERMS_HARD_CAP,
        INDEX_NAME,
        KEYWORD_CLUSTER_FETCH_SIZE,
        KEYWORD_INDEX,
        KEYWORD_P95_PRODUCT_COUNT,
        PRODUCT_FALLBACK_TRIGGER as CONFIG_PRODUCT_FALLBACK_TRIGGER,
        PRODUCT_MAIN_VOTE_SHARE as CONFIG_PRODUCT_MAIN_VOTE_SHARE,
        PRODUCT_SHORT_VOTE_SHARE as CONFIG_PRODUCT_SHORT_VOTE_SHARE,
        PRODUCT_VOTE_WEIGHT as CONFIG_PRODUCT_VOTE_WEIGHT,
        RELIABILITY_BETA,
        SEMANTIC_CLUSTER_WEIGHT as CONFIG_SEMANTIC_CLUSTER_WEIGHT,
    )
    from .internal.common import as_text, clamp, index_exists, trim_terms
    from .internal.query_text import (
        build_query_context,
        canonical_tokens,
        normalize_query_text,
        significant_tokens,
        suggestion_rank_features,
        token_list,
    )
    from ..ml.embeddings import encode_query_text
except ImportError:
    from core.clients import es
    from core.config import (
        AUTO_MAP_CONFIDENCE as CONFIG_AUTO_MAP_CONFIDENCE,
        AUTO_MAP_MARGIN as CONFIG_AUTO_MAP_MARGIN,
        CONFIRM_MAP_CONFIDENCE as CONFIG_CONFIRM_MAP_CONFIDENCE,
        HEAD_TERMS_HARD_CAP,
        INDEX_NAME,
        KEYWORD_CLUSTER_FETCH_SIZE,
        KEYWORD_INDEX,
        KEYWORD_P95_PRODUCT_COUNT,
        PRODUCT_FALLBACK_TRIGGER as CONFIG_PRODUCT_FALLBACK_TRIGGER,
        PRODUCT_MAIN_VOTE_SHARE as CONFIG_PRODUCT_MAIN_VOTE_SHARE,
        PRODUCT_SHORT_VOTE_SHARE as CONFIG_PRODUCT_SHORT_VOTE_SHARE,
        PRODUCT_VOTE_WEIGHT as CONFIG_PRODUCT_VOTE_WEIGHT,
        RELIABILITY_BETA,
        SEMANTIC_CLUSTER_WEIGHT as CONFIG_SEMANTIC_CLUSTER_WEIGHT,
    )
    from services.internal.common import as_text, clamp, index_exists, trim_terms
    from services.internal.query_text import (
        build_query_context,
        canonical_tokens,
        normalize_query_text,
        significant_tokens,
        suggestion_rank_features,
        token_list,
    )
    try:
        from ml.embeddings import encode_query_text
    except ImportError:
        from src.ml.embeddings import encode_query_text


# Mutable runtime knobs used by tuning scripts.
AUTO_MAP_CONFIDENCE = CONFIG_AUTO_MAP_CONFIDENCE
AUTO_MAP_MARGIN = CONFIG_AUTO_MAP_MARGIN
CONFIRM_MAP_CONFIDENCE = CONFIG_CONFIRM_MAP_CONFIDENCE
PRODUCT_FALLBACK_TRIGGER = CONFIG_PRODUCT_FALLBACK_TRIGGER
SEMANTIC_CLUSTER_WEIGHT = CONFIG_SEMANTIC_CLUSTER_WEIGHT
PRODUCT_VOTE_WEIGHT = CONFIG_PRODUCT_VOTE_WEIGHT
PRODUCT_MAIN_VOTE_SHARE = CONFIG_PRODUCT_MAIN_VOTE_SHARE
PRODUCT_SHORT_VOTE_SHARE = CONFIG_PRODUCT_SHORT_VOTE_SHARE


def _reliability_factor(product_count: int, category_count: int) -> float:
    support = math.log1p(max(0, product_count)) / math.log1p(KEYWORD_P95_PRODUCT_COUNT)
    support = clamp(support, 0.0, 1.25)
    ambiguity = 1.0 / (1.0 + RELIABILITY_BETA * max(0, category_count - 1))
    return clamp(support * ambiguity, 0.02, 1.25)


def _keyword_cluster_lexical_hits(query_text: str, size: int, phrase_candidates: list[str] | None = None) -> list[dict]:
    if not query_text or not index_exists(KEYWORD_INDEX):
        return []

    try:
        should_clauses: list[dict[str, Any]] = [
            {"match_phrase": {"keyword_name": {"query": query_text, "boost": 12.0}}},
            {"match_phrase_prefix": {"keyword_name": {"query": query_text, "boost": 10.0}}},
            {"match": {"keyword_name": {"query": query_text, "operator": "and", "boost": 8.0}}},
            {"match": {"keyword_name.stem": {"query": query_text, "operator": "and", "boost": 6.0}}},
            {"match_phrase_prefix": {"variant_terms": {"query": query_text, "boost": 6.0}}},
            {"match": {"variant_terms": {"query": query_text, "operator": "and", "boost": 4.5}}},
            {"match": {"variant_terms.stem": {"query": query_text, "operator": "and", "boost": 3.9}}},
            {"match": {"long_tail_terms": {"query": query_text, "operator": "and", "boost": 2.0}}},
            {"match": {"long_tail_terms.stem": {"query": query_text, "operator": "and", "boost": 1.7}}},
            {"match": {"keyword_name": {"query": query_text, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.8}}},
        ]
        for phrase in (phrase_candidates or [])[:4]:
            should_clauses.append({"match_phrase": {"keyword_name": {"query": phrase, "boost": 10.5}}})
            should_clauses.append({"match_phrase": {"variant_terms": {"query": phrase, "boost": 3.8}}})

        response = es.search(
            index=KEYWORD_INDEX,
            size=size,
            _source=[
                "keyword_name",
                "head_terms",
                "variant_terms",
                "long_tail_terms",
                "product_count",
                "category_count",
                "product_category_ids",
            ],
            query={
                "bool": {
                    "should": should_clauses,
                    "minimum_should_match": 1,
                }
            },
        )
    except Exception:
        return []

    return response.get("hits", {}).get("hits", [])


def _keyword_cluster_semantic_hits(query_text: str, size: int) -> list[dict]:
    if not query_text or not index_exists(KEYWORD_INDEX):
        return []

    try:
        query_vector = list(encode_query_text(query_text))
    except Exception:
        return []

    vector_lanes = [
        ("keyword_vector_longtail", 0.62),
        ("keyword_vector_variants", 0.38),
    ]
    merged: dict[str, dict[str, Any]] = {}

    for field_name, lane_weight in vector_lanes:
        try:
            response = es.search(
                index=KEYWORD_INDEX,
                size=size,
                _source=[
                    "keyword_name",
                    "head_terms",
                    "variant_terms",
                    "long_tail_terms",
                    "product_count",
                    "category_count",
                    "product_category_ids",
                ],
                knn={
                    "field": field_name,
                    "query_vector": query_vector,
                    "k": size,
                    "num_candidates": max(120, size * 4),
                },
            )
        except Exception:
            continue

        hits = response.get("hits", {}).get("hits", [])
        if not hits:
            continue

        max_lane_score = max((float(hit.get("_score") or 0.0) for hit in hits), default=1.0) or 1.0
        for hit in hits:
            hit_id = as_text(hit.get("_id"))
            if not hit_id:
                continue

            lane_score = clamp(float(hit.get("_score") or 0.0) / max_lane_score, 0.0, 1.0)
            weighted_score = lane_score * lane_weight

            bucket = merged.setdefault(
                hit_id,
                {
                    "_id": hit_id,
                    "_source": hit.get("_source", {}),
                    "_score": 0.0,
                },
            )
            bucket["_score"] += weighted_score
            if not bucket.get("_source") and hit.get("_source"):
                bucket["_source"] = hit.get("_source", {})

    if not merged:
        return []

    ranked = sorted(merged.values(), key=lambda item: float(item.get("_score") or 0.0), reverse=True)
    return ranked[:size]


def _cluster_match_signal(source: dict, normalized_query: str, query_tokens: list[str]) -> float:
    if not normalized_query:
        return 0.0

    candidates: list[str] = []
    keyword_name = as_text(source.get("keyword_name"))
    if keyword_name:
        candidates.append(keyword_name)

    candidates.extend(trim_terms(source.get("variant_terms") or [], 10))
    candidates.extend(trim_terms(source.get("long_tail_terms") or [], 6))

    head_terms = source.get("head_terms") or []
    if len(head_terms) <= HEAD_TERMS_HARD_CAP:
        candidates.extend(trim_terms(head_terms, 6))

    significant_query_tokens = canonical_tokens(significant_tokens(query_tokens))
    stage_weight = {0: 1.0, 1: 0.92, 2: 0.84, 3: 0.76, 4: 0.68, 5: 0.55, 6: 0.42, 7: 0.3}

    best = 0.0
    for candidate in candidates:
        candidate_norm = normalize_query_text(candidate)
        if not candidate_norm:
            continue

        features = suggestion_rank_features(candidate_norm, normalized_query)
        base = stage_weight.get(features["stage"], 0.2)

        candidate_tokens = canonical_tokens(token_list(candidate_norm))
        token_overlap = 0.0
        if significant_query_tokens:
            token_overlap = len(set(significant_query_tokens) & set(candidate_tokens)) / max(1, len(significant_query_tokens))

        phrase_bonus = 0.15 if normalized_query in candidate_norm else 0.0
        prefix_bonus = 0.1 if candidate_norm.startswith(normalized_query) else 0.0

        signal = clamp(base * 0.6 + token_overlap * 0.4 + phrase_bonus + prefix_bonus, 0.0, 1.0)
        if signal > best:
            best = signal

    return best


def _new_vote_bucket() -> dict[str, Any]:
    return {
        "raw_score": 0.0,
        "cluster_hits": 0,
        "lexical_cluster_hits": 0,
        "semantic_cluster_hits": 0,
        "product_vote_hits": 0,
        "support_sum": 0.0,
        "ambiguity_sum": 0.0,
        "match_signal_sum": 0.0,
        "sample_products": [],
        "sample_keywords": [],
    }


def _accumulate_cluster_votes(
    category_votes: dict[str, dict[str, Any]],
    hits: list[dict],
    normalized_query: str,
    lane: str,
    lane_weight: float,
) -> int:
    if not hits:
        return 0

    query_tokens = token_list(normalized_query)
    max_score = max((float(hit.get("_score") or 0.0) for hit in hits), default=1.0) or 1.0
    docs_used = 0

    for hit in hits:
        source = hit.get("_source", {})
        category_ids = [as_text(value) for value in (source.get("product_category_ids") or [])]
        category_ids = [value for value in category_ids if value]
        if not category_ids:
            continue

        product_count = int(source.get("product_count") or 0)
        category_count = int(source.get("category_count") or len(set(category_ids)) or 1)
        reliability = _reliability_factor(product_count, category_count)
        match_signal = _cluster_match_signal(source, normalized_query, query_tokens)
        score_norm = clamp(float(hit.get("_score") or 0.0) / max_score, 0.0, 1.0)
        doc_vote = lane_weight * ((0.55 * score_norm) + (0.45 * match_signal)) * reliability
        per_category_vote = doc_vote / max(1, len(set(category_ids)))

        docs_used += 1
        keyword_name = as_text(source.get("keyword_name"))

        for category_id in sorted(set(category_ids)):
            bucket = category_votes.setdefault(category_id, _new_vote_bucket())
            bucket["raw_score"] += per_category_vote
            bucket["cluster_hits"] += 1
            if lane == "semantic":
                bucket["semantic_cluster_hits"] += 1
            else:
                bucket["lexical_cluster_hits"] += 1

            bucket["support_sum"] += float(product_count)
            bucket["ambiguity_sum"] += float(category_count)
            bucket["match_signal_sum"] += match_signal

            if keyword_name and keyword_name not in bucket["sample_keywords"] and len(bucket["sample_keywords"]) < 3:
                bucket["sample_keywords"].append(keyword_name)

    return docs_used


def _product_knn_hits(query_vector: list[float], field_name: str, size: int) -> list[dict]:
    try:
        response = es.search(
            index=INDEX_NAME,
            size=size,
            _source=["productCategory_id", "productName"],
            knn={
                "field": field_name,
                "query_vector": query_vector,
                "k": size,
                "num_candidates": max(120, size * 4),
            },
        )
    except Exception:
        return []

    return response.get("hits", {}).get("hits", [])


def product_short_vector_boost_map(query_text: str, size: int) -> dict[str, float]:
    if not query_text or not index_exists(INDEX_NAME):
        return {}

    try:
        query_vector = list(encode_query_text(query_text))
    except Exception:
        return {}

    short_hits = _product_knn_hits(query_vector, "product_vector_short", size=size)
    if not short_hits:
        return {}

    max_score = max((float(hit.get("_score") or 0.0) for hit in short_hits), default=1.0) or 1.0
    return {
        str(hit.get("_id")): clamp(float(hit.get("_score") or 0.0) / max_score, 0.0, 1.0)
        for hit in short_hits
        if hit.get("_id")
    }


def _product_category_vote_hits(query_text: str, size: int = 48) -> tuple[dict[str, dict[str, Any]], int]:
    if not query_text or not index_exists(INDEX_NAME):
        return {}, 0

    try:
        query_vector = list(encode_query_text(query_text))
    except Exception:
        return {}, 0

    main_hits = _product_knn_hits(query_vector, "product_vector_main", size=size)
    short_hits = _product_knn_hits(query_vector, "product_vector_short", size=size)
    if not main_hits and not short_hits:
        return {}, 0

    category_votes: dict[str, dict[str, Any]] = {}
    seen_doc_ids: set[str] = set()

    def apply_hits(hits: list[dict], vector_weight: float) -> None:
        if not hits or vector_weight <= 0:
            return

        max_score = max((float(hit.get("_score") or 0.0) for hit in hits), default=1.0) or 1.0
        for hit in hits:
            hit_id = as_text(hit.get("_id"))
            if hit_id:
                seen_doc_ids.add(hit_id)

            source = hit.get("_source", {})
            category_id = as_text(source.get("productCategory_id"))
            if not category_id:
                continue

            weight = PRODUCT_VOTE_WEIGHT * vector_weight * clamp(float(hit.get("_score") or 0.0) / max_score, 0.0, 1.0)
            bucket = category_votes.setdefault(category_id, _new_vote_bucket())
            bucket["raw_score"] += weight
            bucket["product_vote_hits"] += 1
            bucket["support_sum"] += 1.0
            bucket["ambiguity_sum"] += 1.0
            bucket["match_signal_sum"] += weight

            product_name = as_text(source.get("productName"))
            if product_name and product_name not in bucket["sample_products"] and len(bucket["sample_products"]) < 2:
                bucket["sample_products"].append(product_name)

    if main_hits and short_hits:
        denom = max(PRODUCT_MAIN_VOTE_SHARE + PRODUCT_SHORT_VOTE_SHARE, 1e-9)
        main_weight = PRODUCT_MAIN_VOTE_SHARE / denom
        short_weight = PRODUCT_SHORT_VOTE_SHARE / denom
    elif main_hits:
        main_weight, short_weight = 1.0, 0.0
    else:
        main_weight, short_weight = 0.0, 1.0

    apply_hits(main_hits, main_weight)
    apply_hits(short_hits, short_weight)

    return category_votes, len(seen_doc_ids)


def _resolve_category_meta(category_ids: list[str]) -> dict[str, dict[str, str]]:
    if not category_ids or not index_exists(INDEX_NAME):
        return {}

    try:
        response = es.search(
            index=INDEX_NAME,
            size=0,
            query={"terms": {"productCategory_id": category_ids}},
            aggs={
                "by_product_category": {
                    "terms": {"field": "productCategory_id", "size": len(category_ids)},
                    "aggs": {
                        "sample": {
                            "top_hits": {
                                "size": 1,
                                "_source": ["category_name", "subCategory_name", "productCategory_name", "productCategory_id"],
                            }
                        }
                    },
                }
            },
        )
    except Exception:
        return {}

    meta: dict[str, dict[str, str]] = {}
    for bucket in response.get("aggregations", {}).get("by_product_category", {}).get("buckets", []):
        category_id = as_text(bucket.get("key"))
        top_hits = bucket.get("sample", {}).get("hits", {}).get("hits", [])
        if not category_id or not top_hits:
            continue
        source = top_hits[0].get("_source", {})
        meta[category_id] = {
            "category_name": as_text(source.get("category_name")),
            "subCategory_name": as_text(source.get("subCategory_name")),
            "productCategory_name": as_text(source.get("productCategory_name")) or category_id,
        }

    return meta


def _build_mapping_cards(category_votes: dict[str, dict[str, Any]], max_cards: int) -> list[dict]:
    if not category_votes:
        return []

    ordered = sorted(category_votes.items(), key=lambda item: item[1]["raw_score"], reverse=True)
    total_score = sum(item[1]["raw_score"] for item in ordered) or 1.0
    top_ids = [category_id for category_id, _bucket in ordered[: max(6, max_cards * 2)]]
    category_meta = _resolve_category_meta(top_ids)

    cards: list[dict] = []
    for category_id, bucket in ordered[:max_cards]:
        confidence = clamp(bucket["raw_score"] / total_score, 0.0, 1.0)
        evidence_count = max(1, bucket["cluster_hits"] + bucket["product_vote_hits"])
        avg_support = bucket["support_sum"] / evidence_count
        avg_ambiguity = bucket["ambiguity_sum"] / evidence_count
        avg_signal = clamp(bucket["match_signal_sum"] / evidence_count, 0.0, 1.0)

        names = category_meta.get(category_id, {})
        breadcrumb = " >> ".join(
            [
                part
                for part in [
                    names.get("category_name", ""),
                    names.get("subCategory_name", ""),
                    names.get("productCategory_name", category_id),
                ]
                if part
            ]
        ) or category_id

        cards.append(
            {
                "product_category_id": category_id,
                "breadcrumb": breadcrumb,
                "count": int(bucket["cluster_hits"] or bucket["product_vote_hits"]),
                "correlation_pct": round(confidence * 100, 1),
                "avg_token_coverage": round(avg_signal, 3),
                "ranking_basis": {
                    "lexical_cluster_hits": int(bucket["lexical_cluster_hits"]),
                    "semantic_cluster_hits": int(bucket["semantic_cluster_hits"]),
                    "product_vote_hits": int(bucket["product_vote_hits"]),
                    "cluster_hits": int(bucket["cluster_hits"]),
                    "exact_hits": int(bucket["lexical_cluster_hits"]),
                    "prefix_hits": 0,
                    "token_and_hits": 0,
                    "semantic_hits": int(bucket["semantic_cluster_hits"]),
                },
                "sample_products": bucket["sample_products"],
                "sample_keywords": bucket["sample_keywords"],
                "confidence": round(confidence, 4),
                "avg_product_support": round(avg_support, 2),
                "avg_category_ambiguity": round(avg_ambiguity, 2),
                "reason": (
                    f"support={avg_support:.2f}, ambiguity={avg_ambiguity:.2f}, "
                    f"lexical_hits={int(bucket['lexical_cluster_hits'])}, semantic_hits={int(bucket['semantic_cluster_hits'])}, "
                    f"product_votes={int(bucket['product_vote_hits'])}"
                ),
            }
        )

    return cards


def _mapping_decision(cards: list[dict]) -> tuple[str, float, float]:
    if not cards:
        return "no_match", 0.0, 0.0

    top_conf = float(cards[0].get("confidence") or 0.0)
    second_conf = float(cards[1].get("confidence") or 0.0) if len(cards) > 1 else 0.0
    margin = top_conf - second_conf

    if top_conf >= AUTO_MAP_CONFIDENCE and margin >= AUTO_MAP_MARGIN:
        return "auto_map", top_conf, margin
    if top_conf >= CONFIRM_MAP_CONFIDENCE:
        return "confirm", top_conf, margin
    return "options", top_conf, margin


def map_query_to_categories(query_text: str, selected_suggestion: str | None = None, max_cards: int = 3) -> dict:
    raw_query = as_text(selected_suggestion) or as_text(query_text)
    query_context = build_query_context(raw_query)
    normalized_query = query_context["normalized_query"]
    intent_query = query_context["intent_query"]
    phrase_candidates = query_context["phrase_candidates"]

    if not normalized_query or not intent_query:
        return {
            "query": query_text,
            "selected_suggestion": selected_suggestion,
            "normalized_query": normalized_query,
            "intent_query": intent_query,
            "phrase_candidates": phrase_candidates,
            "decision": "no_match",
            "confidence": 0.0,
            "margin": 0.0,
            "needs_confirmation": False,
            "auto_mapped": False,
            "cards": [],
            "top_category": None,
            "matched_clusters": 0,
            "semantic_used": False,
            "product_fallback_used": False,
            "lanes_used": [],
        }

    category_votes: dict[str, dict[str, Any]] = {}
    lanes_used: list[str] = []

    lexical_hits = _keyword_cluster_lexical_hits(
        intent_query,
        size=KEYWORD_CLUSTER_FETCH_SIZE,
        phrase_candidates=phrase_candidates,
    )
    lexical_docs = _accumulate_cluster_votes(
        category_votes=category_votes,
        hits=lexical_hits,
        normalized_query=intent_query,
        lane="lexical",
        lane_weight=1.0,
    )
    if lexical_docs:
        lanes_used.append("lexical")

    semantic_used = False
    semantic_docs = 0
    cards = _build_mapping_cards(category_votes, max_cards=max_cards)
    top_conf = float(cards[0].get("confidence") or 0.0) if cards else 0.0

    if top_conf < CONFIRM_MAP_CONFIDENCE and not selected_suggestion:
        semantic_hits = _keyword_cluster_semantic_hits(intent_query, size=max(24, KEYWORD_CLUSTER_FETCH_SIZE // 2))
        semantic_docs = _accumulate_cluster_votes(
            category_votes=category_votes,
            hits=semantic_hits,
            normalized_query=intent_query,
            lane="semantic",
            lane_weight=SEMANTIC_CLUSTER_WEIGHT,
        )
        if semantic_docs:
            semantic_used = True
            lanes_used.append("semantic")

    cards = _build_mapping_cards(category_votes, max_cards=max_cards)
    top_conf = float(cards[0].get("confidence") or 0.0) if cards else 0.0

    product_fallback_used = False
    product_hits = 0
    if top_conf < PRODUCT_FALLBACK_TRIGGER:
        fallback_votes, product_hits = _product_category_vote_hits(intent_query)
        if fallback_votes:
            product_fallback_used = True
            lanes_used.append("product_vote")
            for category_id, bucket in fallback_votes.items():
                target = category_votes.setdefault(category_id, _new_vote_bucket())
                for key in (
                    "raw_score",
                    "cluster_hits",
                    "lexical_cluster_hits",
                    "semantic_cluster_hits",
                    "product_vote_hits",
                    "support_sum",
                    "ambiguity_sum",
                    "match_signal_sum",
                ):
                    target[key] += bucket[key]

                for sample_key in ("sample_products", "sample_keywords"):
                    for item in bucket[sample_key]:
                        if item not in target[sample_key] and len(target[sample_key]) < 3:
                            target[sample_key].append(item)

    cards = _build_mapping_cards(category_votes, max_cards=max_cards)
    decision, confidence, margin = _mapping_decision(cards)

    return {
        "query": query_text,
        "selected_suggestion": selected_suggestion,
        "normalized_query": normalized_query,
        "intent_query": intent_query,
        "phrase_candidates": phrase_candidates,
        "decision": decision,
        "confidence": round(confidence, 4),
        "margin": round(margin, 4),
        "needs_confirmation": decision == "confirm",
        "auto_mapped": decision == "auto_map",
        "cards": cards,
        "top_category": cards[0] if cards else None,
        "matched_clusters": int(lexical_docs + semantic_docs),
        "semantic_used": semantic_used,
        "product_fallback_used": product_fallback_used,
        "product_vote_hits": int(product_hits),
        "lanes_used": lanes_used,
    }


def current_thresholds() -> dict[str, float]:
    return {
        "auto_map": AUTO_MAP_CONFIDENCE,
        "auto_map_margin": AUTO_MAP_MARGIN,
        "confirm": CONFIRM_MAP_CONFIDENCE,
        "product_fallback": PRODUCT_FALLBACK_TRIGGER,
    }
