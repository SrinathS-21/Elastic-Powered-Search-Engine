"""Query event tracking and telemetry indexing.

Tracks search queries and relevance feedback with thread-safe buffering,
accumulating events for periodic indexing to OpenSearch.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from src.core.clients import active_search_backend, es
from src.core.config import (
    ES_REQUEST_TIMEOUT_SEC,
    QUERY_INSIGHTS_ENABLED,
    QUERY_INSIGHTS_INDEX,
    QUERY_INSIGHTS_MAX_QUERY_LENGTH,
)
from src.core.logger import log


_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="query-insights")
_INDEX_LOCK = threading.Lock()
_INDEX_READY: bool | None = None

_WARNING_RATE_LIMIT_SECONDS = 60.0
_LAST_WARNING_AT: dict[str, float] = {}


def _warn_rate_limited(key: str, message: str, *args: Any) -> None:
    now = time.monotonic()
    last = _LAST_WARNING_AT.get(key, 0.0)
    if now - last < _WARNING_RATE_LIMIT_SECONDS:
        return
    _LAST_WARNING_AT[key] = now
    log.warning(message, *args)


def _normalize_query(value: str) -> str:
    return " ".join(value.lower().split())


def _index_with_fallbacks(index_name: str, document: dict[str, Any]) -> None:
    attempts: list[dict[str, Any]] = [
        {"document": document, "request_timeout": ES_REQUEST_TIMEOUT_SEC},
        {"document": document},
        {"body": document, "request_timeout": ES_REQUEST_TIMEOUT_SEC},
        {"body": document},
    ]
    for kwargs in attempts:
        try:
            es.index(index=index_name, **kwargs)
            return
        except TypeError:
            continue
    es.index(index=index_name, body=document)


def _ensure_index() -> bool:
    global _INDEX_READY

    if _INDEX_READY is True:
        return True

    with _INDEX_LOCK:
        if _INDEX_READY is True:
            return True

        try:
            if bool(es.indices.exists(index=QUERY_INSIGHTS_INDEX)):
                _INDEX_READY = True
                return True
        except Exception as exc:
            _warn_rate_limited(
                "query_insights.exists",
                "Query insights index existence check failed (%s): %s",
                QUERY_INSIGHTS_INDEX,
                exc,
            )
            _INDEX_READY = False
            return False

        create_body = {
            "mappings": {
                "dynamic": True,
                "properties": {
                    "timestamp": {"type": "date", "format": "epoch_millis"},
                    "query_text": {
                        "type": "text",
                        "fields": {
                            "keyword": {
                                "type": "keyword",
                                "ignore_above": 512,
                            }
                        },
                    },
                    "query_date": {"type": "keyword"},
                    "query_normalized": {"type": "keyword", "ignore_above": 512},
                    "app_endpoint": {"type": "keyword"},
                    "app_kind": {"type": "keyword"},
                    "app_backend": {"type": "keyword"},
                    "app_mode": {"type": "keyword"},
                    "app_source": {"type": "keyword"},
                },
            }
        }
        try:
            es.indices.create(index=QUERY_INSIGHTS_INDEX, body=create_body)
            _INDEX_READY = True
            log.info("Created query insights index: %s", QUERY_INSIGHTS_INDEX)
            return True
        except Exception as exc:
            if "resource_already_exists_exception" in str(exc):
                _INDEX_READY = True
                return True
            _warn_rate_limited(
                "query_insights.create",
                "Query insights index creation failed (%s): %s",
                QUERY_INSIGHTS_INDEX,
                exc,
            )
            _INDEX_READY = False
            return False


def _write_event(document: dict[str, Any]) -> None:
    if not _ensure_index():
        return
    try:
        _index_with_fallbacks(QUERY_INSIGHTS_INDEX, document)
    except Exception as exc:
        _warn_rate_limited(
            "query_insights.write",
            "Query insights write failed (%s): %s",
            QUERY_INSIGHTS_INDEX,
            exc,
        )


def track_query_event(
    query_text: str,
    endpoint: str,
    query_kind: str,
    mode: str | None = None,
    backend: str | None = None,
) -> None:
    if not QUERY_INSIGHTS_ENABLED:
        return

    query = str(query_text or "").strip()
    if not query:
        return

    max_len = max(32, int(QUERY_INSIGHTS_MAX_QUERY_LENGTH))
    if len(query) > max_len:
        query = query[:max_len]

    timestamp_ms = int(time.time() * 1000)
    query_date = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    backend_name = str(backend or active_search_backend() or "unknown").strip().lower() or "unknown"

    event = {
        "timestamp": timestamp_ms,
        "query_text": query,
        "query_normalized": _normalize_query(query),
        "query_date": query_date,
        "app_endpoint": str(endpoint or "unknown").strip() or "unknown",
        "app_kind": str(query_kind or "unknown").strip() or "unknown",
        "app_backend": backend_name,
        "app_mode": str(mode or "").strip() or "unknown",
        "app_source": "search_api",
    }

    try:
        _EXECUTOR.submit(_write_event, event)
    except RuntimeError:
        pass
