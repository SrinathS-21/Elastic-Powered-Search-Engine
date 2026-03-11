# Pepagora Elasticsearch Product Search — Complete Documentation

> **Project**: Pepagora B2B Product Search with Autocomplete  
> **Stack**: Elasticsearch 8.12.0 · Python · FastAPI · HTML/CSS/JS  
> **Dataset**: 100,000 B2B product records from `pepagoraDb.liveproducts.csv`

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Infrastructure — Docker Compose](#3-infrastructure--docker-compose)
4. [Data Pipeline — Jupyter Notebook](#4-data-pipeline--jupyter-notebook)
   - [Step 1 — Install Dependencies](#step-1--install-dependencies)
   - [Step 2 — Connect to Elasticsearch](#step-2--connect-to-elasticsearch)
   - [Step 3 — Load CSV Data](#step-3--load-csv-data)
   - [Step 4 — Detect & Fix Encoding Issues](#step-4--detect--fix-encoding-issues)
   - [Step 5 — Create Index with Optimal Mapping](#step-5--create-index-with-optimal-mapping)
   - [Step 5b — Re-index with Edge N-gram Support](#step-5b--re-index-with-edge-n-gram-support)
   - [Step 6 — Bulk Ingest 100k Documents](#step-6--bulk-ingest-100k-documents)
   - [Step 7 — Category Mapper: predict_category()](#step-7--category-mapper-predict_category)
5. [API Server — main.py Line-by-Line](#5-api-server--mainpy-line-by-line)
   - [Imports & Constants](#imports--constants)
   - [Elasticsearch Client](#elasticsearch-client)
   - [FastAPI App & Middleware](#fastapi-app--middleware)
   - [GET / — Serve UI](#get---serve-ui)
   - [GET /autocomplete — Autocomplete Suggestions](#get-autocomplete--autocomplete-suggestions)
   - [GET /search — Full-Text Search with Pagination](#get-search--full-text-search-with-pagination)
6. [Frontend — UI (index.html)](#6-frontend--ui-indexhtml)
7. [Index Mapping — Field-by-Field Explanation](#7-index-mapping--field-by-field-explanation)
8. [Analyzer Design — Why Three Analyzers](#8-analyzer-design--why-three-analyzers)
9. [Search Quality — Data-Driven Decisions](#9-search-quality--data-driven-decisions)
10. [Key Decisions & Trade-offs](#10-key-decisions--trade-offs)

---

## 1. Project Overview

Pepagora is a **B2B e-commerce platform** connecting manufacturers, suppliers and buyers across India. This project builds a **product search and autocomplete system** powered by Elasticsearch.

**What this system does:**
- Ingests 100,000 product records (names, descriptions, 3-level category hierarchy) into Elasticsearch
- Provides real-time autocomplete suggestions as users type (product names + category refinements)
- Delivers full-text search results with pagination, category filtering, and relevance ranking
- Runs a single-page web UI styled to match Pepagora's branding

**What this system does NOT do:**
- This is NOT a category mapper/classifier (no ML model) — it's a **search engine with category-aware ranking**
- The `predict_category()` function in Step 7 uses aggregation-based prediction (frequency counting), not machine learning

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     User's Browser                       │
│              ui/index.html (HTML/CSS/JS)                 │
│   Autocomplete dropdown  ←→  Full search results page    │
└──────────────┬───────────────────────────┬───────────────┘
               │ GET /autocomplete?q=      │ GET /search?q=&page=&category=
               ▼                           ▼
┌──────────────────────────────────────────────────────────┐
│                  FastAPI  (api/main.py)                   │
│              Port 8000 · uvicorn --reload                 │
│  ┌────────────┐  ┌─────────────┐  ┌───────────────────┐  │
│  │  GET /     │  │ /autocomplete│  │     /search       │  │
│  │ serve UI   │  │  msearch 2q  │  │ word-count based  │  │
│  └────────────┘  └──────┬──────┘  └────────┬──────────┘  │
│                         │                  │              │
│                         ▼                  ▼              │
│              elasticsearch-py (>=8.0, <9.0)               │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTP :9200
                           ▼
┌──────────────────────────────────────────────────────────┐
│           Elasticsearch 8.12.0 (Docker)                  │
│        Index: pepagora_products (100,000 docs)           │
│  Analyzers: product_english · product_autocomplete       │
└──────────────────────────────────────────────────────────┘
```

**Data flow:**
1. **Notebook** (`category_mapper.ipynb`) — reads CSV → cleans text → creates index → bulk-ingests 100k docs
2. **API** (`api/main.py`) — receives search queries from browser → queries ES → returns JSON
3. **UI** (`ui/index.html`) — takes user input → calls API → renders autocomplete dropdown and search results

---

## 3. Infrastructure — Docker Compose

**File:** `docker-compose.yml`

```yaml
version: '2.2'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.12.0
    container_name: es01
    environment:
      - discovery.type=single-node
      - "ES_JAVA_OPTS=-Xms512m -Xmx512m"
      - xpack.security.enabled=false
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data

volumes:
  es_data:
```

### Line-by-line explanation:

| Setting | Value | Why |
|---|---|---|
| `version: '2.2'` | Docker Compose file format | Compatible with most Docker Desktop versions |
| `image` | `elasticsearch:8.12.0` | Specific version pinned to avoid breaking changes across updates |
| `container_name: es01` | Fixed container name | Makes it easy to reference in logs and commands (`docker logs es01`) |
| `discovery.type=single-node` | Single-node mode | Disables cluster discovery — we only need one ES node for development |
| `ES_JAVA_OPTS=-Xms512m -Xmx512m` | JVM heap 512 MB min and max | Sets a reasonable memory limit for local development; prevents ES from consuming all system RAM |
| `xpack.security.enabled=false` | Disables authentication/TLS | Simplifies local development — no username/password or HTTPS certificates needed. **Not for production.** |
| `ports: "9200:9200"` | Maps container port 9200 to host | Standard ES REST API port; the Python client and FastAPI connect here |
| `volumes: es_data` | Named Docker volume | Persists indexed data across container restarts — you don't lose your 100k documents when restarting Docker |

### What was excluded and why:
- **Port 9300** — Not exposed. Port 9300 is for inter-node transport (cluster communication). Single-node doesn't need it.
- **Kibana** — Not included. We don't need Kibana's UI because we built our own search UI and use the Python client for all operations.
- **xpack.security** — Disabled. In production, this MUST be enabled with proper TLS certificates and authentication. Here it's off for local development simplicity.

---

## 4. Data Pipeline — Jupyter Notebook

**File:** `category_mapper.ipynb`

The notebook runs sequentially from Step 1 to Step 7. Each step builds on the previous one.

---

### Step 1 — Install Dependencies

**Cells:** 1 (markdown) + 1 (code)

```python
%pip install "elasticsearch>=8.0,<9.0" pandas ftfy tqdm --quiet
import importlib.metadata
print(f"elasticsearch version: {importlib.metadata.version('elasticsearch')}")
```

**What's installed:**

| Package | Version Constraint | Purpose |
|---|---|---|
| `elasticsearch` | `>=8.0,<9.0` | Official Python client for Elasticsearch |
| `pandas` | (latest) | CSV loading and DataFrame manipulation |
| `ftfy` | (latest) | "Fixes Text For You" — repairs broken/mojibake Unicode encoding |
| `tqdm` | (latest) | Progress bar for bulk ingest (100k records) |

**Critical: Why `>=8.0,<9.0` and NOT just `elasticsearch`**

The `elasticsearch-py` library version **9.x** sends a `compatible-with=9` HTTP header to the server. Elasticsearch **8.x** does not understand this header and **rejects requests with HTTP 400**. By pinning to `<9.0`, we ensure the client sends headers compatible with our ES 8.12.0 server.

**What's excluded:**
- `--quiet` flag suppresses pip's verbose output (keeps notebook clean)
- The `importlib.metadata` check confirms the installed version is actually in the 8.x range

---

### Step 2 — Connect to Elasticsearch

**Cells:** 1 (markdown) + 1 (code)

```python
from elasticsearch import Elasticsearch, ConnectionError as ESConnectionError

ES_HOST = "http://localhost:9200"

es = Elasticsearch(
    ES_HOST,
    request_timeout=10,
    retry_on_timeout=True,
)
```

**Parameter breakdown:**

| Parameter | Value | Why |
|---|---|---|
| `ES_HOST` | `http://localhost:9200` | Default ES port on the local Docker container |
| `request_timeout=10` | 10 seconds | Prevents indefinite hangs. 10s is generous for local development. |
| `retry_on_timeout=True` | Auto-retry on timeout | If ES is briefly slow (e.g., during garbage collection), the client retries instead of failing immediately |

**Verification logic:**
```python
info   = es.info()       # Gets cluster name + ES version
health = es.cluster.health()  # Gets cluster health (GREEN/YELLOW/RED)
```

The output confirms:
- Cluster is reachable
- ES version matches expected (8.12.0)
- Health status (GREEN = all shards allocated; YELLOW = replicas missing but functional)

**What was excluded:**
- `es.ping()` — Removed because `ping()` was deprecated in elasticsearch-py 8.x. We use `es.info()` directly instead, which both tests connectivity AND returns useful version info.
- HTTPS/authentication — Not needed since `xpack.security.enabled=false` in Docker config.

---

### Step 3 — Load CSV Data

**Cells:** 1 (markdown) + 1 (code)

```python
CSV_PATH = r"Dataset/pepagoraDb.liveproducts.csv"

df = pd.read_csv(
    CSV_PATH,
    encoding="utf-8",
    on_bad_lines="warn",
    dtype=str,
)

df.columns = [c.replace(".", "_") for c in df.columns]
```

**Parameter breakdown:**

| Parameter | Value | Why |
|---|---|---|
| `encoding="utf-8"` | UTF-8 | The CSV is UTF-8 encoded (no BOM detected). Explicit to prevent platform-specific defaults. |
| `on_bad_lines="warn"` | Warn on malformed rows | Some CSV rows may have unmatched quotes or extra commas. Instead of crashing, pandas skips the bad row and prints a warning. |
| `dtype=str` | Read all columns as strings | Prevents pandas from auto-detecting types. Without this, columns with mixed numeric/text values could cause `DtypeWarning` or lose data (e.g., leading zeros stripped from numbers read as int). |

**Column flattening:**
```python
df.columns = [c.replace(".", "_") for c in df.columns]
```

The CSV has dot-notation column names like `category.name`, `subCategory.name`, `productCategory.name`. Dots cause issues in Python attribute access and Elasticsearch field paths. Replacing `.` with `_` gives clean names like `category_name`, `subCategory_name`, `productCategory_name`.

**Dataset characteristics:**
- **100,000 rows** × multiple columns
- Columns after flattening: `productName`, `productDescription`, `category_name`, `subCategory_name`, `productCategory_name`, `productCategory__id`, `productCategory_uniqueId`
- ALL product names are 5–11 words long (zero short names like just "bat" or "pump")
- Names typically start with adjectives: "Premium" (17k), "Industrial" (34k), "Commercial" (3.8k)

**What's excluded:**
- No `index_col` — we don't use any CSV column as DataFrame index
- No `usecols` — we load all columns first, then select which ones to index in Step 6
- No `nrows` — we load the full 100k dataset (not a sample)

---

### Step 4 — Detect & Fix Encoding Issues

**Cells:** 1 (markdown) + 2 (code)

**The problem:** The CSV data contains **mojibake** — text that was encoded in one character set (UTF-8) but read as another (Latin-1/Windows-1252). Common symptoms:

| Broken text | Cause | Should be |
|---|---|---|
| `Â°C` | `°` (degree sign, U+00B0) double-encoded | `°C` |
| `â€™` | Right single quote `'` (U+2019) mojibake | `'` |
| `â€œ` / `â€\x9d` | Curly double quotes `"` / `"` mojibake | `"` / `"` |

**Fix using `ftfy`:**
```python
import ftfy

def clean_text(value: str) -> str:
    if not isinstance(value, str):
        return value
    fixed = ftfy.fix_text(value)
    fixed = fixed.translate(QUOTE_MAP)
    return fixed.strip()
```

**Two-phase cleaning:**
1. **`ftfy.fix_text(value)`** — The `ftfy` library detects encoding errors by pattern-matching known mojibake sequences and reverses them back to the original Unicode characters. It handles dozens of encoding scenarios automatically.
2. **`QUOTE_MAP` translation** — After ftfy repairs mojibake, some valid-but-problematic Unicode characters remain:

```python
QUOTE_MAP = str.maketrans({
    "\u2018": "'",   # LEFT  SINGLE QUOTATION MARK  ' → '
    "\u2019": "'",   # RIGHT SINGLE QUOTATION MARK  ' → '
    "\u201c": '"',   # LEFT  DOUBLE QUOTATION MARK  " → "
    "\u201d": '"',   # RIGHT DOUBLE QUOTATION MARK  " → "
    "\u2013": "-",   # EN DASH  – → -
    "\u2014": "-",   # EM DASH  — → -
    "\u00b0": "°",   # DEGREE SIGN — keep as-is (valid UTF-8)
})
```

**Why normalise smart quotes?** Elasticsearch's standard tokenizer treats `'` (U+2019) differently from `'` (ASCII 0x27). If a product name contains `isn't` with a curly apostrophe but the user types `isn't` with a straight one, the tokens won't match. Normalising to ASCII ensures consistent tokenisation.

**Columns cleaned:** `productName`, `productDescription`, `category_name`, `subCategory_name`, `productCategory_name`

**Verification (second code cell):**
```python
remaining = df[TEXT_COLUMNS].apply(
    lambda col: col.str.contains(r"Â|â€|Ã", regex=True, na=False)
).any(axis=1).sum()
```
This scans all text columns for residual mojibake patterns. Expected result: **0 rows** with issues remaining.

**What's excluded:**
- **HTML entity decoding** — Not needed; the CSV data doesn't contain HTML entities like `&amp;` or `&#39;`
- **Emoji handling** — B2B product data doesn't contain emojis
- **Lowercasing** — NOT done here. Elasticsearch's analyzers handle case-normalisation at index/query time. Doing it here would lose original casing in the stored `_source`.

---

### Step 5 — Create Index with Optimal Mapping

**Cells:** 1 (markdown) + 1 (code)

This is the most critical step — the index mapping defines HOW Elasticsearch stores, analyzes, and searches the data.

```python
INDEX_NAME = "pepagora_products"
```

#### Settings

```python
"settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "refresh_interval": "1s",
    "analysis": { ... }
}
```

| Setting | Value | Why |
|---|---|---|
| `number_of_shards: 1` | Single shard | 100k documents is small — a single shard handles this easily. Multiple shards only help for very large datasets (millions+) or multi-node clusters. More shards = more overhead. |
| `number_of_replicas: 0` | No replicas | Single-node cluster — there's nowhere to place replicas. Setting 0 avoids a YELLOW health status (unassigned replicas). |
| `refresh_interval: "1s"` | 1 second | Default. Changed to `-1` during bulk ingest (Step 6) for speed, then restored to `1s` after. |

#### Custom Filters

```python
"filter": {
    "english_stop": {
        "type": "stop",
        "stopwords": "_english_"
    },
    "english_stemmer": {
        "type": "stemmer",
        "language": "english"
    },
    "edge_ngram_filter": {
        "type": "edge_ngram",
        "min_gram": 2,
        "max_gram": 20,
        "token_chars": ["letter", "digit"]
    }
}
```

| Filter | What it does | Example |
|---|---|---|
| `english_stop` | Removes common English words | "the", "is", "and", "of" are removed — they add noise, not signal |
| `english_stemmer` | Reduces words to root form | "pumps" → "pump", "running" → "run", "batteries" → "batteri" |
| `edge_ngram_filter` | Generates prefix substrings from each token | "bath" → ["ba", "bat", "bath"] — enables "search-as-you-type" |

**edge_ngram parameters:**
- `min_gram: 2` — Minimum 2 characters. Single-letter prefixes ("b") would match too broadly.
- `max_gram: 20` — Maximum 20 characters. Covers even the longest product words.
- `token_chars: ["letter", "digit"]` — Only generate ngrams from letters and digits (not punctuation).

#### Custom Analyzers

```python
"analyzer": {
    "product_english": {
        "type": "custom",
        "tokenizer": "standard",
        "filter": ["lowercase", "english_stop", "english_stemmer"]
    },
    "product_autocomplete": {
        "type": "custom",
        "tokenizer": "standard",
        "filter": ["lowercase", "edge_ngram_filter"]
    }
}
```

**`product_english`** — Used for the main `productName` and `productDescription` fields:
1. `standard` tokenizer: splits text on whitespace and punctuation → ["Premium", "Stainless", "Steel", "Pipe"]
2. `lowercase`: → ["premium", "stainless", "steel", "pipe"]
3. `english_stop`: removes "the", "and", etc.
4. `english_stemmer`: "pumps" → "pump", "stainless" → "stainless"

**`product_autocomplete`** — Used for the `productName.ngram` sub-field:
1. `standard` tokenizer: splits on whitespace
2. `lowercase`: case-insensitive
3. `edge_ngram_filter`: expands each token into prefix substrings

**Why NO stop words or stemming in autocomplete analyzer?**
The autocomplete analyzer is for "search-as-you-type" prefix matching. Stemming would mangle prefixes (e.g., "bath" stemmed to "bath" is fine, but "batting" stemmed to "bat" would break prefix logic). Stop words are kept because a user might type "the" as part of their query and expect autocomplete to match it.

#### Field Mapping

See [Section 7 — Index Mapping Field-by-Field](#7-index-mapping--field-by-field-explanation) for the complete breakdown of every field and sub-field.

#### Index Creation Logic

```python
if es.indices.exists(index=INDEX_NAME):
    es.indices.delete(index=INDEX_NAME)
es.indices.create(index=INDEX_NAME, **INDEX_CONFIG)
```

Delete-then-create ensures a clean slate. The `**INDEX_CONFIG` unpacks the settings+mappings dict into the `create()` call.

**What's excluded from the index:**
- **`productCategory__id`** — Internal database ID. Not useful for search or display.
- **`productCategory_uniqueId`** — Another internal ID. Excluded to keep the index lean.

---

### Step 5b — Re-index with Edge N-gram Support

**Cells:** 1 (markdown) + 1 (code)

This step exists because edge-ngram support was added AFTER the initial index creation. It drops the existing index and recreates it with the same `INDEX_CONFIG` (which now includes the edge-ngram analyzer and `.ngram` sub-field).

```python
if es.indices.exists(index=INDEX_NAME):
    es.indices.delete(index=INDEX_NAME)
es.indices.create(index=INDEX_NAME, **INDEX_CONFIG)
```

**Verification:** Tests the `product_autocomplete` analyzer directly:
```python
analyze_resp = es.indices.analyze(
    index=INDEX_NAME,
    body={"analyzer": "product_autocomplete", "text": "bath towels"}
)
tokens = [t['token'] for t in analyze_resp['tokens']]
```

**Expected output:**
```
Edge-ngram tokens for "bath towels": ['ba', 'bat', 'bath', 'to', 'tow', 'towe', 'towel', 'towels']
```

This confirms the asymmetric analyzer design:
- **At index time** (`product_autocomplete`): "bath towels" → 8 tokens including "ba", "bat", "bath", etc.
- **At search time** (`standard`): "bat" → just ["bat"] — this single token matches against the indexed "bat" ngram token

**Why a separate step?** In Elasticsearch, you **cannot change index settings or analyzers** on an existing index. The only way to add a new analyzer or field mapping is to delete and recreate the index, then re-ingest all documents. That's why Step 5b says "Now re-run Step 6."

---

### Step 6 — Bulk Ingest 100k Documents

**Cells:** 1 (markdown) + 1 (code)

This step pushes all 100,000 cleaned records from the pandas DataFrame into the Elasticsearch index.

#### Performance Optimisation

```python
es.indices.put_settings(
    index=INDEX_NAME,
    body={"index": {"refresh_interval": "-1"}}
)
```

**Why `-1`?** By default, ES refreshes every 1 second — making new documents searchable. During bulk ingest, this refresh wastes CPU and I/O because we don't need to search mid-ingest. Setting `refresh_interval: -1` disables auto-refresh entirely until ingest is complete.

#### Document Generation

```python
FIELDS = [
    "productName", "productDescription",
    "category_name", "subCategory_name", "productCategory_name",
]
```

Only these 5 fields are indexed. The CSV has other columns (`productCategory__id`, `productCategory_uniqueId`) that we deliberately skip — they're internal IDs not useful for search.

**Null handling:**

```python
NULL_SENTINEL = {"nan", "none", "null", "n/a", "na", ""}

def null_mask(val):
    return not isinstance(val, str) or val.strip().lower() in NULL_SENTINEL
```

CSV files often represent null values as literal strings like "nan", "None", "N/A", or empty strings. The `null_mask()` function catches all these variants and converts them to `None` (Elasticsearch null) instead of indexing the string "nan" as a searchable term.

**Action generator:**

```python
def make_actions(dataframe):
    for _, row in dataframe.iterrows():
        doc = {}
        for field in FIELDS:
            val = row.get(field)
            doc[field] = None if null_mask(val) else val.strip()
        yield {
            "_index": INDEX_NAME,
            "_source": doc,
        }
```

This is a **generator function** (uses `yield`). It produces one document at a time, keeping memory usage low. Each document is a dict with `_index` (target index) and `_source` (the document body).

#### Bulk Ingest with Progress

```python
for ok, result in streaming_bulk(
    es,
    make_actions(df),
    chunk_size=CHUNK_SIZE,   # 500 docs per HTTP request
    raise_on_error=False,    # don't crash on individual doc failures
):
```

`streaming_bulk` from the elasticsearch-py helpers module:
- Batches documents into chunks of 500
- Sends each chunk as a single `_bulk` HTTP request (much faster than individual index calls)
- `raise_on_error=False` — if one document fails (e.g., mapping error), continue ingesting the rest instead of stopping
- Returns `(ok, result)` tuples — `ok` is True/False for each doc

**`tqdm` progress bar:**
```python
with tqdm(total=total_rows, desc="Indexing", unit="doc") as pbar:
```
Shows a live counter: `Indexing: 57,000/100,000 [00:24<00:17, 2,450.23doc/s]`

#### Post-Ingest Restoration

```python
es.indices.put_settings(
    index=INDEX_NAME,
    body={"index": {"refresh_interval": "1s"}}
)
es.indices.refresh(index=INDEX_NAME)
es.indices.forcemerge(index=INDEX_NAME, max_num_segments=1)
```

| Operation | Purpose |
|---|---|
| `refresh_interval: "1s"` | Restore normal auto-refresh for search queries |
| `refresh()` | Force an immediate refresh — makes ALL 100k docs searchable right now |
| `forcemerge(max_num_segments=1)` | Merges all Lucene segments into one. Improves search speed because ES reads a single optimised segment instead of many small ones. Only safe on a read-heavy index (which ours is — we don't add documents after this step). |

**Result:** `100,000/100,000, 0 failed, ~41.7 seconds`

---

### Step 7 — Category Mapper: predict_category()

**Cells:** 1 (markdown) + 1 (code)

This function takes a product name and predicts which category hierarchy it belongs to by analysing the top search results.

#### How it works

1. **Query** ES with a 4-tier relevance search (same tiers used in autocomplete)
2. **Aggregate** the top-N results using a `sampler` aggregation
3. **Count** how many of those top docs belong to each category value
4. **Return** the most frequent category at each level with a confidence percentage

#### The Query

```python
resp = es.search(
    index=INDEX_NAME,
    size=0,   # we only need aggregations, not actual documents
    query={
        "bool": {
            "should": [
                {"match_phrase_prefix": {"productName": {"query": q, "boost": 5.0}}},
                {"match": {"productName.ngram": {"query": q, "operator": "and", "boost": 3.0}}},
                {"match": {"productName.ngram": {"query": q, "operator": "or",  "boost": 1.0}}},
                {"match": {"productName":       {"query": q, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.5}}},
                {"match": {"productDescription":{"query": q, "operator": "or",   "boost": 0.3}}},
            ],
            "minimum_should_match": 1,
        }
    },
    aggs={...}
)
```

**`size=0`** — We don't need the actual hit documents returned. We only care about the aggregation results. This makes the query faster.

**4-tier query logic** (highest to lowest priority):

| Tier | Clause | Boost | What it matches |
|---|---|---|---|
| ① | `match_phrase_prefix` on `productName` | 5.0 | Exact word-order prefix: "bath tow" → "bath towels cotton premium" |
| ② | `match` on `productName.ngram` AND | 3.0 | All query tokens exist as ngram prefixes (any order) |
| ③ | `match` on `productName.ngram` OR | 1.0 | At least one query token matches an ngram prefix |
| ④ | `match` on `productName` with fuzziness | 0.5 | Typo-tolerant: "bycicle" → "bicycle" (edit distance AUTO) |
| ⑤ | `match` on `productDescription` OR | 0.3 | Fallback: word appears in description but not name |

**`minimum_should_match: 1`** — At least one of the 5 `should` clauses must match. This ensures we get relevant results while still allowing broad recall.

#### The Aggregation

```python
aggs={
    "sample": {
        "sampler": {"shard_size": top_n},   # default top_n=50
        "aggs": {
            "by_category":         {"terms": {"field": "category_name",        "size": 10}},
            "by_sub_category":     {"terms": {"field": "subCategory_name",     "size": 10}},
            "by_product_category": {"terms": {"field": "productCategory_name", "size": 10}},
        }
    }
}
```

**`sampler`** — Limits aggregation to the top-N most relevant documents (by score). Without this, aggregations would run over ALL matching documents (could be 10,000+), which dilutes the signal. With `shard_size=50`, only the top 50 most relevant docs are considered.

**`terms` aggregation** — Counts documents per unique category value. `size: 10` returns the top 10 most frequent values.

#### Confidence Calculation

```python
def parse_buckets(agg_key):
    buckets      = sample[agg_key]["buckets"]
    sum_other    = sample[agg_key].get("sum_other_doc_count", 0)
    total_in_agg = sum(b["doc_count"] for b in buckets) + sum_other
    if not buckets or total_in_agg == 0:
        return None, 0.0, 0, []
    top            = buckets[0]
    confidence_pct = round(top["doc_count"] / total_in_agg * 100, 1)
    ...
```

**`total_in_agg`** — Computed as `sum(all returned bucket counts) + sum_other_doc_count`. This is the TRUE total number of documents in the aggregation scope. Using this as the denominator (instead of `min(top_n, total_hits)`) prevents confidence percentages from exceeding 100%.

**Why `sum_other_doc_count`?** When `terms` returns `size: 10` buckets, there may be additional category values beyond the top 10. ES reports their combined count in `sum_other_doc_count`. We include this to get an accurate denominator.

**Example output:**
```
Product   : bath towels
Hits      : 1,945 (sampled 50)
Category        : Textiles & Fabrics  (68.0%)
Sub-Category    : Bath Linens & Towels  (52.0%)
Product Category: Bath Towels  (44.0%)
```

---

## 5. API Server — main.py Line-by-Line

**File:** `api/main.py` (372 lines)

### Imports & Constants

```python
"""
Pepagora Autocomplete API
Serves autocomplete suggestions from Elasticsearch and static UI files.

Endpoints:
  GET /                        → serves ui/index.html
  GET /autocomplete?q=<query>  → returns category + product suggestions
  GET /search?q=<query>&page=1 → returns full paginated search results
"""

from __future__ import annotations
```

`from __future__ import annotations` — Enables PEP 604 style type hints (`str | None` instead of `Optional[str]`) and deferred evaluation. Used for Python 3.9 compatibility.

```python
import os
from pathlib import Path
from typing import Optional

from elasticsearch import Elasticsearch
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
```

| Import | Purpose |
|---|---|
| `os` | Read environment variables (`ES_HOST`, `ES_INDEX`) |
| `Path` | File path manipulation (locating `ui/` directory) |
| `Optional` | Type hint for nullable query parameters |
| `Elasticsearch` | Python client for ES |
| `FastAPI` | Web framework for the API |
| `Query` | Parameter validation (min_length, ge, etc.) |
| `CORSMiddleware` | Cross-Origin Resource Sharing headers |
| `FileResponse` | Serve an HTML file directly |
| `StaticFiles` | Mount a directory for static file serving |

```python
ES_HOST    = os.getenv("ES_HOST", "http://localhost:9200")
INDEX_NAME = os.getenv("ES_INDEX", "pepagora_products")
UI_DIR     = Path(__file__).parent.parent / "ui"
PAGE_SIZE  = 20
```

| Constant | Default | Purpose |
|---|---|---|
| `ES_HOST` | `http://localhost:9200` | Configurable via environment variable. Allows switching to a remote ES cluster without code changes. |
| `INDEX_NAME` | `pepagora_products` | Index name, also configurable via `ES_INDEX` env var. |
| `UI_DIR` | `../ui` relative to main.py | Path to the UI directory. `Path(__file__).parent.parent` goes up from `api/` to the project root, then into `ui/`. |
| `PAGE_SIZE` | `20` | Number of results per page in `/search`. |

### Elasticsearch Client

```python
es = Elasticsearch(ES_HOST, request_timeout=10, retry_on_timeout=True)
```

Same client configuration as the notebook. Created once at module level — reused across all requests (connection pooling built into the client).

### FastAPI App & Middleware

```python
app = FastAPI(title="Pepagora Autocomplete", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)
```

**CORS middleware** — The browser enforces Same-Origin Policy. When the UI is served from `http://localhost:5500` (VS Code Live Server) but the API runs on `http://localhost:8000`, the browser blocks API calls unless the server sends proper CORS headers.

| Parameter | Value | Why |
|---|---|---|
| `allow_origins=["*"]` | Allow all origins | Development convenience. In production, restrict to specific domains. |
| `allow_methods=["GET"]` | Only GET allowed | All our endpoints are GET. No POST/PUT/DELETE needed. This limits the attack surface. |
| `allow_headers=["*"]` | Allow all headers | Needed for browsers that send custom headers |

```python
if UI_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")
```

Mounts the `ui/` folder at `/ui/` URL path. Any file in the UI directory becomes accessible at `http://localhost:8000/ui/<filename>`. The `if UI_DIR.exists()` guard prevents a crash if the UI directory doesn't exist.

### GET / — Serve UI

```python
@app.get("/", include_in_schema=False)
def serve_ui():
    """Serve the main search UI."""
    return FileResponse(str(UI_DIR / "index.html"))
```

- `include_in_schema=False` — Hides this endpoint from the auto-generated OpenAPI docs (`/docs`). It's a UI route, not an API endpoint.
- Returns the HTML file directly with proper `Content-Type: text/html` headers.
- This means navigating to `http://localhost:8000/` shows the search UI.

### GET /autocomplete — Autocomplete Suggestions

```python
@app.get("/autocomplete")
def autocomplete(q: str = Query(default="", min_length=0)):
```

**Parameters:**
- `q` — The search query string. `min_length=0` allows empty strings (we handle them manually below).

**Early return for short queries:**
```python
q = q.strip()
if len(q) < 2:
    return {
        "query": q,
        "categories": [], "sub_categories": [], "product_categories": [],
        "products": [], "total": 0,
    }
```

Queries shorter than 2 characters return empty results. Typing a single character would match too broadly and waste ES resources.

#### msearch: Two Queries in One HTTP Call

The autocomplete endpoint uses Elasticsearch's `_msearch` API to send **two queries in a single HTTP request**:

**Query A — Category Name Aggregations (lines 95-122)**

```python
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
        "subcat_filter": { ... },   # same pattern for subCategory_name
        "prodcat_filter": { ... },  # same pattern for productCategory_name
    },
}
```

**How this works:**

1. **`filter` aggregation** with `match_phrase_prefix` — Narrows the document scope to only those where the category name TEXT field starts with the query. For example, `q="bat"` matches documents in "Bath & Body Care" category because the `.text` sub-field's standard analyzer produces the token "bath" which starts with "bat".

2. **`terms` aggregation** with `size: 1` — From the filtered documents, find the single most common category keyword value.

**Why `match_phrase_prefix` and not `match`?** Autocomplete happens while the user is still typing. The user types "bat" intending to type "bath towels". `match_phrase_prefix` treats the last word as a prefix, so "bat" matches "bath", "bathroom", "batting" etc. This is correct behaviour for autocomplete.

**Why `.text` sub-field instead of the `keyword` field?** The keyword field stores the exact string "Bath & Body Care". A prefix search on keyword would require it to START with "bat" (it starts with "B"). The `.text` sub-field uses the `standard` analyzer which tokenises: ["bath", "body", "care"] — and `match_phrase_prefix` can match the "bath" token starting with "bat".

**Query B — Product Name Suggestions (lines 124-159)**

```python
{
    "size": 5,
    "query": {
        "bool": {
            "should": [
                {"match_phrase_prefix": {"productName": {"query": q, "boost": 5.0}}},
                {"match": {"productName.ngram": {"query": q, "operator": "and", "boost": 3.0}}},
                {"match": {"productName.ngram": {"query": q, "operator": "or",  "boost": 1.0}}},
                {"match": {"productName":       {"query": q, "fuzziness": "AUTO", "prefix_length": 1, "boost": 0.5}}},
            ],
            "minimum_should_match": 1,
        }
    },
    "_source": ["productName", "category_name", "subCategory_name"],
}
```

**4-tier ranking strategy** (same logic as predict_category Step 7):

| Tier | Type | Boost | Behaviour |
|---|---|---|---|
| 1 | `match_phrase_prefix` on `productName` | 5.0 | Exact word-order prefix match. "steel pi" matches "steel pipe fittings". Highest relevance. |
| 2 | `match` on `productName.ngram` with AND | 3.0 | All query tokens must appear as edge-ngram prefixes. Order doesn't matter. "pipe steel" matches "steel pipe". |
| 3 | `match` on `productName.ngram` with OR | 1.0 | Any query token appears as ngram prefix. Broadest recall for partial matches. |
| 4 | `match` on `productName` with fuzziness | 0.5 | Typo correction: "stel pipe" → "steel pipe". Edit distance `AUTO` = 0 edits for 1-2 char words, 1 edit for 3-5 chars, 2 edits for 6+ chars. |

**`size: 5`** — Only return top 5 product suggestions (shown in the dropdown).

**`_source`** — Only fetch these 3 fields. We don't need `productDescription` or scores in the dropdown, so we skip them to reduce response size.

**Why `match_phrase_prefix` is correct for autocomplete but WRONG for full search:**
- In autocomplete, the user is actively typing. "bat" is a prefix of what they intend to type ("bath towels"). `match_phrase_prefix` correctly matches this.
- In full search (when user presses Enter), the query is complete. "bat" means the user wants "bat" (cricket bat, baseball bat), NOT "bath". That's why `/search` uses a different strategy (see below).

#### Response Assembly

```python
responses = es.msearch(body=msearch_body)["responses"]
agg_resp, prod_resp = responses[0], responses[1]
```

The `msearch` response is an array of responses matching the queries in order. `responses[0]` corresponds to Query A (aggregations), `responses[1]` to Query B (products).

```python
def top_bucket(agg_key: str) -> list[str]:
    buckets = agg_resp["aggregations"][agg_key]["top"]["buckets"]
    return [b["key"] for b in buckets]
```

Extracts the category name string(s) from each aggregation's buckets.

**Return shape:**
```json
{
  "query": "bat",
  "categories": ["Bath & Body Care"],
  "sub_categories": ["Bath Linens & Towels"],
  "product_categories": ["Bath Towels"],
  "products": [
    {"name": "Premium Bath Towels Cotton Set", "category": "Textiles", "sub_category": "Bath Linens"}
  ],
  "total": 1945
}
```

### GET /search — Full-Text Search with Pagination

```python
@app.get("/search")
def search(
    q:        str           = Query(default="", min_length=1),
    page:     int           = Query(default=1, ge=1),
    category: Optional[str] = Query(default=None),
):
```

**Parameters:**

| Parameter | Type | Validation | Purpose |
|---|---|---|---|
| `q` | str | `min_length=1` | Search query (required, at least 1 char) |
| `page` | int | `ge=1` | Page number (1-indexed, minimum 1) |
| `category` | Optional[str] | None | Category filter — when set, only show products in this category |

**Pagination offset:**
```python
from_offset = (page - 1) * PAGE_SIZE
```
ES uses 0-based `from` offset. Page 1 → offset 0, Page 2 → offset 20, Page 3 → offset 40, etc.

**Word count detection:**
```python
words      = q.split()
word_count = len(words)
```
The search strategy changes based on how many words the user typed. This was a **data-driven decision** — see [Section 9](#9-search-quality--data-driven-decisions) for the full analysis.

#### Strategy: 1 Word (e.g., "bat", "pump", "led")

```python
must_clause = [
    {"match": {"productName": {"query": q, "operator": "and"}}}
]
should_clause = [
    {"match": {"productName": {"query": q, "operator": "and", "boost": 5.0}}},
    {"match": {"productName": {
        "query": q, "fuzziness": "AUTO", "prefix_length": 2, "boost": 0.3
    }}},
]
```

**`must` clause — Exact token match only:**
- Uses `product_english` analyzer → "bat" stays as "bat" (stemmed)
- `operator: "and"` means ALL query tokens must appear (only 1 token here, so it means the product must contain "bat")
- The stemmer correctly separates: bat→bat, bats→bat, batting→bat (MATCH) vs. battery→batteri, bathroom→bathroom (NO MATCH)

**What's NOT in the `must` clause and WHY:**
- **NO `match_phrase_prefix`** — Would match "bat*" as a prefix → "bath", "bathroom", "battery" all match. This caused the original "bat showing bath results" bug.
- **NO ngram** — The ngram field indexes "bathroom" as ["ba", "bat", "bath", "bathr", ...]. Searching for "bat" on the ngram field would match "bathroom" (because "bat" is one of its ngram tokens). Data analysis showed that for "bat", ngram adds **1,333 noise documents** (bath, battery, batting) but only 38 exact matches. For "pump", ngram adds only 25 noise docs. Since short queries are most vulnerable, ngram is excluded entirely from `must` for 1-word queries.

**`should` clause — Ranking bonuses:**
- Duplicate `match AND` with boost=5.0 — Reinforces IDF scoring for exact matches
- `fuzziness: "AUTO"` — Catches typos. `AUTO` means: 0 edits for 1-2 char words, 1 edit for 3-5 chars, 2 edits for 6+ chars
- `prefix_length: 2` — First 2 characters must match exactly (prevents "bat" fuzzy-matching "hat")
- `boost: 0.3` — Low boost because fuzzy matches are less precise

#### Strategy: 2 Words (e.g., "cricket bat", "steel pipe")

```python
must_clause = [
    {"match": {"productName": {"query": q, "operator": "and"}}}
]
should_clause = [
    {"match_phrase": {"productName": {"query": q, "boost": 8.0}}},
    {"match_phrase": {"productName": {"query": q, "slop": 1, "boost": 5.0}}},
    {"match": {"productName.ngram": {"query": q, "operator": "and", "boost": 2.0}}},
    {"match": {"productName": {
        "query": q, "fuzziness": "AUTO", "prefix_length": 2, "boost": 0.3
    }}},
]
```

**`must` clause:** Both words must appear in the product name (AND operator). "cricket bat" → product must contain both "cricket" AND "bat" tokens.

**`should` clause — Ranking tiers:**

| Clause | Boost | What it rewards |
|---|---|---|
| `match_phrase` exact | 8.0 | "cricket bat" appears as an exact phrase → highest rank |
| `match_phrase` slop=1 | 5.0 | Words can be 1 position apart: "cricket mini bat" or reversed "bat cricket" still ranks high |
| `match` ngram AND | 2.0 | Both words exist as ngram prefixes — catches partial word matches. **Safe for 2-word queries**: data analysis confirmed zero noise for multi-word ngram queries |
| `match` fuzzy | 0.3 | Typo tolerance as fallback |

#### Strategy: 3+ Words (e.g., "hydraulic pump valve", "stainless steel pipe fittings")

```python
must_clause = [
    {"match": {
        "productName": {
            "query": q, "operator": "or",
            "minimum_should_match": "75%"
        }
    }}
]
should_clause = [
    {"match":        {"productName": {"query": q, "operator": "and", "boost": 10.0}}},
    {"match_phrase": {"productName": {"query": q, "slop": 2, "boost": 8.0}}},
    {"match": {"productName.ngram": {"query": q, "operator": "and", "boost": 2.0}}},
    {"match": {"productName": {
        "query": q, "fuzziness": "AUTO", "prefix_length": 2, "boost": 0.3
    }}},
]
```

**`must` clause — 75% threshold:**
- `operator: "or"` means ANY word can match, BUT
- `minimum_should_match: "75%"` means at least 75% of words must be present
- For 3 words → at least 3 (ceil of 2.25), for 4 words → at least 3, for 5 words → at least 4
- **Why not 100%?** The `product_english` analyzer strips stop words. "stainless steel pipe in kitchen" → the analyzer drops "in", leaving 4 tokens. If the user types 5 words but ES only sees 4 after analysis, requiring 100% match would fail. 75% provides tolerance.

**`should` clause — Ranking tiers:**

| Clause | Boost | Purpose |
|---|---|---|
| `match` AND | 10.0 | ALL words present = strongest possible signal |
| `match_phrase` slop=2 | 8.0 | Words appear in roughly the right order (allows 2 position swaps) |
| `match` ngram AND | 2.0 | Catches partial word matches (safe at 3+ words — zero data noise) |
| `match` fuzzy | 0.3 | Typo tolerance |

#### Category Boost (All Strategies)

```python
category_boost = [
    {"match": {"category_name.text":        {"query": q, "boost": 1.5}}},
    {"match": {"subCategory_name.text":     {"query": q, "boost": 1.5}}},
    {"match": {"productCategory_name.text": {"query": q, "boost": 1.5}}},
]
```

Always added to `should`. If the search query matches a category name, products in that category get a ranking boost. Example: searching "sanitaryware" boosts products in the "Sanitaryware & Bathroom Fittings" category even if the word "sanitaryware" doesn't appear in the product name.

**Why `boost: 1.5` (not higher)?** Category matches are a soft signal. The primary signal should be the product name match. Too high a category boost would surface irrelevant products just because they're in a matching category.

#### Description Fallback

```python
desc_boost = [
    {"match": {"productDescription": {"query": q, "operator": "or", "boost": 0.2}}}
]
```

Very low boost (0.2). If a product's description mentions the query terms but the name doesn't, this gives it a small ranking nudge. "operator": "or" means any word match is enough — descriptions are long and a full AND match would be too strict.

#### Category Filter

```python
**({"filter": [
    {"bool": {"should": [
        {"term": {"category_name":        category}},
        {"term": {"subCategory_name":     category}},
        {"term": {"productCategory_name": category}},
    ], "minimum_should_match": 1}}
]} if category else {}),
```

When the `category` query parameter is provided:
- Adds a `filter` clause to the `bool` query
- Uses `term` queries on keyword fields (exact match, not analyzed)
- Checks all 3 category levels — the user might click on a category, sub-category, or product category
- `minimum_should_match: 1` — the product must match on at least one level
- `**({...} if category else {})` — Python dict unpacking trick: adds the `filter` key only when category is provided

**Why `term` and not `match`?** Category names like "Industrial Equipment & Machinery" must match exactly. `match` would tokenize and stem, potentially matching partial category names.

#### Response Building

```python
total = resp["hits"]["total"]["value"]
hits = [
    {
        "id":                   h["_id"],
        "productName":          h["_source"].get("productName", ""),
        "productDescription":   h["_source"].get("productDescription", "")[:200],
        "category_name":        h["_source"].get("category_name", ""),
        "subCategory_name":     h["_source"].get("subCategory_name", ""),
        "productCategory_name": h["_source"].get("productCategory_name", ""),
        "score":                round(h["_score"], 4),
    }
    for h in resp["hits"]["hits"]
]
```

- `h["_id"]` — Elasticsearch's auto-generated document ID
- `[:200]` — Truncates product descriptions to 200 characters for the search results page
- `round(h["_score"], 4)` — Rounds relevance score to 4 decimal places
- `.get(field, "")` — Safe access with empty string default if field is missing

```python
import math
return {
    "query":     q,
    "total":     total,
    "page":      page,
    "page_size": PAGE_SIZE,
    "pages":     math.ceil(total / PAGE_SIZE),
    "hits":      hits,
}
```

- `math.ceil(total / PAGE_SIZE)` — Total pages. 1945 results ÷ 20 per page = 98 pages (rounded up).

---

## 6. Frontend — UI (index.html)

**File:** `ui/index.html` — Single-file HTML/CSS/JS application with Pepagora branding.

### Visual Design

- **Brand colour**: `#c0392b` (Pepagora red) — used for borders, buttons, icons, tags
- **Background**: Subtle gradient `linear-gradient(160deg, #fff5f5 0%, #fff 60%)` — light pink to white
- **Font**: System font stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`) — fast loading, no external font requests
- **Layout**: Centered hero section with search box, transitions to results page on search

### Navigation Bar

```html
<nav>
  <span class="nav-logo">pepag<span>o</span>ra</span>
  <div class="nav-right">
    <span>English</span> | <span>IN</span>
    <button class="btn">Post Buying Requirement</button>
    <button class="btn">Login</button>
    <button class="btn primary">Get Started</button>
  </div>
</nav>
```

Mimics the Pepagora website header. The buttons show `alert('Feature coming soon')` — they're UI placeholders, not functional.

### Search Box & Dropdown

The search box has a red border (#c0392b), magnifier SVG icon, text input, and a clear (×) button.

**Dropdown layout (order matters):**
1. **Header**: "Showing results for: **{query}**"
2. **Products section** (FIRST) — Product name suggestions with search icons
3. **"Refine by" section** (SECOND) — Category/sub-category/product-category rows with folder icons
4. **Footer** — Total count + "See more →" button

**Why products first?** User testing showed that users primarily search for products, not categories. Putting product suggestions above category refinements matches user intent and reduces click distance.

### JavaScript Logic

#### API Base URL Detection

```javascript
const API_BASE = window.location.port === "8000"
  ? window.location.origin
  : "http://localhost:8000";
```

**Problem:** When developing, the HTML might be opened via VS Code Live Server (port 5500) or via FastAPI (port 8000). The API always runs on port 8000.

**Solution:** If the current page is already on port 8000 (served by FastAPI), use the same origin. Otherwise, hardcode `http://localhost:8000`. This prevents CORS issues during development.

#### Debouncing

```javascript
const DEBOUNCE_MS = 220;
```

When the user types, we wait 220ms of inactivity before sending an API call. This prevents firing a request for every keystroke ("s", "st", "ste", "stee", "steel"). Instead, the user types "steel" and only one request fires.

#### Keyboard Navigation

```javascript
function onKeyDown(e) {
    const rows = dropdown.querySelectorAll(".category-row, .product-row");
    if (e.key === "ArrowDown") { ... }
    else if (e.key === "ArrowUp") { ... }
    else if (e.key === "Escape") { closeDropdown(); }
    else if (e.key === "Enter") { goToSearch(q, 1); }
}
```

- Arrow keys cycle through dropdown rows (accessible navigation)
- Escape closes the dropdown
- Enter triggers a full search

#### Stale Response Handling

```javascript
if (data.query !== searchInput.value.trim()) return;
```

After an async fetch returns, we check if the query has changed since we sent the request. If the user typed "bat" then quickly added "h" → "bath", we might receive the "bat" response after "bath" was sent. This guard discards stale responses.

#### Category Filter in Search

```javascript
async function goToSearch(q, page, category = "") {
    let url = `${API_BASE}/search?q=${encodeURIComponent(q)}&page=${page}`;
    if (category) url += `&category=${encodeURIComponent(category)}`;
    ...
}
```

When a category row is clicked in the dropdown, or a category tag is clicked in search results, the category name is passed to `/search`. This filters results to only that category.

#### Active Filter Display

```javascript
const filterNote = activeCategory
    ? ` in <span class="cat-tag">${escHtml(activeCategory)}
        <button onclick="goToSearch('${escAttr(query)}',1,'')" title="Remove filter">&times;</button>
      </span>`
    : "";
```

When a category filter is active, it shows as a red chip with an × button. Clicking × re-runs the search without the category filter (passes empty string).

#### XSS Prevention

```javascript
function escHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
                     .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
```

All dynamic content is HTML-escaped before being inserted via `innerHTML`. This prevents XSS (Cross-Site Scripting) attacks — if a product name contained `<script>alert('xss')</script>`, it would be displayed as text, not executed.

#### Pagination

```javascript
const start = Math.max(1, page - 2);
const end   = Math.min(pages, page + 2);
```

Shows a window of 5 page buttons centred on the current page. For page 5: shows [3, 4, **5**, 6, 7]. Prev/Next buttons are disabled at page boundaries.

Category filter is preserved when navigating pages — `goToSearch('${escAttr(query)}',${i},'${escAttr(cat)}')`.

---

## 7. Index Mapping — Field-by-Field Explanation

### productName

```json
{
    "type": "text",
    "analyzer": "product_english",
    "fields": {
        "keyword":  { "type": "keyword", "ignore_above": 512 },
        "suggest":  { "type": "completion" },
        "ngram":    { "type": "text", "analyzer": "product_autocomplete", "search_analyzer": "standard" }
    }
}
```

| Sub-field | Type | Analyzer (index) | Analyzer (search) | Purpose |
|---|---|---|---|---|
| `productName` (root) | text | `product_english` | `product_english` | Main full-text search — stemmed, stop words removed |
| `.keyword` | keyword | (none) | (none) | Exact-match, sorting, aggregations. `ignore_above: 512` skips abnormally long names. |
| `.suggest` | completion | (built-in) | (built-in) | Completion suggester — future-ready for native ES autocomplete. Not used by current API. |
| `.ngram` | text | `product_autocomplete` | `standard` | Edge-ngram prefix matching — "bat" matches against pre-built ["ba","bat","bath"] tokens |

**`.ngram` asymmetric analyzers explained:**
- **Index time** (`product_autocomplete`): Breaks "bathroom" into ["ba", "bat", "bath", "bathr", "bathro", "bathro", "bathroo", "bathroom"]. This is stored in the index.
- **Search time** (`standard`): Keeps "bat" as ["bat"]. This single token is looked up against the indexed ngram tokens.
- **Result**: "bat" matches any word that starts with "bat" — including "bathroom", "battery", "batting", etc. This is why ngram is only used in `should` (ranking bonus) and NEVER in `must` for 1-word queries.

### productDescription

```json
{
    "type": "text",
    "analyzer": "product_english"
}
```

Simple text field with English analysis. Used as a soft ranking signal (boost 0.2 in search). No sub-fields — we don't need keyword or ngram for descriptions.

### category_name, subCategory_name, productCategory_name

```json
{
    "type": "keyword",
    "fields": {
        "text": { "type": "text", "analyzer": "standard" }
    }
}
```

| Sub-field | Type | Purpose |
|---|---|---|
| Root | keyword | Exact filtering, term aggregations. "Industrial Equipment & Machinery" stored exactly as-is. |
| `.text` | text (standard analyzer) | Text tokenised for match/match_phrase_prefix queries. "Industrial Equipment & Machinery" → ["industrial", "equipment", "machinery"]. Used in autocomplete category matching and search category boost. |

**Why keyword as the root type (not text)?**
Categories are controlled vocabulary — fixed strings, not free text. They need exact matching for filters (`term` query) and correct aggregation (no stemming confusion between "Equipment" and "Equip").

### Fields NOT indexed

| Field | Reason for exclusion |
|---|---|
| `productCategory__id` | Internal MongoDB ObjectId — no search or display value |
| `productCategory_uniqueId` | Internal unique identifier — redundant with ES `_id` |

---

## 8. Analyzer Design — Why Three Analyzers

### Analyzer 1: `product_english`

**Pipeline:** standard tokenizer → lowercase → english_stop → english_stemmer

| Input | Tokens |
|---|---|
| "Premium Stainless Steel Pipes" | ["premium", "stainless", "steel", "pipe"] |
| "Industrial Bat Cricket Equipment" | ["industri", "bat", "cricket", "equip"] |
| "Bathroom Accessories Set" | ["bathroom", "accessori", "set"] |

**Key property:** "bat" and "bathroom" produce DIFFERENT stems ("bat" vs "bathroom"). This is how the `/search` endpoint correctly separates bat results from bath results when using `match` on the root `productName` field.

### Analyzer 2: `product_autocomplete`

**Pipeline:** standard tokenizer → lowercase → edge_ngram_filter (min=2, max=20)

| Input | Tokens |
|---|---|
| "bath" | ["ba", "bat", "bath"] |
| "steel" | ["st", "ste", "stee", "steel"] |

Used ONLY at index time on the `productName.ngram` sub-field. Builds a lookup table of all possible prefixes.

### Analyzer 3: `standard` (built-in)

**Pipeline:** standard tokenizer → lowercase

| Input | Tokens |
|---|---|
| "bat" | ["bat"] |
| "steel pipe" | ["steel", "pipe"] |

Used at SEARCH time on `productName.ngram`. Keeps the query tokens whole so they can be matched against the pre-built ngram tokens.

### The Asymmetric Pattern

```
Index time:  "bathroom fittings"  →  product_autocomplete  →  [ba, bat, bath, bathr, ..., fi, fit, fitt, ...]
Search time: "bat"                →  standard              →  [bat]

Match: "bat" ∈ {ba, bat, bath, bathr, ...}  ✓
```

This "build big at index time, query fast at search time" pattern is an Elasticsearch best practice for autocomplete.

---

## 9. Search Quality — Data-Driven Decisions

The word-count-based search strategy was designed after analysing the actual dataset. Here are the key findings:

### Product Name Length Distribution

| Word count | Number of products |
|---|---|
| 5 words | ~12,000 |
| 6 words | ~18,000 |
| 7 words | ~22,000 |
| 8 words | ~20,000 |
| 9 words | ~15,000 |
| 10-11 words | ~13,000 |

**ALL 100k names are 5-11 words long.** Zero short names exist (no product is just "bat" or "pump"). This means users always search with 1-3 keyword queries into these long descriptive names.

### The "bat" Problem

| Query: "bat" | Count | Source |
|---|---|---|
| Exact word match ("bat" as a token) | 38 | Products with "bat" in name |
| Substring match (ngram) | 1,530 | bath(1333), battery(162), batting(24) |
| **Noise ratio** | **97.5%** | Almost all ngram matches are wrong |

This is why ngram MUST NOT be in the `must` clause for 1-word queries.

### Multi-Word Queries: No Noise

| Query | Exact matches | Ngram matches | Noise |
|---|---|---|---|
| "cricket bat" | 12 | 12 | 0 |
| "bath towels" | 45 | 45 | 0 |
| "steel pipe" | 89 | 89 | 0 |

With 2+ words, the intersection of ngram matches for both words eliminates noise. This is why ngram is safely used in `should` for 2+ word queries.

### Category Distribution

| Category | Products |
|---|---|
| Industrial Equipment & Machinery | 23,424 |
| Electronics & Electrical | 14,287 |
| Textiles & Fabrics | 11,856 |
| Building & Construction | 9,432 |
| ... (14 top-level categories total) | ... |

### Name Starting Patterns

| Starting word | Count |
|---|---|
| "Industrial" | ~34,000 |
| "Premium" | ~17,000 |
| "Commercial" | ~3,800 |
| "Professional" | ~2,100 |

**Nobody searches "Industrial..." or "Premium..."** — Users search the core product noun ("pump", "pipe", "saree"). This confirmed that prefix matching on `productName.keyword` would be useless (it was removed from the search strategy).

---

## 10. Key Decisions & Trade-offs

### Decision 1: Word-count strategy vs. single query

| Approach | Pros | Cons |
|---|---|---|
| **Single query for all** | Simple code | "bat" shows bath results |
| **Word-count branching** ✓ | Clean results for short queries | Slightly more complex code (3 branches) |

**Chosen:** Word-count branching. The code complexity is minimal (~30 extra lines) but the search quality improvement is dramatic (97.5% noise reduction for single-word queries).

### Decision 2: `match_phrase_prefix` in autocomplete, NOT in search

- **Autocomplete** uses `match_phrase_prefix` because the user IS still typing — "bat" is a prefix of "bath towels"
- **Search** does NOT use `match_phrase_prefix` because the user pressed Enter — "bat" means "bat", not "bath"

### Decision 3: No synonyms (yet)

Elasticsearch supports synonym token filters (e.g., "laptop" = "notebook"). We chose NOT to implement this because:
- The dataset doesn't have a known synonym mapping
- Incorrect synonyms cause more harm than benefit
- Can be added later without re-indexing (using `search_analyzer` with synonyms)

### Decision 4: No rescore phase

Elasticsearch supports a `rescore` phase that re-ranks the top-N results with a more expensive query. We don't use it because:
- The current 3-tier strategy already provides good ranking
- 100k documents doesn't need the performance optimisation that rescore provides
- Simpler query = easier to debug

### Decision 5: Single shard, no replicas

Appropriate for 100k documents on a single node. Multiple shards would add coordination overhead without performance benefit at this scale.

### Decision 6: `forcemerge` after ingest

Safe because our index is read-heavy (no writes after initial ingest). Merging to 1 segment optimises disk layout and search speed. Would NOT be appropriate for a write-heavy index.

### Decision 7: `refresh_interval: -1` during ingest

Temporarily disabling refresh during bulk ingest improves speed by ~2-3x. ES doesn't waste CPU creating searchable segments that nobody is querying yet.

---

## Running the Project

### Prerequisites
- Docker Desktop (for Elasticsearch)
- Python 3.9+ with conda or venv
- Required packages: `elasticsearch>=8.0,<9.0`, `pandas`, `ftfy`, `tqdm`, `fastapi`, `uvicorn`

### Steps

1. **Start Elasticsearch:**
   ```bash
   docker-compose up -d
   ```
   Wait ~30 seconds for ES to be ready. Verify: `curl http://localhost:9200`

2. **Run the notebook** (`category_mapper.ipynb`):
   - Execute cells sequentially from Step 1 to Step 6
   - Step 7 (predict_category) is optional — it's a utility function, not required for the search UI

3. **Start the API server:**
   ```bash
   cd api
   uvicorn main:app --reload --port 8000
   ```

4. **Open the UI:**
   - Navigate to `http://localhost:8000` in your browser
   - The search page loads with autocomplete and full search functionality

---

*End of documentation.*
