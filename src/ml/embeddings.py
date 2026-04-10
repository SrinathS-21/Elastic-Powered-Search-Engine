from __future__ import annotations

import os
from functools import lru_cache
from typing import Iterable

# Shared embedding settings used by API query-time and indexing-time encoding.
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-base-en-v1.5")
EMBED_DIM = int(os.getenv("EMBED_DIM", "768"))
EMBED_DEVICE = os.getenv("EMBED_DEVICE", "").strip()
EMBED_BATCH_SIZE = int(os.getenv("EMBED_BATCH_SIZE", "256"))
EMBED_MAX_SEQ_LENGTH = int(os.getenv("EMBED_MAX_SEQ_LENGTH", "0"))
BGE_QUERY_PREFIX = os.getenv(
    "BGE_QUERY_PREFIX",
    "Represent this sentence for searching relevant passages: ",
)
BGE_DOCUMENT_PREFIX = os.getenv("BGE_DOCUMENT_PREFIX", "")

_model = None


def _prepare_text(text: str, prefix: str) -> str:
    value = (text or "").strip()
    if not value:
        value = "unknown"
    return f"{prefix}{value}" if prefix else value


def _resolve_embed_device() -> str:
    preferred = EMBED_DEVICE.lower()
    try:
        import torch
    except Exception:
        return preferred or "cpu"

    if preferred:
        if preferred.startswith("cuda") and not torch.cuda.is_available():
            print("[embeddings] EMBED_DEVICE requests CUDA but CUDA is unavailable; falling back to CPU")
            return "cpu"
        return preferred

    return "cuda" if torch.cuda.is_available() else "cpu"


def get_embed_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        device = _resolve_embed_device()
        _model = SentenceTransformer(EMBED_MODEL_NAME, device=device)
        if EMBED_MAX_SEQ_LENGTH > 0:
            _model.max_seq_length = EMBED_MAX_SEQ_LENGTH
        model_dim = int(_model.get_sentence_embedding_dimension())
        if model_dim != EMBED_DIM:
            raise RuntimeError(
                f"Embedding dim mismatch: model '{EMBED_MODEL_NAME}' returns {model_dim}, "
                f"but EMBED_DIM={EMBED_DIM}"
            )
        print(
            f"[embeddings] model={EMBED_MODEL_NAME}, device={getattr(_model, 'device', device)}, "
            f"dim={model_dim}, max_seq_length={getattr(_model, 'max_seq_length', 'unknown')}"
        )
    return _model


@lru_cache(maxsize=1024)
def encode_query_text(text: str) -> tuple[float, ...]:
    model = get_embed_model()
    payload = _prepare_text(text, BGE_QUERY_PREFIX)
    vector = model.encode(
        payload,
        normalize_embeddings=True,
        batch_size=max(1, EMBED_BATCH_SIZE),
        show_progress_bar=False,
    )
    return tuple(float(x) for x in vector.tolist())


def encode_document_batch(texts: Iterable[str]) -> list[list[float]]:
    prepared = [_prepare_text(text, BGE_DOCUMENT_PREFIX) for text in texts]
    if not prepared:
        return []

    model = get_embed_model()
    matrix = model.encode(
        prepared,
        normalize_embeddings=True,
        batch_size=max(1, EMBED_BATCH_SIZE),
        show_progress_bar=False,
    )
    return [list(map(float, row.tolist())) for row in matrix]


def encode_document_text(text: str) -> list[float]:
    return encode_document_batch([text])[0]
