"""Query-to-category mapping with confidence calibration.

Maps queries to product categories with semantic clustering, confidence scores,
and product fallback logic. Includes confidence calibration and mapping history.
"""

from __future__ import annotations

import hashlib
import json
import math
import concurrent.futures
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.core.clients import active_search_backend, es
from src.core.config import (
    AUTO_MAP_CONFIDENCE as CONFIG_AUTO_MAP_CONFIDENCE,
    AUTO_MAP_MARGIN as CONFIG_AUTO_MAP_MARGIN,
    CONFIRM_MAP_CONFIDENCE as CONFIG_CONFIRM_MAP_CONFIDENCE,
    HEAD_TERMS_HARD_CAP,
    INDEX_NAME,
    KEYWORD_CLUSTER_FETCH_SIZE,
    KEYWORD_INDEX,
    KEYWORD_P95_PRODUCT_COUNT,
    MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD as CONFIG_MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD,
    MAPPING_ALERT_LOW_MARGIN_THRESHOLD as CONFIG_MAPPING_ALERT_LOW_MARGIN_THRESHOLD,
    MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO as CONFIG_MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO,
    MAPPING_CONFIDENCE_MODEL_FILE as CONFIG_MAPPING_CONFIDENCE_MODEL_FILE,
    MAPPING_ENABLE_CONFIDENCE_CALIBRATION as CONFIG_MAPPING_ENABLE_CONFIDENCE_CALIBRATION,
    MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION as CONFIG_MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION,
    MAPPING_ENABLE_PRODUCT_FALLBACK as CONFIG_MAPPING_ENABLE_PRODUCT_FALLBACK,
    MAPPING_ENABLE_SEMANTIC_FALLBACK as CONFIG_MAPPING_ENABLE_SEMANTIC_FALLBACK,
    MAPPING_PHASE3_CANARY_PERCENT as CONFIG_MAPPING_PHASE3_CANARY_PERCENT,
    MAPPING_TELEMETRY_ENABLED as CONFIG_MAPPING_TELEMETRY_ENABLED,
    MAPPING_TELEMETRY_FILE as CONFIG_MAPPING_TELEMETRY_FILE,
    PRODUCT_FALLBACK_MAX_GAIN_RATIO as CONFIG_PRODUCT_FALLBACK_MAX_GAIN_RATIO,
    PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO as CONFIG_PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO,
    PRODUCT_FALLBACK_STRONG_CONFIDENCE as CONFIG_PRODUCT_FALLBACK_STRONG_CONFIDENCE,
    PRODUCT_FALLBACK_STRONG_COVERAGE as CONFIG_PRODUCT_FALLBACK_STRONG_COVERAGE,
    PRODUCT_FALLBACK_TRIGGER as CONFIG_PRODUCT_FALLBACK_TRIGGER,
    PRODUCT_MAIN_VOTE_SHARE as CONFIG_PRODUCT_MAIN_VOTE_SHARE,
    PRODUCT_SHORT_VOTE_SHARE as CONFIG_PRODUCT_SHORT_VOTE_SHARE,
    PRODUCT_VOTE_WEIGHT as CONFIG_PRODUCT_VOTE_WEIGHT,
    RELIABILITY_BETA,
    SEMANTIC_CLUSTER_WEIGHT as CONFIG_SEMANTIC_CLUSTER_WEIGHT,
)
from src.core.embedding_client import encode_query_text
from src.core.logger import log
from src.services.internal.calibration import apply_isotonic_calibration, is_calibration_model_valid
from src.services.internal.common import as_text, clamp, index_exists, sample_hierarchy_cards, trim_terms
from src.services.internal.query_text import (
    build_query_context,
    canonical_token_list,
    canonical_tokens,
    normalize_query_text,
    significant_tokens,
    suggestion_rank_features,
    token_list,
)


# Mutable runtime knobs used by tuning scripts.
AUTO_MAP_CONFIDENCE = CONFIG_AUTO_MAP_CONFIDENCE
AUTO_MAP_MARGIN = CONFIG_AUTO_MAP_MARGIN
CONFIRM_MAP_CONFIDENCE = CONFIG_CONFIRM_MAP_CONFIDENCE
PRODUCT_FALLBACK_TRIGGER = CONFIG_PRODUCT_FALLBACK_TRIGGER
PRODUCT_FALLBACK_MAX_GAIN_RATIO = CONFIG_PRODUCT_FALLBACK_MAX_GAIN_RATIO
PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO = CONFIG_PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO
PRODUCT_FALLBACK_STRONG_CONFIDENCE = CONFIG_PRODUCT_FALLBACK_STRONG_CONFIDENCE
PRODUCT_FALLBACK_STRONG_COVERAGE = CONFIG_PRODUCT_FALLBACK_STRONG_COVERAGE
MAPPING_PHASE3_CANARY_PERCENT = CONFIG_MAPPING_PHASE3_CANARY_PERCENT
MAPPING_ENABLE_CONFIDENCE_CALIBRATION = CONFIG_MAPPING_ENABLE_CONFIDENCE_CALIBRATION
MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION = CONFIG_MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION
MAPPING_ENABLE_SEMANTIC_FALLBACK = CONFIG_MAPPING_ENABLE_SEMANTIC_FALLBACK
MAPPING_ENABLE_PRODUCT_FALLBACK = CONFIG_MAPPING_ENABLE_PRODUCT_FALLBACK
MAPPING_TELEMETRY_ENABLED = CONFIG_MAPPING_TELEMETRY_ENABLED
MAPPING_TELEMETRY_FILE = CONFIG_MAPPING_TELEMETRY_FILE
MAPPING_CONFIDENCE_MODEL_FILE = CONFIG_MAPPING_CONFIDENCE_MODEL_FILE
MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD = CONFIG_MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD
MAPPING_ALERT_LOW_MARGIN_THRESHOLD = CONFIG_MAPPING_ALERT_LOW_MARGIN_THRESHOLD
MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO = CONFIG_MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO
SEMANTIC_CLUSTER_WEIGHT = CONFIG_SEMANTIC_CLUSTER_WEIGHT
PRODUCT_VOTE_WEIGHT = CONFIG_PRODUCT_VOTE_WEIGHT
PRODUCT_MAIN_VOTE_SHARE = CONFIG_PRODUCT_MAIN_VOTE_SHARE
PRODUCT_SHORT_VOTE_SHARE = CONFIG_PRODUCT_SHORT_VOTE_SHARE

_CONFIDENCE_MODEL_CACHE: dict[str, Any] | None = None
_CONFIDENCE_MODEL_MTIME: float | None = None


DEMOGRAPHIC_TOKENS = {
    "women", "woman", "ladies", "lady", "men", "man", "male", "female",
    "boys", "boy", "girls", "girl", "kids", "kid", "children", "child", "unisex",
}

_FEMALE_DEMOGRAPHIC_TOKENS = {
    "women", "woman", "ladies", "lady", "female", "girls", "girl",
}
_MALE_DEMOGRAPHIC_TOKENS = {
    "men", "man", "male", "boys", "boy",
}
_KIDS_DEMOGRAPHIC_TOKENS = {
    "kids", "kid", "children", "child",
}
_UNISEX_DEMOGRAPHIC_TOKENS = {"unisex"}

_CANON_FEMALE_DEMOGRAPHIC_TOKENS = set(canonical_tokens(list(_FEMALE_DEMOGRAPHIC_TOKENS)))
_CANON_MALE_DEMOGRAPHIC_TOKENS = set(canonical_tokens(list(_MALE_DEMOGRAPHIC_TOKENS)))
_CANON_KIDS_DEMOGRAPHIC_TOKENS = set(canonical_tokens(list(_KIDS_DEMOGRAPHIC_TOKENS)))
_CANON_UNISEX_DEMOGRAPHIC_TOKENS = set(canonical_tokens(list(_UNISEX_DEMOGRAPHIC_TOKENS)))


def _opensearch_knn_query(field_name: str, query_vector: list[float], k: int, num_candidates: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "vector": query_vector,
        "k": int(k),
    }
    if num_candidates > 0:
        payload["method_parameters"] = {"ef_search": int(num_candidates)}
    return {"knn": {field_name: payload}}


def _knn_search_kwargs(field_name: str, query_vector: list[float], k: int, num_candidates: int) -> dict[str, Any]:
    if active_search_backend() == "opensearch":
        return {
            "query": _opensearch_knn_query(
                field_name=field_name,
                query_vector=query_vector,
                k=k,
                num_candidates=num_candidates,
            )
        }
    return {
        "knn": {
            "field": field_name,
            "query_vector": query_vector,
            "k": int(k),
            "num_candidates": int(num_candidates),
        }
    }


def _demographic_groups_for_tokens(tokens: set[str]) -> set[str]:
    groups: set[str] = set()
    if tokens & _CANON_FEMALE_DEMOGRAPHIC_TOKENS:
        groups.add("female")
    if tokens & _CANON_MALE_DEMOGRAPHIC_TOKENS:
        groups.add("male")
    if tokens & _CANON_KIDS_DEMOGRAPHIC_TOKENS:
        groups.add("kids")
    if tokens & _CANON_UNISEX_DEMOGRAPHIC_TOKENS:
        groups.add("unisex")
    return groups


def _phase3_enabled_for_query(query_text: str) -> bool:
    percent = clamp(float(MAPPING_PHASE3_CANARY_PERCENT), 0.0, 100.0)
    if percent >= 100.0:
        return True
    if percent <= 0.0:
        return False

    digest = hashlib.sha1(as_text(query_text).encode("utf-8")).hexdigest()[:8]
    bucket = (int(digest, 16) / 0xFFFFFFFF) * 100.0
    return bucket < percent


def _emit_mapping_telemetry(payload: dict[str, Any]) -> None:
    if not MAPPING_TELEMETRY_ENABLED:
        return

    try:
        log_path = Path(MAPPING_TELEMETRY_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _load_confidence_model() -> dict[str, Any] | None:
    global _CONFIDENCE_MODEL_CACHE
    global _CONFIDENCE_MODEL_MTIME

    if not MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION:
        return None

    model_path = Path(MAPPING_CONFIDENCE_MODEL_FILE)
    if not model_path.exists():
        _CONFIDENCE_MODEL_CACHE = None
        _CONFIDENCE_MODEL_MTIME = None
        return None

    try:
        mtime = model_path.stat().st_mtime
    except Exception:
        return _CONFIDENCE_MODEL_CACHE

    if _CONFIDENCE_MODEL_CACHE is not None and _CONFIDENCE_MODEL_MTIME == mtime:
        return _CONFIDENCE_MODEL_CACHE

    try:
        payload = json.loads(model_path.read_text(encoding="utf-8"))
    except Exception:
        return _CONFIDENCE_MODEL_CACHE

    if not isinstance(payload, dict) or not is_calibration_model_valid(payload):
        return _CONFIDENCE_MODEL_CACHE

    _CONFIDENCE_MODEL_CACHE = payload
    _CONFIDENCE_MODEL_MTIME = mtime
    return _CONFIDENCE_MODEL_CACHE


def _learned_confidence(raw_confidence: float) -> tuple[float | None, bool]:
    model = _load_confidence_model()
    if not model:
        return None, False

    try:
        calibrated = apply_isotonic_calibration(raw_confidence, model)
    except Exception:
        return None, False
    return clamp(float(calibrated), 0.0, 1.0), True


def _reliability_factor(product_count: int, category_count: int) -> float:
    support = math.log1p(max(0, product_count)) / math.log1p(KEYWORD_P95_PRODUCT_COUNT)
    support = clamp(support, 0.0, 1.25)
    ambiguity = 1.0 / (1.0 + RELIABILITY_BETA * max(0, category_count - 1))
    return clamp(support * ambiguity, 0.02, 1.25)


def _keyword_cluster_lexical_hits(query_text: str, size: int, phrase_candidates: list[str] | None = None) -> list[dict]:
    if not query_text or not index_exists(KEYWORD_INDEX):
        return []

    try:
        q_tokens = token_list(query_text)
        is_single_token = len(q_tokens) <= 1
        should_clauses: list[dict[str, Any]] = [
            {"match_phrase": {"keyword_name": {"query": query_text, "boost": 12.0}}},
            {"match_phrase_prefix": {"keyword_name": {"query": query_text, "boost": 10.0}}},
            {"match": {"keyword_name": {"query": query_text, "operator": "and", "boost": 8.0}}},
            {"match": {"variant_terms": {"query": query_text, "operator": "and", "boost": 4.5}}},
            {"match": {"long_tail_terms": {"query": query_text, "operator": "and", "boost": 2.0}}},
        ]
        # Stemming/fuzzy helps recall, but for 1-word queries it can over-match
        # unrelated stems (e.g. "whiskey" vs "whisk"). Prefer precise lexical
        # signals for single-token inputs.
        if not is_single_token:
            should_clauses.extend(
                [
                    {"match": {"keyword_name.stem": {"query": query_text, "operator": "and", "boost": 6.0}}},
                    {"match": {"variant_terms.stem": {"query": query_text, "operator": "and", "boost": 3.9}}},
                    {"match": {"long_tail_terms.stem": {"query": query_text, "operator": "and", "boost": 1.7}}},
                    {"match": {"keyword_name": {"query": query_text, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.8}}},
                ]
            )
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

    def fetch_lane(field_name: str) -> tuple[str, list[dict]]:
        try:
            resp = es.search(
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
                **_knn_search_kwargs(
                    field_name=field_name,
                    query_vector=query_vector,
                    k=size,
                    num_candidates=max(120, size * 4),
                ),
            )
            return field_name, resp.get("hits", {}).get("hits", [])
        except Exception:
            return field_name, []

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        lane_results = list(executor.map(lambda lane: fetch_lane(lane[0]), vector_lanes))

    results_by_lane = dict(lane_results)

    for field_name, lane_weight in vector_lanes:
        hits = results_by_lane.get(field_name, [])
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


def _query_anchor_constraints(query_context: dict[str, Any]) -> dict[str, Any]:
    canonical_intent_tokens = [
        as_text(token).lower().strip()
        for token in (query_context.get("canonical_intent_tokens") or [])
        if as_text(token).strip()
    ]
    raw_anchor_tokens = [
        as_text(token).lower().strip()
        for token in (query_context.get("anchor_tokens") or [])
        if as_text(token).strip()
    ]
    canonical_anchor_tokens = canonical_tokens(raw_anchor_tokens)
    trailing_intent_tokens = canonical_intent_tokens[-2:] if len(canonical_intent_tokens) >= 2 else canonical_intent_tokens[:]
    if canonical_anchor_tokens:
        for token in trailing_intent_tokens:
            if token and token not in canonical_anchor_tokens:
                canonical_anchor_tokens.append(token)
    else:
        canonical_anchor_tokens = trailing_intent_tokens

    domain_anchors = {token for token in canonical_anchor_tokens if token and token not in DEMOGRAPHIC_TOKENS}
    demographic_anchors = {token for token in canonical_anchor_tokens if token in DEMOGRAPHIC_TOKENS}

    return {
        "domain": domain_anchors,
        "demographic": demographic_anchors,
        # Strict anchors help short, focused queries but can over-prune long-tail titles.
        "strict": bool(3 <= len(canonical_intent_tokens) <= 5 and domain_anchors),
    }


def _cluster_anchor_signal(source: dict[str, Any], domain_anchors: set[str], demographic_anchors: set[str]) -> float:
    if not domain_anchors and not demographic_anchors:
        return 1.0

    corpus: list[str] = []
    keyword_name = as_text(source.get("keyword_name"))
    if keyword_name:
        corpus.append(keyword_name)

    corpus.extend(trim_terms(source.get("variant_terms") or [], 12))
    corpus.extend(trim_terms(source.get("long_tail_terms") or [], 8))

    head_terms = source.get("head_terms") or []
    if len(head_terms) <= HEAD_TERMS_HARD_CAP:
        corpus.extend(trim_terms(head_terms, 8))

    token_pool: set[str] = set()
    for value in corpus:
        token_pool.update(canonical_token_list(value))

    if not token_pool:
        return 0.0

    domain_overlap = len(domain_anchors & token_pool) / max(1, len(domain_anchors)) if domain_anchors else 1.0
    demographic_overlap = (
        len(demographic_anchors & token_pool) / max(1, len(demographic_anchors))
        if demographic_anchors
        else 1.0
    )
    return clamp((0.8 * domain_overlap) + (0.2 * demographic_overlap), 0.0, 1.0)


def _new_vote_bucket() -> dict[str, Any]:
    return {
        "raw_score": 0.0,
        "raw_lexical_score": 0.0,
        "raw_semantic_score": 0.0,
        "raw_product_vote_score": 0.0,
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
    domain_anchors: set[str] | None = None,
    demographic_anchors: set[str] | None = None,
    strict_anchor: bool = False,
) -> int:
    if not hits:
        return 0

    query_tokens = token_list(normalized_query)
    required_domain_anchors = domain_anchors or set()
    preferred_demographic_anchors = demographic_anchors or set()
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
        anchor_signal = _cluster_anchor_signal(source, required_domain_anchors, preferred_demographic_anchors)
        if strict_anchor and required_domain_anchors and anchor_signal <= 0.0:
            continue

        if required_domain_anchors:
            match_signal = clamp((0.7 * match_signal) + (0.3 * anchor_signal), 0.0, 1.0)

        score_norm = clamp(float(hit.get("_score") or 0.0) / max_score, 0.0, 1.0)
        doc_vote = lane_weight * ((0.55 * score_norm) + (0.45 * match_signal)) * reliability
        if required_domain_anchors:
            doc_vote *= (0.6 + (0.4 * anchor_signal))
        per_category_vote = doc_vote / max(1, len(set(category_ids)))

        docs_used += 1
        keyword_name = as_text(source.get("keyword_name"))

        for category_id in sorted(set(category_ids)):
            bucket = category_votes.setdefault(category_id, _new_vote_bucket())
            bucket["raw_score"] += per_category_vote
            bucket["cluster_hits"] += 1
            if lane == "semantic":
                bucket["semantic_cluster_hits"] += 1
                bucket["raw_semantic_score"] += per_category_vote
            else:
                bucket["lexical_cluster_hits"] += 1
                bucket["raw_lexical_score"] += per_category_vote

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
            **_knn_search_kwargs(
                field_name=field_name,
                query_vector=query_vector,
                k=size,
                num_candidates=max(120, size * 4),
            ),
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

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_main = executor.submit(_product_knn_hits, query_vector, "product_vector_main", size)
        future_short = executor.submit(_product_knn_hits, query_vector, "product_vector_short", size)
        main_hits = future_main.result()
        short_hits = future_short.result()

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
            bucket["raw_product_vote_score"] += weight
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


@lru_cache(maxsize=2048)
def _cached_category_meta(category_ids_tuple: tuple[str, ...]) -> dict[str, dict[str, str]]:
    category_ids = list(category_ids_tuple)
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


def _resolve_category_meta(category_ids: list[str]) -> dict[str, dict[str, str]]:
    """Fetches human readable names for category IDs, heavily cached in memory."""
    if not category_ids:
        return {}
    # Convert list to tuple for lru_cache hashing, sort to ensure cache hits for same items
    return _cached_category_meta(tuple(sorted(set(category_ids))))


def _category_name_intent_overlap(names: dict[str, str], intent_tokens: set[str]) -> float:
    if not intent_tokens:
        return 0.0

    text = " ".join(
        part
        for part in [
            as_text(names.get("category_name")),
            as_text(names.get("subCategory_name")),
            as_text(names.get("productCategory_name")),
        ]
        if part
    )
    category_tokens = set(canonical_token_list(text))
    if not category_tokens:
        return 0.0

    return clamp(len(category_tokens & intent_tokens) / max(1, len(intent_tokens)), 0.0, 1.0)


def _category_demographic_factor(names: dict[str, str], preferred_demographic_anchors: set[str]) -> float:
    if not preferred_demographic_anchors:
        return 1.0

    query_groups = _demographic_groups_for_tokens(set(canonical_tokens(list(preferred_demographic_anchors))))
    if not query_groups:
        return 1.0

    text = " ".join(
        part
        for part in [
            as_text(names.get("category_name")),
            as_text(names.get("subCategory_name")),
            as_text(names.get("productCategory_name")),
        ]
        if part
    )
    category_tokens = set(canonical_token_list(text))
    category_groups = _demographic_groups_for_tokens(category_tokens)
    if not category_groups:
        return 1.0
    if "unisex" in category_groups:
        return 1.02
    if query_groups & category_groups:
        return 1.08
    return 0.6


def _calibrated_confidence(bucket: dict[str, Any], total_score: float) -> tuple[float, float]:
    raw_share = clamp(float(bucket.get("raw_score") or 0.0) / max(total_score, 1e-9), 0.0, 1.0)
    evidence_count = max(1, int(bucket.get("cluster_hits") or 0) + int(bucket.get("product_vote_hits") or 0))
    lexical_hits = int(bucket.get("lexical_cluster_hits") or 0)
    semantic_hits = int(bucket.get("semantic_cluster_hits") or 0)
    product_vote_hits = int(bucket.get("product_vote_hits") or 0)

    lex_sem_hits = lexical_hits + semantic_hits
    lex_sem_ratio = clamp(lex_sem_hits / max(1, evidence_count), 0.0, 1.0)
    avg_signal = clamp(float(bucket.get("match_signal_sum") or 0.0) / evidence_count, 0.0, 1.0)
    avg_ambiguity = float(bucket.get("ambiguity_sum") or 0.0) / evidence_count
    ambiguity_penalty = 1.0 / (1.0 + 0.12 * max(0.0, avg_ambiguity - 1.0))

    calibrated = clamp(
        (0.55 * raw_share)
        + (0.25 * avg_signal)
        + (0.15 * lex_sem_ratio)
        + (0.05 * ambiguity_penalty),
        0.0,
        1.0,
    )

    # Penalize pure product-vote dominance when lexical/semantic evidence is weak.
    if product_vote_hits > (lex_sem_hits * 2) and lex_sem_hits < 4:
        calibrated *= 0.76

    return raw_share, clamp(calibrated, 0.0, 1.0)


def _build_mapping_cards(
    category_votes: dict[str, dict[str, Any]],
    max_cards: int,
    use_calibrated_confidence: bool,
    intent_tokens: list[str] | None = None,
    preferred_demographic_anchors: set[str] | None = None,
) -> list[dict]:
    if not category_votes:
        return []

    intent_token_set = set(canonical_tokens(intent_tokens or []))
    preferred_demographic_set = {
        token for token in canonical_tokens(list(preferred_demographic_anchors or set())) if token
    }

    ordered = sorted(
        category_votes.items(),
        key=lambda item: (
            -float(item[1].get("raw_score") or 0.0),
            -int(item[1].get("lexical_cluster_hits") or 0),
            -int(item[1].get("semantic_cluster_hits") or 0),
            -int(item[1].get("cluster_hits") or 0),
            -float(item[1].get("match_signal_sum") or 0.0),
            as_text(item[0]),
        ),
    )
    total_score = sum(item[1]["raw_score"] for item in ordered) or 1.0
    candidate_limit = max(6, max_cards * 2)
    top_ids = [category_id for category_id, _bucket in ordered[:candidate_limit]]
    category_meta = _resolve_category_meta(top_ids)

    cards: list[dict] = []
    for category_id, bucket in ordered[:candidate_limit]:
        evidence_count = max(1, bucket["cluster_hits"] + bucket["product_vote_hits"])
        avg_support = bucket["support_sum"] / evidence_count
        avg_ambiguity = bucket["ambiguity_sum"] / evidence_count
        avg_signal = clamp(bucket["match_signal_sum"] / evidence_count, 0.0, 1.0)
        raw_confidence, heuristic_confidence = _calibrated_confidence(bucket, total_score)
        learned_confidence: float | None = None
        confidence_model_used = False
        if use_calibrated_confidence:
            learned_confidence, confidence_model_used = _learned_confidence(raw_confidence)
            if learned_confidence is None:
                confidence = heuristic_confidence
            else:
                confidence = learned_confidence
                if abs(learned_confidence - heuristic_confidence) >= 0.25 and raw_confidence < 0.75:
                    # Prevent overconfident step-calibration spikes from flattening tie-break margins.
                    confidence = (0.35 * learned_confidence) + (0.65 * heuristic_confidence)
                    confidence_model_used = False
        else:
            confidence = raw_confidence
        lexical_hits = int(bucket["lexical_cluster_hits"])
        semantic_hits = int(bucket["semantic_cluster_hits"])
        product_vote_hits = int(bucket["product_vote_hits"])
        total_evidence_hits = int(bucket["cluster_hits"] + bucket["product_vote_hits"])
        lane_scores = {
            "lexical": float(bucket.get("raw_lexical_score") or 0.0),
            "semantic": float(bucket.get("raw_semantic_score") or 0.0),
            "product_vote": float(bucket.get("raw_product_vote_score") or 0.0),
        }
        lane_total = sum(lane_scores.values()) or 1.0
        lane_score_pct = {
            lane: round((score / lane_total) * 100.0, 1)
            for lane, score in lane_scores.items()
        }

        names = category_meta.get(category_id, {})
        resolved_label_present = bool(
            as_text(names.get("category_name"))
            or as_text(names.get("subCategory_name"))
            or as_text(names.get("productCategory_name"))
        )
        if not resolved_label_present:
            continue

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

        intent_overlap = _category_name_intent_overlap(names, intent_token_set)
        demographic_factor = _category_demographic_factor(names, preferred_demographic_set)
        raw_bucket_score = float(bucket.get("raw_score") or 0.0)
        lexical_semantic_share = clamp(
            (lane_scores["lexical"] + lane_scores["semantic"]) / max(lane_total, 1e-9),
            0.0,
            1.0,
        )
        intent_factor = 0.65 + (0.70 * intent_overlap)
        evidence_balance_factor = 0.55 + (0.45 * lexical_semantic_share)
        rank_score = (
            raw_bucket_score
            * demographic_factor
            * intent_factor
            * evidence_balance_factor
            * (0.85 + (0.15 * avg_signal))
        )

        cards.append(
            {
                "product_category_id": category_id,
                "breadcrumb": breadcrumb,
                "_rank_score": round(rank_score, 6),
                "count": total_evidence_hits,
                "correlation_pct": round(raw_confidence * 100, 1),
                "avg_token_coverage": round(avg_signal, 3),
                "ranking_basis": {
                    "lexical_cluster_hits": lexical_hits,
                    "semantic_cluster_hits": semantic_hits,
                    "product_vote_hits": product_vote_hits,
                    "cluster_hits": int(bucket["cluster_hits"]),
                    "total_evidence_hits": total_evidence_hits,
                    "exact_hits": lexical_hits,
                    "prefix_hits": 0,
                    "token_and_hits": 0,
                    "semantic_hits": semantic_hits,
                },
                "lane_scores": {
                    "lexical": round(lane_scores["lexical"], 4),
                    "semantic": round(lane_scores["semantic"], 4),
                    "product_vote": round(lane_scores["product_vote"], 4),
                },
                "lane_score_pct": lane_score_pct,
                "sample_products": bucket["sample_products"],
                "sample_keywords": bucket["sample_keywords"],
                "confidence": round(confidence, 4),
                "confidence_raw": round(raw_confidence, 4),
                "confidence_calibrated_heuristic": round(heuristic_confidence, 4),
                "confidence_calibrated_learned": round(learned_confidence, 4) if learned_confidence is not None else None,
                "confidence_model_used": confidence_model_used,
                "avg_product_support": round(avg_support, 2),
                "avg_category_ambiguity": round(avg_ambiguity, 2),
                "reason": (
                    f"conf_raw={raw_confidence:.3f}, conf_cal={confidence:.3f}, conf_heur={heuristic_confidence:.3f}, "
                    f"conf_model={'on' if confidence_model_used else 'off'}, support={avg_support:.2f}, "
                    f"ambiguity={avg_ambiguity:.2f}, lexical_hits={lexical_hits}, semantic_hits={semantic_hits}, "
                    f"product_votes={product_vote_hits}"
                ),
            }
        )

    cards.sort(
        key=lambda card: (
            -float(card.get("_rank_score") or 0.0),
            -float(card.get("confidence_raw") or 0.0),
            -float(card.get("avg_token_coverage") or 0.0),
            -int((card.get("ranking_basis") or {}).get("total_evidence_hits") or 0),
            as_text(card.get("product_category_id")),
        )
    )
    cards = cards[:max_cards]
    for card in cards:
        card.pop("_rank_score", None)

    return cards


def _mapping_decision(cards: list[dict]) -> tuple[str, float, float]:
    if not cards:
        return "no_match", 0.0, 0.0

    top_conf = float(cards[0].get("confidence") or 0.0)
    second_conf = float(cards[1].get("confidence") or 0.0) if len(cards) > 1 else 0.0
    margin = top_conf - second_conf
    confirm_margin = max(MAPPING_ALERT_LOW_MARGIN_THRESHOLD, 0.05)

    if top_conf >= AUTO_MAP_CONFIDENCE and margin >= AUTO_MAP_MARGIN:
        return "auto_map", top_conf, margin
    if top_conf >= CONFIRM_MAP_CONFIDENCE and (len(cards) == 1 or margin >= confirm_margin):
        return "confirm", top_conf, margin
    if top_conf >= CONFIRM_MAP_CONFIDENCE:
        return "options", top_conf, margin
    return "options", top_conf, margin


def _semantic_fallback_needed(cards: list[dict], selected_suggestion: str | None, intent_token_count: int = 0) -> bool:
    """Decide whether to run the semantic lane.

    Improvements vs original:
    - Short queries (<=2 tokens) use semantic only when lexical evidence is weak.
      Single/two-word products (Yoga Mat, LED Lamp, Alarm Clock) can have sparse
      lexical coverage, but always-on semantic can also overfire on substring /
      embedding artefacts (e.g. "whiskey" drifting toward "whisk").
    - Long-tail queries (>=6 tokens) always use semantic: long product titles
      contain multiple overlapping concepts that need vector-space disambiguation
      to pick the dominant product intent.
    """
    if not cards:
        return True

    # For very short queries, semantic is helpful, but it can also dominate due to
    # embedding/subword quirks. Prefer lexical when it already looks confident.
    if intent_token_count <= 2:
        top = cards[0]
        top_conf = float(top.get("confidence") or 0.0)
        second_conf = float(cards[1].get("confidence") or 0.0) if len(cards) > 1 else 0.0
        margin = top_conf - second_conf
        ranking_basis = top.get("ranking_basis") or {}
        lexical_hits = int(ranking_basis.get("lexical_cluster_hits") or 0)
        total_hits = int(ranking_basis.get("total_evidence_hits") or 0)
        avg_coverage = float(top.get("avg_token_coverage") or 0.0)

        short_query_strong_lexical = (
            top_conf >= CONFIRM_MAP_CONFIDENCE
            and margin >= max(MAPPING_ALERT_LOW_MARGIN_THRESHOLD, 0.05)
            and avg_coverage >= 0.55
            and lexical_hits >= 4
            and total_hits >= 6
        )
        return not short_query_strong_lexical

    # Always use semantic for long-tail product titles — they contain multiple
    # concepts and need vector-space to find the dominant product intent.
    if intent_token_count >= 6:
        return True

    top = cards[0]
    top_conf = float(top.get("confidence") or 0.0)
    second_conf = float(cards[1].get("confidence") or 0.0) if len(cards) > 1 else 0.0
    margin = top_conf - second_conf

    ranking_basis = top.get("ranking_basis") or {}
    lexical_hits = int(ranking_basis.get("lexical_cluster_hits") or 0)
    total_hits = int(ranking_basis.get("total_evidence_hits") or 0)
    avg_coverage = float(top.get("avg_token_coverage") or 0.0)

    strong_lexical = (
        top_conf >= max(AUTO_MAP_CONFIDENCE, CONFIRM_MAP_CONFIDENCE + 0.15)
        and margin >= AUTO_MAP_MARGIN
        and avg_coverage >= 0.65
        and lexical_hits >= 6
        and total_hits >= 8
    )
    if strong_lexical:
        return False

    if (
        selected_suggestion
        and top_conf >= CONFIRM_MAP_CONFIDENCE
        and margin >= AUTO_MAP_MARGIN
        and avg_coverage >= 0.55
        and lexical_hits >= 4
    ):
        return False

    if top_conf < CONFIRM_MAP_CONFIDENCE:
        return True

    ambiguous_margin = margin < max(AUTO_MAP_MARGIN, MAPPING_ALERT_LOW_MARGIN_THRESHOLD + 0.02)
    weak_coverage = avg_coverage < 0.55
    sparse_lexical = lexical_hits < 4 or total_hits < 6
    return ambiguous_margin or (weak_coverage and sparse_lexical)


def _mapping_alerts(
    cards: list[dict],
    decision: str,
    margin: float,
    product_fallback_used: bool,
) -> list[str]:
    alerts: list[str] = []
    if not cards:
        return ["no_match"]

    top = cards[0]
    top_conf = float(top.get("confidence") or 0.0)
    lane_pct = top.get("lane_score_pct") or {}
    product_vote_pct = float(lane_pct.get("product_vote") or 0.0) / 100.0

    if top_conf < MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD:
        alerts.append("low_confidence")
    if margin < MAPPING_ALERT_LOW_MARGIN_THRESHOLD and len(cards) > 1:
        alerts.append("low_margin")
    if product_fallback_used and product_vote_pct >= MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO:
        alerts.append("product_vote_dominant")
    if decision == "options" and top_conf >= CONFIRM_MAP_CONFIDENCE:
        alerts.append("decision_conflict")

    return alerts


def map_query_to_categories(
    query_text: str,
    selected_suggestion: str | None = None,
    max_cards: int = 3,
    emit_telemetry: bool = True,
) -> dict:
    raw_query = as_text(selected_suggestion) or as_text(query_text)
    # Canary should control rollout visibility, not core relevance behavior.
    phase3_active = _phase3_enabled_for_query(raw_query)
    use_calibrated_confidence = bool(MAPPING_ENABLE_CONFIDENCE_CALIBRATION)
    query_context = build_query_context(raw_query)
    normalized_query = query_context["normalized_query"]
    intent_query = query_context["intent_query"]
    phrase_candidates = query_context["phrase_candidates"]
    anchor_constraints = _query_anchor_constraints(query_context)

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
            "phase3_active": phase3_active,
            "alerts": ["no_match"],
        }

    category_votes: dict[str, dict[str, Any]] = {}
    lanes_used: list[str] = []

    # Fan out all major external queries concurrently.
    # We will only process the results of semantic/product if they are needed,
    # but starting them now collapses the network latency to the maximum of 1 query.
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        future_lexical = executor.submit(
            _keyword_cluster_lexical_hits,
            intent_query,
            size=KEYWORD_CLUSTER_FETCH_SIZE,
            phrase_candidates=phrase_candidates,
        )
        future_semantic = executor.submit(
            _keyword_cluster_semantic_hits,
            intent_query,
            size=max(24, KEYWORD_CLUSTER_FETCH_SIZE // 2)
        )
        future_product = executor.submit(
            _product_category_vote_hits,
            intent_query
        )

        lexical_hits = future_lexical.result()
    lexical_docs = _accumulate_cluster_votes(
        category_votes=category_votes,
        hits=lexical_hits,
        normalized_query=intent_query,
        lane="lexical",
        lane_weight=1.0,
        domain_anchors=anchor_constraints["domain"],
        demographic_anchors=anchor_constraints["demographic"],
        strict_anchor=bool(anchor_constraints["strict"]),
    )
    if lexical_docs:
        lanes_used.append("lexical")

    semantic_used = False
    semantic_docs = 0
    cards = _build_mapping_cards(
        category_votes,
        max_cards=max_cards,
        use_calibrated_confidence=use_calibrated_confidence,
        intent_tokens=query_context.get("canonical_intent_tokens") or [],
        preferred_demographic_anchors=anchor_constraints["demographic"],
    )
    _intent_token_count = len(query_context.get("canonical_intent_tokens") or [])
    if MAPPING_ENABLE_SEMANTIC_FALLBACK and _semantic_fallback_needed(cards, selected_suggestion, intent_token_count=_intent_token_count):
        semantic_hits = future_semantic.result()
        semantic_docs = _accumulate_cluster_votes(
            category_votes=category_votes,
            hits=semantic_hits,
            normalized_query=intent_query,
            lane="semantic",
            lane_weight=SEMANTIC_CLUSTER_WEIGHT,
            domain_anchors=anchor_constraints["domain"],
            demographic_anchors=anchor_constraints["demographic"],
            strict_anchor=bool(anchor_constraints["strict"]),
        )
        if semantic_docs:
            semantic_used = True
            lanes_used.append("semantic")

    cards = _build_mapping_cards(
        category_votes,
        max_cards=max_cards,
        use_calibrated_confidence=use_calibrated_confidence,
        intent_tokens=query_context.get("canonical_intent_tokens") or [],
        preferred_demographic_anchors=anchor_constraints["demographic"],
    )
    top_conf = float(cards[0].get("confidence") or 0.0) if cards else 0.0

    pre_fallback_cards = cards[:]
    pre_fallback_scores = {
        category_id: float(bucket.get("raw_score") or 0.0)
        for category_id, bucket in category_votes.items()
    }

    pre_fallback_top_id = as_text(pre_fallback_cards[0].get("product_category_id")) if pre_fallback_cards else ""
    pre_fallback_top_score = pre_fallback_scores.get(pre_fallback_top_id, 0.0)
    pre_fallback_top_conf = float(pre_fallback_cards[0].get("confidence") or 0.0) if pre_fallback_cards else 0.0
    pre_fallback_top_cov = float(pre_fallback_cards[0].get("avg_token_coverage") or 0.0) if pre_fallback_cards else 0.0
    pre_fallback_top_lexical_hits = (
        int((pre_fallback_cards[0].get("ranking_basis") or {}).get("lexical_cluster_hits") or 0)
        if pre_fallback_cards
        else 0
    )
    strong_primary_intent = bool(
        pre_fallback_cards
        and pre_fallback_top_conf >= PRODUCT_FALLBACK_STRONG_CONFIDENCE
        and pre_fallback_top_cov >= PRODUCT_FALLBACK_STRONG_COVERAGE
        and pre_fallback_top_lexical_hits >= 4
    )

    product_fallback_used = False
    product_hits = 0
    if MAPPING_ENABLE_PRODUCT_FALLBACK and top_conf < PRODUCT_FALLBACK_TRIGGER:
        fallback_votes, product_hits = future_product.result()
        if fallback_votes:
            fallback_applied = False
            intent_token_set = set(canonical_tokens(query_context.get("canonical_intent_tokens") or []))
            fallback_meta = _resolve_category_meta(list(fallback_votes.keys()))
            for category_id, bucket in fallback_votes.items():
                fallback_scale = 1.0
                fallback_raw = float(bucket.get("raw_score") or 0.0)
                names = fallback_meta.get(category_id, {})
                if not names:
                    continue

                intent_overlap = (
                    _category_name_intent_overlap(names, intent_token_set)
                    if intent_token_set
                    else 0.0
                )
                if category_id not in pre_fallback_scores and intent_token_set and intent_overlap < 0.18:
                    continue

                if strong_primary_intent and fallback_raw > 0:
                    if category_id in pre_fallback_scores:
                        cap = pre_fallback_scores[category_id] * PRODUCT_FALLBACK_MAX_GAIN_RATIO
                    else:
                        cap = pre_fallback_top_score * PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO
                    fallback_scale = clamp((cap / fallback_raw) if cap > 0 else 0.0, 0.0, 1.0)

                if intent_token_set:
                    overlap_scale = 0.45 + (0.55 * intent_overlap)
                    if category_id in pre_fallback_scores:
                        overlap_scale = max(0.55, overlap_scale)
                    fallback_scale *= overlap_scale

                if fallback_scale <= 0.0:
                    continue

                target = category_votes.setdefault(category_id, _new_vote_bucket())
                for key in (
                    "raw_score",
                    "raw_lexical_score",
                    "raw_semantic_score",
                    "raw_product_vote_score",
                    "cluster_hits",
                    "lexical_cluster_hits",
                    "semantic_cluster_hits",
                    "product_vote_hits",
                    "support_sum",
                    "ambiguity_sum",
                    "match_signal_sum",
                ):
                    target[key] += float(bucket[key]) * fallback_scale

                fallback_applied = True

                for sample_key in ("sample_products", "sample_keywords"):
                    for item in bucket[sample_key]:
                        if item not in target[sample_key] and len(target[sample_key]) < 3:
                            target[sample_key].append(item)

            if fallback_applied:
                product_fallback_used = True
                lanes_used.append("product_vote")

    cards = _build_mapping_cards(
        category_votes,
        max_cards=max_cards,
        use_calibrated_confidence=use_calibrated_confidence,
        intent_tokens=query_context.get("canonical_intent_tokens") or [],
        preferred_demographic_anchors=anchor_constraints["demographic"],
    )

    if strong_primary_intent and cards and pre_fallback_top_id:
        if as_text(cards[0].get("product_category_id")) != pre_fallback_top_id:
            cards = pre_fallback_cards
            product_fallback_used = False
            product_hits = 0
            lanes_used = [lane for lane in lanes_used if lane != "product_vote"]

    decision, confidence, margin = _mapping_decision(cards)
    alerts = _mapping_alerts(cards=cards, decision=decision, margin=margin, product_fallback_used=product_fallback_used)

    if emit_telemetry:
        top_card = cards[0] if cards else {}
        _emit_mapping_telemetry(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "query": as_text(query_text),
                "selected": as_text(selected_suggestion),
                "phase3_active": phase3_active,
                "decision": decision,
                "confidence": round(confidence, 4),
                "margin": round(margin, 4),
                "alerts": alerts,
                "lanes_used": lanes_used,
                "semantic_used": semantic_used,
                "product_fallback_used": product_fallback_used,
                "top_category_id": as_text(top_card.get("product_category_id")),
                "top_breadcrumb": as_text(top_card.get("breadcrumb")),
                "top_confidence": float(top_card.get("confidence") or 0.0),
                "top_confidence_raw": float(top_card.get("confidence_raw") or 0.0),
                "top_confidence_heuristic": float(top_card.get("confidence_calibrated_heuristic") or 0.0),
                "top_confidence_model_used": bool(top_card.get("confidence_model_used")),
                "top_lane_score_pct": top_card.get("lane_score_pct") or {},
            }
        )

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
        "phase3_active": phase3_active,
        "alerts": alerts,
    }


def current_thresholds() -> dict[str, Any]:
    return {
        "auto_map": AUTO_MAP_CONFIDENCE,
        "auto_map_margin": AUTO_MAP_MARGIN,
        "confirm": CONFIRM_MAP_CONFIDENCE,
        "product_fallback": PRODUCT_FALLBACK_TRIGGER,
        "product_fallback_max_gain_ratio": PRODUCT_FALLBACK_MAX_GAIN_RATIO,
        "product_fallback_new_category_cap_ratio": PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO,
        "product_fallback_strong_confidence": PRODUCT_FALLBACK_STRONG_CONFIDENCE,
        "product_fallback_strong_coverage": PRODUCT_FALLBACK_STRONG_COVERAGE,
        "phase3_canary_percent": MAPPING_PHASE3_CANARY_PERCENT,
        "alert_low_confidence": MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD,
        "alert_low_margin": MAPPING_ALERT_LOW_MARGIN_THRESHOLD,
        "alert_product_dominance_ratio": MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO,
        "enable_confidence_calibration": MAPPING_ENABLE_CONFIDENCE_CALIBRATION,
        "enable_learned_confidence_calibration": MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION,
        "confidence_model_file": MAPPING_CONFIDENCE_MODEL_FILE,
        "enable_semantic_fallback": MAPPING_ENABLE_SEMANTIC_FALLBACK,
        "enable_product_fallback": MAPPING_ENABLE_PRODUCT_FALLBACK,
    }
