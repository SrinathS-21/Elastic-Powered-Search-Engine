from __future__ import annotations

import os
from pathlib import Path

# Force transformers to stay on the PyTorch code path. This avoids importing
# incompatible TensorFlow packages from user site-packages during API startup.
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
INDEX_NAME = os.getenv("ES_INDEX", "pepagora_products")
SUPPLIER_INDEX = os.getenv("ES_SUPPLIER_INDEX", "pepagora_suppliers")
KEYWORD_INDEX = os.getenv("ES_KEYWORD_INDEX", "pepagora_keyword_cluster")
PAGE_SIZE = 20

BASE_DIR = Path(__file__).resolve().parent.parent.parent
UI_DIR = BASE_DIR / "ui"

SAMPLE_KEYWORD_MAP: dict[str, list[str]] = {
    "sports watch": [
        "Watches >> Wristwatches >> Sports",
        "Watches >> Smartwatches >> Fitness",
        "Watches >> Wristwatches >> Casual",
    ],
    "smart watch": [
        "Watches >> Smartwatches >> Fitness",
        "Watches >> Smartwatches >> Lifestyle",
        "Watches >> Smartwatches >> Kids",
    ],
    "stainless steel pipe": [
        "Industrial Supplies >> Pipes & Tubes >> Stainless Steel Pipes",
        "Industrial Supplies >> Pipe Fittings >> Stainless Steel Fittings",
        "Industrial Supplies >> Valves >> Stainless Steel Valves",
    ],
}

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
PRODUCT_FALLBACK_TRIGGER = float(os.getenv("PRODUCT_FALLBACK_TRIGGER", "0.39"))
SEMANTIC_CLUSTER_WEIGHT = float(os.getenv("SEMANTIC_CLUSTER_WEIGHT", "0.62"))
PRODUCT_VOTE_WEIGHT = float(os.getenv("PRODUCT_VOTE_WEIGHT", "0.70"))
PRODUCT_MAIN_VOTE_SHARE = float(os.getenv("PRODUCT_MAIN_VOTE_SHARE", "0.60"))
PRODUCT_SHORT_VOTE_SHARE = float(os.getenv("PRODUCT_SHORT_VOTE_SHARE", "0.40"))
SHORT_VECTOR_RERANK_BOOST = float(os.getenv("SHORT_VECTOR_RERANK_BOOST", "0.18"))
PHRASE_CANDIDATE_LIMIT = int(os.getenv("PHRASE_CANDIDATE_LIMIT", "8"))
