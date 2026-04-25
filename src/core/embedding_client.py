"""HTTP client for the external embedding service.

This project does not host the embedding model locally. It only calls the
configured embedding API and converts responses into vectors for semantic use.

The Pepagora embedding service (http://52.66.148.21:8080) exposes ONLY:
  GET  /health           -> {"status":"ok","model_name":...,"dim":...,"device":...}
  POST /encode/query     -> {"text": <str>}  returns {"embedding":[...],"dim":N,"model_name":...}
  POST /encode/documents -> {"texts":[<str>,...]} returns batch embeddings

All other paths (/embed, /embeddings, /v1/embeddings, /info, /model) return 404.
The client tries /encode/query first (the canonical endpoint), then falls back to
/encode/documents, then legacy paths for future server migrations.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "http://127.0.0.1:8001").strip().rstrip("/")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-base-en-v1.5").strip()
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))
_EMBED_TIMEOUT_SEC = max(1.0, float(os.getenv("EMBED_REQUEST_TIMEOUT_SEC", "20.0")))


def _extract_embedding(payload: Any) -> list[float]:
    """Extract a float vector from various response shapes."""
    if isinstance(payload, list):
        if payload and isinstance(payload[0], (int, float)):
            return [float(value) for value in payload]
        if payload and isinstance(payload[0], dict):
            for item in payload:
                if isinstance(item, dict) and "embedding" in item:
                    return _extract_embedding(item["embedding"])

    if isinstance(payload, dict):
        for key in ("embedding", "vector", "embeddings"):
            value = payload.get(key)
            if value is not None:
                return _extract_embedding(value)
        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and "embedding" in first:
                return _extract_embedding(first["embedding"])

    raise RuntimeError("Embedding service response did not contain a vector")


def _extract_metadata(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {
            "model_name": payload.get("model_name") or payload.get("model") or EMBED_MODEL_NAME,
            "dim": payload.get("dim") or payload.get("dimension") or EMBED_DIM,
            "url": EMBEDDING_API_URL,
        }
    return {"model_name": EMBED_MODEL_NAME, "dim": EMBED_DIM, "url": EMBEDDING_API_URL}


@lru_cache(maxsize=1)
def get_embed_model() -> dict[str, Any]:
    """Probe the embedding service and return model metadata.

    Only GET /health is available on the Pepagora embedding server.
    """
    if not EMBEDDING_API_URL:
        raise RuntimeError("EMBEDDING_API_URL is not configured")

    url = f"{EMBEDDING_API_URL}/health"
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=_EMBED_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(
            f"Unable to contact embedding service at {EMBEDDING_API_URL}: {exc}"
        ) from exc

    return _extract_metadata(payload)


@lru_cache(maxsize=4096)
def encode_query_text(text: str) -> tuple[float, ...]:
    """Encode a query string into a dense embedding vector.

    Primary endpoint: POST /encode/query with {"text": <str>}
    The server response is: {"embedding": [...768 floats...], "dim": 768, "model_name": "..."}

    Falls back through /encode/documents and legacy paths in case the
    server is swapped or upgraded in the future.
    """
    if not EMBEDDING_API_URL:
        raise RuntimeError("EMBEDDING_API_URL is not configured")

    normalized = (text or "").strip()
    if not normalized:
        return tuple(0.0 for _ in range(EMBED_DIM))

    # Ordered list of (path, payload) — first success wins.
    # /encode/query is the canonical Pepagora embedding service endpoint.
    candidates: list[tuple[str, dict[str, Any]]] = [
        ("/encode/query",     {"text": normalized}),
        ("/encode/documents", {"texts": [normalized]}),
    ]

    last_error: Exception | None = None
    for path, payload in candidates:
        url = f"{EMBEDDING_API_URL}{path}"
        body = json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=_EMBED_TIMEOUT_SEC) as response:
                raw = response.read().decode("utf-8")
                parsed = json.loads(raw) if raw else {}
            vector = _extract_embedding(parsed)
            if len(vector) != EMBED_DIM:
                raise RuntimeError(
                    f"Embedding dimension mismatch: expected {EMBED_DIM}, got {len(vector)}"
                )
            return tuple(vector)
        except HTTPError as exc:
            if exc.code == 404:
                # This path doesn't exist on the current server — try next silently
                last_error = exc
                continue
            last_error = exc
        except (URLError, TimeoutError, ValueError, RuntimeError) as exc:
            last_error = exc

    if last_error is None:
        raise RuntimeError(f"Unable to encode query text via {EMBEDDING_API_URL}")
    raise RuntimeError(
        f"Unable to encode query text via {EMBEDDING_API_URL}: {last_error}"
    ) from last_error
