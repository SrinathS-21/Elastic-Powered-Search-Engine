"""Centralized environment configuration and runtime settings.

Loads and validates all environment variables for API configuration, index names,
feature flags, timeouts, and file paths. Provides typed access to settings.
"""

from __future__ import annotations

import os
from pathlib import Path

# Force transformers to stay on the PyTorch code path. This avoids importing
# incompatible TensorFlow packages from user site-packages during API startup.
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}. Define it in .env.")
    return value.strip()


def _required_port(name: str) -> int:
    raw = _required_env(name)
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid {name}='{raw}'. Expected integer port.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"Invalid {name}='{raw}'. Expected value between 1 and 65535.")
    return port


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


ES_HOST = _required_env("ES_HOST")
ES_REQUEST_TIMEOUT_SEC = _env_float("ES_REQUEST_TIMEOUT_SEC", 20.0)
ES_INDEX = os.getenv("ES_INDEX", "pepagora_products")
ES_KEYWORD_INDEX = os.getenv("ES_KEYWORD_INDEX", "pepagora_keyword_cluster")
ES_USERNAME = os.getenv("ES_USERNAME", "").strip()
ES_PASSWORD = os.getenv("ES_PASSWORD", "").strip()

# OpenSearch configuration (separate from Elasticsearch)
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "").strip()
OPENSEARCH_REQUEST_TIMEOUT_SEC = _env_float("OPENSEARCH_REQUEST_TIMEOUT_SEC", 20.0)
OPENSEARCH_PRODUCT_READ_ALIAS = os.getenv(
    "OPENSEARCH_PRODUCT_READ_ALIAS",
    os.getenv("OPENSEARCH_PRODUCT_INDEX", "pepagora_products"),
).strip()
OPENSEARCH_PRODUCT_WRITE_ALIAS = os.getenv(
    "OPENSEARCH_PRODUCT_WRITE_ALIAS",
    OPENSEARCH_PRODUCT_READ_ALIAS,
).strip()
# Backward compatibility alias for older imports/usages.
OPENSEARCH_PRODUCT_INDEX = OPENSEARCH_PRODUCT_READ_ALIAS
OPENSEARCH_KEYWORD_INDEX = os.getenv("OPENSEARCH_KEYWORD_INDEX", "pepagora_keyword_cluster")
OPENSEARCH_USERNAME = os.getenv("OPENSEARCH_USERNAME", "").strip()
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "").strip()

# Search backend selection
SEARCH_BACKEND = os.getenv("SEARCH_BACKEND", "elasticsearch").strip().lower()

# Active configuration based on selected backend
if SEARCH_BACKEND == "opensearch":
    if not OPENSEARCH_HOST:
        raise RuntimeError("SEARCH_BACKEND=opensearch but OPENSEARCH_HOST not defined in .env")
    INDEX_NAME = OPENSEARCH_PRODUCT_READ_ALIAS
    KEYWORD_INDEX = OPENSEARCH_KEYWORD_INDEX
    ACTIVE_HOST = OPENSEARCH_HOST
    ACTIVE_USERNAME = OPENSEARCH_USERNAME
    ACTIVE_PASSWORD = OPENSEARCH_PASSWORD
    ACTIVE_TIMEOUT_SEC = OPENSEARCH_REQUEST_TIMEOUT_SEC
elif SEARCH_BACKEND == "elasticsearch":
    INDEX_NAME = ES_INDEX
    KEYWORD_INDEX = ES_KEYWORD_INDEX
    ACTIVE_HOST = ES_HOST
    ACTIVE_USERNAME = ES_USERNAME
    ACTIVE_PASSWORD = ES_PASSWORD
    ACTIVE_TIMEOUT_SEC = ES_REQUEST_TIMEOUT_SEC
else:
    raise RuntimeError(f"Invalid SEARCH_BACKEND='{SEARCH_BACKEND}'. Must be 'elasticsearch' or 'opensearch'.")

PAGE_SIZE = 20

BASE_DIR = Path(__file__).resolve().parent.parent.parent
UI_DIR = BASE_DIR / "ui"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


APP_SCHEME = _required_env("APP_SCHEME")
APP_HOST = _required_env("APP_HOST")
APP_PORT = _required_port("APP_PORT")
UI_API_BASE_URL = (os.getenv("UI_API_BASE_URL") or "").strip().rstrip("/")


# Suggestion safety controls tuned for sparse keyword clusters.
KEYWORD_SUGGEST_DOCS = int(os.getenv("KEYWORD_SUGGEST_DOCS", "96"))
HEAD_TERMS_HARD_CAP = int(os.getenv("HEAD_TERMS_HARD_CAP", "1000"))
HEAD_TERMS_PER_DOC_LIMIT = int(os.getenv("HEAD_TERMS_PER_DOC_LIMIT", "48"))
VARIANT_TERMS_PER_DOC_LIMIT = int(os.getenv("VARIANT_TERMS_PER_DOC_LIMIT", "32"))
LONG_TAIL_TERMS_PER_DOC_LIMIT = int(os.getenv("LONG_TAIL_TERMS_PER_DOC_LIMIT", "24"))

# Category mapping confidence controls.
KEYWORD_CLUSTER_FETCH_SIZE = int(os.getenv("KEYWORD_CLUSTER_FETCH_SIZE", "80"))
KEYWORD_P95_PRODUCT_COUNT = max(1, int(os.getenv("KEYWORD_P95_PRODUCT_COUNT", "17")))
RELIABILITY_BETA = float(os.getenv("RELIABILITY_BETA", "0.35"))
AUTO_MAP_CONFIDENCE = float(os.getenv("AUTO_MAP_CONFIDENCE", "0.64"))
AUTO_MAP_MARGIN = float(os.getenv("AUTO_MAP_MARGIN", "0.10"))
CONFIRM_MAP_CONFIDENCE = float(os.getenv("CONFIRM_MAP_CONFIDENCE", "0.40"))
PRODUCT_FALLBACK_TRIGGER = float(os.getenv("PRODUCT_FALLBACK_TRIGGER", "0.32"))
SEMANTIC_CLUSTER_WEIGHT = float(os.getenv("SEMANTIC_CLUSTER_WEIGHT", "0.62"))
PRODUCT_VOTE_WEIGHT = float(os.getenv("PRODUCT_VOTE_WEIGHT", "0.45"))
PRODUCT_MAIN_VOTE_SHARE = float(os.getenv("PRODUCT_MAIN_VOTE_SHARE", "0.60"))
PRODUCT_SHORT_VOTE_SHARE = float(os.getenv("PRODUCT_SHORT_VOTE_SHARE", "0.40"))
PRODUCT_FALLBACK_MAX_GAIN_RATIO = float(os.getenv("PRODUCT_FALLBACK_MAX_GAIN_RATIO", "0.55"))
PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO = float(os.getenv("PRODUCT_FALLBACK_NEW_CATEGORY_CAP_RATIO", "0.18"))
PRODUCT_FALLBACK_STRONG_CONFIDENCE = float(os.getenv("PRODUCT_FALLBACK_STRONG_CONFIDENCE", "0.30"))
PRODUCT_FALLBACK_STRONG_COVERAGE = float(os.getenv("PRODUCT_FALLBACK_STRONG_COVERAGE", "0.45"))

# Phase-3 rollout switches and telemetry.
MAPPING_PHASE3_CANARY_PERCENT = float(os.getenv("MAPPING_PHASE3_CANARY_PERCENT", "100"))
MAPPING_ENABLE_CONFIDENCE_CALIBRATION = _env_bool("MAPPING_ENABLE_CONFIDENCE_CALIBRATION", True)
MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION = _env_bool("MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION", True)
MAPPING_ENABLE_SEMANTIC_FALLBACK = _env_bool("MAPPING_ENABLE_SEMANTIC_FALLBACK", True)
MAPPING_ENABLE_PRODUCT_FALLBACK = _env_bool("MAPPING_ENABLE_PRODUCT_FALLBACK", True)
MAPPING_TELEMETRY_ENABLED = _env_bool("MAPPING_TELEMETRY_ENABLED", True)
MAPPING_TELEMETRY_FILE = os.getenv("MAPPING_TELEMETRY_FILE", str(BASE_DIR / "logs" / "mapping_telemetry.jsonl"))
MAPPING_CONFIDENCE_MODEL_FILE = os.getenv(
    "MAPPING_CONFIDENCE_MODEL_FILE",
    str(BASE_DIR / "config" / "mapping_confidence_calibration.json"),
)

# Alert thresholds for monitoring and triage.
MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD = float(os.getenv("MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD", "0.30"))
MAPPING_ALERT_LOW_MARGIN_THRESHOLD = float(os.getenv("MAPPING_ALERT_LOW_MARGIN_THRESHOLD", "0.05"))
MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO = float(os.getenv("MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO", "0.70"))
SHORT_VECTOR_RERANK_BOOST = float(os.getenv("SHORT_VECTOR_RERANK_BOOST", "0.18"))
PHRASE_CANDIDATE_LIMIT = int(os.getenv("PHRASE_CANDIDATE_LIMIT", "8"))

# Fallback sample keyword-to-hierarchy map used by UI helpers when live index
# data is unavailable.
SAMPLE_KEYWORD_MAP: dict[str, list[str]] = {}

# Query insights and telemetry tracking.
QUERY_INSIGHTS_ENABLED = _env_bool("QUERY_INSIGHTS_ENABLED", False)
QUERY_INSIGHTS_INDEX = os.getenv("QUERY_INSIGHTS_INDEX", "query_insights")
QUERY_INSIGHTS_MAX_QUERY_LENGTH = int(os.getenv("QUERY_INSIGHTS_MAX_QUERY_LENGTH", "500"))
