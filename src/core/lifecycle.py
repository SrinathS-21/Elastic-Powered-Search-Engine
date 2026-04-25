"""FastAPI application lifespan manager.

Handles pre-warming of runtime dependencies (embedding service) at application startup
to ensure all services are ready before processing requests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from .embedding_client import EMBED_DIM, EMBED_MODEL_NAME, EMBEDDING_API_URL, get_embed_model
from .logger import log


@asynccontextmanager
async def lifespan(_app):
    """Pre-warm runtime dependencies at startup."""
    try:
        meta = get_embed_model()
        log.info(
            "Embedding service reachable (%s, %s dims) at %s",
            meta.get("model_name") or EMBED_MODEL_NAME,
            meta.get("dim") or EMBED_DIM,
            EMBEDDING_API_URL,
        )
    except Exception as exc:
        log.warning("Embedding service unavailable at startup (%s), semantic mode disabled: %s", EMBEDDING_API_URL, exc)

    yield
