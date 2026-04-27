"""Web UI and configuration API routes.

Serves HTML pages, configuration APIs, and runtime metadata for the web interface.
Includes backend status and index information endpoints.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from src.core.clients import active_search_backend, backend_availability, first_available_backend
from src.core.config import APP_HOST, APP_PORT, APP_SCHEME, UI_API_BASE_URL, UI_DIR
from src.services.mapping import map_query_to_categories, sample_hierarchy_cards
from src.services.internal.query_insights import track_query_event
from src.services.suggestions import fetch_keyword_suggestions

router = APIRouter()


@router.get("/", include_in_schema=False)
def root_info():
    index_file = UI_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {
        "service": "pepagora-search-api",
        "message": "API is running. HTML UI file not found.",
        "endpoints": ["/ui-api/runtime-config", "/ui-api/backend-status", "/ui-api/suggestions", "/ui-api/map-category"],
    }


@router.get("/pbr-quick-post-enrich.html", include_in_schema=False)
def ui_quick_post_enrich():
    pbr_file = UI_DIR / "pbr-quick-post-enrich.html"
    if pbr_file.exists():
        return FileResponse(pbr_file, media_type="text/html")
    return {
        "error": "pbr-quick-post-enrich.html not found",
        "available_at": "/ui/pbr-quick-post-enrich.html",
    }


@router.get("/ui-api/runtime-config")
def ui_runtime_config():
    """Returns API base URL and configured backend — no live health checks, always fast."""
    if UI_API_BASE_URL:
        api_base_url = UI_API_BASE_URL
        source = "UI_API_BASE_URL"
    else:
        api_base_url = f"{APP_SCHEME}://{APP_HOST}:{APP_PORT}"
        source = "APP_SCHEME+APP_HOST+APP_PORT"

    # Read directly from env — no live backend pings here so this stays <5ms.
    configured_default_backend = (os.getenv("SEARCH_BACKEND", "elasticsearch").strip().lower() or "elasticsearch")

    return {
        "api_base_url": api_base_url,
        "api_host": APP_HOST,
        "api_port": APP_PORT,
        "source": source,
        "default_backend": configured_default_backend,
        "active_backend": configured_default_backend,
        "backend_query_param": "backend",
        # Availability is intentionally omitted here — call /ui-api/backend-status for that.
        "available_backends": [configured_default_backend],
        "backend_availability": {},
    }


@router.get("/ui-api/backend-status")
def ui_backend_status():
    """Returns live backend availability. Slower (does real network pings). Call lazily."""
    availability = backend_availability()
    preferred_backend = active_search_backend()
    active_backend = first_available_backend(preferred_backend=preferred_backend) or preferred_backend
    available_backends = [
        backend_name
        for backend_name, status in availability.items()
        if bool(status.get("available"))
    ]
    return {
        "active_backend": active_backend,
        "available_backends": available_backends,
        "backend_availability": availability,
    }



@router.get("/ui-api/suggestions")
def ui_keyword_suggestions(
    q: str = Query(default="", min_length=0),
    limit: int = Query(default=12, ge=3, le=30),
):
    start = time.perf_counter()
    suggestions = fetch_keyword_suggestions(q, limit=limit)
    track_query_event(q, endpoint="/ui-api/suggestions", query_kind="suggestions")
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


@router.get("/ui-api/map-category")
def ui_map_category(
    q: str = Query(..., min_length=2),
    selected: Optional[str] = Query(default=None),
    max_cards: int = Query(default=3, ge=1, le=6),
):
    start = time.perf_counter()
    tracked_query = selected or q
    track_query_event(tracked_query, endpoint="/ui-api/map-category", query_kind="map_category")
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
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
    }
