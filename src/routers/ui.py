from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

try:
    from ..core.config import INDEX_NAME, KEYWORD_INDEX, UI_DIR
    from ..services.internal.common import index_doc_count, index_exists, sample_hierarchy_cards
    from ..services.mapping import current_thresholds, map_query_to_categories
    from ..services.internal.suggestions import fetch_keyword_suggestions
    from ..services.suppliers import supplier_doc_count, supplier_enrichment_enabled, supplier_index_exists
    from ..services.synonyms import expand_synonyms
except ImportError:
    from core.config import INDEX_NAME, KEYWORD_INDEX, UI_DIR
    from services.internal.common import index_doc_count, index_exists, sample_hierarchy_cards
    from services.mapping import current_thresholds, map_query_to_categories
    from services.internal.suggestions import fetch_keyword_suggestions
    from services.suppliers import supplier_doc_count, supplier_enrichment_enabled, supplier_index_exists
    from services.synonyms import expand_synonyms

router = APIRouter()


@router.get("/", include_in_schema=False)
def root_info():
    index_file = UI_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "service": "pepagora-search-api",
        "message": "API is running. HTML UI file not found.",
        "endpoints": ["/ui-api/snapshot", "/ui-api/suggestions", "/ui-api/hierarchy", "/search", "/quality/benchmark"],
    }


@router.get("/ui-api/snapshot")
def ui_data_snapshot():
    start = time.perf_counter()
    try:
        products_exists = index_exists(INDEX_NAME)
        keywords_exists = index_exists(KEYWORD_INDEX)
        products = index_doc_count(INDEX_NAME, known_exists=products_exists)
        keywords = index_doc_count(KEYWORD_INDEX, known_exists=keywords_exists)

        suppliers_enabled = supplier_enrichment_enabled()
        suppliers_exists = supplier_index_exists() if suppliers_enabled else False
        suppliers = supplier_doc_count() if suppliers_exists else 0

        return {
            "products": products,
            "keywords": keywords,
            "suppliers": suppliers,
            "suppliers_feature_enabled": suppliers_enabled,
            "products_index_exists": products_exists,
            "keywords_index_exists": keywords_exists,
            "suppliers_index_exists": suppliers_exists,
            "data_source": "elasticsearch",
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }
    except Exception as exc:
        return {
            "products": 0,
            "keywords": 0,
            "suppliers": 0,
            "suppliers_feature_enabled": False,
            "products_index_exists": False,
            "keywords_index_exists": False,
            "suppliers_index_exists": False,
            "data_source": "fallback",
            "error": str(exc),
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        }


@router.get("/ui-api/suggestions")
def ui_keyword_suggestions(
    q: str = Query(default="", min_length=0),
    limit: int = Query(default=12, ge=3, le=30),
):
    start = time.perf_counter()
    suggestions = fetch_keyword_suggestions(q, limit=limit)
    return {
        "query": q,
        "suggestions": suggestions,
        "ranking_order": [
            "exact",
            "prefix",
            "ordered_phrase",
            "ordered_tokens",
            "weak_contains",
            "fuzzy_fallback",
        ],
        "count": len(suggestions),
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
    }


@router.get("/ui-api/hierarchy")
def ui_hierarchy_mapping(
    keyword: str = Query(..., min_length=2),
    max_cards: int = Query(default=3, ge=1, le=6),
    selected_term: bool = Query(default=True),
):
    start = time.perf_counter()
    mapping = map_query_to_categories(
        query_text=keyword,
        selected_suggestion=keyword if selected_term else None,
        max_cards=max_cards,
        emit_telemetry=False,
    )
    cards = mapping.get("cards") or sample_hierarchy_cards(keyword, max_cards=max_cards)
    return {
        "keyword": keyword,
        "expanded_keyword": expand_synonyms(keyword) or keyword,
        "ranking_order": [
            "lexical_clusters_with_reliability",
            "semantic_cluster_fallback",
            "product_vote_fallback",
        ],
        "decision": mapping.get("decision", "options"),
        "confidence": mapping.get("confidence", 0.0),
        "margin": mapping.get("margin", 0.0),
        "needs_confirmation": mapping.get("needs_confirmation", False),
        "auto_mapped": mapping.get("auto_mapped", False),
        "intent_query": mapping.get("intent_query", ""),
        "lanes_used": mapping.get("lanes_used", []),
        "semantic_used": mapping.get("semantic_used", False),
        "product_fallback_used": mapping.get("product_fallback_used", False),
        "phase3_active": mapping.get("phase3_active", False),
        "alerts": mapping.get("alerts", []),
        "cards": cards,
        "matched_docs": mapping.get("matched_clusters", 0),
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        "count": len(cards),
    }


@router.get("/ui-api/map-category")
def ui_map_category(
    q: str = Query(..., min_length=2),
    selected: Optional[str] = Query(default=None),
    max_cards: int = Query(default=3, ge=1, le=6),
):
    start = time.perf_counter()
    mapping = map_query_to_categories(
        query_text=q,
        selected_suggestion=selected,
        max_cards=max_cards,
        emit_telemetry=False,
    )

    cards = mapping.get("cards") or sample_hierarchy_cards(selected or q, max_cards=max_cards)
    return {
        "query": q,
        "selected": selected,
        "normalized_query": mapping.get("normalized_query", ""),
        "decision": mapping.get("decision", "options"),
        "confidence": mapping.get("confidence", 0.0),
        "margin": mapping.get("margin", 0.0),
        "needs_confirmation": mapping.get("needs_confirmation", False),
        "auto_mapped": mapping.get("auto_mapped", False),
        "intent_query": mapping.get("intent_query", ""),
        "phrase_candidates": mapping.get("phrase_candidates", []),
        "top_category": mapping.get("top_category"),
        "cards": cards,
        "matched_clusters": mapping.get("matched_clusters", 0),
        "product_vote_hits": mapping.get("product_vote_hits", 0),
        "lanes_used": mapping.get("lanes_used", []),
        "semantic_used": mapping.get("semantic_used", False),
        "product_fallback_used": mapping.get("product_fallback_used", False),
        "phase3_active": mapping.get("phase3_active", False),
        "alerts": mapping.get("alerts", []),
        "thresholds": current_thresholds(),
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
    }
