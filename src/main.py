"""
Pepagora Search API entrypoint.

This module now composes routers and shared runtime dependencies.
Business logic is split into dedicated service modules for maintainability.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

try:
    from .core.config import UI_DIR
    from .core.lifecycle import lifespan
    from .routers import admin as admin_router
    from .routers import quality as quality_router
    from .routers import search as search_router
    from .routers import ui as ui_router
    from .services.benchmark import run_quality_benchmark
    from .services import mapping as mapping_service
    from .services.search import autocomplete_search, build_keyword_query, search_products
    from .services.synonyms import COMPACT_BENCHMARK_QUERIES, DEFAULT_BENCHMARK_QUERIES, expand_synonyms
except ImportError:
    from core.config import UI_DIR
    from core.lifecycle import lifespan
    from routers import admin as admin_router
    from routers import quality as quality_router
    from routers import search as search_router
    from routers import ui as ui_router
    from services.benchmark import run_quality_benchmark
    import services.mapping as mapping_service
    from services.search import autocomplete_search, build_keyword_query, search_products
    from services.synonyms import COMPACT_BENCHMARK_QUERIES, DEFAULT_BENCHMARK_QUERIES, expand_synonyms


AUTO_MAP_CONFIDENCE = mapping_service.AUTO_MAP_CONFIDENCE
AUTO_MAP_MARGIN = mapping_service.AUTO_MAP_MARGIN
CONFIRM_MAP_CONFIDENCE = mapping_service.CONFIRM_MAP_CONFIDENCE
PRODUCT_FALLBACK_TRIGGER = mapping_service.PRODUCT_FALLBACK_TRIGGER
SEMANTIC_CLUSTER_WEIGHT = mapping_service.SEMANTIC_CLUSTER_WEIGHT
PRODUCT_VOTE_WEIGHT = mapping_service.PRODUCT_VOTE_WEIGHT
PRODUCT_MAIN_VOTE_SHARE = mapping_service.PRODUCT_MAIN_VOTE_SHARE
PRODUCT_SHORT_VOTE_SHARE = mapping_service.PRODUCT_SHORT_VOTE_SHARE


def _sync_mapping_knobs() -> None:
    mapping_service.AUTO_MAP_CONFIDENCE = AUTO_MAP_CONFIDENCE
    mapping_service.AUTO_MAP_MARGIN = AUTO_MAP_MARGIN
    mapping_service.CONFIRM_MAP_CONFIDENCE = CONFIRM_MAP_CONFIDENCE
    mapping_service.PRODUCT_FALLBACK_TRIGGER = PRODUCT_FALLBACK_TRIGGER
    mapping_service.SEMANTIC_CLUSTER_WEIGHT = SEMANTIC_CLUSTER_WEIGHT
    mapping_service.PRODUCT_VOTE_WEIGHT = PRODUCT_VOTE_WEIGHT
    mapping_service.PRODUCT_MAIN_VOTE_SHARE = PRODUCT_MAIN_VOTE_SHARE
    mapping_service.PRODUCT_SHORT_VOTE_SHARE = PRODUCT_SHORT_VOTE_SHARE


def map_query_to_categories(
    query_text: str,
    selected_suggestion: str | None = None,
    max_cards: int = 3,
    emit_telemetry: bool = True,
):
    _sync_mapping_knobs()
    return mapping_service.map_query_to_categories(
        query_text=query_text,
        selected_suggestion=selected_suggestion,
        max_cards=max_cards,
        emit_telemetry=emit_telemetry,
    )


def current_thresholds() -> dict[str, float]:
    _sync_mapping_knobs()
    return mapping_service.current_thresholds()


def product_short_vector_boost_map(query_text: str, size: int):
    return mapping_service.product_short_vector_boost_map(query_text, size)


app = FastAPI(title="Pepagora Autocomplete", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")

app.include_router(admin_router.router)
app.include_router(ui_router.router)
app.include_router(search_router.router)
app.include_router(quality_router.router)


# Compatibility wrappers for existing utility scripts that import from src.main.
def search(
    q: str = "",
    page: int = 1,
    category: str | None = None,
    sub_category: str | None = None,
    prod_category: str | None = None,
    mode: str = "hybrid",
):
    return search_products(
        query=q,
        page=page,
        category=category,
        sub_category=sub_category,
        prod_category=prod_category,
        mode=mode,
    )


def autocomplete(q: str = ""):
    return autocomplete_search(q)
