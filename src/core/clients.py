"""Elasticsearch/OpenSearch client factory with split backend configuration.

Provides separate configuration for Elasticsearch and OpenSearch with runtime
backend selection, health checks, and per-request backend override.
"""

from __future__ import annotations

import contextvars
import time
from typing import Any

# TTL-based cache for backend reachability (avoids live network ping per request)
_REACHABILITY_CACHE: dict[str, tuple[bool, float]] = {}  # backend -> (is_reachable, timestamp)
_REACHABILITY_TTL = 10.0  # seconds — recheck at most once every 10 seconds
_AVAILABILITY_CACHE: dict[str, Any] = {}  # cached backend_availability() result
_AVAILABILITY_CACHE_TS: float = 0.0
_AVAILABILITY_TTL = 5.0  # seconds

# Short timeout used only for reachability probes — NOT for actual queries.
# ES_REQUEST_TIMEOUT_SEC (20s) is for queries; health checks must fail fast.
_HEALTH_CHECK_TIMEOUT_SEC = 2.0

try:
    from opensearchpy import OpenSearch
except Exception:
    OpenSearch = None  # type: ignore[assignment]

from elasticsearch import Elasticsearch

from .config import (
    SEARCH_BACKEND,
    ES_HOST,
    ES_USERNAME,
    ES_PASSWORD,
    ES_REQUEST_TIMEOUT_SEC,
    OPENSEARCH_HOST,
    OPENSEARCH_USERNAME,
    OPENSEARCH_PASSWORD,
    OPENSEARCH_REQUEST_TIMEOUT_SEC,
)

# Request-level backend override (contextvars for async safety)
_request_backend: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_backend", default=None)


class _OpenSearchCompatClient:
    """Accept Elasticsearch-style kwargs and translate for opensearch-py."""

    def __init__(self, client: Any):
        self._client = client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def search(self, index: str | None = None, **kwargs: Any) -> Any:
        # Transport-level kwargs must never be copied into the JSON body.
        kwargs.pop("request_timeout", None)
        params = kwargs.pop("params", None)
        headers = kwargs.pop("headers", None)
        body = dict(kwargs.pop("body", {}) or {})
        for key, value in kwargs.items():
            if value is None:
                continue
            body["from" if key == "from_" else key] = value
        return self._client.search(index=index, body=body, params=params, headers=headers)

    def msearch(self, body: Any = None, index: str | None = None, **kwargs: Any) -> Any:
        kwargs.pop("request_timeout", None)
        params = {k: v for k, v in kwargs.items() if v is not None}
        return self._client.msearch(body=body, index=index, params=params or None)

    def field_caps(self, index: str | None = None, fields: Any = None, **kwargs: Any) -> Any:
        params: dict[str, Any] = {}
        if fields is not None:
            if isinstance(fields, (list, tuple, set)):
                params["fields"] = ",".join(str(f) for f in fields)
            else:
                params["fields"] = str(fields)
        for key, value in kwargs.items():
            if value is None:
                continue
            params[key] = str(value).lower() if isinstance(value, bool) else value
        return self._client.field_caps(index=index, params=params or None)


def _build_client() -> Any:
    """Build appropriate client based on SEARCH_BACKEND configuration."""
    if SEARCH_BACKEND == "opensearch":
        # OpenSearch backend
        if not OPENSEARCH_HOST:
            raise RuntimeError("SEARCH_BACKEND=opensearch but OPENSEARCH_HOST not configured")

        if OpenSearch is not None:
            # Use native OpenSearch client
            auth = None
            if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
                auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

            raw_client = OpenSearch(
                hosts=[OPENSEARCH_HOST],
                basic_auth=auth if auth else None,
                timeout=OPENSEARCH_REQUEST_TIMEOUT_SEC,
                retry_on_timeout=True,
                max_retries=3,
                http_compress=True,
            )
            return _OpenSearchCompatClient(raw_client)
        else:
            # Fallback to elasticsearch-py for OpenSearch
            auth = None
            if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
                auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

            return Elasticsearch(
                OPENSEARCH_HOST,
                basic_auth=auth if auth else None,
                request_timeout=OPENSEARCH_REQUEST_TIMEOUT_SEC,
                retry_on_timeout=True,
                headers={
                    "content-type": "application/json",
                    "accept": "application/json",
                },
            )
    else:
        # Elasticsearch backend (default)
        auth = None
        if ES_USERNAME and ES_PASSWORD:
            auth = (ES_USERNAME, ES_PASSWORD)

        return Elasticsearch(
            ES_HOST,
            basic_auth=auth if auth else None,
            request_timeout=ES_REQUEST_TIMEOUT_SEC,
            retry_on_timeout=True,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
            },
        )


es = _build_client()


# Backend management functions
def active_search_backend() -> str:
    """Get the active search backend ('elasticsearch' or 'opensearch')."""
    request_backend = _request_backend.get()
    if request_backend:
        return request_backend
    return SEARCH_BACKEND


def normalize_search_backend(backend: str | None) -> str:
    """Normalize backend name to lowercase, return valid backend name."""
    if not backend:
        return active_search_backend()
    normalized = str(backend).strip().lower()
    if normalized not in ("elasticsearch", "opensearch"):
        raise ValueError(f"Invalid backend '{backend}'. Must be 'elasticsearch' or 'opensearch'.")
    return normalized


def set_request_search_backend(backend: str) -> contextvars.Token[str | None]:
    """Set backend for current request (overrides SEARCH_BACKEND)."""
    normalized = normalize_search_backend(backend)
    return _request_backend.set(normalized)


def reset_request_search_backend(token: contextvars.Token[str | None] | None = None) -> None:
    """Reset request-level backend override."""
    if token is None:
        _request_backend.set(None)
        return
    _request_backend.reset(token)


def is_backend_reachable(backend: str | None = None) -> bool:
    """Check if a backend is reachable (TTL-cached to avoid per-request network pings).

    If the backend is NOT the configured SEARCH_BACKEND, it is immediately marked
    unreachable without any network call — prevents ES pings when running on OpenSearch.
    """
    backend = normalize_search_backend(backend)

    # Fast-path: if this backend is not our configured backend, skip the network ping.
    # E.g. when SEARCH_BACKEND=opensearch, ES is always unreachable — no ping needed.
    if backend != SEARCH_BACKEND:
        _REACHABILITY_CACHE[backend] = (False, time.monotonic())
        return False

    # Serve from cache if still fresh
    cached = _REACHABILITY_CACHE.get(backend)
    if cached is not None:
        result, ts = cached
        if time.monotonic() - ts < _REACHABILITY_TTL:
            return result

    # Actually check reachability (only for configured backend)
    try:
        if backend == "opensearch":
            if not OPENSEARCH_HOST:
                _REACHABILITY_CACHE[backend] = (False, time.monotonic())
                return False
            test_client = _build_opensearch_health_client()
        else:
            test_client = _build_elasticsearch_health_client()

        # Simple health check — must use short timeout version of client
        result = test_client.info()
        reachable = bool(result)
    except Exception:
        reachable = False

    _REACHABILITY_CACHE[backend] = (reachable, time.monotonic())
    return reachable


def _build_elasticsearch_client() -> Any:
    """Build Elasticsearch client for QUERY use (uses full timeout)."""
    auth = None
    if ES_USERNAME and ES_PASSWORD:
        auth = (ES_USERNAME, ES_PASSWORD)

    return Elasticsearch(
        ES_HOST,
        basic_auth=auth if auth else None,
        request_timeout=ES_REQUEST_TIMEOUT_SEC,
        retry_on_timeout=True,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
        },
    )


def _build_elasticsearch_health_client() -> Any:
    """Build Elasticsearch client for HEALTH CHECK use (short 2s timeout)."""
    auth = None
    if ES_USERNAME and ES_PASSWORD:
        auth = (ES_USERNAME, ES_PASSWORD)

    return Elasticsearch(
        ES_HOST,
        basic_auth=auth if auth else None,
        request_timeout=_HEALTH_CHECK_TIMEOUT_SEC,
        retry_on_timeout=False,
        headers={
            "content-type": "application/json",
            "accept": "application/json",
        },
    )


def _build_opensearch_client() -> Any:
    """Build OpenSearch client for QUERY use (uses full timeout)."""
    if not OPENSEARCH_HOST:
        raise RuntimeError("OPENSEARCH_HOST not configured")

    if OpenSearch is not None:
        auth = None
        if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
            auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

        raw_client = OpenSearch(
            hosts=[OPENSEARCH_HOST],
            basic_auth=auth if auth else None,
            timeout=OPENSEARCH_REQUEST_TIMEOUT_SEC,
            retry_on_timeout=True,
            max_retries=3,
            http_compress=True,
        )
        return _OpenSearchCompatClient(raw_client)
    else:
        auth = None
        if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
            auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

        return Elasticsearch(
            OPENSEARCH_HOST,
            basic_auth=auth if auth else None,
            request_timeout=OPENSEARCH_REQUEST_TIMEOUT_SEC,
            retry_on_timeout=True,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
            },
        )


def _build_opensearch_health_client() -> Any:
    """Build OpenSearch client for HEALTH CHECK use (short 2s timeout, no retries)."""
    if not OPENSEARCH_HOST:
        raise RuntimeError("OPENSEARCH_HOST not configured")

    if OpenSearch is not None:
        auth = None
        if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
            auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

        raw_client = OpenSearch(
            hosts=[OPENSEARCH_HOST],
            basic_auth=auth if auth else None,
            timeout=_HEALTH_CHECK_TIMEOUT_SEC,
            retry_on_timeout=False,
            max_retries=0,
            http_compress=False,
        )
        return _OpenSearchCompatClient(raw_client)
    else:
        auth = None
        if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
            auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

        return Elasticsearch(
            OPENSEARCH_HOST,
            basic_auth=auth if auth else None,
            request_timeout=_HEALTH_CHECK_TIMEOUT_SEC,
            retry_on_timeout=False,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
            },
        )


def backend_availability() -> dict[str, dict[str, Any]]:
    """Get availability status of all backends (TTL-cached)."""
    global _AVAILABILITY_CACHE, _AVAILABILITY_CACHE_TS
    if _AVAILABILITY_CACHE and (time.monotonic() - _AVAILABILITY_CACHE_TS < _AVAILABILITY_TTL):
        return _AVAILABILITY_CACHE

    _AVAILABILITY_CACHE = {
        "elasticsearch": {
            "available": is_backend_reachable("elasticsearch"),
            "configured": bool(ES_HOST),
            "host": ES_HOST,
        },
        "opensearch": {
            "available": is_backend_reachable("opensearch"),
            "configured": bool(OPENSEARCH_HOST),
            "host": OPENSEARCH_HOST or "not configured",
        },
    }
    _AVAILABILITY_CACHE_TS = time.monotonic()
    return _AVAILABILITY_CACHE


def first_available_backend(preferred_backend: str | None = None, exclude_backend: str | None = None) -> str | None:
    """Find first available backend, optionally with preference and exclusion."""
    backends_to_try = []

    if preferred_backend:
        try:
            normalized_pref = normalize_search_backend(preferred_backend)
            backends_to_try.append(normalized_pref)
        except ValueError:
            pass

    # Add all backends to try list, excluding preferred if already added
    for backend in ["elasticsearch", "opensearch"]:
        if exclude_backend and normalize_search_backend(exclude_backend) == backend:
            continue
        if backend not in backends_to_try:
            backends_to_try.append(backend)

    # Try each backend
    for backend in backends_to_try:
        if is_backend_reachable(backend):
            return backend

    return None
