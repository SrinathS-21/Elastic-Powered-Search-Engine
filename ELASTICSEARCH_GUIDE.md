# Elasticsearch — In-Depth Guide for Pepagora Category Mapper

> **Purpose:** This document explains Elasticsearch concepts in depth and maps every capability to the Pepagora product dataset (`pepagoraDb.liveproducts.csv` — 100,000 products). Use this as your reference throughout the project.

---

## Table of Contents

1. [What is Elasticsearch?](#1-what-is-elasticsearch)
2. [Core Terminology & Concepts](#2-core-terminology--concepts)
3. [Mappings — Defining Your Schema](#3-mappings--defining-your-schema)
4. [Analyzers & Tokenizers — How Text Gets Processed](#4-analyzers--tokenizers--how-text-gets-processed)
5. [Query DSL — The Powerhouse](#5-query-dsl--the-powerhouse)
6. [Relevance Scoring — BM25](#6-relevance-scoring--bm25)
7. [Aggregations — Analytics Engine](#7-aggregations--analytics-engine)
8. [Filters vs Queries — Performance](#8-filters-vs-queries--performance)
9. [Percolator — Reverse Search (Key for Category Mapping)](#9-percolator--reverse-search-key-for-category-mapping)
10. [kNN Vector Search — Semantic Similarity](#10-knn-vector-search--semantic-similarity)
11. [Highlighting](#11-highlighting)
12. [Autocomplete & Suggestions](#12-autocomplete--suggestions)
13. [Bulk Indexing — Ingesting 100k Records](#13-bulk-indexing--ingesting-100k-records)
14. [Cluster Health & Monitoring](#14-cluster-health--monitoring)
15. [Roadmap — What to Build for Category Mapping](#15-roadmap--what-to-build-for-category-mapping)
16. [Quick Reference — Query Cheat Sheet](#16-quick-reference--query-cheat-sheet)

---

## 1. What is Elasticsearch?

Elasticsearch is a **distributed, RESTful search and analytics engine** built on top of Apache Lucene. It is designed to:

- Store, search, and analyze large volumes of data in near real-time
- Handle full-text search with relevance ranking
- Perform aggregations (analytics) across millions of documents
- Scale horizontally across many nodes

### Why Elasticsearch for Category Mapping?

Your challenge: *"Given a product name and description, automatically determine the correct category."*

Elasticsearch solves this because:
- It can do **full-text search** against a knowledge base of labeled products
- It **ranks results by relevance** using BM25 scoring
- It allows **aggregations** to find which category best represents matching products
- It supports **percolator** — a reverse-search pattern ideal for rule-based category assignment
- It supports **vector search** — semantic similarity using ML embeddings (ES 8.x)

---

## 2. Core Terminology & Concepts

### Hierarchy Comparison

```
Relational DB          Elasticsearch
──────────────         ─────────────────────
Database         →     Cluster
Table            →     Index
Row              →     Document
Column           →     Field
Schema           →     Mapping
SQL Query        →     Query DSL (JSON)
Index (SQL)      →     Inverted Index (built-in, always)
```

### Cluster
A cluster is a collection of one or more nodes. In your `docker-compose.yml`, you have a **single-node cluster** (`discovery.type=single-node`) which is correct for development.

### Node
A single running instance of Elasticsearch. In production, you'd have multiple nodes for redundancy and throughput.

### Index
An index is the equivalent of a database table. You will create one index (e.g., `pepagora_products`) containing all 100,000 product documents.

### Document
A single JSON object stored in an index. Each product in your CSV becomes one document:

```json
{
  "_id": "68a6ef467746111262d8376c",
  "productName": "Industrial Power Systems Medium Voltage Capacitor Surface Mount Super Capacitor",
  "productDescription": "Industrial Power Systems Medium Voltage Capacitor delivers reliable surface mount super capacitor performance for AC motor applications, featuring stainless steel construction and stable operation from -25°C to +55°C for industrial power factor correction and reactive power compensation needs.",
  "category_name": "Electronics & Electrical",
  "subCategory_name": "Capacitors, Resistors, Inductors",
  "productCategory_name": "Electrolytic Capacitors",
  "productCategory_uniqueId": "pck0p93l8i"
}
```

### Inverted Index
This is the core data structure that makes search fast. For every unique token (word), ES stores a list of which documents contain it:

```
Token          →  Documents containing it
────────────────────────────────────────────
"capacitor"    →  [doc_1, doc_5, doc_23, doc_401, ...]
"industrial"   →  [doc_1, doc_2, doc_88, ...]
"stainless"    →  [doc_1, doc_45, ...]
"rice"         →  [doc_3, doc_4, doc_7, ...]
```

When you search for `"industrial capacitor"`, ES looks up both tokens in the inverted index and finds the intersection — instantly, even across millions of documents.

### Shard
Each index is divided into shards (partitions). For a single-node dev setup, 1 shard is fine. In production, multiple shards allow parallel search across nodes.

### Replica
A copy of a shard on a different node. Provides fault tolerance and read throughput. In your single-node setup, replicas will be unassigned (this is normal and expected).

---

## 3. Mappings — Defining Your Schema

Mappings define how each field is stored, indexed, and searched. This is the **most important configuration decision** — wrong mappings mean poor search results.

### Why not use dynamic mapping?

Elasticsearch can auto-detect field types (dynamic mapping), but for production use you should define explicit mappings because:
- Auto-mapping may choose the wrong field type (e.g., treating a category name as `text` when it should be `keyword`)
- You lose control over analyzers
- Re-mapping later requires re-indexing everything

### Field Types — Key Ones for Your Dataset

| Type | Description | Use When |
|---|---|---|
| `text` | Analyzed — tokenized, lowercased, stemmed | Full-text search (productName, description) |
| `keyword` | Not analyzed — exact string, case-sensitive | Filtering, sorting, aggregations, category names |
| `scaled_float` | Decimal number with fixed scaling factor | Prices, ratings |
| `integer` / `long` | Whole numbers | Counts, IDs |
| `date` | Date/time values | Created dates |
| `boolean` | true/false | Active flag |
| `dense_vector` | Floating-point vector array | ML embeddings for kNN |

### Multi-Field Mapping

The most powerful pattern: map a single field as **both** `text` and `keyword`:

```json
"productName": {
  "type": "text",
  "analyzer": "english",
  "fields": {
    "keyword": {
      "type": "keyword",
      "ignore_above": 512
    }
  }
}
```

This lets you:
- Search `productName` with full-text analysis (typo tolerance, stemming)
- Filter/aggregate on `productName.keyword` as an exact string

### Recommended Mapping for Your Dataset

```json
PUT /pepagora_products
{
  "settings": {
    "number_of_shards": 1,
    "number_of_replicas": 0,
    "analysis": {
      "analyzer": {
        "product_analyzer": {
          "type": "custom",
          "tokenizer": "standard",
          "filter": ["lowercase", "stop", "english_stemmer", "synonym_filter"]
        }
      },
      "filter": {
        "english_stemmer": {
          "type": "stemmer",
          "language": "english"
        },
        "synonym_filter": {
          "type": "synonym",
          "synonyms": [
            "mobile, cellphone, smartphone",
            "tv, television, monitor",
            "ac, air conditioner, aircon"
          ]
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "productName": {
        "type": "text",
        "analyzer": "product_analyzer",
        "fields": {
          "keyword": { "type": "keyword", "ignore_above": 512 },
          "suggest": { "type": "completion" }
        }
      },
      "productDescription": {
        "type": "text",
        "analyzer": "product_analyzer"
      },
      "category_name": {
        "type": "keyword",
        "fields": {
          "text": { "type": "text", "analyzer": "standard" }
        }
      },
      "subCategory_name": {
        "type": "keyword",
        "fields": {
          "text": { "type": "text", "analyzer": "standard" }
        }
      },
      "productCategory_name": {
        "type": "keyword",
        "fields": {
          "text": { "type": "text", "analyzer": "standard" }
        }
      },
      "productCategory_uniqueId": { "type": "keyword" }
    }
  }
}
```

---

## 4. Analyzers & Tokenizers — How Text Gets Processed

An **analyzer** is a pipeline applied to text fields at **index time** (when storing) and **search time** (when querying). Both should use the same analyzer so they match correctly.

### Analyzer Pipeline

```
Input Text
    ↓
[1] Character Filter   →  Pre-process raw text (strip HTML, replace characters)
    ↓
[2] Tokenizer          →  Split text into tokens (words)
    ↓
[3] Token Filters      →  Transform tokens (lowercase, stemming, stopwords, synonyms)
    ↓
Token Stream (stored in inverted index)
```

### Example — `english` Analyzer

```
Input:   "Industrial Power Systems delivers reliable performance for AC motor applications"

Step 1 — Character Filter:   (none by default)
Step 2 — Tokenizer:          ["Industrial", "Power", "Systems", "delivers", "reliable",
                               "performance", "for", "AC", "motor", "applications"]
Step 3 — Lowercase:          ["industrial", "power", "systems", "delivers", "reliable",
                               "performance", "for", "ac", "motor", "applications"]
Step 4 — Stop words:         ["industrial", "power", "systems", "delivers", "reliable",
                               "performance", "ac", "motor", "applications"]
                               (removed: "for")
Step 5 — Stemmer (english):  ["industri", "power", "system", "deliv", "reliabl",
                               "perform", "ac", "motor", "applic"]

Final tokens stored in inverted index.
```

**Effect:** A search for `"capacity performing systems"` will still find this document because `"performing"` stems to `"perform"`, `"systems"` stems to `"system"`.

### Built-in Analyzers Compared

| Analyzer | Tokenizer | Lowercase | Stopwords | Stemming | Best For |
|---|---|---|---|---|---|
| `standard` | Unicode word boundaries | ✅ | ❌ | ❌ | General purpose |
| `english` | Standard | ✅ | ✅ English | ✅ English | English product text |
| `whitespace` | Whitespace only | ❌ | ❌ | ❌ | Codes, IDs |
| `simple` | Non-letter chars | ✅ | ❌ | ❌ | Simple text |
| `keyword` | None (whole string) | ❌ | ❌ | ❌ | Exact values |

### Custom Synonym Filter (Useful for Products)

```json
"synonym_filter": {
  "type": "synonym",
  "synonyms": [
    "capacitor, cap, condenser",
    "resistor, resistance",
    "led, light emitting diode",
    "pvc, polyvinyl chloride",
    "ss, stainless steel"
  ]
}
```

This means searching for `"condenser"` will also find documents containing `"capacitor"`.

### Testing Your Analyzer

You can test what tokens any analyzer produces without indexing:

```json
POST /pepagora_products/_analyze
{
  "analyzer": "english",
  "text": "Industrial Power Systems delivering for AC motor applications"
}
```

---

## 5. Query DSL — The Powerhouse

Elasticsearch uses a **JSON-based Query Domain Specific Language (DSL)**. There are two main categories:

- **Leaf queries** — search in a specific field (`match`, `term`, `range`)
- **Compound queries** — combine multiple queries (`bool`, `dis_max`, `function_score`)

---

### 5.1 `match` — Standard Full-Text Search

The most commonly used query type. Analyzes the query text and finds documents.

```json
GET /pepagora_products/_search
{
  "query": {
    "match": {
      "productName": "voltage capacitor industrial"
    }
  }
}
```

**Options:**
```json
{
  "match": {
    "productName": {
      "query": "voltage capacitor",
      "operator": "AND",        // all words must appear (default is OR)
      "minimum_should_match": "75%",  // at least 75% of words must match
      "fuzziness": "AUTO",      // tolerate spelling mistakes
      "analyzer": "english"     // override analyzer at query time
    }
  }
}
```

---

### 5.2 `match_phrase` — Exact Phrase Search

All words must appear **in the same order** with no gaps:

```json
{
  "match_phrase": {
    "productDescription": "stainless steel construction"
  }
}
// "stainless steel" matches, "steel stainless" does NOT
```

With `slop` — words can be up to N positions apart:
```json
{
  "match_phrase": {
    "productDescription": {
      "query": "stainless construction",
      "slop": 2   // "stainless steel construction" would match (2 words apart)
    }
  }
}
```

---

### 5.3 `multi_match` — Search Multiple Fields Simultaneously

**The most important query for category mapping.** Search `productName` AND `productDescription` at once, with field boosting:

```json
{
  "multi_match": {
    "query": "stainless steel capacitor industrial",
    "fields": [
      "productName^3",         // 3x boost — matches here score higher
      "productDescription^1",  // 1x boost — standard weight
      "productCategory_name.text^2"
    ],
    "type": "best_fields",     // see types below
    "fuzziness": "AUTO"
  }
}
```

**`type` options:**

| Type | Behavior | Use When |
|---|---|---|
| `best_fields` (default) | Score = best single field match | Short product names |
| `most_fields` | Sum scores from all fields | Documents with same content in multiple fields |
| `cross_fields` | Treat all fields as one big field | Person names (first + last in different fields) |
| `phrase` | `match_phrase` across fields | Exact phrase matching |
| `bool_prefix` | Autocomplete across fields | Search-as-you-type |

---

### 5.4 `bool` — Compound Query (Most Powerful)

Combines multiple queries. This is the backbone of any real search system.

```json
{
  "bool": {
    "must": [        // REQUIRED — affects score
      { "match": { "productDescription": "stainless steel" } }
    ],
    "should": [      // OPTIONAL — boosts score if present
      { "match": { "productName": "capacitor" } },
      { "term":  { "category_name": "Electronics & Electrical" } }
    ],
    "filter": [      // REQUIRED — does NOT affect score (faster)
      { "term": { "category_name": "Electronics & Electrical" } }
    ],
    "must_not": [    // EXCLUDED — must not match
      { "term": { "category_name": "Food & Agriculture" } }
    ],
    "minimum_should_match": 1   // at least 1 "should" clause must match
  }
}
```

**Key insight:** Use `filter` instead of `must` for category/keyword filtering — it's cached and much faster.

---

### 5.5 `term` / `terms` — Exact Keyword Match

No analysis — exact string match. Use only on `keyword` fields.

```json
// Single value
{ "term": { "category_name": "Electronics & Electrical" } }

// Multiple values (like SQL IN)
{ "terms": { "category_name": ["Electronics & Electrical", "Raw Materials & Chemicals"] } }
```

---

### 5.6 `match_all` — Return Everything

```json
{ "match_all": {} }   // returns all documents, score = 1.0 for all
```

---

### 5.7 `fuzzy` — Typo-Tolerant Search

```json
{
  "fuzzy": {
    "productName": {
      "value": "capaicitor",   // misspelled
      "fuzziness": "AUTO",     // AUTO: 0 edits ≤2 chars, 1 edit 3-5 chars, 2 edits >5 chars
      "max_expansions": 50
    }
  }
}
```

Or simpler — just add `fuzziness` to a `match` query:
```json
{ "match": { "productName": { "query": "capaicitor", "fuzziness": "AUTO" } } }
```

---

### 5.8 `range` — Range Queries

```json
{ "range": { "price": { "gte": 100, "lte": 500 } } }
```

---

### 5.9 `wildcard` — Pattern Matching

```json
{ "wildcard": { "productName.keyword": "Industrial*" } }
// Matches "Industrial Power Systems", "Industrial Capacitor", etc.
```

**Note:** Wildcards are slow on large datasets. Prefer `match` or `prefix` queries.

---

### 5.10 `prefix` — Starts With

```json
{ "prefix": { "productName.keyword": "Industrial" } }
```

---

### 5.11 `function_score` — Custom Scoring

Override or modify the calculated relevance score with custom logic:

```json
{
  "function_score": {
    "query": { "match": { "productName": "capacitor" } },
    "functions": [
      {
        "filter": { "term": { "category_name": "Electronics & Electrical" } },
        "weight": 2.0    // multiply score by 2 for this category
      }
    ],
    "boost_mode": "multiply"
  }
}
```

---

## 6. Relevance Scoring — BM25

Elasticsearch uses the **BM25 (Okapi BM25)** algorithm to score how relevant a document is to a query.

### Formula

$$\text{score}(D, Q) = \sum_{i=1}^{n} \text{IDF}(q_i) \cdot \frac{f(q_i, D) \cdot (k_1 + 1)}{f(q_i, D) + k_1 \cdot (1 - b + b \cdot \frac{|D|}{\text{avgdl}})}$$

Where:
- `f(q_i, D)` = frequency of term `q_i` in document `D` (Term Frequency)
- `|D|` = length of document
- `avgdl` = average document length
- `k1` = term frequency saturation (default 1.2)
- `b` = field length normalization (default 0.75)

### What this means practically

| Factor | Effect on Score |
|---|---|
| Term appears many times in document | Higher score (but with diminishing returns — BM25 saturates TF) |
| Term is rare across all documents | Higher score (IDF — "capacitor" in food products is very significant) |
| Short document matches term | Higher score than long document (field length normalization) |
| Term appears in boosted field | Score multiplied by boost factor |

### BM25 vs TF-IDF

BM25 is an improvement over classic TF-IDF:
- TF-IDF: score grows linearly with term frequency (document saying "capacitor" 100 times scores 100× a document saying it once)
- BM25: saturates at high TF (saying "capacitor" 10 times vs 100 times barely differs in score)

This makes BM25 much more robust for real-world text.

---

## 7. Aggregations — Analytics Engine

Aggregations let you compute statistics and analytics over your 100,000 products.

### 7.1 Terms Aggregation — Top Values

```json
GET /pepagora_products/_search
{
  "size": 0,    // don't return documents, just aggregation results
  "aggs": {
    "top_categories": {
      "terms": {
        "field": "category_name",
        "size": 50    // return top 50 categories
      }
    }
  }
}
```

**Example result:**
```json
{
  "aggregations": {
    "top_categories": {
      "buckets": [
        { "key": "Electronics & Electrical", "doc_count": 18234 },
        { "key": "Food & Agriculture",        "doc_count": 14891 },
        { "key": "Raw Materials & Chemicals", "doc_count": 12047 },
        ...
      ]
    }
  }
}
```

---

### 7.2 Nested Aggregation — Category → Sub-category Tree

```json
{
  "size": 0,
  "aggs": {
    "by_category": {
      "terms": { "field": "category_name", "size": 30 },
      "aggs": {
        "by_subcategory": {
          "terms": { "field": "subCategory_name", "size": 20 },
          "aggs": {
            "by_product_category": {
              "terms": { "field": "productCategory_name", "size": 10 }
            }
          }
        }
      }
    }
  }
}
```

This produces the **full 3-level category taxonomy** from your data.

---

### 7.3 Cardinality — Count Unique Values

```json
{
  "size": 0,
  "aggs": {
    "unique_categories":     { "cardinality": { "field": "category_name" } },
    "unique_subcategories":  { "cardinality": { "field": "subCategory_name" } },
    "unique_product_cats":   { "cardinality": { "field": "productCategory_name" } }
  }
}
```

---

### 7.4 Aggregation + Query — Analytics on Search Results

Find top categories among products matching a search term:

```json
{
  "query": {
    "multi_match": {
      "query": "stainless steel capacitor",
      "fields": ["productName^3", "productDescription"]
    }
  },
  "size": 5,   // return top 5 matching documents
  "aggs": {
    "matched_categories": {
      "terms": { "field": "category_name", "size": 10 }
    },
    "matched_subcategories": {
      "terms": { "field": "subCategory_name", "size": 10 }
    }
  }
}
```

**This is the core of category mapping:** Search for the input product → aggregate which categories the matching products belong to → the top category is the predicted one.

---

### 7.5 Significant Terms — Statistically Unusual Terms

Finds terms that are **unusually common** in a subset compared to the full dataset:

```json
{
  "query": { "term": { "category_name": "Food & Agriculture" } },
  "aggs": {
    "significant_product_names": {
      "significant_terms": { "field": "productName", "size": 20 }
    }
  }
}
// Returns words that appear much more in "Food & Agriculture" than in the whole index
// e.g., "rice", "wheat", "pulses", "grain" — useful for building category rules
```

---

## 8. Filters vs Queries — Performance

This distinction is critical for performance at scale.

| | Query | Filter |
|---|---|---|
| **What it does** | Finds AND scores documents | Finds documents (no scoring) |
| **Cached?** | ❌ No | ✅ Yes — cached in memory |
| **Performance** | Slower | Much faster |
| **Use for** | Full-text search (relevance matters) | Exact matches, ranges, categories |
| **Example** | `match`, `multi_match` | `term`, `terms`, `range` |

### Best Practice Pattern

```json
{
  "bool": {
    "must":   [ { "multi_match": { ... } } ],   // scoring query
    "filter": [ { "term": { "category_name": "Electronics & Electrical" } } ]  // fast filter
  }
}
```

The `filter` clause runs first, narrows the candidate set, and the expensive scoring only runs on the filtered subset.

---

## 9. Percolator — Reverse Search (Key for Category Mapping)

### Concept

Normal search: *"I have a query, give me matching documents"*
Percolator: *"I have a document, tell me which stored queries match it"*

### Why This Is Perfect for Category Mapping

You create **one percolator query per category/subcategory** — each query defines the rules for what belongs in that category. When a new product arrives, you run it through the percolator and ES tells you which category queries it matches.

### Setup

**Step 1: Create a percolator index with a special mapping:**
```json
PUT /category_rules
{
  "mappings": {
    "properties": {
      "query": { "type": "percolator" },  // stores the query
      "category_name": { "type": "keyword" },
      "subCategory_name": { "type": "keyword" },
      "productCategory_name": { "type": "keyword" },
      "priority": { "type": "integer" },
      // mirror the product document fields for query analysis:
      "productName": { "type": "text", "analyzer": "english" },
      "productDescription": { "type": "text", "analyzer": "english" }
    }
  }
}
```

**Step 2: Store category rules as percolator queries:**
```json
PUT /category_rules/_doc/electronics_capacitors
{
  "category_name": "Electronics & Electrical",
  "subCategory_name": "Capacitors, Resistors, Inductors",
  "productCategory_name": "Electrolytic Capacitors",
  "priority": 1,
  "query": {
    "bool": {
      "should": [
        { "match": { "productName":        { "query": "capacitor electrolytic aluminium", "operator": "OR" } } },
        { "match": { "productDescription": { "query": "capacitor farad voltage ripple",  "operator": "OR" } } }
      ],
      "minimum_should_match": 1
    }
  }
}
```

**Step 3: Match a new product against all stored rules:**
```json
GET /category_rules/_search
{
  "query": {
    "percolate": {
      "field": "query",
      "document": {
        "productName": "450V Aluminium Electrolytic Capacitor 1000uF",
        "productDescription": "High ripple current aluminium electrolytic capacitor for power supply filtering"
      }
    }
  }
}
```

**Example result:**
```json
{
  "hits": [
    {
      "_id": "electronics_capacitors",
      "_score": 4.21,
      "_source": {
        "category_name": "Electronics & Electrical",
        "subCategory_name": "Capacitors, Resistors, Inductors",
        "productCategory_name": "Electrolytic Capacitors"
      }
    }
  ]
}
```

### Auto-generating Percolator Rules from Existing Data

You can use your 100k products to **auto-generate rules** using significant terms per category:

```
For each category:
  1. Find the most significant terms in productName (significant_terms aggregation)
  2. Create a percolator query using those terms
  3. Store it
```

This gives you data-driven rules without manual effort.

---

## 10. kNN Vector Search — Semantic Similarity

### What Is It?

Instead of matching keywords, **k-Nearest Neighbors (kNN) search** finds documents that are semantically similar by comparing mathematical vectors (embeddings).

```
"vehicle brake pads"    →  [0.23, -0.41, 0.88, ...]  (384 dimensions)
"car disc brakes"       →  [0.21, -0.39, 0.85, ...]  ← semantically close
"rice rava crisp"       →  [-0.12, 0.67, -0.23, ...]  ← semantically far
```

### Why It Solves Problems BM25 Can't

| Scenario | BM25 Result | kNN Result |
|---|---|---|
| Search `"brake pads"`, doc has `"disc brakes"` | ❌ No keyword overlap | ✅ Semantically similar |
| Search `"grain"`, doc has `"cereal, wheat, pulses"` | ❌ No overlap | ✅ Same semantic space |
| Search `"capacitor"`, doc has `"condenser"` | ❌ (unless synonym) | ✅ Automatically |

### How to Implement (ES 8.x)

**Step 1: Add a vector field to your mapping:**
```json
"productName_vector": {
  "type": "dense_vector",
  "dims": 384,           // matches your embedding model output
  "index": true,
  "similarity": "cosine"
}
```

**Step 2: Generate embeddings using a sentence transformer model:**
```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")   # fast, 384-dim, great quality
embedding = model.encode("Industrial Power Systems Capacitor").tolist()
```

**Step 3: kNN search:**
```json
GET /pepagora_products/_search
{
  "knn": {
    "field": "productName_vector",
    "query_vector": [0.23, -0.41, ...],  // embedding of query product
    "k": 10,              // return 10 nearest neighbours
    "num_candidates": 100
  },
  "_source": ["productName", "category_name"]
}
```

### Hybrid Search — BM25 + kNN Combined

The most accurate approach — combines lexical (BM25) and semantic (kNN) signals:

```json
{
  "query": {
    "bool": {
      "should": [
        { "multi_match": { "query": "industrial capacitor", "fields": ["productName^3", "productDescription"] } }
      ]
    }
  },
  "knn": {
    "field": "productName_vector",
    "query_vector": [...],
    "k": 50,
    "num_candidates": 200,
    "boost": 0.5           // balance BM25 vs kNN contribution
  }
}
```

---

## 11. Highlighting

Highlighting returns the matched text **with the matching terms wrapped in HTML tags**. Essential for debugging and building search UIs.

```json
GET /pepagora_products/_search
{
  "query": { "match": { "productDescription": "stainless steel" } },
  "highlight": {
    "fields": {
      "productDescription": {
        "pre_tags":  ["<strong>"],
        "post_tags": ["</strong>"],
        "fragment_size": 150,    // characters per fragment
        "number_of_fragments": 3 // return 3 best fragments
      }
    }
  }
}
```

**Result:**
```json
"highlight": {
  "productDescription": [
    "...featuring <strong>stainless steel</strong> construction and stable operation from -25°C..."
  ]
}
```

---

## 12. Autocomplete & Suggestions

Two approaches in Elasticsearch:

### 12.1 Completion Suggester (Fastest)

Map a field with type `completion`, then query it:

```json
// Mapping
"productName_suggest": { "type": "completion" }

// Query — instant prefix autocomplete
GET /pepagora_products/_search
{
  "suggest": {
    "product_suggest": {
      "prefix": "Industrial Po",
      "completion": {
        "field": "productName_suggest",
        "size": 5
      }
    }
  }
}
```

Returns: `"Industrial Power Systems..."`, `"Industrial Polymer Coating..."`, etc.

### 12.2 search_as_you_type Field

More flexible — matches anywhere in the string, not just prefix:

```json
// Mapping
"productName": { "type": "search_as_you_type" }

// Query
GET /pepagora_products/_search
{
  "query": {
    "multi_match": {
      "query": "power capacitor",
      "type": "bool_prefix",
      "fields": ["productName", "productName._2gram", "productName._3gram"]
    }
  }
}
```

---

## 13. Bulk Indexing — Ingesting 100k Records

### The Bulk API

Elasticsearch provides a `/_bulk` endpoint for efficient batch operations. Never index documents one-by-one for large datasets — it is ~50x slower.

**Bulk request format (alternating action + document lines):**
```json
{ "index": { "_index": "pepagora_products", "_id": "68a6ef46..." } }
{ "productName": "Industrial Power Systems...", "category_name": "Electronics & Electrical", ... }
{ "index": { "_index": "pepagora_products", "_id": "68a6ef4d..." } }
{ "productName": "Non Explosive Demolition Agent...", "category_name": "Raw Materials & Chemicals", ... }
```

### Python Bulk Helper

```python
from elasticsearch.helpers import bulk
import pandas as pd

def generate_actions(df, index_name):
    for _, row in df.iterrows():
        yield {
            "_index": index_name,
            "_id":    row["_id"],
            "_source": {
                "productName":              row["productName"],
                "productDescription":       row["productDescription"],
                "category_name":            row["category_name"],
                "subCategory_name":         row["subCategory_name"],
                "productCategory_name":     row["productCategory_name"],
                "productCategory_uniqueId": row["productCategory_uniqueId"],
            }
        }

success, failed = bulk(es, generate_actions(df, "pepagora_products"), chunk_size=500)
print(f"Indexed: {success} | Failed: {failed}")
```

### Chunk Size Guidelines

| Dataset Size | Recommended chunk_size |
|---|---|
| < 10k docs | 200–500 |
| 10k–100k docs | 500–1000 |
| > 100k docs | 1000–2000 |
| Large docs (>1KB each) | Reduce to 200–500 |

For your 100k products: **chunk_size=500** is optimal.

---

## 14. Cluster Health & Monitoring

### Health Levels

| Status | Meaning |
|---|---|
| 🟢 `green` | All primary and replica shards assigned |
| 🟡 `yellow` | All primary shards assigned, some replicas unassigned (normal for single-node) |
| 🔴 `red` | Some primary shards unassigned — data loss possible |

**Single-node will always show `yellow`** — this is normal because replicas can't be placed on the same node as primary shards.

### Useful API Endpoints

```bash
# Cluster health
GET http://localhost:9200/_cluster/health

# All indices and their sizes
GET http://localhost:9200/_cat/indices?v

# Count documents in an index
GET http://localhost:9200/pepagora_products/_count

# Index mapping
GET http://localhost:9200/pepagora_products/_mapping

# Index settings
GET http://localhost:9200/pepagora_products/_settings

# Node info
GET http://localhost:9200/_nodes?pretty

# Performance stats
GET http://localhost:9200/pepagora_products/_stats
```

### Python Health Check

```python
# Cluster health
health = es.cluster.health()
print(health["status"])          # green / yellow / red

# Document count
count = es.count(index="pepagora_products")
print(count["count"])            # 100000

# Index stats
stats = es.indices.stats(index="pepagora_products")
print(stats["_all"]["total"]["store"]["size_in_bytes"])
```

---

## 15. Roadmap — What to Build for Category Mapping

### Phase 1 — Foundation (Complete Once Notebook Cells Run)
- [x] Elasticsearch running (Docker)
- [ ] Index created with proper mappings (english analyzer, multi-fields)
- [ ] 100k products bulk-indexed
- [ ] Basic `multi_match` search working
- [ ] Aggregation query to find top category from search results

**Accuracy baseline:** ~70–80% for well-described products

---

### Phase 2 — Smarter Queries
- [ ] `bool` query combining name + description + category hints
- [ ] Field boosting: `productName^3`, `productCategory_name.text^2`
- [ ] Synonym filter for common product terms
- [ ] Fuzzy search for typo tolerance
- [ ] Evaluate accuracy on 1,000 test products

**Expected accuracy:** ~80–90%

---

### Phase 3 — Percolator-Based Rule Engine
- [ ] Extract significant terms per category (significant_terms aggregation)
- [ ] Auto-generate percolator queries per category
- [ ] Build a `/classify` endpoint: input product name → output category
- [ ] Score confidence based on percolator match score

**Expected accuracy:** ~85–92% (depends on rule quality)

---

### Phase 4 — Semantic / ML (Optional, High Impact)
- [ ] Add `dense_vector` field to index mapping
- [ ] Generate embeddings with `sentence-transformers` (all-MiniLM-L6-v2)
- [ ] kNN search for semantic similarity
- [ ] Hybrid BM25 + kNN search
- [ ] Fine-tune embeddings on your product data

**Expected accuracy:** ~92–97%

---

### Decision: Which Phase to Prioritise?

| Phase | Dev Effort | Accuracy | When to Use |
|---|---|---|---|
| Phase 1 | Low (hours) | 70–80% | MVP, quick demo |
| Phase 2 | Low (hours) | 80–90% | Production baseline |
| Phase 3 | Medium (days) | 85–92% | When rules are needed |
| Phase 4 | High (weeks) | 92–97% | When accuracy is critical |

**Recommended path:** Complete Phase 1 → 2 first, validate accuracy, then decide whether Phase 3 or 4 is needed.

---

## 16. Quick Reference — Query Cheat Sheet

```python
from elasticsearch import Elasticsearch
es = Elasticsearch("http://localhost:9200")
INDEX = "pepagora_products"

# ── 1. Simple full-text search ─────────────────────────────────────────────────
es.search(index=INDEX, query={"match": {"productName": "stainless capacitor"}})

# ── 2. Multi-field boosted search ─────────────────────────────────────────────
es.search(index=INDEX, query={
    "multi_match": {
        "query": "industrial stainless steel",
        "fields": ["productName^3", "productDescription"],
        "fuzziness": "AUTO"
    }
})

# ── 3. Filter by category ──────────────────────────────────────────────────────
es.search(index=INDEX, query={
    "bool": {
        "must":   [{"match": {"productName": "capacitor"}}],
        "filter": [{"term": {"category_name": "Electronics & Electrical"}}]
    }
})

# ── 4. Top categories for a search ────────────────────────────────────────────
es.search(index=INDEX, size=0, query={
    "multi_match": {"query": "rice wheat grain", "fields": ["productName^3", "productDescription"]}
}, aggs={"top_cats": {"terms": {"field": "category_name", "size": 10}}})

# ── 5. Category taxonomy ───────────────────────────────────────────────────────
es.search(index=INDEX, size=0, aggs={
    "categories": {
        "terms": {"field": "category_name", "size": 50},
        "aggs": {"subcategories": {"terms": {"field": "subCategory_name", "size": 30}}}
    }
})

# ── 6. Count documents ─────────────────────────────────────────────────────────
es.count(index=INDEX)["count"]

# ── 7. Get a single document ───────────────────────────────────────────────────
es.get(index=INDEX, id="68a6ef467746111262d8376c")

# ── 8. Delete index (use with caution) ────────────────────────────────────────
es.indices.delete(index=INDEX, ignore_unavailable=True)
```

---

*Last updated: March 2026 — Elasticsearch 8.12.0, Python elasticsearch-py 8.x*
