"""Search API routes for product discovery.

Provides autocomplete and product search endpoints with query event tracking
and search backend selection.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from src.services.internal.query_insights import track_query_event
from src.services.search import autocomplete_search, search_products

router = APIRouter()


@router.get("/autocomplete")
def autocomplete(q: str = Query(default="", min_length=0)):
    payload = autocomplete_search(q)
    track_query_event(q, endpoint="/autocomplete", query_kind="autocomplete")
    return payload


@router.get("/search")
def search(
    q: str = Query(default="", min_length=1),
    page: int = Query(default=1, ge=1),
    category: Optional[str] = Query(default=None),
    sub_category: Optional[str] = Query(default=None),
    prod_category: Optional[str] = Query(default=None),
    mode: str = Query(default="hybrid", pattern="^(keyword|semantic|hybrid)$"),
):
    payload = search_products(
        query=q,
        page=page,
        category=category,
        sub_category=sub_category,
        prod_category=prod_category,
        mode=mode,
    )
    track_query_event(q, endpoint="/search", query_kind="search", mode=mode)
    return payload
