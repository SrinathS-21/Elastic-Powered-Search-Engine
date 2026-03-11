"""
Pepagora Autocomplete API
Serves autocomplete suggestions from Elasticsearch and static UI files.

Endpoints:
  GET /                        → serves ui/index.html
  GET /autocomplete?q=<query>  → returns category + product suggestions
  GET /search?q=<query>&page=1 → returns full paginated search results
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from elasticsearch import Elasticsearch
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# ── Constants ─────────────────────────────────────────────────────────────────
ES_HOST    = os.getenv("ES_HOST", "http://localhost:9200")
INDEX_NAME = os.getenv("ES_INDEX", "pepagora_products")
UI_DIR     = Path(__file__).parent.parent / "ui"
PAGE_SIZE  = 20

# ── ES client ─────────────────────────────────────────────────────────────────
es = Elasticsearch(ES_HOST, request_timeout=10, retry_on_timeout=True)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Pepagora Autocomplete", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Serve static UI files (CSS, JS, images if added later)
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def serve_ui():
    """Serve the main search UI."""
    return FileResponse(str(UI_DIR / "index.html"))


@app.get("/autocomplete")
def autocomplete(q: str = Query(default="", min_length=0)):
    """
    Returns autocomplete suggestions for the given prefix query.

    Response shape:
    {
      "query": "bat",
      "categories":         ["Bath & Body Care"],          // L1 — may be empty
      "sub_categories":     ["Bath Linens & Towels"],      // L2 — may be empty
      "product_categories": ["Bath Towels"],               // L3 — may be empty
      "products": [
        {"name": "...", "category": "...", "sub_category": "..."},
        ...  // up to 5
      ],
      "total": 1945
    }
    """
    q = q.strip()
    if len(q) < 2:
        return {
            "query": q,
            "categories": [],
            "sub_categories": [],
            "product_categories": [],
            "products": [],
            "total": 0,
            "latency_ms": 0,
        }

    t0 = time.perf_counter()

    # ── msearch: 3 queries in one HTTP round-trip ─────────────────────────────
    #
    # Query A: filtered aggregations — finds category NAMES that start with q
    # Query B: match_phrase_prefix on productName — finds product suggestions
    # Query C: multi_match count — total matching products
    #
    msearch_body = [
        # ── Query A header + body ──────────────────────────────────────────────
        {"index": INDEX_NAME},
        {
            "size": 0,
            "aggs": {
                "cat_filter": {
                    "filter": {
                        "match_phrase_prefix": {"category_name.text": q}
                    },
                    "aggs": {
                        "top": {"terms": {"field": "category_name", "size": 1}}
                    },
                },
                "subcat_filter": {
                    "filter": {
                        "match_phrase_prefix": {"subCategory_name.text": q}
                    },
                    "aggs": {
                        "top": {"terms": {"field": "subCategory_name", "size": 1}}
                    },
                },
                "prodcat_filter": {
                    "filter": {
                        "match_phrase_prefix": {"productCategory_name.text": q}
                    },
                    "aggs": {
                        "top": {"terms": {"field": "productCategory_name", "size": 1}}
                    },
                },
            },
        },
        # ── Query B header + body ──────────────────────────────────────────────
        # 4-tier strategy (highest → lowest priority):
        #   ① match_phrase_prefix  — exact prefix, preserves word order  boost=5
        #   ② ngram AND            — all typed tokens in edge-ngram index  boost=3
        #   ③ ngram OR             — any typed token matches               boost=1
        #   ④ fuzzy                — typo tolerance (edit distance AUTO)   boost=0.5
        {"index": INDEX_NAME},
        {
            "size": 5,
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase_prefix": {
                            "productName": {"query": q, "boost": 5.0}
                        }},
                        {"match": {
                            "productName.ngram": {
                                "query": q, "operator": "and", "boost": 3.0
                            }
                        }},
                        {"match": {
                            "productName.ngram": {
                                "query": q, "operator": "or", "boost": 1.0
                            }
                        }},
                        {"match": {
                            "productName": {
                                "query": q, "fuzziness": "AUTO",
                                "prefix_length": 1, "boost": 0.5
                            }
                        }},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "_source": ["productName", "category_name", "subCategory_name"],
        },
    ]

    responses = es.msearch(body=msearch_body)["responses"]
    agg_resp, prod_resp = responses[0], responses[1]

    def top_bucket(agg_key: str) -> list[str]:
        buckets = agg_resp["aggregations"][agg_key]["top"]["buckets"]
        return [b["key"] for b in buckets]

    max_score = prod_resp["hits"].get("max_score") or 1

    products = [
        {
            "name":         h["_source"].get("productName", ""),
            "category":     h["_source"].get("category_name", ""),
            "sub_category": h["_source"].get("subCategory_name", ""),
            "score":        round(h["_score"], 4),
            "confidence":   round(min(h["_score"] / max_score * 100, 100), 1),
        }
        for h in prod_resp["hits"]["hits"]
    ]

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "query":             q,
        "categories":        top_bucket("cat_filter"),
        "sub_categories":    top_bucket("subcat_filter"),
        "product_categories":top_bucket("prodcat_filter"),
        "products":          products,
        "total":             prod_resp["hits"]["total"]["value"],
        "latency_ms":        latency_ms,
    }


@app.get("/search")
def search(
    q:        str           = Query(default="", min_length=1),
    page:     int           = Query(default=1, ge=1),
    category: Optional[str] = Query(default=None),
):
    """
    Full-text search with pagination.

    Response shape:
    {
      "query": "bat",
      "total": 1945,
      "page": 1,
      "page_size": 20,
      "pages": 98,
      "hits": [
        {
          "id": "...",
          "productName": "...",
          "productDescription": "...",
          "category_name": "...",
          "subCategory_name": "...",
          "productCategory_name": "...",
          "score": 12.34
        },
        ...
      ]
    }
    """
    q = q.strip()
    if not q:
        return {"query": q, "total": 0, "page": page, "page_size": PAGE_SIZE, "pages": 0, "hits": [], "latency_ms": 0}

    t0 = time.perf_counter()

    from_offset = (page - 1) * PAGE_SIZE
    words       = q.split()
    word_count  = len(words)

    # ── Data-driven query strategy ────────────────────────────────────────────
    #
    # Key facts from dataset analysis:
    #   • ALL 100k product names are 5-11 words long (zero short names)
    #   • Users type 1-3 keyword queries into these long descriptive sentences
    #   • product_english stemmer correctly separates:
    #       bat→bat, bats→bat, batting→bat   (same stem ✓)
    #       battery→batteri, bathroom→bathroom  (different stems ✓)
    #   • ngram field adds +1,333 noise for 3-char query "bat" (bath, battery...)
    #     but only +25 noise for "pump" — so ngram must NEVER be in must clause
    #     for single-word queries; it lives only in should for ranking bonus
    #   • Multi-word queries (2+ words) produce zero ngram noise — exact = ngram
    #
    # Strategy by word count:
    #
    #   1 WORD  → must: exact token (product_english analyzer, AND)
    #             should: phrase_prefix (autocomplete-style ranking), fuzzy
    #             NO ngram in must — it causes bath/battery pollution for "bat"
    #
    #   2 WORDS → must: both words present (AND)
    #             should: phrase match (word-order bonus), fuzzy one word
    #
    #   3+ WORDS → must: ≥75% words present (handles stop words stripped by analyzer)
    #              should: phrase(slop=1), AND, fuzzy

    if word_count == 1:
        # ── Single keyword: "bat", "pump", "led", "saree" ────────────────────
        # Must: exact token only.  No ngram here — it pollutes short queries.
        # Should: phrase_prefix for "starts-with" ranking bonus (pump→pumps ranks higher)
        #         fuzzy for typos (ciket→cricket) — only fires for longer words
        must_clause = [
            {"match": {
                "productName": {"query": q, "operator": "and"}
            }}
        ]
        should_clause = [
            # Reward docs where the word appears multiple times (IDF boost)
            {"match": {"productName": {"query": q, "operator": "and", "boost": 5.0}}},
            # Fuzzy — only useful for words ≥5 chars (AUTO: 0-2→0, 3-5→1, 6+→2 edits)
            {"match": {"productName": {
                "query": q, "fuzziness": "AUTO",
                "prefix_length": 2, "boost": 0.3
            }}},
        ]

    elif word_count == 2:
        # ── Two keywords: "cricket bat", "steel pipe", "led bulb" ─────────────
        # Must: both words must appear somewhere in productName
        must_clause = [
            {"match": {"productName": {"query": q, "operator": "and"}}}
        ]
        should_clause = [
            # Exact phrase (correct order) → big bonus
            {"match_phrase": {"productName": {"query": q, "boost": 8.0}}},
            # Phrase with slop=1 (reversed order, one word gap)
            {"match_phrase": {"productName": {"query": q, "slop": 1, "boost": 5.0}}},
            # Ngram AND is noise-free for 2-word queries (data confirmed)
            {"match": {"productName.ngram": {"query": q, "operator": "and", "boost": 2.0}}},
            # Fuzzy on full phrase — helps with typos
            {"match": {"productName": {
                "query": q, "fuzziness": "AUTO",
                "prefix_length": 2, "boost": 0.3
            }}},
        ]

    else:
        # ── 3+ keywords: "hydraulic pump valve", "stainless steel pipe fittings"
        # Must: ≥75% of words present to handle stop words being stripped
        must_clause = [
            {"match": {
                "productName": {
                    "query": q, "operator": "or",
                    "minimum_should_match": "75%"
                }
            }}
        ]
        should_clause = [
            # ALL words present → strongest signal
            {"match":        {"productName": {"query": q, "operator": "and", "boost": 10.0}}},
            # Phrase match with small slop (word reordering ok)
            {"match_phrase": {"productName": {"query": q, "slop": 2, "boost": 8.0}}},
            # Ngram AND (zero noise for multi-word — data confirmed)
            {"match": {"productName.ngram": {"query": q, "operator": "and", "boost": 2.0}}},
            {"match": {"productName": {
                "query": q, "fuzziness": "AUTO",
                "prefix_length": 2, "boost": 0.3
            }}},
        ]

    # ── Category name match (boosts products whose category matches query) ────
    # e.g. "sanitaryware" → surface products in Sanitaryware category even if
    # the word doesn't appear in productName
    category_boost = [
        {"match": {"category_name.text":        {"query": q, "boost": 1.5}}},
        {"match": {"subCategory_name.text":     {"query": q, "boost": 1.5}}},
        {"match": {"productCategory_name.text": {"query": q, "boost": 1.5}}},
    ]

    # ── Description fallback (soft signal only) ───────────────────────────────
    desc_boost = [
        {"match": {"productDescription": {"query": q, "operator": "or", "boost": 0.2}}}
    ]


    resp = es.search(
        index=INDEX_NAME,
        size=PAGE_SIZE,
        from_=from_offset,
        query={
            "bool": {
                "must":   must_clause,
                "should": should_clause + category_boost + desc_boost,
                **({"filter": [
                    {"bool": {"should": [
                        {"term": {"category_name":        category}},
                        {"term": {"subCategory_name":     category}},
                        {"term": {"productCategory_name": category}},
                    ], "minimum_should_match": 1}}
                ]} if category else {}),
            }
        },
    )

    total     = resp["hits"]["total"]["value"]
    max_score = resp["hits"].get("max_score") or 1

    hits = [
        {
            "id":                   h["_id"],
            "productName":          h["_source"].get("productName", ""),
            "productDescription":   h["_source"].get("productDescription", "")[:200],
            "category_name":        h["_source"].get("category_name", ""),
            "subCategory_name":     h["_source"].get("subCategory_name", ""),
            "productCategory_name": h["_source"].get("productCategory_name", ""),
            "score":                round(h["_score"], 4),
            "confidence":           round(min(h["_score"] / max_score * 100, 100), 1),
        }
        for h in resp["hits"]["hits"]
    ]

    import math
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    return {
        "query":      q,
        "total":      total,
        "page":       page,
        "page_size":  PAGE_SIZE,
        "pages":      math.ceil(total / PAGE_SIZE),
        "hits":       hits,
        "latency_ms": latency_ms,
    }
