from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

try:
    from ..services.search import autocomplete_search, search_products
except ImportError:
    from services.search import autocomplete_search, search_products

router = APIRouter()


@router.get("/autocomplete")
def autocomplete(q: str = Query(default="", min_length=0)):
    return autocomplete_search(q)


@router.get("/search")
def search(
    q: str = Query(default="", min_length=1),
    page: int = Query(default=1, ge=1),
    category: Optional[str] = Query(default=None),
    sub_category: Optional[str] = Query(default=None),
    prod_category: Optional[str] = Query(default=None),
    mode: str = Query(default="hybrid", pattern="^(keyword|semantic|hybrid)$"),
):
    return search_products(
        query=q,
        page=page,
        category=category,
        sub_category=sub_category,
        prod_category=prod_category,
        mode=mode,
    )
