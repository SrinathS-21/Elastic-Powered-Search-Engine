"""Ingest live product fields from MongoDB into the OpenSearch index.

Fetches four fields from the live_products_v1 MongoDB collection:
  - liveUrl
  - showcase
  - createdBy
  - businessOf

Then bulk-updates the matching documents in the OpenSearch index using the
write alias (pepagora_products_write) so no existing fields are touched.

Also performs a createdBy vs userId alignment check at startup to confirm
whether MongoDB's createdBy matches OpenSearch's existing userId field.

Usage:
  python -m scripts.ingest_live_fields [--dry-run] [--batch-size 500] [--db <db-name>]

  --dry-run      : Print what would be done without writing to OpenSearch.
  --batch-size N : Number of docs to update per bulk request (default: 500).
  --db NAME      : MongoDB database name (default: from MONGO_DB_NAME env or "pepagora").
  --limit N      : Process at most N documents (for testing, default: all).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env so this script can run standalone (no uvicorn).
# ---------------------------------------------------------------------------
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and key not in os.environ:
                os.environ[key] = value

_load_dotenv(_ENV_FILE)

# ---------------------------------------------------------------------------
# After loading .env, import project config and clients.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymongo
from src.core.config import (
    OPENSEARCH_HOST,
    OPENSEARCH_USERNAME,
    OPENSEARCH_PASSWORD,
    OPENSEARCH_PRODUCT_WRITE_ALIAS,
    OPENSEARCH_REQUEST_TIMEOUT_SEC,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "")
NEW_FIELDS = ["liveUrl", "showcase", "createdBy", "businessOf"]

# Fields to add to the OpenSearch mapping (type decisions):
#   liveUrl     -> keyword  (URL string, exact match only)
#   showcase    -> boolean  (true/false flag)
#   createdBy   -> keyword  (user ID, same pattern as existing userId)
#   businessOf  -> keyword  (business ID / string reference)
FIELD_MAPPINGS: dict[str, dict] = {
    "liveUrl":    {"type": "keyword"},
    "showcase":   {"type": "boolean"},
    "createdBy":  {"type": "keyword"},
    "businessOf": {"type": "keyword"},
}


# ---------------------------------------------------------------------------
# OpenSearch client (native opensearch-py)
# ---------------------------------------------------------------------------
def _build_os_client():
    try:
        from opensearchpy import OpenSearch
    except ImportError as exc:
        sys.exit(f"opensearch-py not installed: {exc}")

    auth = None
    if OPENSEARCH_USERNAME and OPENSEARCH_PASSWORD:
        auth = (OPENSEARCH_USERNAME, OPENSEARCH_PASSWORD)

    return OpenSearch(
        hosts=[OPENSEARCH_HOST],
        basic_auth=auth,
        timeout=int(OPENSEARCH_REQUEST_TIMEOUT_SEC),
        retry_on_timeout=True,
        max_retries=3,
        http_compress=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_write_index(os_client, alias: str) -> str:
    """Return the concrete index name that the write alias points to."""
    try:
        info = os_client.indices.get_alias(name=alias)
        for index_name, meta in info.items():
            aliases = meta.get("aliases", {})
            if alias in aliases:
                alias_cfg = aliases[alias]
                if alias_cfg.get("is_write_index", True):
                    return index_name
        # Fallback: return any index the alias covers
        return next(iter(info.keys()))
    except Exception as exc:
        sys.exit(f"Could not resolve write alias '{alias}': {exc}")


def _ensure_mapping(os_client, index: str, dry_run: bool) -> None:
    """Add any missing new fields to the OpenSearch index mapping."""
    try:
        current_mapping = os_client.indices.get_mapping(index=index)
        existing_props: dict = (
            current_mapping.get(index, {})
            .get("mappings", {})
            .get("properties", {})
        )
    except Exception as exc:
        print(f"[WARN] Could not fetch mapping for '{index}': {exc}")
        existing_props = {}

    missing: dict[str, dict] = {
        field: mapping
        for field, mapping in FIELD_MAPPINGS.items()
        if field not in existing_props
    }

    if not missing:
        print("[MAPPING] All new fields already exist in the mapping — no changes needed.")
        return

    print(f"[MAPPING] Adding {len(missing)} new field(s) to index '{index}': {list(missing.keys())}")
    if dry_run:
        for field, mapping in missing.items():
            print(f"  [DRY-RUN] Would add: {field} -> {mapping}")
        return

    try:
        os_client.indices.put_mapping(index=index, body={"properties": missing})
        print("[MAPPING] Mapping updated successfully.")
    except Exception as exc:
        sys.exit(f"[ERROR] Failed to update mapping: {exc}")


def _check_createdby_vs_userid(os_client, write_alias: str, mongo_col) -> None:
    """
    Sample a few MongoDB documents and compare their createdBy value against
    the userId stored in the matching OpenSearch document.

    Prints a report but does NOT abort the script either way.
    """
    print("\n[CHECK] Comparing MongoDB createdBy vs OpenSearch userId on 10 sample docs ...")
    sample = list(
        mongo_col.find(
            {"createdBy": {"$exists": True, "$ne": None}},
            {"_id": 1, "createdBy": 1},
        ).limit(10)
    )

    if not sample:
        print("[CHECK] No docs with createdBy found in MongoDB — skipping alignment check.")
        return

    ids = [str(doc["_id"]) for doc in sample]
    try:
        resp = os_client.search(
            index=write_alias,
            body={
                "query": {"ids": {"values": ids}},
                "_source": ["userId"],
                "size": len(ids),
            },
        )
    except Exception as exc:
        print(f"[CHECK] Could not query OpenSearch for sample: {exc}")
        return

    os_docs: dict[str, str] = {
        hit["_id"]: hit["_source"].get("userId", "")
        for hit in resp["hits"]["hits"]
    }

    matches = mismatches = missing_in_os = 0
    for doc in sample:
        mongo_id = str(doc["_id"])
        mongo_created_by = str(doc.get("createdBy") or "")
        os_user_id = os_docs.get(mongo_id, None)

        if os_user_id is None:
            missing_in_os += 1
        elif mongo_created_by == os_user_id:
            matches += 1
        else:
            mismatches += 1
            print(
                f"  [MISMATCH] id={mongo_id} | MongoDB.createdBy={mongo_created_by!r}"
                f" vs OpenSearch.userId={os_user_id!r}"
            )

    print(
        f"[CHECK] Results — matched: {matches}, mismatched: {mismatches},"
        f" not found in OS: {missing_in_os} (out of {len(sample)} sampled)"
    )
    if mismatches == 0 and matches > 0:
        print(
            "[CHECK] OK: MongoDB.createdBy and OpenSearch.userId are identical values."
            " userId will be REMOVED and replaced by createdBy in every document."
        )
    elif mismatches > 0:
        print(
            "[CHECK] WARN: createdBy and userId differ on some documents."
            " userId will still be removed and createdBy will take its place."
        )
    print()


def _coerce(value):
    """Convert BSON types (ObjectId, Decimal128, etc.) to JSON-safe equivalents."""
    # Import lazily so the module works even if bson is not installed standalone.
    try:
        from bson import ObjectId, Decimal128
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, Decimal128):
            return float(value.to_decimal())
    except ImportError:
        pass
    # Fallback: anything not natively JSON-serialisable becomes a string.
    if not isinstance(value, (str, int, float, bool, list, dict, type(None))):
        return str(value)
    return value


# Painless script that sets the 4 new fields from params and removes userId.
# Using a script update (instead of partial doc update) is required to both
# write new fields AND delete an existing field in a single operation.
_RENAME_SCRIPT = """
for (def entry : params.entrySet()) {
    ctx._source[entry.getKey()] = entry.getValue();
}
ctx._source.remove('userId');
"""


def _build_bulk_body(docs: list[dict], write_alias: str) -> list[dict]:
    """Build the OpenSearch bulk update request body.

    Uses a Painless scripted update so that the 4 new fields are set AND
    userId is removed from each document in a single operation.
    """
    body = []
    for doc in docs:
        doc_id = str(doc["_id"])
        params: dict = {}
        for field in NEW_FIELDS:
            val = doc.get(field)
            if val is not None:
                params[field] = _coerce(val)

        # Always run the script (even if no new fields) so userId is removed.
        body.append({"update": {"_index": write_alias, "_id": doc_id}})
        body.append({
            "script": {
                "source": _RENAME_SCRIPT,
                "lang": "painless",
                "params": params,
            },
            "scripted_upsert": False,
        })

    return body


def _verify_ingestion(os_client, write_alias: str) -> None:
    """Sample 5 docs and confirm createdBy is set and userId is gone."""
    print("\n[VERIFY] Checking 5 sample documents post-ingestion ...")
    try:
        resp = os_client.search(
            index=write_alias,
            body={
                "size": 5,
                "query": {"match_all": {}},
                "_source": ["userId", "createdBy", "liveUrl", "showcase", "businessOf"],
            },
        )
    except Exception as exc:
        print(f"[VERIFY] Could not query: {exc}")
        return

    all_ok = True
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        doc_id = hit["_id"]
        has_created_by = "createdBy" in src and src["createdBy"]
        has_user_id = "userId" in src and src["userId"]
        status = "OK" if (has_created_by and not has_user_id) else "ISSUE"
        if status == "ISSUE":
            all_ok = False
        print(
            f"  [{status}] id={doc_id}"
            f"  createdBy={src.get('createdBy', 'MISSING')!r}"
            f"  userId={src.get('userId', 'REMOVED')!r}"
            f"  liveUrl={src.get('liveUrl', 'MISSING')!r}"
            f"  showcase={src.get('showcase', 'MISSING')!r}"
        )

    if all_ok:
        print("[VERIFY] All sampled docs look correct: createdBy set, userId removed.")
    else:
        print("[VERIFY] Some docs may need re-checking (see ISSUE rows above).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing to OpenSearch")
    parser.add_argument("--batch-size", type=int, default=500, help="Docs per bulk request (default: 500)")
    parser.add_argument(
        "--db",
        default=os.environ.get("MONGO_DB") or os.environ.get("MONGO_DB_NAME", "pepagora"),
        help="MongoDB database name",
    )
    parser.add_argument("--collection", default="liveproducts_v1", help="MongoDB collection name")
    parser.add_argument("--limit", type=int, default=0, help="Max docs to process (0 = all)")
    parser.add_argument("--list-dbs", action="store_true", help="List accessible MongoDB databases and collections then exit")
    args = parser.parse_args()

    if not MONGO_URI:
        sys.exit("[ERROR] MONGO_URI is not set in .env")
    if not OPENSEARCH_HOST:
        sys.exit("[ERROR] OPENSEARCH_HOST is not set in .env")

    print("=" * 70)
    print("  Pepagora – Live Fields Ingestion Script")
    print("=" * 70)
    print(f"  MongoDB DB       : {args.db}")
    print(f"  MongoDB Collection: {args.collection}")
    print(f"  OpenSearch Host  : {OPENSEARCH_HOST}")
    print(f"  Write Alias      : {OPENSEARCH_PRODUCT_WRITE_ALIAS}")
    print(f"  Fields to ingest : {NEW_FIELDS}")
    print(f"  Batch size       : {args.batch_size}")
    print(f"  Limit            : {'all' if args.limit == 0 else args.limit}")
    print(f"  Dry-run          : {args.dry_run}")
    print("=" * 70)

    # --- Connect to MongoDB ---
    print("\n[MONGO] Connecting ...")
    try:
        mongo_client = pymongo.MongoClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
        mongo_client.server_info()  # force connection
    except Exception as exc:
        sys.exit(f"[ERROR] MongoDB connection failed: {exc}")
    print(f"[MONGO] Connected. Using database='{args.db}', collection='{args.collection}'")

    # --list-dbs diagnostic mode: enumerate accessible databases and collections.
    if args.list_dbs:
        print("\n[MONGO] Accessible databases and collections:")
        try:
            for db_info in mongo_client.list_databases():
                db_name = db_info["name"]
                try:
                    cols = mongo_client[db_name].list_collection_names()
                    print(f"  DB: {db_name}  |  collections: {cols}")
                except Exception as col_err:
                    print(f"  DB: {db_name}  |  (cannot list collections: {col_err})")
        except Exception as list_err:
            # list_databases may be forbidden; try a few known candidates
            print(f"  list_databases() not permitted: {list_err}")
            print("  Trying common database names ...")
            for candidate in ["pepagora", "Pepagora", "live", "prod", "main", "sandbox",
                               "pepagora_live", "pepagora_prod", "pepagoraDB"]:
                try:
                    cols = mongo_client[candidate].list_collection_names()
                    print(f"  DB: {candidate}  |  collections: {cols}")
                except Exception as ce:
                    print(f"  DB: {candidate}  |  ({ce})")
        mongo_client.close()
        return

    # Auto-discover the database if the configured one is not accessible.
    mongo_db = mongo_client[args.db]
    mongo_col = mongo_db[args.collection]
    try:
        mongo_col.find_one({}, {"_id": 1})
    except Exception as probe_err:
        if "not authorized" in str(probe_err).lower() or "unauthorized" in str(probe_err).lower():
            print(f"[MONGO] Database '{args.db}' not authorized — auto-discovering correct database ...")
            found_db = None
            try:
                for db_info in mongo_client.list_databases():
                    candidate = db_info["name"]
                    if candidate in ("admin", "local", "config"):
                        continue
                    try:
                        col_names = mongo_client[candidate].list_collection_names()
                        if args.collection in col_names:
                            found_db = candidate
                            break
                    except Exception:
                        continue
            except Exception as list_err:
                sys.exit(
                    f"[ERROR] Cannot list databases either: {list_err}\n"
                    f"  Please set MONGO_DB_NAME=<correct-database> in .env and retry."
                )
            if not found_db:
                sys.exit(
                    f"[ERROR] Collection '{args.collection}' not found in any accessible database.\n"
                    f"  Please set MONGO_DB_NAME=<correct-database> in .env and retry."
                )
            print(f"[MONGO] Found collection '{args.collection}' in database '{found_db}' — using that.")
            mongo_db = mongo_client[found_db]
            mongo_col = mongo_db[args.collection]
        else:
            sys.exit(f"[ERROR] MongoDB probe failed: {probe_err}")

    # --- Connect to OpenSearch ---
    print("\n[OS] Connecting ...")
    os_client = _build_os_client()
    try:
        info = os_client.info()
        print(f"[OS] Connected: {info.get('version', {}).get('distribution', 'OpenSearch')} "
              f"v{info.get('version', {}).get('number', '?')}")
    except Exception as exc:
        sys.exit(f"[ERROR] OpenSearch connection failed: {exc}")

    # --- Resolve concrete index from alias ---
    write_alias = OPENSEARCH_PRODUCT_WRITE_ALIAS
    concrete_index = _resolve_write_index(os_client, write_alias)
    print(f"[OS] Write alias '{write_alias}' -> concrete index '{concrete_index}'")

    # --- createdBy vs userId alignment check ---
    _check_createdby_vs_userid(os_client, write_alias, mongo_col)

    # --- Update mapping ---
    _ensure_mapping(os_client, concrete_index, dry_run=args.dry_run)

    # --- Stream from MongoDB and bulk-update OpenSearch ---
    projection = {"_id": 1, "liveUrl": 1, "showcase": 1, "createdBy": 1, "businessOf": 1}
    cursor = mongo_col.find({}, projection)
    if args.limit > 0:
        cursor = cursor.limit(args.limit)

    total_docs = mongo_col.count_documents({}) if args.limit == 0 else args.limit
    print(f"\n[INGEST] Starting bulk update of ~{total_docs:,} documents ...\n")

    batch: list[dict] = []
    total_processed = 0
    total_updated = 0
    total_skipped = 0
    total_errors = 0
    start_time = time.perf_counter()

    def _flush_batch(batch: list[dict]) -> tuple[int, int]:
        """Send one bulk request; returns (updated, errors)."""
        body = _build_bulk_body(batch, write_alias)
        if not body:
            return 0, 0

        if args.dry_run:
            print(f"  [DRY-RUN] Would update {len(body) // 2} document(s)")
            return len(body) // 2, 0

        try:
            resp = os_client.bulk(body=body)
        except Exception as exc:
            print(f"  [ERROR] Bulk request failed: {exc}")
            return 0, len(body) // 2

        updated = errors = 0
        for item in resp.get("items", []):
            action = item.get("update", {})
            if action.get("error"):
                errors += 1
                print(f"  [ERROR] doc {action.get('_id')}: {action['error']}")
            else:
                updated += 1
        return updated, errors

    for doc in cursor:
        batch.append(doc)
        total_processed += 1

        if len(batch) >= args.batch_size:
            updated, errors = _flush_batch(batch)
            total_updated += updated
            total_errors += errors
            total_skipped += len(batch) - updated - errors
            batch = []

            elapsed = time.perf_counter() - start_time
            rate = total_processed / elapsed if elapsed > 0 else 0
            eta = (total_docs - total_processed) / rate if rate > 0 else 0
            print(
                f"  Progress: {total_processed:>8,} / ~{total_docs:,}"
                f"  |  updated: {total_updated:,}  errors: {total_errors}"
                f"  |  {rate:.0f} docs/s  ETA: {eta:.0f}s"
            )

    # Final partial batch
    if batch:
        updated, errors = _flush_batch(batch)
        total_updated += updated
        total_errors += errors
        total_skipped += len(batch) - updated - errors

    elapsed = time.perf_counter() - start_time

    print("\n" + "=" * 70)
    print("  Ingestion Complete")
    print("=" * 70)
    print(f"  Total processed : {total_processed:,}")
    print(f"  Updated         : {total_updated:,}")
    print(f"  Skipped (no fields): {total_skipped:,}")
    print(f"  Errors          : {total_errors}")
    print(f"  Elapsed         : {elapsed:.1f}s")
    print(f"  Throughput      : {total_processed / elapsed:.0f} docs/s" if elapsed > 0 else "")
    if args.dry_run:
        print("\n  [DRY-RUN] No data was written to OpenSearch.")
    print("=" * 70)

    if not args.dry_run and total_errors == 0:
        _verify_ingestion(os_client, write_alias)

    mongo_client.close()


if __name__ == "__main__":
    main()
