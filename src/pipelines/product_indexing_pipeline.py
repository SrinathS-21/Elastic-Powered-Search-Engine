from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bson import ObjectId
from elasticsearch import Elasticsearch, helpers
from pymongo import MongoClient

try:
    from ..ml.embeddings import EMBED_DIM, EMBED_MODEL_NAME, encode_document_batch
except ImportError:
    try:
        from ml.embeddings import EMBED_DIM, EMBED_MODEL_NAME, encode_document_batch
    except ImportError:
        from src.ml.embeddings import EMBED_DIM, EMBED_MODEL_NAME, encode_document_batch

try:
    from ..core.synonym_data import load_protected_tokens, load_synonym_rules
except ImportError:
    try:
        from core.synonym_data import load_protected_tokens, load_synonym_rules
    except ImportError:
        from src.core.synonym_data import load_protected_tokens, load_synonym_rules


ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_PRODUCT_INDEX = os.getenv("ES_PRODUCT_INDEX", os.getenv("ES_INDEX", "pepagora_products"))
ES_PRODUCT_READ_ALIAS = os.getenv("ES_PRODUCT_READ_ALIAS", f"{ES_PRODUCT_INDEX}_current")
ES_PRODUCT_WRITE_ALIAS = os.getenv("ES_PRODUCT_WRITE_ALIAS", f"{ES_PRODUCT_INDEX}_write")
ES_PRODUCT_INDEX_PATTERN = os.getenv("ES_PRODUCT_INDEX_PATTERN", f"{ES_PRODUCT_INDEX}-*")
ES_PRODUCT_TEMPLATE = os.getenv("ES_PRODUCT_TEMPLATE", "pepagora_products_template_v1")
ES_PRODUCT_COMPONENT_TEMPLATE = os.getenv("ES_PRODUCT_COMPONENT_TEMPLATE", "pepagora_products_component_v1")
ES_PRODUCT_ILM_POLICY = os.getenv("ES_PRODUCT_ILM_POLICY", "pepagora_products_ilm_v1")
ES_PRODUCT_INGEST_PIPELINE = os.getenv("ES_PRODUCT_INGEST_PIPELINE", "pepagora_products_ingest_v1")
ES_PRODUCT_SHARDS = int(os.getenv("ES_PRODUCT_SHARDS", "1"))
ES_PRODUCT_REPLICAS = int(os.getenv("ES_PRODUCT_REPLICAS", "0"))
ES_PRODUCT_REFRESH_INTERVAL = os.getenv("ES_PRODUCT_REFRESH_INTERVAL", "1s")
ES_PRODUCT_BULK_REFRESH_INTERVAL = os.getenv("ES_PRODUCT_BULK_REFRESH_INTERVAL", "-1")
ES_PRODUCT_ROLLOVER_MAX_AGE = os.getenv("ES_PRODUCT_ROLLOVER_MAX_AGE", "30d")
ES_PRODUCT_ROLLOVER_MAX_PRIMARY_SHARD_SIZE = os.getenv("ES_PRODUCT_ROLLOVER_MAX_PRIMARY_SHARD_SIZE", "30gb")
ES_PRODUCT_ROLLOVER_MAX_DOCS = int(os.getenv("ES_PRODUCT_ROLLOVER_MAX_DOCS", "5000000"))
ES_BULK_THREADS = int(os.getenv("ES_BULK_THREADS", "6"))
ES_BULK_QUEUE_SIZE = int(os.getenv("ES_BULK_QUEUE_SIZE", "16"))

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://localhost:27017/admin",
)
MONGO_DB = os.getenv("MONGO_DB", "internSandboxDb")
MONGO_PRODUCTS = os.getenv("MONGO_PRODUCTS_COLLECTION", "liveproducts_v1")
MONGO_CATEGORIES = os.getenv("MONGO_CATEGORIES_COLLECTION", "categories")
MONGO_SUBCATEGORIES = os.getenv("MONGO_SUBCATEGORIES_COLLECTION", "subcategories")
MONGO_PRODUCTCATEGORIES = os.getenv("MONGO_PRODUCTCATEGORIES_COLLECTION", "productcategories")

EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", f"{EMBED_MODEL_NAME}-v23")

TARGET_WORDS_PER_CHUNK = int(os.getenv("PRODUCT_CHUNK_TARGET_WORDS", "280"))
MAX_WORDS_PER_CHUNK = int(os.getenv("PRODUCT_CHUNK_MAX_WORDS", "340"))
OVERLAP_SENTENCES = int(os.getenv("PRODUCT_CHUNK_OVERLAP_SENTENCES", "1"))

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;:])\s+")
MULTISPACE_RE = re.compile(r"\s+")

B2B_SYNONYM_RULES = load_synonym_rules()
B2B_PROTECTED_TOKENS = sorted(load_protected_tokens())


@dataclass
class LookupMaps:
    categories: dict[str, str]
    subcategories: dict[str, str]
    productcategories: dict[str, str]


def es_client() -> Elasticsearch:
    return Elasticsearch(ES_HOST, request_timeout=60, retry_on_timeout=True)


def mongo_client() -> MongoClient:
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=15000)


def _norm_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return str(ObjectId(value))
        except Exception:
            return value
    if isinstance(value, dict):
        return _norm_id(value.get("_id"))
    return None


def _to_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return None


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _extract_user_id(doc: dict[str, Any]) -> str:
    created_by = doc.get("createdBy")
    account_id = doc.get("accountId")

    for candidate in (created_by, account_id):
        if isinstance(candidate, dict):
            oid = _as_text(candidate.get("$oid"))
            if oid:
                return oid
            norm = _norm_id(candidate.get("_id"))
            if norm:
                return norm
        else:
            text = _as_text(candidate)
            if text:
                return text
    return ""


def _build_lookup_map(collection) -> dict[str, str]:
    result: dict[str, str] = {}
    for doc in collection.find({}, {"_id": 1, "name": 1}):
        doc_id = _norm_id(doc.get("_id"))
        if not doc_id:
            continue
        result[doc_id] = _as_text(doc.get("name"))
    return result


def load_lookup_maps(db) -> LookupMaps:
    return LookupMaps(
        categories=_build_lookup_map(db[MONGO_CATEGORIES]),
        subcategories=_build_lookup_map(db[MONGO_SUBCATEGORIES]),
        productcategories=_build_lookup_map(db[MONGO_PRODUCTCATEGORIES]),
    )


def _word_count(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


def _normalize_space(text: str) -> str:
    return MULTISPACE_RE.sub(" ", text or "").strip()


def _split_sentences(text: str) -> list[str]:
    normalized = _normalize_space(text)
    if not normalized:
        return []
    parts = [p.strip() for p in SENTENCE_SPLIT_RE.split(normalized) if p.strip()]
    return parts or [normalized]


def _split_long_sentence(sentence: str, max_words: int) -> list[str]:
    sentence = _normalize_space(sentence)
    if not sentence:
        return []

    if _word_count(sentence) <= max_words:
        return [sentence]

    clauses = [c.strip() for c in CLAUSE_SPLIT_RE.split(sentence) if c.strip()]
    if len(clauses) <= 1:
        words = sentence.split()
        chunks: list[str] = []
        for i in range(0, len(words), max_words):
            chunks.append(" ".join(words[i : i + max_words]))
        return chunks

    out: list[str] = []
    current: list[str] = []
    current_words = 0
    for clause in clauses:
        c_words = _word_count(clause)
        if c_words > max_words:
            if current:
                out.append(" ".join(current))
                current = []
                current_words = 0
            words = clause.split()
            for i in range(0, len(words), max_words):
                out.append(" ".join(words[i : i + max_words]))
            continue

        if current and current_words + c_words > max_words:
            out.append(" ".join(current))
            current = [clause]
            current_words = c_words
        else:
            current.append(clause)
            current_words += c_words

    if current:
        out.append(" ".join(current))
    return out


def _chunk_by_sentences(
    text: str,
    target_words: int = TARGET_WORDS_PER_CHUNK,
    max_words: int = MAX_WORDS_PER_CHUNK,
    overlap_sentences: int = OVERLAP_SENTENCES,
) -> list[str]:
    sentences = _split_sentences(text)
    expanded: list[str] = []
    for sentence in sentences:
        expanded.extend(_split_long_sentence(sentence, max_words))

    if not expanded:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for sentence in expanded:
        s_words = _word_count(sentence)
        if current and current_words + s_words > max_words:
            chunks.append(" ".join(current))

            overlap = current[-overlap_sentences:] if overlap_sentences > 0 else []
            overlap_words = sum(_word_count(x) for x in overlap)
            while overlap and overlap_words > target_words:
                overlap.pop(0)
                overlap_words = sum(_word_count(x) for x in overlap)

            current = overlap[:]
            current_words = overlap_words

        current.append(sentence)
        current_words += s_words

    if current:
        chunks.append(" ".join(current))

    return [_normalize_space(c) for c in chunks if _normalize_space(c)]


def _mean_pool_and_normalize(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return [0.0] * EMBED_DIM

    dims = len(vectors[0])
    accum = [0.0] * dims
    for vec in vectors:
        for i, value in enumerate(vec):
            accum[i] += float(value)

    inv = 1.0 / float(len(vectors))
    mean_vec = [value * inv for value in accum]
    norm = math.sqrt(sum(v * v for v in mean_vec))
    if norm <= 1e-12:
        return mean_vec
    return [v / norm for v in mean_vec]


def _build_product_texts(
    product_name: str,
    product_description: str,
    detailed_description: str,
    category_name: str,
    subcategory_name: str,
    productcategory_name: str,
) -> tuple[str, str, str, str]:
    search_text = _normalize_space(
        " ".join(
            part
            for part in [
                product_name,
                product_description,
                detailed_description,
                category_name,
                subcategory_name,
                productcategory_name,
            ]
            if part
        )
    )

    suggest_text = _normalize_space(" ".join(part for part in [product_name, productcategory_name] if part))

    main_embedding_text = _normalize_space(
        " ".join(
            part
            for part in [
                product_name,
                product_description,
                detailed_description,
            ]
            if part
        )
    )

    short_embedding_text = _normalize_space(" ".join(part for part in [product_name, product_description] if part))
    return search_text, suggest_text, main_embedding_text, short_embedding_text


def _encode_product_vectors(main_text: str, short_text: str) -> tuple[list[float], list[float], dict[str, Any]]:
    chunks = _chunk_by_sentences(main_text)
    if not chunks:
        chunks = [short_text or main_text or "unknown"]

    chunk_vectors = encode_document_batch(chunks)
    main_vector = _mean_pool_and_normalize(chunk_vectors)
    short_vector = encode_document_batch([short_text or main_text or "unknown"])[0]

    chunk_word_counts = [_word_count(chunk) for chunk in chunks]
    vector_meta = {
        "chunk_strategy": "sentence",
        "chunk_count": len(chunks),
        "avg_chunk_words": int(round(sum(chunk_word_counts) / len(chunk_word_counts))) if chunk_word_counts else 0,
        "max_chunk_words": max(chunk_word_counts) if chunk_word_counts else 0,
    }
    return main_vector, short_vector, vector_meta


def _product_analysis_settings() -> dict[str, Any]:
    token_filters: dict[str, dict[str, Any]] = {
        "english_possessive_stemmer": {"type": "stemmer", "name": "possessive_english"},
        "english_stemmer": {"type": "stemmer", "name": "english"},
    }

    if B2B_PROTECTED_TOKENS:
        token_filters["b2b_keyword_protect"] = {
            "type": "keyword_marker",
            "keywords": B2B_PROTECTED_TOKENS,
        }
    if B2B_SYNONYM_RULES:
        token_filters["b2b_synonym_graph"] = {
            "type": "synonym_graph",
            "synonyms": B2B_SYNONYM_RULES,
        }

    stemmed_filters = ["lowercase", "asciifolding"]
    if B2B_PROTECTED_TOKENS:
        stemmed_filters.append("b2b_keyword_protect")
    stemmed_filters.extend(["english_possessive_stemmer", "english_stemmer"])

    stemmed_search_filters = ["lowercase", "asciifolding"]
    if B2B_PROTECTED_TOKENS:
        stemmed_search_filters.append("b2b_keyword_protect")
    if B2B_SYNONYM_RULES:
        stemmed_search_filters.append("b2b_synonym_graph")
    stemmed_search_filters.extend(["english_possessive_stemmer", "english_stemmer"])

    return {
        "tokenizer": {
            "edge_autocomplete_tokenizer": {
                "type": "edge_ngram",
                "min_gram": 2,
                "max_gram": 20,
                "token_chars": ["letter", "digit"],
            }
        },
        "filter": token_filters,
        "analyzer": {
            "edge_autocomplete": {
                "tokenizer": "edge_autocomplete_tokenizer",
                "filter": ["lowercase"],
            },
            "b2b_stemmed": {
                "tokenizer": "standard",
                "filter": stemmed_filters,
            },
            "b2b_stemmed_search": {
                "tokenizer": "standard",
                "filter": stemmed_search_filters,
            },
        },
    }


def product_index_body() -> dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": ES_PRODUCT_SHARDS,
            "number_of_replicas": ES_PRODUCT_REPLICAS,
            "refresh_interval": ES_PRODUCT_REFRESH_INTERVAL,
            "default_pipeline": ES_PRODUCT_INGEST_PIPELINE,
            "analysis": _product_analysis_settings(),
        },
        "mappings": {
            "dynamic": True,
            "properties": {
                "productName": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword", "ignore_above": 512},
                        "ngram": {
                            "type": "text",
                            "analyzer": "edge_autocomplete",
                            "search_analyzer": "standard",
                        },
                        "stem": {
                            "type": "text",
                            "analyzer": "b2b_stemmed",
                            "search_analyzer": "b2b_stemmed_search",
                        },
                    },
                },
                "productName_autocomplete": {"type": "search_as_you_type", "max_shingle_size": 3},
                "productName_completion": {
                    "type": "completion",
                    "analyzer": "simple",
                    "preserve_position_increments": True,
                    "preserve_separators": True,
                },
                "productDescription": {
                    "type": "text",
                    "fields": {
                        "stem": {
                            "type": "text",
                            "analyzer": "b2b_stemmed",
                            "search_analyzer": "b2b_stemmed_search",
                        }
                    },
                },
                "detailedDescription": {"type": "text"},
                "search_text": {
                    "type": "text",
                    "fields": {
                        "stem": {
                            "type": "text",
                            "analyzer": "b2b_stemmed",
                            "search_analyzer": "b2b_stemmed_search",
                        }
                    },
                },
                "suggest_text": {
                    "type": "text",
                    "fields": {
                        "keyword": {"type": "keyword", "ignore_above": 1024},
                        "stem": {
                            "type": "text",
                            "analyzer": "b2b_stemmed",
                            "search_analyzer": "b2b_stemmed_search",
                        },
                    },
                },
                "category_name": {"type": "keyword", "fields": {"text": {"type": "text"}}},
                "subCategory_name": {"type": "keyword", "fields": {"text": {"type": "text"}}},
                "productCategory_name": {"type": "keyword", "fields": {"text": {"type": "text"}}},
                "category_id": {"type": "keyword"},
                "subCategory_id": {"type": "keyword"},
                "productCategory_id": {"type": "keyword"},
                "status": {"type": "keyword"},
                "showInCatalog": {"type": "boolean"},
                "isArchived": {"type": "boolean"},
                "isDraft": {"type": "boolean"},
                "userId": {"type": "keyword"},
                "createdAt": {"type": "date"},
                "updatedAt": {"type": "date"},
                "reference_status": {
                    "properties": {
                        "category": {"type": "keyword"},
                        "subCategory": {"type": "keyword"},
                        "productCategory": {"type": "keyword"},
                    }
                },
                "embedding_version": {"type": "keyword"},
                "vector_meta": {
                    "properties": {
                        "chunk_strategy": {"type": "keyword"},
                        "chunk_count": {"type": "integer"},
                        "avg_chunk_words": {"type": "integer"},
                        "max_chunk_words": {"type": "integer"},
                    }
                },
                "product_vector_main": {
                    "type": "dense_vector",
                    "dims": EMBED_DIM,
                    "index": True,
                    "similarity": "cosine",
                },
                "product_vector_short": {
                    "type": "dense_vector",
                    "dims": EMBED_DIM,
                    "index": True,
                    "similarity": "cosine",
                },
            },
        },
    }


def _product_ingest_pipeline_body() -> dict[str, Any]:
    return {
        "description": "Normalize product text and hydrate autocomplete fields.",
        "processors": [
            {
                "script": {
                    "lang": "painless",
                    "source": (
                        "String norm(def v) {"
                        " if (v == null) return null;"
                        " String out = v.toString();"
                        " out = out.replace(\"-mm\", \" mm\").replace(\"-MM\", \" mm\");"
                        " for (int d = 0; d <= 9; d++) {"
                        "  String digit = Integer.toString(d);"
                        "  out = out.replace(digit + \"mm\", digit + \" mm\");"
                        "  out = out.replace(digit + \"MM\", digit + \" mm\");"
                        " }"
                        " while (out.contains(\"  \")) { out = out.replace(\"  \", \" \" ); }"
                        " out = out.trim();"
                        " return out;"
                        "}"
                        "String pn = norm(ctx.productName);"
                        "if (pn != null && !pn.isEmpty()) {"
                        " ctx.productName = pn;"
                        " ctx.productName_autocomplete = pn;"
                        " ctx.productName_completion = pn;"
                        "}"
                        "for (String f : new String[]{'productDescription','detailedDescription','search_text','suggest_text'}) {"
                        " if (ctx.containsKey(f) && ctx[f] != null) {"
                        "   String nv = norm(ctx[f]);"
                        "   if (nv != null) { ctx[f] = nv; }"
                        " }"
                        "}"
                    ),
                }
            }
        ],
        "on_failure": [{"set": {"field": "ingest_error", "value": "{{ _ingest.on_failure_message }}"}}],
    }


def _product_ilm_policy_body() -> dict[str, Any]:
    return {
        "policy": {
            "phases": {
                "hot": {
                    "actions": {
                        "rollover": {
                            "max_age": ES_PRODUCT_ROLLOVER_MAX_AGE,
                            "max_primary_shard_size": ES_PRODUCT_ROLLOVER_MAX_PRIMARY_SHARD_SIZE,
                            "max_docs": ES_PRODUCT_ROLLOVER_MAX_DOCS,
                        }
                    }
                }
            }
        }
    }


def _product_component_template_body(use_ilm: bool) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "number_of_shards": ES_PRODUCT_SHARDS,
        "number_of_replicas": ES_PRODUCT_REPLICAS,
        "refresh_interval": ES_PRODUCT_REFRESH_INTERVAL,
        "default_pipeline": ES_PRODUCT_INGEST_PIPELINE,
    }

    return {
        "template": {"settings": settings},
        "_meta": {"owner": "pepagora", "purpose": "product-index-defaults"},
    }


def _product_index_template_body(index_pattern: str) -> dict[str, Any]:
    body = product_index_body()
    return {
        "index_patterns": [index_pattern],
        "composed_of": [ES_PRODUCT_COMPONENT_TEMPLATE],
        "priority": 550,
        "template": {
            "settings": {"analysis": body["settings"]["analysis"]},
            "mappings": body["mappings"],
        },
        "_meta": {"owner": "pepagora", "purpose": "product-index-template"},
    }


def _list_alias_indices(es: Elasticsearch, alias_name: str) -> list[str]:
    try:
        return sorted(es.indices.get_alias(name=alias_name).keys())
    except Exception:
        return []


def _next_versioned_index_name(es: Elasticsearch, base_index: str, pattern: str) -> str:
    suffix_re = re.compile(rf"^{re.escape(base_index)}-(\\d{{6}})$")
    max_suffix = 0
    try:
        existing = es.indices.get(index=pattern, expand_wildcards="all")
    except Exception:
        existing = {}

    for index_name in existing.keys():
        match = suffix_re.match(index_name)
        if not match:
            continue
        max_suffix = max(max_suffix, int(match.group(1)))
    return f"{base_index}-{max_suffix + 1:06d}"


def install_product_assets(es: Elasticsearch, use_ilm: bool = True) -> None:
    json_headers = {
        "content-type": "application/vnd.elasticsearch+json; compatible-with=8",
        "accept": "application/vnd.elasticsearch+json; compatible-with=8",
    }
    es.ingest.put_pipeline(id=ES_PRODUCT_INGEST_PIPELINE, body=_product_ingest_pipeline_body())
    if use_ilm:
        es.perform_request(
            "PUT",
            f"/_ilm/policy/{ES_PRODUCT_ILM_POLICY}",
            body=_product_ilm_policy_body(),
            headers=json_headers,
        )

    es.perform_request(
        "PUT",
        f"/_component_template/{ES_PRODUCT_COMPONENT_TEMPLATE}",
        body=_product_component_template_body(use_ilm=use_ilm),
        headers=json_headers,
    )
    es.perform_request(
        "PUT",
        f"/_index_template/{ES_PRODUCT_TEMPLATE}",
        body=_product_index_template_body(index_pattern=ES_PRODUCT_INDEX_PATTERN),
        headers=json_headers,
    )
    print(
        f"[assets] product assets ready: pipeline={ES_PRODUCT_INGEST_PIPELINE}, "
        f"component={ES_PRODUCT_COMPONENT_TEMPLATE}, template={ES_PRODUCT_TEMPLATE}, ilm={ES_PRODUCT_ILM_POLICY}"
    )


def promote_product_aliases(es: Elasticsearch, index_name: str, use_ilm: bool = True) -> None:
    actions: list[dict[str, Any]] = []
    for existing_index in _list_alias_indices(es, ES_PRODUCT_READ_ALIAS):
        if existing_index != index_name:
            actions.append({"remove": {"index": existing_index, "alias": ES_PRODUCT_READ_ALIAS}})
    for existing_index in _list_alias_indices(es, ES_PRODUCT_WRITE_ALIAS):
        actions.append({"remove": {"index": existing_index, "alias": ES_PRODUCT_WRITE_ALIAS}})

    actions.append({"add": {"index": index_name, "alias": ES_PRODUCT_READ_ALIAS}})
    actions.append({"add": {"index": index_name, "alias": ES_PRODUCT_WRITE_ALIAS, "is_write_index": True}})
    es.indices.update_aliases(actions=actions)
    if use_ilm:
        try:
            es.indices.put_settings(
                index=index_name,
                body={
                    "index": {
                        "lifecycle.name": ES_PRODUCT_ILM_POLICY,
                        "lifecycle.rollover_alias": ES_PRODUCT_WRITE_ALIAS,
                    }
                },
            )
        except Exception:
            pass
    print(f"[alias] promoted {index_name} -> {ES_PRODUCT_READ_ALIAS}, {ES_PRODUCT_WRITE_ALIAS}")


def create_or_update_product_index(
    es: Elasticsearch,
    recreate: bool,
    use_aliases: bool,
    index_name: str | None = None,
    install_assets: bool = True,
    use_ilm: bool = True,
    promote_alias: bool = False,
) -> str:
    if install_assets:
        install_product_assets(es, use_ilm=use_ilm)

    target_index = index_name or (
        _next_versioned_index_name(es, ES_PRODUCT_INDEX, ES_PRODUCT_INDEX_PATTERN) if use_aliases else ES_PRODUCT_INDEX
    )

    if recreate and not use_aliases and es.indices.exists(index=target_index):
        es.indices.delete(index=target_index)
        print(f"[index] deleted: {target_index}")

    body = product_index_body()
    if use_ilm and use_aliases and promote_alias:
        body["settings"]["index.lifecycle.name"] = ES_PRODUCT_ILM_POLICY
        body["settings"]["index.lifecycle.rollover_alias"] = ES_PRODUCT_WRITE_ALIAS

    if use_aliases and promote_alias:
        body["aliases"] = {
            ES_PRODUCT_READ_ALIAS: {},
            ES_PRODUCT_WRITE_ALIAS: {"is_write_index": True},
        }

    if not es.indices.exists(index=target_index):
        es.indices.create(index=target_index, body=body)
        print(f"[index] created: {target_index}")
    else:
        print(f"[index] already exists: {target_index}")

    if use_aliases and promote_alias:
        promote_product_aliases(es, target_index, use_ilm=use_ilm)
    elif use_aliases:
        print(f"[alias] deferred promotion for {target_index}; backfill first, then run promote-alias")

    return target_index


def _ref_status(ref_id: str | None, ref_name: str) -> str:
    if not ref_id:
        return "missing"
    return "resolved" if ref_name else "unresolved"


def _safe_bulk(es: Elasticsearch, actions: list[dict[str, Any]], chunk_size: int) -> tuple[int, int]:
    if not actions:
        return 0, 0

    if ES_BULK_THREADS <= 1:
        success, errors = helpers.bulk(
            es,
            actions,
            chunk_size=chunk_size,
            request_timeout=120,
            raise_on_error=False,
        )
        error_count = len(errors) if errors else 0
        if error_count:
            print(f"[bulk] errors: {error_count}")
        return int(success), int(error_count)

    success = 0
    error_count = 0
    for ok, _item in helpers.parallel_bulk(
        es,
        actions,
        thread_count=ES_BULK_THREADS,
        queue_size=max(1, ES_BULK_QUEUE_SIZE),
        chunk_size=chunk_size,
        request_timeout=120,
        raise_on_error=False,
        raise_on_exception=False,
    ):
        if ok:
            success += 1
        else:
            error_count += 1

    if error_count:
        print(f"[bulk] errors: {error_count}")
    return success, error_count


def _render_progress(
    indexed: int,
    total: int,
    errors: int,
    skipped: int,
    started_at: float,
) -> None:
    elapsed = max(time.monotonic() - started_at, 1e-9)
    rate = indexed / elapsed if indexed > 0 else 0.0
    pct = (indexed / total * 100.0) if total > 0 else 0.0
    bar_len = 28
    filled = int(bar_len * min(max(pct, 0.0), 100.0) / 100.0)
    bar = "#" * filled + "-" * (bar_len - filled)
    remaining = max(total - indexed, 0)
    eta = int(remaining / rate) if rate > 0 else 0
    print(
        f"\r[products] |{bar}| {indexed:,}/{total:,} ({pct:5.1f}%) "
        f"{rate:,.1f} docs/s eta={eta}s errors={errors} skipped={skipped}",
        end="",
        flush=True,
    )


def read_product_ids(path: str) -> set[str]:
    file_path = Path(path)
    if not file_path.exists():
        return set()
    return {
        line.strip()
        for line in file_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _products_query_for_ids(product_ids_filter: set[str] | None) -> dict[str, Any]:
    if not product_ids_filter:
        return {}

    string_ids = sorted(product_ids_filter)
    object_ids: list[ObjectId] = []
    for value in string_ids:
        try:
            object_ids.append(ObjectId(value))
        except Exception:
            continue

    clauses: list[dict[str, Any]] = []
    if object_ids:
        clauses.append({"_id": {"$in": object_ids}})
    if string_ids:
        clauses.append({"_id": {"$in": string_ids}})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$or": clauses}


def _fetch_refresh_interval(es: Elasticsearch, index_name: str) -> str | None:
    try:
        settings = es.indices.get_settings(index=index_name)
    except Exception:
        return None

    for payload in settings.values():
        idx = payload.get("settings", {}).get("index", {})
        interval = idx.get("refresh_interval")
        if interval:
            return str(interval)
    return None


def _set_refresh_interval(es: Elasticsearch, index_name: str, refresh_interval: str) -> bool:
    try:
        es.indices.put_settings(index=index_name, body={"index": {"refresh_interval": refresh_interval}})
        return True
    except Exception:
        return False


def backfill_products(
    es: Elasticsearch,
    db,
    batch_size: int,
    published_only: bool,
    target_index: str,
    product_ids_filter: set[str] | None = None,
    limit: int | None = None,
) -> None:
    lookup = load_lookup_maps(db)
    products = db[MONGO_PRODUCTS]

    query_clauses: list[dict[str, Any]] = []
    if published_only:
        query_clauses.append(
            {
            "status": {"$in": ["live", "approved"]},
            "showInCatalog": True,
            "isArchived": False,
            "isDraft": False,
            }
        )

    ids_query = _products_query_for_ids(product_ids_filter)
    if ids_query:
        query_clauses.append(ids_query)

    query: dict[str, Any]
    if not query_clauses:
        query = {}
    elif len(query_clauses) == 1:
        query = query_clauses[0]
    else:
        query = {"$and": query_clauses}

    total = products.count_documents(query)
    target_total = min(total, limit) if limit else total
    print(f"[products] source docs: {total:,}")
    if product_ids_filter:
        print(f"[products] filtering by product ids: {len(product_ids_filter):,}")
    if limit:
        print(f"[products] limiting to first {target_total:,} docs")
    print(f"[products] embedding model: {EMBED_MODEL_NAME} ({EMBED_DIM} dims)")
    print(f"[products] bulk threads: {ES_BULK_THREADS}, bulk queue: {ES_BULK_QUEUE_SIZE}")
    print(f"[products] target index: {target_index}")
    print(
        f"[products] chunk config: strategy=sentence, target_words={TARGET_WORDS_PER_CHUNK}, "
        f"max_words={MAX_WORDS_PER_CHUNK}, overlap_sentences={OVERLAP_SENTENCES}"
    )

    original_refresh_interval: str | None = None
    refresh_tuned = False
    if ES_PRODUCT_BULK_REFRESH_INTERVAL:
        original_refresh_interval = _fetch_refresh_interval(es, target_index)
        if original_refresh_interval and original_refresh_interval != ES_PRODUCT_BULK_REFRESH_INTERVAL:
            refresh_tuned = _set_refresh_interval(es, target_index, ES_PRODUCT_BULK_REFRESH_INTERVAL)
            if refresh_tuned:
                print(
                    "[products] bulk mode refresh interval: "
                    f"{original_refresh_interval} -> {ES_PRODUCT_BULK_REFRESH_INTERVAL}"
                )

    cursor = products.find(query, no_cursor_timeout=True).batch_size(batch_size)
    batch: list[dict[str, Any]] = []
    indexed = 0
    errors = 0
    skipped = 0
    read_count = 0
    started_at = time.monotonic()

    def flush(docs: list[dict[str, Any]]) -> tuple[int, int, int]:
        if not docs:
            return 0, 0, 0

        records: list[dict[str, Any]] = []
        chunk_ranges: list[tuple[int, int]] = []
        all_chunk_texts: list[str] = []
        short_texts: list[str] = []
        local_skipped = 0

        for doc in docs:
            doc_id = _norm_id(doc.get("_id"))
            if not doc_id:
                local_skipped += 1
                continue

            category = doc.get("category") or {}
            subcategory = doc.get("subCategory") or {}
            productcategory = doc.get("productCategory") or {}

            category_id = _norm_id(category.get("_id"))
            subcategory_id = _norm_id(subcategory.get("_id"))
            productcategory_id = _norm_id(productcategory.get("_id"))

            category_name = _as_text(category.get("name")) or lookup.categories.get(category_id or "", "")
            subcategory_name = _as_text(subcategory.get("name")) or lookup.subcategories.get(subcategory_id or "", "")
            productcategory_name = _as_text(productcategory.get("name")) or lookup.productcategories.get(productcategory_id or "", "")

            product_name = _as_text(doc.get("productName"))
            product_description = _as_text(doc.get("productDescription"))
            detailed_description = _as_text(doc.get("detailedDescription"))

            search_text, suggest_text, main_text, short_text = _build_product_texts(
                product_name,
                product_description,
                detailed_description,
                category_name,
                subcategory_name,
                productcategory_name,
            )

            chunks = _chunk_by_sentences(main_text)
            if not chunks:
                chunks = [short_text or main_text or "unknown"]

            start = len(all_chunk_texts)
            all_chunk_texts.extend(chunks)
            end = len(all_chunk_texts)
            chunk_ranges.append((start, end))

            short_payload = short_text or main_text or "unknown"
            short_texts.append(short_payload)

            chunk_word_counts = [_word_count(chunk) for chunk in chunks]
            vector_meta = {
                "chunk_strategy": "sentence",
                "chunk_count": len(chunks),
                "avg_chunk_words": int(round(sum(chunk_word_counts) / len(chunk_word_counts))) if chunk_word_counts else 0,
                "max_chunk_words": max(chunk_word_counts) if chunk_word_counts else 0,
            }

            source = {
                "productName": product_name,
                "productDescription": product_description,
                "detailedDescription": detailed_description,
                "search_text": search_text,
                "suggest_text": suggest_text,
                "category_name": category_name,
                "subCategory_name": subcategory_name,
                "productCategory_name": productcategory_name,
                "category_id": category_id or "",
                "subCategory_id": subcategory_id or "",
                "productCategory_id": productcategory_id or "",
                "status": _as_text(doc.get("status")).lower() or "unknown",
                "showInCatalog": bool(doc.get("showInCatalog", False)),
                "isArchived": bool(doc.get("isArchived", False)),
                "isDraft": bool(doc.get("isDraft", False)),
                "userId": _extract_user_id(doc),
                "createdAt": _to_datetime(doc.get("createdAt")),
                "updatedAt": _to_datetime(doc.get("updatedAt")),
                "reference_status": {
                    "category": _ref_status(category_id, category_name),
                    "subCategory": _ref_status(subcategory_id, subcategory_name),
                    "productCategory": _ref_status(productcategory_id, productcategory_name),
                },
                "embedding_version": EMBEDDING_VERSION,
                "vector_meta": vector_meta,
            }

            records.append(
                {
                    "doc_id": doc_id,
                    "source": source,
                }
            )

        if not records:
            return 0, 0, local_skipped

        chunk_vectors = encode_document_batch(all_chunk_texts)
        short_vectors = encode_document_batch(short_texts)

        actions: list[dict[str, Any]] = []
        for rec, (start, end), short_vec in zip(records, chunk_ranges, short_vectors):
            main_vec = _mean_pool_and_normalize(chunk_vectors[start:end])
            rec["source"]["product_vector_main"] = main_vec
            rec["source"]["product_vector_short"] = short_vec

            actions.append(
                {
                    "_op_type": "index",
                    "_index": target_index,
                    "_id": rec["doc_id"],
                    "_source": rec["source"],
                }
            )

        ok, err = _safe_bulk(es, actions, chunk_size=batch_size)
        return ok, err, local_skipped

    try:
        for doc in cursor:
            batch.append(doc)
            read_count += 1

            if len(batch) >= batch_size:
                ok, err, local_skipped = flush(batch)
                indexed += ok
                errors += err
                skipped += local_skipped
                _render_progress(indexed, target_total, errors, skipped, started_at)
                batch.clear()

            if limit and read_count >= limit:
                break

        if batch:
            ok, err, local_skipped = flush(batch)
            indexed += ok
            errors += err
            skipped += local_skipped
    finally:
        cursor.close()
        if refresh_tuned and original_refresh_interval:
            if _set_refresh_interval(es, target_index, original_refresh_interval):
                print(f"\n[products] refresh interval restored to {original_refresh_interval}")
        try:
            es.indices.refresh(index=target_index)
        except Exception:
            pass

    if target_total > 0:
        _render_progress(indexed, target_total, errors, skipped, started_at)
        print()

    print(f"[products] done: indexed={indexed:,}, errors={errors}, skipped={skipped}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Product indexing pipeline v2.3")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_cmd = subparsers.add_parser("create-index", help="Create product index")
    create_cmd.add_argument("--recreate", action="store_true", help="Delete and recreate index")
    create_cmd.add_argument("--use-aliases", action="store_true", help="Create a versioned index and promote aliases")
    create_cmd.add_argument("--index-name", type=str, default="", help="Explicit concrete index name")
    create_cmd.add_argument("--skip-assets", action="store_true", help="Skip ingest/template/ILM asset installation")
    create_cmd.add_argument("--no-ilm", action="store_true", help="Create index without ILM policy wiring")
    create_cmd.add_argument("--promote-now", action="store_true", help="Immediately switch read/write aliases to new index")

    assets_cmd = subparsers.add_parser("install-assets", help="Install product ingest/template/ILM assets")
    assets_cmd.add_argument("--no-ilm", action="store_true", help="Install assets without ILM policy")

    promote_cmd = subparsers.add_parser("promote-alias", help="Promote aliases to an existing concrete index")
    promote_cmd.add_argument("--index-name", type=str, required=True, help="Concrete index to attach aliases to")

    backfill_cmd = subparsers.add_parser("backfill", help="Backfill products to Elasticsearch")
    backfill_cmd.add_argument("--batch-size", type=int, default=192)
    backfill_cmd.add_argument("--limit", type=int, default=0, help="Optional max number of products to index")
    backfill_cmd.add_argument("--index-name", type=str, default="", help="Index or alias to write documents into")
    backfill_cmd.add_argument("--use-write-alias", action="store_true", help="Write into configured write alias")
    backfill_cmd.add_argument(
        "--product-ids-file",
        type=str,
        default="",
        help="Optional file with one product id per line to filter products",
    )
    backfill_cmd.add_argument(
        "--published-only",
        action="store_true",
        help="Filter only live/approved catalog-visible products",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    es = es_client()
    mongo = mongo_client()
    db = mongo[MONGO_DB]

    try:
        if args.command == "create-index":
            created_index = create_or_update_product_index(
                es,
                recreate=bool(args.recreate),
                use_aliases=bool(args.use_aliases),
                index_name=str(args.index_name).strip() or None,
                install_assets=not bool(args.skip_assets),
                use_ilm=not bool(args.no_ilm),
                promote_alias=bool(args.promote_now),
            )
            print(f"[index] ready: {created_index}")
            return

        if args.command == "install-assets":
            install_product_assets(es, use_ilm=not bool(args.no_ilm))
            return

        if args.command == "promote-alias":
            promote_product_aliases(es, str(args.index_name).strip(), use_ilm=True)
            return

        if args.command == "backfill":
            product_ids_filter: set[str] | None = None
            if args.product_ids_file:
                product_ids_filter = read_product_ids(str(args.product_ids_file))
                print(f"[products] loaded {len(product_ids_filter):,} product ids from {args.product_ids_file}")

            limit = int(args.limit) if int(args.limit) > 0 else None
            target_index = (
                str(args.index_name).strip()
                or (ES_PRODUCT_WRITE_ALIAS if bool(args.use_write_alias) else ES_PRODUCT_INDEX)
            )
            backfill_products(
                es,
                db,
                batch_size=int(args.batch_size),
                published_only=bool(args.published_only),
                target_index=target_index,
                product_ids_filter=product_ids_filter,
                limit=limit,
            )
            return
    finally:
        mongo.close()


if __name__ == "__main__":
    main()