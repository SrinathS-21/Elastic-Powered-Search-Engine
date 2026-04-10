from __future__ import annotations

from contextlib import asynccontextmanager

from .clients import es
from .config import SUPPLIER_INDEX
from .logger import log

try:
    from ..ml.embeddings import EMBED_DIM, EMBED_MODEL_NAME, get_embed_model
except ImportError:
    try:
        from ml.embeddings import EMBED_DIM, EMBED_MODEL_NAME, get_embed_model
    except ImportError:
        from src.ml.embeddings import EMBED_DIM, EMBED_MODEL_NAME, get_embed_model


@asynccontextmanager
async def lifespan(_app):
    """Pre-warm runtime dependencies at startup."""
    try:
        if es.indices.exists(index=SUPPLIER_INDEX):
            count = es.count(index=SUPPLIER_INDEX)["count"]
            log.info("Supplier index '%s': %s suppliers ready", SUPPLIER_INDEX, f"{count:,}")
        else:
            log.warning("Supplier index '%s' not found", SUPPLIER_INDEX)
    except Exception as exc:
        log.warning("Elasticsearch unavailable at startup: %s", exc)

    try:
        get_embed_model()
        log.info("Embedding model loaded (%s, %s dims)", EMBED_MODEL_NAME, EMBED_DIM)
    except Exception as exc:
        log.warning("Embedding model unavailable at startup, semantic mode disabled: %s", exc)

    yield
