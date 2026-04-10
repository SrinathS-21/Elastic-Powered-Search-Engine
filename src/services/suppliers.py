from __future__ import annotations

try:
    from ..core.clients import es
    from ..core.config import SUPPLIER_INDEX
except ImportError:
    from core.clients import es
    from core.config import SUPPLIER_INDEX

_supplier_cache: dict[str, dict] = {}


def _fetch_suppliers_from_es(user_ids: list[str]) -> dict[str, dict]:
    if not user_ids:
        return {}
    response = es.mget(index=SUPPLIER_INDEX, ids=user_ids)
    result: dict[str, dict] = {}
    for item in response.get("docs", []):
        if item.get("found"):
            result[item["_id"]] = item["_source"]
    return result


def get_suppliers(user_ids: list[str]) -> list[dict]:
    missing = [uid for uid in user_ids if uid not in _supplier_cache]
    if missing:
        fetched = _fetch_suppliers_from_es(missing)
        _supplier_cache.update(fetched)

    suppliers = [_supplier_cache[uid] for uid in user_ids if uid in _supplier_cache]
    return sorted(suppliers, key=lambda s: s.get("packageRank", 4))


def refresh_suppliers_cache() -> int:
    count = len(_supplier_cache)
    _supplier_cache.clear()
    return count
