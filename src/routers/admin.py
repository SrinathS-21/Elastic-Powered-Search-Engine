from __future__ import annotations

from fastapi import APIRouter

try:
    from ..services.suppliers import refresh_suppliers_cache
except ImportError:
    from services.suppliers import refresh_suppliers_cache

router = APIRouter()


@router.post("/admin/refresh-suppliers", include_in_schema=False)
def refresh_suppliers() -> dict:
    count = refresh_suppliers_cache()
    return {"cleared": count, "message": "Supplier cache cleared - next requests will re-fetch from ES"}
