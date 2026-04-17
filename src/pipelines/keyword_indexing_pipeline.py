from __future__ import annotations

import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from bson import ObjectId
from elasticsearch import Elasticsearch, helpers
from pymongo import MongoClient

from embedding_service.client import EMBED_DIM, EMBED_MODEL_NAME, encode_document_batch

try:
    from ..core.synonym_data import load_protected_tokens, load_synonym_rules
except ImportError:
    try:
        from core.synonym_data import load_protected_tokens, load_synonym_rules
    except ImportError:
        from src.core.synonym_data import load_protected_tokens, load_synonym_rules


ES_HOST = os.getenv("ES_HOST", "http://localhost:9200")
ES_KEYWORD_INDEX = os.getenv("ES_KEYWORD_INDEX", "pepagora_keyword_cluster")
ES_KEYWORD_READ_ALIAS = os.getenv("ES_KEYWORD_READ_ALIAS", f"{ES_KEYWORD_INDEX}_current")
ES_KEYWORD_WRITE_ALIAS = os.getenv("ES_KEYWORD_WRITE_ALIAS", f"{ES_KEYWORD_INDEX}_write")
ES_KEYWORD_INDEX_PATTERN = os.getenv("ES_KEYWORD_INDEX_PATTERN", f"{ES_KEYWORD_INDEX}-*")
ES_KEYWORD_TEMPLATE = os.getenv("ES_KEYWORD_TEMPLATE", "pepagora_keyword_template_v1")
ES_KEYWORD_COMPONENT_TEMPLATE = os.getenv("ES_KEYWORD_COMPONENT_TEMPLATE", "pepagora_keyword_component_v1")
ES_KEYWORD_ILM_POLICY = os.getenv("ES_KEYWORD_ILM_POLICY", "pepagora_keyword_ilm_v1")
ES_KEYWORD_INGEST_PIPELINE = os.getenv("ES_KEYWORD_INGEST_PIPELINE", "pepagora_keyword_ingest_v1")
ES_KEYWORD_SHARDS = int(os.getenv("ES_KEYWORD_SHARDS", "1"))
ES_KEYWORD_REPLICAS = int(os.getenv("ES_KEYWORD_REPLICAS", "0"))
ES_KEYWORD_REFRESH_INTERVAL = os.getenv("ES_KEYWORD_REFRESH_INTERVAL", "1s")
ES_KEYWORD_BULK_REFRESH_INTERVAL = os.getenv("ES_KEYWORD_BULK_REFRESH_INTERVAL", "-1")
ES_KEYWORD_ROLLOVER_MAX_AGE = os.getenv("ES_KEYWORD_ROLLOVER_MAX_AGE", "30d")
ES_KEYWORD_ROLLOVER_MAX_PRIMARY_SHARD_SIZE = os.getenv("ES_KEYWORD_ROLLOVER_MAX_PRIMARY_SHARD_SIZE", "20gb")
ES_KEYWORD_ROLLOVER_MAX_DOCS = int(os.getenv("ES_KEYWORD_ROLLOVER_MAX_DOCS", "10000000"))
ES_BULK_THREADS = int(os.getenv("ES_BULK_THREADS", "6"))
ES_BULK_QUEUE_SIZE = int(os.getenv("ES_BULK_QUEUE_SIZE", "16"))

MONGO_URI = os.getenv(
    "MONGO_URI",
    "mongodb://localhost:27017/admin",
)
MONGO_DB = os.getenv("MONGO_DB", "internSandboxDb")
MONGO_KEYWORDS = os.getenv("MONGO_KEYWORDS_COLLECTION", "keyword_cluster")

EMBEDDING_VERSION = os.getenv("EMBEDDING_VERSION", f"{EMBED_MODEL_NAME}-v23")

B2B_SYNONYM_RULES = load_synonym_rules()
B2B_PROTECTED_TOKENS = sorted(load_protected_tokens())


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


def _dedupe_terms(values: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _as_text(value)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _keyword_analysis_settings() -> dict[str, Any]:
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


def keyword_index_body() -> dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": ES_KEYWORD_SHARDS,
            "number_of_replicas": ES_KEYWORD_REPLICAS,
            "refresh_interval": ES_KEYWORD_REFRESH_INTERVAL,
            "default_pipeline": ES_KEYWORD_INGEST_PIPELINE,
            "analysis": _keyword_analysis_settings(),
        },
        "mappings": {
            "dynamic": True,
            "properties": {
                "keyword_name": {
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
                "head_terms": {"type": "keyword"},
                "variant_terms": {
                    "type": "text",
                    "analyzer": "edge_autocomplete",
                    "search_analyzer": "standard",
                    "fields": {
                        "keyword": {"type": "keyword", "ignore_above": 512},
                        "stem": {
                            "type": "text",
                            "analyzer": "b2b_stemmed",
                            "search_analyzer": "b2b_stemmed_search",
                        },
                    },
                },
                "long_tail_terms": {
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
                "product_ids": {"type": "keyword"},
                "product_category_ids": {"type": "keyword"},
                "product_count": {"type": "integer"},
                "category_count": {"type": "integer"},
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "embedding_version": {"type": "keyword"},
                "keyword_vector_longtail": {
                    "type": "dense_vector",
                    "dims": EMBED_DIM,
                    "index": True,
                    "similarity": "cosine",
                },
                "keyword_vector_variants": {
                    "type": "dense_vector",
                    "dims": EMBED_DIM,
                    "index": True,
                    "similarity": "cosine",
                },
            },
        },
    }


def _keyword_ingest_pipeline_body() -> dict[str, Any]:
    return {
        "description": "Normalize keyword cluster fields.",
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
                        "def normList(def arr) {"
                        " List out = new ArrayList();"
                        " if (arr == null) return out;"
                        " Set seen = new HashSet();"
                        " for (def item : arr) {"
                        "  String n = norm(item);"
                        "  if (n == null || n.isEmpty()) continue;"
                        "  String key = n.toLowerCase();"
                        "  if (seen.contains(key)) continue;"
                        "  seen.add(key);"
                        "  out.add(n);"
                        " }"
                        " return out;"
                        "}"
                        "String kn = norm(ctx.keyword_name);"
                        "if (kn != null) { ctx.keyword_name = kn; }"
                        "ctx.variant_terms = normList(ctx.variant_terms);"
                        "ctx.long_tail_terms = normList(ctx.long_tail_terms);"
                        "ctx.head_terms = normList(ctx.head_terms);"
                    ),
                }
            }
        ],
        "on_failure": [{"set": {"field": "ingest_error", "value": "{{ _ingest.on_failure_message }}"}}],
    }


def _keyword_ilm_policy_body() -> dict[str, Any]:
    return {
        "policy": {
            "phases": {
                "hot": {
                    "actions": {
                        "rollover": {
                            "max_age": ES_KEYWORD_ROLLOVER_MAX_AGE,
                            "max_primary_shard_size": ES_KEYWORD_ROLLOVER_MAX_PRIMARY_SHARD_SIZE,
                            "max_docs": ES_KEYWORD_ROLLOVER_MAX_DOCS,
                        }
                    }
                }
            }
        }
    }


def _keyword_component_template_body(use_ilm: bool) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "number_of_shards": ES_KEYWORD_SHARDS,
        "number_of_replicas": ES_KEYWORD_REPLICAS,
        "refresh_interval": ES_KEYWORD_REFRESH_INTERVAL,
        "default_pipeline": ES_KEYWORD_INGEST_PIPELINE,
    }

    return {
        "template": {"settings": settings},
        "_meta": {"owner": "pepagora", "purpose": "keyword-index-defaults"},
    }


def _keyword_index_template_body(index_pattern: str) -> dict[str, Any]:
    body = keyword_index_body()
    return {
        "index_patterns": [index_pattern],
        "composed_of": [ES_KEYWORD_COMPONENT_TEMPLATE],
        "priority": 560,
        "template": {
            "settings": {"analysis": body["settings"]["analysis"]},
            "mappings": body["mappings"],
        },
        "_meta": {"owner": "pepagora", "purpose": "keyword-index-template"},
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


def install_keyword_assets(es: Elasticsearch, use_ilm: bool = True) -> None:
    json_headers = {
        "content-type": "application/vnd.elasticsearch+json; compatible-with=8",
        "accept": "application/vnd.elasticsearch+json; compatible-with=8",
    }
    es.ingest.put_pipeline(id=ES_KEYWORD_INGEST_PIPELINE, body=_keyword_ingest_pipeline_body())
    if use_ilm:
        es.perform_request(
            "PUT",
            f"/_ilm/policy/{ES_KEYWORD_ILM_POLICY}",
            body=_keyword_ilm_policy_body(),
            headers=json_headers,
        )

    es.perform_request(
        "PUT",
        f"/_component_template/{ES_KEYWORD_COMPONENT_TEMPLATE}",
        body=_keyword_component_template_body(use_ilm=use_ilm),
        headers=json_headers,
    )
    es.perform_request(
        "PUT",
        f"/_index_template/{ES_KEYWORD_TEMPLATE}",
        body=_keyword_index_template_body(index_pattern=ES_KEYWORD_INDEX_PATTERN),
        headers=json_headers,
    )
    print(
        f"[assets] keyword assets ready: pipeline={ES_KEYWORD_INGEST_PIPELINE}, "
        f"component={ES_KEYWORD_COMPONENT_TEMPLATE}, template={ES_KEYWORD_TEMPLATE}, ilm={ES_KEYWORD_ILM_POLICY}"
    )


def promote_keyword_aliases(es: Elasticsearch, index_name: str, use_ilm: bool = True) -> None:
    actions: list[dict[str, Any]] = []
    for existing_index in _list_alias_indices(es, ES_KEYWORD_READ_ALIAS):
        if existing_index != index_name:
            actions.append({"remove": {"index": existing_index, "alias": ES_KEYWORD_READ_ALIAS}})
    for existing_index in _list_alias_indices(es, ES_KEYWORD_WRITE_ALIAS):
        actions.append({"remove": {"index": existing_index, "alias": ES_KEYWORD_WRITE_ALIAS}})

    actions.append({"add": {"index": index_name, "alias": ES_KEYWORD_READ_ALIAS}})
    actions.append({"add": {"index": index_name, "alias": ES_KEYWORD_WRITE_ALIAS, "is_write_index": True}})
    es.indices.update_aliases(actions=actions)
    if use_ilm:
        try:
            es.indices.put_settings(
                index=index_name,
                body={
                    "index": {
                        "lifecycle.name": ES_KEYWORD_ILM_POLICY,
                        "lifecycle.rollover_alias": ES_KEYWORD_WRITE_ALIAS,
                    }
                },
            )
        except Exception:
            pass
    print(f"[alias] promoted {index_name} -> {ES_KEYWORD_READ_ALIAS}, {ES_KEYWORD_WRITE_ALIAS}")


def create_or_update_keyword_index(
    es: Elasticsearch,
    recreate: bool,
    use_aliases: bool,
    index_name: str | None = None,
    install_assets: bool = True,
    use_ilm: bool = True,
    promote_alias: bool = False,
) -> str:
    if install_assets:
        install_keyword_assets(es, use_ilm=use_ilm)

    target_index = index_name or (
        _next_versioned_index_name(es, ES_KEYWORD_INDEX, ES_KEYWORD_INDEX_PATTERN) if use_aliases else ES_KEYWORD_INDEX
    )
    if recreate and not use_aliases and es.indices.exists(index=target_index):
        es.indices.delete(index=target_index)
        print(f"[index] deleted: {target_index}")

    body = keyword_index_body()
    if use_ilm and use_aliases and promote_alias:
        body["settings"]["index.lifecycle.name"] = ES_KEYWORD_ILM_POLICY
        body["settings"]["index.lifecycle.rollover_alias"] = ES_KEYWORD_WRITE_ALIAS

    if use_aliases and promote_alias:
        body["aliases"] = {
            ES_KEYWORD_READ_ALIAS: {},
            ES_KEYWORD_WRITE_ALIAS: {"is_write_index": True},
        }

    if not es.indices.exists(index=target_index):
        es.indices.create(index=target_index, body=body)
        print(f"[index] created: {target_index}")
    else:
        print(f"[index] already exists: {target_index}")

    if use_aliases and promote_alias:
        promote_keyword_aliases(es, target_index, use_ilm=use_ilm)
    elif use_aliases:
        print(f"[alias] deferred promotion for {target_index}; backfill first, then run promote-alias")

    return target_index


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


def _render_progress(indexed: int, total: int, errors: int, started_at: float) -> None:
    elapsed = max(time.monotonic() - started_at, 1e-9)
    rate = indexed / elapsed if indexed > 0 else 0.0
    pct = (indexed / total * 100.0) if total > 0 else 0.0
    bar_len = 28
    filled = int(bar_len * min(max(pct, 0.0), 100.0) / 100.0)
    bar = "#" * filled + "-" * (bar_len - filled)
    remaining = max(total - indexed, 0)
    eta = int(remaining / rate) if rate > 0 else 0
    print(
        f"\r[keywords] |{bar}| {indexed:,}/{total:,} ({pct:5.1f}%) "
        f"{rate:,.1f} docs/s eta={eta}s errors={errors}",
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


def _keyword_query_for_products(product_ids_filter: set[str] | None) -> dict[str, Any]:
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
    if string_ids:
        clauses.append({"product_id": {"$in": string_ids}})
    if object_ids:
        clauses.append({"product_id": {"$in": object_ids}})

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


def backfill_keywords(
    es: Elasticsearch,
    db,
    batch_size: int,
    target_index: str,
    product_ids_filter: set[str] | None = None,
    limit: int | None = None,
) -> None:
    keywords = db[MONGO_KEYWORDS]
    query = _keyword_query_for_products(product_ids_filter)

    total = keywords.count_documents(query)
    target_total = min(total, limit) if limit else total
    print(f"[keywords] source docs: {total:,}")
    if product_ids_filter:
        print(f"[keywords] filtering by product ids: {len(product_ids_filter):,}")
    if limit:
        print(f"[keywords] limiting to first {target_total:,} docs")
    print(f"[keywords] embedding model: {EMBED_MODEL_NAME} ({EMBED_DIM} dims)")
    print(f"[keywords] bulk threads: {ES_BULK_THREADS}, bulk queue: {ES_BULK_QUEUE_SIZE}")
    print(f"[keywords] target index: {target_index}")

    original_refresh_interval: str | None = None
    refresh_tuned = False
    if ES_KEYWORD_BULK_REFRESH_INTERVAL:
        original_refresh_interval = _fetch_refresh_interval(es, target_index)
        if original_refresh_interval and original_refresh_interval != ES_KEYWORD_BULK_REFRESH_INTERVAL:
            refresh_tuned = _set_refresh_interval(es, target_index, ES_KEYWORD_BULK_REFRESH_INTERVAL)
            if refresh_tuned:
                print(
                    "[keywords] bulk mode refresh interval: "
                    f"{original_refresh_interval} -> {ES_KEYWORD_BULK_REFRESH_INTERVAL}"
                )

    cursor = keywords.find(query, no_cursor_timeout=True).batch_size(batch_size)
    batch: list[dict[str, Any]] = []
    indexed = 0
    errors = 0
    read_count = 0
    started_at = time.monotonic()

    def flush(docs: list[dict[str, Any]]) -> tuple[int, int]:
        if not docs:
            return 0, 0

        payloads: list[dict[str, Any]] = []
        longtail_texts: list[str] = []
        variants_texts: list[str] = []

        for doc in docs:
            doc_id = _norm_id(doc.get("_id"))
            if not doc_id:
                continue

            terms = doc.get("keywords") or {}
            head_terms = _dedupe_terms((terms.get("head") or []) + (terms.get("Head") or []))
            variant_terms = _dedupe_terms(terms.get("variants") or [])
            long_tail_terms = _dedupe_terms(terms.get("long_tail") or [])

            normalized_product_ids = [_norm_id(value) or str(value) for value in (doc.get("product_id") or [])]
            normalized_product_ids = [value for value in normalized_product_ids if value]

            if product_ids_filter and not (set(normalized_product_ids) & product_ids_filter):
                continue

            normalized_category_ids = [
                _norm_id(value) or str(value) for value in (doc.get("product_category_id") or [])
            ]
            normalized_category_ids = [value for value in normalized_category_ids if value]

            keyword_name = _as_text(doc.get("keyword_name"))

            longtail_embed_text = " ".join(_dedupe_terms([keyword_name] + long_tail_terms))
            variants_embed_text = " ".join(_dedupe_terms([keyword_name] + variant_terms))

            payloads.append(
                {
                    "doc_id": doc_id,
                    "source": {
                        "keyword_name": keyword_name,
                        "head_terms": head_terms,
                        "variant_terms": variant_terms,
                        "long_tail_terms": long_tail_terms,
                        "product_ids": normalized_product_ids,
                        "product_category_ids": normalized_category_ids,
                        "product_count": int(doc.get("product_count", 0) or 0),
                        "category_count": int(doc.get("category_count", 0) or 0),
                        "created_at": _to_datetime(doc.get("created_at")),
                        "updated_at": _to_datetime(doc.get("updated_at")),
                        "embedding_version": EMBEDDING_VERSION,
                    },
                }
            )
            longtail_texts.append(longtail_embed_text or keyword_name or "unknown")
            variants_texts.append(variants_embed_text or keyword_name or "unknown")

        if not payloads:
            return 0, 0

        combined_texts = longtail_texts + variants_texts
        combined_vectors = encode_document_batch(combined_texts)
        split_index = len(longtail_texts)
        longtail_vectors = combined_vectors[:split_index]
        variants_vectors = combined_vectors[split_index:]
        actions: list[dict[str, Any]] = []

        for payload, longtail_vec, variants_vec in zip(payloads, longtail_vectors, variants_vectors):
            payload["source"]["keyword_vector_longtail"] = longtail_vec
            payload["source"]["keyword_vector_variants"] = variants_vec
            actions.append(
                {
                    "_op_type": "index",
                    "_index": target_index,
                    "_id": payload["doc_id"],
                    "_source": payload["source"],
                }
            )

        return _safe_bulk(es, actions, chunk_size=batch_size)

    try:
        for doc in cursor:
            batch.append(doc)
            read_count += 1

            if len(batch) >= batch_size:
                ok, err = flush(batch)
                indexed += ok
                errors += err
                _render_progress(indexed, target_total, errors, started_at)
                batch.clear()

            if limit and read_count >= limit:
                break

        if batch:
            ok, err = flush(batch)
            indexed += ok
            errors += err
    finally:
        cursor.close()
        if refresh_tuned and original_refresh_interval:
            if _set_refresh_interval(es, target_index, original_refresh_interval):
                print(f"\n[keywords] refresh interval restored to {original_refresh_interval}")
        try:
            es.indices.refresh(index=target_index)
        except Exception:
            pass

    if target_total > 0:
        _render_progress(indexed, target_total, errors, started_at)
        print()

    print(f"[keywords] done: indexed={indexed:,}, errors={errors}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Keyword indexing pipeline v2.3")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_cmd = subparsers.add_parser("create-index", help="Create keyword index")
    create_cmd.add_argument("--recreate", action="store_true", help="Delete and recreate index")
    create_cmd.add_argument("--use-aliases", action="store_true", help="Create a versioned index and promote aliases")
    create_cmd.add_argument("--index-name", type=str, default="", help="Explicit concrete index name")
    create_cmd.add_argument("--skip-assets", action="store_true", help="Skip ingest/template/ILM asset installation")
    create_cmd.add_argument("--no-ilm", action="store_true", help="Create index without ILM policy wiring")
    create_cmd.add_argument("--promote-now", action="store_true", help="Immediately switch read/write aliases to new index")

    assets_cmd = subparsers.add_parser("install-assets", help="Install keyword ingest/template/ILM assets")
    assets_cmd.add_argument("--no-ilm", action="store_true", help="Install assets without ILM policy")

    promote_cmd = subparsers.add_parser("promote-alias", help="Promote aliases to an existing concrete index")
    promote_cmd.add_argument("--index-name", type=str, required=True, help="Concrete index to attach aliases to")

    backfill_cmd = subparsers.add_parser("backfill", help="Backfill keyword clusters to Elasticsearch")
    backfill_cmd.add_argument("--batch-size", type=int, default=400)
    backfill_cmd.add_argument("--limit", type=int, default=0, help="Optional max number of keyword docs")
    backfill_cmd.add_argument("--index-name", type=str, default="", help="Index or alias to write documents into")
    backfill_cmd.add_argument("--use-write-alias", action="store_true", help="Write into configured write alias")
    backfill_cmd.add_argument(
        "--product-ids-file",
        type=str,
        default="",
        help="Optional file with one product id per line to filter keyword clusters",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    es = es_client()
    mongo = mongo_client()
    db = mongo[MONGO_DB]

    try:
        if args.command == "create-index":
            created_index = create_or_update_keyword_index(
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
            install_keyword_assets(es, use_ilm=not bool(args.no_ilm))
            return

        if args.command == "promote-alias":
            promote_keyword_aliases(es, str(args.index_name).strip(), use_ilm=True)
            return

        if args.command == "backfill":
            product_ids_filter: set[str] | None = None
            if args.product_ids_file:
                product_ids_filter = read_product_ids(str(args.product_ids_file))
                print(f"[keywords] loaded {len(product_ids_filter):,} product ids from {args.product_ids_file}")

            limit = int(args.limit) if int(args.limit) > 0 else None
            target_index = (
                str(args.index_name).strip()
                or (ES_KEYWORD_WRITE_ALIAS if bool(args.use_write_alias) else ES_KEYWORD_INDEX)
            )
            backfill_keywords(
                es,
                db,
                batch_size=int(args.batch_size),
                target_index=target_index,
                product_ids_filter=product_ids_filter,
                limit=limit,
            )
            return
    finally:
        mongo.close()


if __name__ == "__main__":
    main()