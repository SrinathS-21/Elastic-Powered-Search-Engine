from __future__ import annotations

try:
    from ..core.clients import es
    from ..core.config import SUPPLIER_ENRICHMENT_ENABLED, SUPPLIER_INDEX
except ImportError:
    from core.clients import es
    from core.config import SUPPLIER_ENRICHMENT_ENABLED, SUPPLIER_INDEX

_supplier_cache: dict[str, dict] = {}
_supplier_index_ready: bool | None = None


def supplier_enrichment_enabled() -> bool:
    return bool(SUPPLIER_ENRICHMENT_ENABLED and str(SUPPLIER_INDEX).strip())


def supplier_index_exists() -> bool:
    global _supplier_index_ready

    if not supplier_enrichment_enabled():
        _supplier_index_ready = False
        return False

    if _supplier_index_ready is not None:
        return _supplier_index_ready

    try:
        _supplier_index_ready = bool(es.indices.exists(index=SUPPLIER_INDEX))
    except Exception:
        _supplier_index_ready = False
    return _supplier_index_ready


def supplier_doc_count() -> int:
    global _supplier_index_ready

    if not supplier_index_exists():
        return 0

    try:
        return int(es.count(index=SUPPLIER_INDEX)["count"])
    except Exception:
        _supplier_index_ready = False
        return 0


def _fetch_suppliers_from_es(user_ids: list[str]) -> dict[str, dict]:
    global _supplier_index_ready

    if not user_ids or not supplier_index_exists():
        return {}
    try:
        response = es.mget(index=SUPPLIER_INDEX, ids=user_ids)
    except Exception:
        _supplier_index_ready = False
        return {}

    result: dict[str, dict] = {}
    for item in response.get("docs", []):
        if item.get("found"):
            result[item["_id"]] = item["_source"]
    return result


def get_suppliers(user_ids: list[str]) -> list[dict]:
    if not supplier_enrichment_enabled() or not user_ids:
        return []

    missing = [uid for uid in user_ids if uid not in _supplier_cache]
    if missing:
        fetched = _fetch_suppliers_from_es(missing)
        _supplier_cache.update(fetched)

    suppliers = [_supplier_cache[uid] for uid in user_ids if uid in _supplier_cache]
    return sorted(suppliers, key=lambda s: s.get("packageRank", 4))


def refresh_suppliers_cache() -> int:
    global _supplier_index_ready

    count = len(_supplier_cache)
    _supplier_cache.clear()
    _supplier_index_ready = None
    return count
