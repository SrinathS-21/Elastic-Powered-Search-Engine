from __future__ import annotations

import math
import time
from typing import Any

from fastapi import HTTPException

try:
    from ..core.clients import es
    from ..core.config import INDEX_NAME, PAGE_SIZE, SHORT_VECTOR_RERANK_BOOST
    from ..core.logger import log
    from .mapping import product_short_vector_boost_map
    from .suppliers import get_suppliers
    from .synonyms import expand_synonyms
    from ..ml.embeddings import encode_query_text
except ImportError:
    from core.clients import es
    from core.config import INDEX_NAME, PAGE_SIZE, SHORT_VECTOR_RERANK_BOOST
    from core.logger import log
    from services.mapping import product_short_vector_boost_map
    from services.suppliers import get_suppliers
    from services.synonyms import expand_synonyms
    try:
        from ml.embeddings import encode_query_text
    except ImportError:
        from src.ml.embeddings import encode_query_text


_COMPLETION_FIELD_AVAILABLE: bool | None = None


def _completion_field_available() -> bool:
    global _COMPLETION_FIELD_AVAILABLE
    if _COMPLETION_FIELD_AVAILABLE is not None:
        return _COMPLETION_FIELD_AVAILABLE

    try:
        caps = es.field_caps(index=INDEX_NAME, fields=["productName_completion"], ignore_unavailable=True)
        _COMPLETION_FIELD_AVAILABLE = "productName_completion" in caps.get("fields", {})
    except Exception:
        _COMPLETION_FIELD_AVAILABLE = False
    return _COMPLETION_FIELD_AVAILABLE


def build_keyword_query(
    query: str,
    category: str | None = None,
    sub_category: str | None = None,
    prod_category: str | None = None,
) -> tuple[dict, str | None, list[dict]]:
    words = query.split()
    word_count = len(words)

    if word_count == 1:
        must_clause = [{"match": {"productName": {"query": query, "operator": "and"}}}]
        should_clause = [
            {"match": {"productName": {"query": query, "operator": "and", "boost": 5.0}}},
            {"match": {"productName.stem": {"query": query, "operator": "and", "boost": 3.6}}},
            {"match": {"search_text": {"query": query, "operator": "and", "boost": 2.4}}},
            {"match": {"search_text.stem": {"query": query, "operator": "and", "boost": 2.0}}},
            {"match": {"suggest_text": {"query": query, "operator": "or", "boost": 2.6}}},
            {"match": {"suggest_text.stem": {"query": query, "operator": "or", "boost": 1.9}}},
            {
                "match": {
                    "productName": {
                        "query": query,
                        "fuzziness": "AUTO",
                        "prefix_length": 2,
                        "boost": 0.3,
                    }
                }
            },
        ]
    elif word_count == 2:
        must_clause = [{"match": {"productName": {"query": query, "operator": "and"}}}]
        should_clause = [
            {"match_phrase": {"productName": {"query": query, "boost": 8.0}}},
            {"match_phrase": {"productName": {"query": query, "slop": 1, "boost": 5.0}}},
            {"match": {"productName.stem": {"query": query, "operator": "and", "boost": 4.2}}},
            {"match": {"search_text": {"query": query, "operator": "and", "boost": 2.6}}},
            {"match": {"search_text.stem": {"query": query, "operator": "and", "boost": 2.3}}},
            {"match": {"suggest_text": {"query": query, "operator": "and", "boost": 2.0}}},
            {"match": {"suggest_text.stem": {"query": query, "operator": "and", "boost": 1.7}}},
            {"match": {"productName.ngram": {"query": query, "operator": "and", "boost": 2.0}}},
            {
                "match": {
                    "productName": {
                        "query": query,
                        "fuzziness": "AUTO",
                        "prefix_length": 2,
                        "boost": 0.3,
                    }
                }
            },
        ]
    else:
        must_clause = [
            {
                "match": {
                    "productName": {
                        "query": query,
                        "operator": "or",
                        "minimum_should_match": "75%",
                    }
                }
            }
        ]
        should_clause = [
            {"match": {"productName": {"query": query, "operator": "and", "boost": 10.0}}},
            {"match_phrase": {"productName": {"query": query, "slop": 2, "boost": 8.0}}},
            {"match": {"productName.stem": {"query": query, "operator": "and", "boost": 5.0}}},
            {"match": {"search_text": {"query": query, "operator": "and", "boost": 3.0}}},
            {"match": {"search_text.stem": {"query": query, "operator": "and", "boost": 2.6}}},
            {"match": {"suggest_text": {"query": query, "operator": "or", "boost": 1.8}}},
            {"match": {"suggest_text.stem": {"query": query, "operator": "or", "boost": 1.5}}},
            {"match": {"productName.ngram": {"query": query, "operator": "and", "boost": 2.0}}},
            {
                "match": {
                    "productName": {
                        "query": query,
                        "fuzziness": "AUTO",
                        "prefix_length": 2,
                        "boost": 0.3,
                    }
                }
            },
        ]

    category_boost = [
        {"match": {"category_name.text": {"query": query, "boost": 1.5}}},
        {"match": {"subCategory_name.text": {"query": query, "boost": 1.5}}},
        {"match": {"productCategory_name.text": {"query": query, "boost": 1.5}}},
    ]
    desc_boost = [
        {"match": {"productDescription": {"query": query, "operator": "or", "boost": 0.2}}},
        {"match": {"productDescription.stem": {"query": query, "operator": "or", "boost": 0.25}}},
        {"match": {"search_text": {"query": query, "operator": "or", "boost": 0.35}}},
        {"match": {"search_text.stem": {"query": query, "operator": "or", "boost": 0.4}}},
        {"match": {"suggest_text": {"query": query, "operator": "or", "boost": 0.45}}},
        {"match": {"suggest_text.stem": {"query": query, "operator": "or", "boost": 0.35}}},
    ]

    expanded = expand_synonyms(query)
    if expanded:
        exp_op = "and" if word_count <= 2 else "or"
        exp_extra = {"minimum_should_match": "75%"} if word_count > 2 else {}
        exp_match = {
            "bool": {
                "should": [
                    {"match": {"productName": {"query": expanded, "operator": exp_op, **exp_extra}}},
                    {
                        "match": {
                            "productName.stem": {
                                "query": expanded,
                                "operator": exp_op,
                                "boost": 0.9,
                                **exp_extra,
                            }
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }
        final_must = [{"bool": {"should": must_clause + [exp_match], "minimum_should_match": 1}}]
    else:
        final_must = must_clause

    cat_filters: list[dict] = []
    if category:
        cat_filters.append({"term": {"category_name": category}})
    if sub_category:
        cat_filters.append({"term": {"subCategory_name": sub_category}})
    if prod_category:
        cat_filters.append({"term": {"productCategory_name": prod_category}})

    keyword_query = {
        "bool": {
            "must": final_must,
            "should": should_clause + category_boost + desc_boost,
            **({"filter": cat_filters} if cat_filters else {}),
        }
    }
    return keyword_query, expanded, cat_filters


def autocomplete_search(query: str) -> dict:
    query = query.strip()
    if len(query) < 2:
        return {
            "query": query,
            "categories": [],
            "sub_categories": [],
            "product_categories": [],
            "products": [],
            "total": 0,
            "latency_ms": 0,
        }

    start = time.perf_counter()

    msearch_body = [
        {"index": INDEX_NAME},
        {
            "size": 0,
            "aggs": {
                "cat_filter": {
                    "filter": {"match_phrase_prefix": {"category_name.text": query}},
                    "aggs": {"top": {"terms": {"field": "category_name", "size": 1}}},
                },
                "subcat_filter": {
                    "filter": {"match_phrase_prefix": {"subCategory_name.text": query}},
                    "aggs": {"top": {"terms": {"field": "subCategory_name", "size": 1}}},
                },
                "prodcat_filter": {
                    "filter": {"match_phrase_prefix": {"productCategory_name.text": query}},
                    "aggs": {"top": {"terms": {"field": "productCategory_name", "size": 1}}},
                },
            },
        },
        {"index": INDEX_NAME},
        {
            "size": 5,
            "query": {
                "bool": {
                    "should": [
                        {"match_phrase_prefix": {"productName": {"query": query, "boost": 5.0}}},
                        {
                            "multi_match": {
                                "query": query,
                                "type": "bool_prefix",
                                "fields": [
                                    "productName_autocomplete",
                                    "productName_autocomplete._2gram",
                                    "productName_autocomplete._3gram",
                                ],
                                "boost": 4.4,
                            }
                        },
                        {"match": {"productName.stem": {"query": query, "operator": "and", "boost": 3.4}}},
                        {"match": {"search_text": {"query": query, "operator": "and", "boost": 2.0}}},
                        {"match": {"suggest_text": {"query": query, "operator": "and", "boost": 2.8}}},
                        {"match": {"suggest_text.stem": {"query": query, "operator": "and", "boost": 2.0}}},
                        {"match": {"productName.ngram": {"query": query, "operator": "and", "boost": 3.0}}},
                        {"match": {"productName.ngram": {"query": query, "operator": "or", "boost": 1.0}}},
                        {
                            "match": {
                                "productName": {
                                    "query": query,
                                    "fuzziness": "AUTO",
                                    "prefix_length": 1,
                                    "boost": 0.5,
                                }
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
            "_source": ["productName", "category_name", "subCategory_name", "userId"],
            "highlight": {
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
                "fields": {
                    "productName": {"number_of_fragments": 0},
                    "suggest_text": {"number_of_fragments": 0},
                },
            },
        },
    ]

    try:
        responses = es.msearch(body=msearch_body)["responses"]
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Search backend unavailable: {exc}") from exc

    agg_response, product_response = responses[0], responses[1]

    completion_suggestions: list[str] = []
    if _completion_field_available():
        try:
            completion_response = es.search(
                index=INDEX_NAME,
                size=0,
                suggest={
                    "product_completion": {
                        "prefix": query,
                        "completion": {
                            "field": "productName_completion",
                            "skip_duplicates": True,
                            "size": 8,
                        },
                    }
                },
            )
            options = completion_response.get("suggest", {}).get("product_completion", [])
            if options:
                completion_suggestions = [
                    option.get("text", "")
                    for option in options[0].get("options", [])
                    if option.get("text")
                ]
        except Exception:
            completion_suggestions = []

    def top_bucket(agg_key: str) -> list[str]:
        buckets = agg_response["aggregations"][agg_key]["top"]["buckets"]
        return [bucket["key"] for bucket in buckets]

    max_score = product_response["hits"].get("max_score") or 1
    products = [
        {
            "name": hit["_source"].get("productName", ""),
            "highlight": (hit.get("highlight", {}).get("productName") or [""])[0],
            "category": hit["_source"].get("category_name", ""),
            "sub_category": hit["_source"].get("subCategory_name", ""),
            "score": round(hit["_score"], 4),
            "confidence": round(min(hit["_score"] / max_score * 100, 100), 1),
        }
        for hit in product_response["hits"]["hits"]
    ]

    supplier_query, _, _ = build_keyword_query(query)
    try:
        supplier_match_response = es.search(
            index=INDEX_NAME,
            size=12,
            query=supplier_query,
            _source=["userId"],
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Search backend unavailable: {exc}") from exc

    supplier_user_ids = list(
        {
            hit.get("_source", {}).get("userId", "")
            for hit in supplier_match_response.get("hits", {}).get("hits", [])
            if hit.get("_source", {}).get("userId", "")
        }
    )
    enriched_suppliers = get_suppliers(supplier_user_ids)[:6]
    suppliers = [
        {
            "businessName": supplier.get("businessName", ""),
            "logoUrl": supplier.get("logoUrl") or "",
            "country": supplier.get("country") or "",
            "categories": supplier.get("categories") or [],
            "packageType": supplier.get("packageType") or "free",
            "profileUrl": supplier.get("profileUrl") or "",
        }
        for supplier in enriched_suppliers
        if supplier.get("businessName")
    ]

    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    return {
        "query": query,
        "categories": top_bucket("cat_filter"),
        "sub_categories": top_bucket("subcat_filter"),
        "product_categories": top_bucket("prodcat_filter"),
        "products": products,
        "completion_suggestions": completion_suggestions,
        "suppliers": suppliers,
        "total": product_response["hits"]["total"]["value"],
        "latency_ms": latency_ms,
    }


def search_products(
    query: str,
    page: int = 1,
    category: str | None = None,
    sub_category: str | None = None,
    prod_category: str | None = None,
    mode: str = "hybrid",
) -> dict:
    query = str(query or "").strip()
    page = int(page or 1)
    mode = str(mode or "hybrid").lower()
    if mode not in {"keyword", "semantic", "hybrid"}:
        mode = "hybrid"

    if not query:
        return {
            "query": query,
            "total": 0,
            "page": page,
            "page_size": PAGE_SIZE,
            "pages": 0,
            "hits": [],
            "latency_ms": 0,
            "facets": {},
        }

    start = time.perf_counter()

    from_offset = (page - 1) * PAGE_SIZE
    keyword_query, expanded, cat_filters = build_keyword_query(query, category, sub_category, prod_category)

    highlight_cfg = {
        "pre_tags": ["<mark>"],
        "post_tags": ["</mark>"],
        "fields": {
            "productName": {"number_of_fragments": 0},
            "productDescription": {"number_of_fragments": 1, "fragment_size": 150},
        },
    }

    suggest_cfg = {
        "text": query,
        "spell_check": {
            "term": {
                "field": "productName",
                "suggest_mode": "popular",
                "sort": "frequency",
                "min_word_length": 3,
            }
        },
    }

    facet_aggs = {
        "facet_category": {"terms": {"field": "category_name", "size": 20}},
        "facet_sub_category": {"terms": {"field": "subCategory_name", "size": 30}},
        "facet_prod_category": {"terms": {"field": "productCategory_name", "size": 30}},
    }

    knn_cfg = None
    if mode in ("semantic", "hybrid"):
        try:
            query_vector = list(encode_query_text(query))
            knn_k = max(PAGE_SIZE, from_offset + PAGE_SIZE)
            knn_cfg = {
                "field": "product_vector_main",
                "query_vector": query_vector,
                "k": knn_k,
                "num_candidates": max(180, knn_k * 3),
                **({"filter": {"bool": {"must": cat_filters}}} if cat_filters else {}),
            }
        except Exception as exc:
            if mode == "semantic":
                raise HTTPException(
                    status_code=503,
                    detail=f"Semantic search unavailable (embedding model not loaded): {exc}",
                ) from exc
            log.warning("Semantic component unavailable for hybrid mode, falling back to keyword: %s", exc)
            mode = "keyword"

    search_kwargs: dict[str, Any] = {
        "index": INDEX_NAME,
        "size": PAGE_SIZE,
        "from_": from_offset,
        "highlight": highlight_cfg,
        "suggest": suggest_cfg,
        "aggs": facet_aggs,
    }

    if mode == "keyword":
        search_kwargs["query"] = keyword_query
    elif mode == "semantic":
        search_kwargs["knn"] = knn_cfg
    else:
        search_kwargs["query"] = keyword_query
        search_kwargs["knn"] = knn_cfg

    try:
        response = es.search(**search_kwargs)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Search backend unavailable: {exc}") from exc

    total = response["hits"]["total"]["value"]

    short_boost_map: dict[str, float] = {}
    semantic_short_used = False
    if mode in ("semantic", "hybrid"):
        short_boost_map = product_short_vector_boost_map(query, size=max(PAGE_SIZE * 6, 120))
        semantic_short_used = bool(short_boost_map)

    hits: list[dict[str, Any]] = []
    for hit in response["hits"]["hits"]:
        raw_score = float(hit.get("_score") or 0.0)
        short_boost = short_boost_map.get(str(hit.get("_id")), 0.0)
        final_score = raw_score + (SHORT_VECTOR_RERANK_BOOST * short_boost)
        hits.append(
            {
                "id": hit["_id"],
                "productName": hit["_source"].get("productName", ""),
                "productDescription": hit["_source"].get("productDescription", "")[:200],
                "highlightName": (hit.get("highlight", {}).get("productName") or [""])[0],
                "highlightDesc": (hit.get("highlight", {}).get("productDescription") or [""])[0],
                "category_name": hit["_source"].get("category_name") or "",
                "subCategory_name": hit["_source"].get("subCategory_name") or "",
                "productCategory_name": hit["_source"].get("productCategory_name") or "",
                "score": round(final_score, 4),
                "score_raw": round(raw_score, 4),
                "short_vector_boost": round(short_boost, 4),
            }
        )

    hits.sort(key=lambda item: item["score"], reverse=True)
    max_score = max((float(item["score"]) for item in hits), default=1.0) or 1.0
    for item in hits:
        item["confidence"] = round(min((float(item["score"]) / max_score) * 100, 100), 1)

    seen_users: list[str] = list(
        {
            hit["_source"].get("userId", "")
            for hit in response["hits"]["hits"]
            if hit["_source"].get("userId", "")
        }
    )
    unique_suppliers = get_suppliers(seen_users)

    suggestion = None
    if total == 0 and "suggest" in response:
        corrected_parts: list[str] = []
        changed = False
        for entry in response["suggest"].get("spell_check", []):
            if entry["options"]:
                corrected_parts.append(entry["options"][0]["text"])
                changed = True
            else:
                corrected_parts.append(entry["text"])
        if changed:
            suggestion = " ".join(corrected_parts)

    facets: dict[str, list[dict[str, Any]]] = {}
    for facet_key, response_key in [
        ("category", "facet_category"),
        ("sub_category", "facet_sub_category"),
        ("prod_category", "facet_prod_category"),
    ]:
        agg = response.get("aggregations", {}).get(response_key, {})
        facets[facet_key] = [{"name": b["key"], "count": b["doc_count"]} for b in agg.get("buckets", [])]

    latency_ms = round((time.perf_counter() - start) * 1000, 1)

    return {
        "query": query,
        "total": total,
        "page": page,
        "page_size": PAGE_SIZE,
        "pages": math.ceil(total / PAGE_SIZE),
        "hits": hits,
        "facets": facets,
        "latency_ms": latency_ms,
        "suggestion": suggestion,
        "synonym_expanded": expanded,
        "search_mode": mode,
        "semantic_short_used": semantic_short_used,
        "suppliers": unique_suppliers,
    }
