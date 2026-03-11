# Pepagora — Elasticsearch-Powered Product Search System

**Document Version:** 2.0  
**Date:** March 2026  
**Prepared for:** Stakeholders, Technical Leads & Decision-Makers  
**Status:** Fully operational — 100,000 products indexed and searchable

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Problem Statement](#2-problem-statement)
3. [Solution Overview](#3-solution-overview)
4. [System Architecture](#4-system-architecture)
5. [How the Search Works](#5-how-the-search-works)
   - 5.1 [Text Analysis — How Products Become Searchable](#51-text-analysis--how-products-become-searchable)
   - 5.2 [Autocomplete — Instant Suggestions While Typing](#52-autocomplete--instant-suggestions-while-typing)
   - 5.3 [Full Search — Ranked Results with Filtering](#53-full-search--ranked-results-with-filtering)
   - 5.4 [Category Prediction — Automatic Classification](#54-category-prediction--automatic-classification)
6. [Data Pipeline — From Raw CSV to Searchable Index](#6-data-pipeline--from-raw-csv-to-searchable-index)
7. [Search Intelligence — The Word-Count Strategy](#7-search-intelligence--the-word-count-strategy)
8. [Search Quality — How We Fixed the "bat ≠ bath" Problem](#8-search-quality--how-we-fixed-the-bat--bath-problem)
9. [User Interface — What the User Sees](#9-user-interface--what-the-user-sees)
10. [Performance](#10-performance)
11. [Technology Stack](#11-technology-stack)
12. [Key Design Decisions](#12-key-design-decisions)
13. [How to Run the System](#13-how-to-run-the-system)
14. [Glossary](#14-glossary)

---

## 1. Introduction

This document describes the **Elasticsearch-powered product search system** built for Pepagora, a B2B marketplace. The system enables users to search across 100,000 products with instant autocomplete, intelligent relevance ranking, and automatic category prediction.

The goal of this document is to explain **what the system does, how it works, and why it was built this way** — without requiring the reader to examine source code.

---

## 2. Problem Statement

Pepagora's product catalog contains **100,000 live products** organized into a three-level category hierarchy:

- **14 top-level categories** (e.g., "Sanitaryware & Bathroom Fittings", "Industrial Machinery")
- **Sub-categories** within each (e.g., "Bath Accessories", "Pumps & Motors")
- **Product categories** at the most specific level (e.g., "Bath Towels", "Centrifugal Pumps")

The existing system relied on basic database lookups, which had several limitations:

| Limitation | Impact |
|---|---|
| No fuzzy matching | A typo like "towls" instead of "towels" returns zero results |
| No prefix matching | Typing "bath tow" returns nothing until the full word is typed |
| No relevance ranking | All results are treated equally — no concept of "best match" |
| No autocomplete | Users must type complete queries and press Enter |
| No automatic categorization | New products must be manually categorized |
| Slow for full-text searches | Database `LIKE` queries scan entire tables |

**The business needed a search engine that could handle partial words, tolerate typos, rank results intelligently, and suggest categories automatically.**

---

## 3. Solution Overview

The solution replaces database lookups with **Elasticsearch**, a search engine purpose-built for full-text search. The system has four key capabilities:

### 3.1 Instant Autocomplete

As the user types into the search box, results appear in a dropdown **within milliseconds**. The dropdown shows:

- **Product suggestions** — the top 5 matching products by relevance, each with a confidence badge
- **Category refinements** — matching category names the user can click to narrow results
- **Performance metrics** — response latency and total match count

### 3.2 Full-Text Search with Pagination

When the user presses Enter or clicks "See more," a full results page appears showing:

- **Relevance-ranked products** — best matches appear first, each with a confidence meter showing match quality
- **Category tags** on each result — clickable to filter by that category
- **Performance metrics** — response latency and total match count displayed at the top
- **Pagination** — 20 results per page with page navigation

### 3.3 Smart Query Understanding

The system adapts its search strategy based on what the user types:

- **Single word** (e.g., "bat") → treats it as a complete word, requires exact match
- **Two words** (e.g., "bath towels") → uses phrase matching and prefix matching
- **Three or more words** (e.g., "premium cotton bath towels") → uses broad matching with a similarity threshold

This ensures precise results for short queries and comprehensive results for detailed queries.

### 3.4 Category Prediction

Given any product name, the system can predict which category, sub-category, and product-category it belongs to, along with a **confidence score** (percentage). This is useful for:

- Automatically categorizing new products added to the catalog
- Verifying that existing products are in the correct category
- Understanding category distribution for any search term

---

## 4. System Architecture

The system consists of four components that work together:

```
┌─────────────────────────────────────────────────────────────────┐
│                       USER'S BROWSER                            │
│                                                                 │
│   ┌──────────────────────────────────────────────────────────┐  │
│   │              Search User Interface                        │  │
│   │  • Search box with instant autocomplete dropdown          │  │
│   │  • Results page with category filters & pagination        │  │
│   │  • Pepagora-branded design                                │  │
│   └──────────────────┬───────────────────────────────────────┘  │
│                      │ HTTP requests                             │
└──────────────────────┼──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API SERVER (FastAPI)                         │
│                  http://localhost:8000                           │
│                                                                 │
│   • /autocomplete  →  Returns suggestions as user types         │
│   • /search        →  Returns full search results with pages    │
│   • /              →  Serves the UI to the browser              │
│                                                                 │
│   Translates user queries into Elasticsearch queries,           │
│   applies the word-count strategy, handles pagination           │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                  ELASTICSEARCH 8.12.0 (Docker)                  │
│                  http://localhost:9200                           │
│                                                                 │
│   Index: pepagora_products                                      │
│   • 100,000 product documents                                   │
│   • 5 fields per document                                       │
│   • 3 custom text analyzers for intelligent matching            │
│   • Aggregation engine for category counting                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│               DATA PIPELINE (Jupyter Notebook)                  │
│                                                                 │
│   One-time setup process:                                       │
│   CSV file → Text cleaning → Index creation → Bulk ingestion    │
│   Also contains the category prediction function                │
└─────────────────────────────────────────────────────────────────┘
```

**Data flow for a typical search:**

1. User types "bath towels" into the search box
2. After 220ms of no typing, the browser sends a request to the API server
3. The API server translates the query into an Elasticsearch query
4. Elasticsearch searches the index using its text analyzers, ranks results by relevance, and returns matches
5. The API server formats the results and sends them back to the browser
6. The browser renders the results in the UI

---

## 5. How the Search Works

### 5.1 Text Analysis — How Products Become Searchable

When a product is added to the index, Elasticsearch does not store the text as-is. Instead, it processes the text through **analyzers** — pipelines that break text into searchable tokens. The system uses three different analyzers, each serving a specific purpose:

#### Analyzer 1: English Text Analyzer

**Purpose:** Powers the main full-text search.

**What it does:** Takes product text, splits it into words, converts to lowercase, removes common words ("the", "is", "and"), and reduces words to their root form (stemming).

**Example:**

| Input | → | Stored Tokens |
|---|---|---|
| "Premium Industrial Bath Towels" | → | "premium", "industri", "bath", "towel" |

"Industrial" becomes "industri" and "Towels" becomes "towel" through stemming. This means a search for "towel" will match products containing "towels", "toweling", etc. — all different forms of the same root word.

#### Analyzer 2: Autocomplete Analyzer

**Purpose:** Powers instant-as-you-type suggestions.

**What it does:** Takes product text, splits it into words, converts to lowercase, then generates **prefix tokens** for each word (called edge n-grams).

**Example:**

| Input | → | Stored Tokens |
|---|---|---|
| "Bath Towels" | → | "ba", "bat", "bath", "to", "tow", "towe", "towel", "towels" |

Every prefix from 2 to 15 characters is stored. When the user types "tow", it directly matches the stored prefix "tow" — enabling instant autocomplete without re-analyzing the entire dataset.

**Important asymmetry:** This analyzer is only used when **storing** product data. When the user **searches**, their input is analyzed with the simpler standard analyzer (just lowercase + split into words). This prevents the user's search from being prefix-expanded, which would cause excessive noise.

#### Analyzer 3: Standard Analyzer

**Purpose:** Clean, un-stemmed matching for exact terms.

**What it does:** Simply splits text into words and converts to lowercase. No stemming, no stop word removal.

| Input | → | Stored Tokens |
|---|---|---|
| "Bath Towels" | → | "bath", "towels" |

This is used when we need exact word matching without the transformations of stemming.

#### Why Three Analyzers?

Each analyzer makes a trade-off between **recall** (finding more results) and **precision** (finding the right results):

| Analyzer | Recall | Precision | Best For |
|---|---|---|---|
| English (stemmed) | High | Medium | Full-text search — "towel" matches "towels" |
| Autocomplete (prefixes) | Very High | Low | As-you-type suggestions |
| Standard (plain) | Medium | High | Exact term matching |

By applying all three analyzers to the same field (as different sub-fields), the system can choose the right one for each situation.

### 5.2 Autocomplete — Instant Suggestions While Typing

When the user types at least 2 characters, the system sends **two queries simultaneously** to Elasticsearch in a single network call:

**Query 1 — Category Suggestions:**
- Uses filtered aggregations to check which category names, sub-category names, and product-category names start with what the user typed
- Each level is independently filtered using prefix matching directly on the category keyword fields
- Returns at most one match per level (the most common matching value)

**Query 2 — Product Suggestions:**
- Finds the top 5 most relevant products matching the search text
- Uses a 4-tier relevance strategy:
  1. **Exact phrase prefix** (highest priority) — the entire search text matches at the beginning of a product name
  2. **All words match via prefixes** — every typed word appears as a prefix in the product name
  3. **Any word matches via prefixes** — at least one typed word matches
  4. **Fuzzy match** (lowest priority) — allows for typos (e.g., "towls" → "towels")
- Each product hit includes a **confidence score** calculated as a percentage relative to the highest-scoring result

**What the user sees in the dropdown:**

| Section | Content |
|---|---|
| **Metrics bar** (top) | Response latency (color-coded: green < 100ms, amber < 300ms, red ≥ 300ms) and total match count |
| **Products** (shown first) | Up to 5 product names with confidence badges (green/amber/red) — clicking one searches for it |
| **Refine by** (shown second) | Matching category names with type labels (Category, Sub-Category, Product Category) — clicking one filters search results to that category |
| **Footer** | Total matching results count + "See more" button — clicking goes to full search results |

Products are shown before categories because users typically want product suggestions first, not category navigation.

### 5.3 Full Search — Ranked Results with Filtering

When the user presses Enter or clicks "See more," the system runs a full search query that returns:

- **Up to 20 results per page**, ranked by relevance score
- **Latency and match count metrics** displayed at the top of results
- **Total match count** with page navigation (page 1, 2, 3, etc.)
- **Category tags** on each result — clicking any tag filters results to that category
- **Confidence meter** on each result — a visual bar showing how confident the match is (green ≥ 70%, amber 40–69%, red < 40%), calculated relative to the highest-scoring result
- **Relevance score** displayed numerically on each result
- **Active filter indicator** with a "✕" button to remove the filter

**How results are ranked:**

Every matching product receives a relevance score based on multiple factors:

| Factor | Weight | Description |
|---|---|---|
| Exact word match in product name | Highest | All search words appear as whole tokens in the product name |
| Phrase match in product name | High | Search words appear in the correct order |
| Prefix match in product name | Medium | Search words match the beginning of words in the product name |
| Fuzzy match | Low | Near-matches allowing for typos |
| Category name match | Bonus | Search words appear in the product's category name — small score boost |
| Description match | Low bonus | Search words appear in the product's description |

Products with higher combined scores appear first in the results.

**Category filtering:**

When the user clicks a category tag (e.g., "Bath Accessories"), the search re-runs with a filter that restricts results to products in that category. The filter:

- Checks all three category levels (category, sub-category, product-category)
- Does not affect relevance ranking — it only removes non-matching products
- Persists across page navigation
- Can be removed by clicking "✕ Remove"

### 5.4 Category Prediction — Automatic Classification

The `predict_category()` function in the notebook takes any product name and predicts its most likely category at each level.

**How it works:**

1. **Search** — Run the product name as a query against the entire index using the 4-tier relevance strategy
2. **Sample** — Take the top 50 most relevant matching products
3. **Count** — For each category level, count how many of those 50 products belong to each category value
4. **Score** — The category with the highest count wins. Its confidence = (its count ÷ total sampled) × 100%

**Example:**

| Input | "bath towels" |
|---|---|
| Total matches found | 1,530 products matched |
| Documents sampled | Top 50 most relevant |
| **Predicted category** | Sanitaryware & Bathroom Fittings (**82% confidence** — 41 of 50 sampled docs) |
| **Predicted sub-category** | Bath Accessories (**66% confidence** — 33 of 50) |
| **Predicted product-category** | Bath Towels (**58% confidence** — 29 of 50) |

The confidence decreases at lower levels because categories become more specific and there are more possible values, so fewer products agree on the exact match.

**The full breakdown** is also available — showing all category values that appeared in the sample with their individual confidence scores. This is useful when the prediction is ambiguous (e.g., 40% vs. 35% between two categories).

---

## 6. Data Pipeline — From Raw CSV to Searchable Index

The data pipeline is a one-time setup process that transforms the raw product CSV file into a searchable Elasticsearch index. It runs in a Jupyter notebook with 7 sequential steps:

### Step 1 — Install Dependencies

Installs four Python packages:

| Package | Purpose |
|---|---|
| elasticsearch (version 8.x) | Python client for communicating with Elasticsearch |
| pandas | Data loading and manipulation |
| tqdm | Progress bar during bulk ingestion |
| ftfy | Unicode text repair |

**Important:** The Elasticsearch client is pinned to version 8.x (not 9.x) because the 9.x client sends a compatibility header that Elasticsearch 8.12.0 does not understand, causing all requests to fail with an error.

### Step 2 — Connect to Elasticsearch

Establishes a connection to the Elasticsearch instance running in Docker at `localhost:9200`. The connection is configured with a 30-second timeout and automatic retry on network failures (up to 3 retries).

### Step 3 — Load the CSV Dataset

Loads the file `Dataset/pepagoraDb.liveproducts.csv` containing 100,000 product rows. Key fields used by the system:

| Field | Description | Example |
|---|---|---|
| productName | The product's display name | "Premium Industrial Bath Towels Set" |
| productDescription | Longer product description | "High-quality cotton bath towels for..." |
| category_name | Top-level category | "Sanitaryware & Bathroom Fittings" |
| subCategory_name | Mid-level category | "Bath Accessories" |
| productCategory_name | Most specific category | "Bath Towels" |

### Step 4 — Clean Text with ftfy

Repairs garbled Unicode characters (called "mojibake") that occur when text is encoded in one character set but decoded in another. For example, `"Ã©"` is repaired back to `"é"`. This ensures all product text is clean before indexing.

### Step 5 — Define Index Settings & Mappings

Creates the Elasticsearch index with:

- **1 shard, 0 replicas** — optimal for a single-node setup with 100,000 documents
- **3 custom analyzers** — the English analyzer, autocomplete analyzer, and standard analyzer described in Section 5.1
- **Field mappings** — defines how each product field is stored and searched

Each text field (productName, productDescription) is indexed three ways simultaneously:

| Sub-field | Analyzer | Purpose |
|---|---|---|
| Main field | English (stemmed) | Full-text search with word-form matching |
| .ngram | Autocomplete (prefixes at index, standard at search) | Type-ahead suggestions |
| .text | Standard (plain) | Exact un-stemmed matching |

Category fields (category_name, subCategory_name, productCategory_name) are stored as **keywords** — exact values with no text processing. This allows precise filtering and counting (e.g., "show me all products in Sanitaryware & Bathroom Fittings").

### Step 5b — Drop & Recreate Index

A safety step that deletes any existing index before creating a fresh one. This allows the entire pipeline to be re-run cleanly without conflicts.

### Step 6 — Bulk Ingest 100,000 Documents

Inserts all products into Elasticsearch in an optimized batch process:

1. **Automatic refresh is temporarily disabled** — normally Elasticsearch makes new documents searchable every second. During bulk loading, this wastes processing power. It is turned off during ingestion and restored afterward.

2. **Documents are sent in batches of 500** — rather than inserting one product at a time (100,000 individual requests), the system sends batches, reducing network overhead dramatically.

3. **Null values are cleaned** — fields containing "nan", "none", "null", "N/A", or empty strings are stored as proper null values instead of misleading text.

4. **After ingestion completes**, the index is refreshed (all documents become searchable) and compacted into a single optimized segment for maximum read performance.

**Results:**

| Metric | Value |
|---|---|
| Documents submitted | 100,000 |
| Documents succeeded | 100,000 |
| Documents failed | 0 |
| Total time | 41.7 seconds (~2,400 docs/sec) |

### Step 7 — Category Prediction Function

Contains the `predict_category()` function described in Section 5.4. Includes a smoke test with 5 diverse product names to verify predictions are sensible:

| Test Product | Predicted Category | Confidence |
|---|---|---|
| "bath towels" | Sanitaryware & Bathroom Fittings | 82% |
| "industrial pump" | Industrial Machinery | 90% |
| "led bulb 9w" | Electrical & Electronics | 88% |
| "cotton saree" | Textiles & Fabrics | 94% |
| "cement 53 grade" | Building & Construction | 96% |

---

## 7. Search Intelligence — The Word-Count Strategy

The search system adapts its behavior based on the **number of words** in the user's query. This is a critical design choice that dramatically improves result quality.

### Why Different Strategies?

A single search approach cannot handle both short and long queries well:

- **Short queries** (1 word) need **precision** — "bat" should find cricket bats, not bath towels
- **Long queries** (3+ words) need **recall** — "premium cotton bath towels white" should find results even if not all 5 words appear

### The Three Strategies

#### Single-Word Queries (e.g., "bat", "pump", "saree")

The system requires the word to match as a **complete token** in the product name. No prefix matching is used. No autocomplete-style prefix expansion is used.

This means "bat" matches products containing the word "bat" but NOT "bath", "bathroom", or "battery".

A small amount of fuzzy matching is included to handle typos (e.g., "pummp" still finds "pump").

#### Two-Word Queries (e.g., "bath towels", "led bulb")

The system requires **both words to match** in the product name. Additionally:

- Products where the words appear **in the correct order and adjacent** score highest (phrase matching)
- Products where the words appear **in the correct order but with a word between them** score second highest
- Prefix matching is now safe to use because requiring BOTH words to match naturally prevents noise

#### Three-or-More-Word Queries (e.g., "premium industrial bath towels")

The system uses **broad matching** — at least 75% of the search words must appear in the product name. For a 4-word query, at least 3 words must match. This ensures reasonable results even when the user includes words not present in product names.

Products where **all words match** get the highest relevance score. Products where the words appear as a phrase score second highest.

---

## 8. Search Quality — How We Fixed the "bat ≠ bath" Problem

This section documents a critical quality issue and how it was resolved.

### The Problem

During testing, searching for **"bat"** returned products like "Premium Bath Towels", "Industrial Bathroom Fixtures", and "Battery Pack 12V." Users searching for cricket bats or baseball bats were getting completely irrelevant bathroom and battery products.

### Why It Happened

The original search treated every query the same way, using a technique called **prefix matching** where the search term is treated as the beginning of a word. So "bat" was interpreted as "bat…" — matching any word starting with "bat":

- "bat" → **bat**h, **bat**hroom, **bat**tery, **bat** ✓

### How Bad Was It?

We analyzed the actual data:

| Metric | Count |
|---|---|
| Products containing "bat" as a complete word | 38 |
| Products containing words starting with "bat" (bath, bathroom, battery, etc.) | 1,530 |
| **Noise ratio** | **97.5% of results were wrong** |

For every 1 correct result, there were approximately 40 incorrect results.

### The Fix

Instead of using prefix matching for all queries, the system now uses the **word-count strategy** (Section 7):

- For **single-word queries** like "bat" — no prefix matching, no autocomplete-style expansion. The word must match as a complete token.
- For **multi-word queries** — prefix matching is re-enabled because having multiple words naturally prevents noise.

### Results After the Fix

| Query | Before (broken) | After (fixed) |
|---|---|---|
| "bat" | 1,530 results (bath, bathroom, battery...) | 38 results (all actual bat products) |
| "bath towels" | ✓ Worked correctly | ✓ Still works correctly |
| "pump" | Mixed with "pumpkin" etc. | Only pump-related products |
| "led bulb" | ✓ Worked correctly | ✓ Still works correctly |

Single-word precision improved dramatically without affecting multi-word queries.

---

## 9. User Interface — What the User Sees

### Home Page

The home page presents a clean, focused search experience:

- **Navigation bar** at the top with the Pepagora text logo (styled in brand red), along with language selector ("English | IN"), "Post Buying Requirement", "Login", and "Get Started" buttons
- **Central hero area** with a large "pepagora" text logo, a vertical divider, "The B2B Growth Engine" tagline, and a prominent search box
- **Animated scroll hint** — a bouncing downward arrow with "Scroll down" text below the search box

The search box has a **red pill-shaped border** matching Pepagora's brand colors, with a subtle red shadow effect that deepens when the input is focused.

### Autocomplete Dropdown

As soon as the user types 2+ characters and pauses for 220 milliseconds:

1. A dropdown appears below the search box
2. **Metrics bar** at the top shows response latency (color-coded: green for fast, amber for moderate, red for slow) and total match count
3. **Product suggestions** appear first — each showing a magnifier icon, the product name, and a **confidence badge** (green ≥ 70%, amber 40–69%, red < 40%) indicating match quality
4. **"Refine by" section** appears below — showing matching categories with red folder icons, category name, and a type label (Category, Sub-Category, or Product Category)
5. **Footer** shows the total matching results count on the left and a "See more" button with an arrow on the right

The dropdown supports **keyboard navigation** — Arrow keys move between items, Enter selects, Escape closes.

**Stale response handling:** If the user types quickly, multiple API calls may be in flight simultaneously. The system automatically discards responses that no longer match what the user has typed, preventing the dropdown from flickering between old and new results.

### Search Results Page

After pressing Enter or clicking a suggestion:

- The hero section collapses
- A **metrics strip** at the top shows response latency and total match count
- Results appear in **white cards** with subtle borders and a red-tinted shadow on hover
- Each card shows:
  - **Product name** (bold)
  - **Description** (if available, truncated to 200 characters)
  - **Category tags** (pill-shaped, light red) — clickable to filter results by that category
  - **Confidence meter** — a horizontal bar with color fill (green/amber/red) showing match confidence as a percentage, plus the raw relevance score
- When a filter is active, the category appears as a tag in the header with a "✕" button to remove it
- **Pagination** with "← Prev" / "Next →" buttons and page numbers, showing a window of ±2 pages around the current page
- A **"← New Search"** button in the header returns to the home page

---

## 10. Performance

| Metric | Value |
|---|---|
| Total products indexed | 100,000 |
| Bulk ingestion time | 41.7 seconds (~2,400 docs/sec) |
| Index size on disk | ~50 MB |
| Autocomplete response time | < 50 ms typical |
| Full search response time | < 30 ms typical |
| Elasticsearch memory usage | 512 MB (JVM heap) |
| UI file size | ~25 KB (single file, no dependencies) |
| Frontend debounce interval | 220 ms (limits API calls during fast typing) |

These are measured on a local development machine. Production performance will depend on hardware and network conditions.

**Real-time latency visibility:** Both API endpoints measure their own response time and return it in the response (`latency_ms`). The UI displays this as a color-coded chip on every autocomplete dropdown and search results page, giving users and developers instant visibility into how fast the system is performing at any moment.

---

## 11. Technology Stack

| Component | Technology | Why This Choice |
|---|---|---|
| **Search engine** | Elasticsearch 8.12.0 | Purpose-built for full-text search — offers analyzers, fuzzy matching, autocomplete, relevance ranking, and aggregations that databases cannot efficiently provide |
| **Infrastructure** | Docker Compose | Elasticsearch runs in a container — one command to start, no manual installation, consistent across machines |
| **Data pipeline** | Python + Jupyter Notebook | Interactive step-by-step execution, easy to re-run individual steps, visual output for verification |
| **Backend API** | FastAPI (Python) | Lightweight, fast, auto-generates API documentation, native async support |
| **Frontend** | Vanilla HTML + CSS + JavaScript | Zero dependencies, no build step, instant load, easy to deploy — appropriate for this scope |
| **Data cleaning** | ftfy (Python library) | Automatically detects and repairs garbled Unicode text |
| **Dataset** | CSV (100,000 rows) | Simple, portable format — loaded once during the pipeline |

---

## 12. Key Design Decisions

### Why Elasticsearch instead of a database?

| Capability | Database (e.g., PostgreSQL) | Elasticsearch |
|---|---|---|
| Autocomplete | Not built-in | Native edge-ngram support |
| Relevance ranking | Manual and complex | Automatic BM25 algorithm with boost controls |
| Typo tolerance | Not built-in | Native fuzzy matching |
| Stemming | Basic or via extensions | Built-in for 30+ languages |
| Prefix matching | Slow `LIKE 'xyz%'` | Pre-computed prefix tokens (instant) |
| Category counting | `GROUP BY` (works) | Purpose-built aggregation framework |

For a search-centric use case with 100,000 products, Elasticsearch provides capabilities that would be extremely difficult and slow to replicate in a traditional database.

### Why three analyzers instead of one?

A single analyzer cannot serve all needs. Stemming (needed for full-text search) conflicts with prefix generation (needed for autocomplete), which conflicts with exact matching (needed for precise queries). Three specialized analyzers, applied as sub-fields on the same data, let the system use the right approach for each situation.

### Why adapt the query based on word count?

The "bat ≠ bath" problem (Section 8) proved that one-size-fits-all queries degrade quality for short searches. The word-count strategy is the simplest solution that correctly handles the full spectrum from 1-word to many-word queries.

### Why a single-file frontend?

For this project's scope (one search page with autocomplete), a single HTML file with embedded CSS and JavaScript is optimal:
- No build tools required (no npm, webpack, or bundler)
- No framework dependencies to manage or update
- Loads instantly (25 KB total)
- Portable — works on any web server

A component framework (React, Vue, etc.) would be the right choice if the UI grows significantly larger.

### Why is the Elasticsearch client pinned to version 8.x?

The 9.x Python client sends a compatibility header (`compatible-with=9`) that Elasticsearch 8.12.0 rejects with an HTTP 400 error. This is a known cross-version incompatibility. The pin ensures reliable communication with the server.

### Why is CORS set to allow all origins?

During development, the UI may be served from different origins (e.g., VS Code Live Server vs. FastAPI on port 8000). The wildcard allows both to work. **This must be restricted to the actual domain before production deployment.**

---

## 13. How to Run the System

### Prerequisites

- **Docker Desktop** — installed and running
- **Python 3.9 or later** — with a virtual or conda environment
- **Jupyter support** — VS Code with the Jupyter extension, or JupyterLab

### Steps

| Step | Action | What It Does |
|---|---|---|
| 1 | Run `docker-compose up -d` in the project folder | Starts Elasticsearch in a Docker container |
| 2 | Wait 15–30 seconds, then verify at `http://localhost:9200` | Confirms Elasticsearch is running |
| 3 | Open `category_mapper.ipynb` and run all cells (Steps 1–7) | Installs packages, cleans data, creates the index, ingests 100,000 products |
| 4 | Run the API server: `cd api && uvicorn main:app --reload --port 8000` | Starts the backend on port 8000 |
| 5 | Open `http://localhost:8000` in a browser | Access the search UI |

### Verification Checklist

- [ ] Type "bath" in the search box — dropdown appears with metrics bar, product suggestions with confidence badges, and category refinements
- [ ] Press Enter — full results page shows with latency metrics, confidence meters on each result, and pagination
- [ ] Click a category tag on a result — results filter to that category, with "✕" to remove
- [ ] Type "bat" — results show actual bat products, NOT bath/bathroom/battery products
- [ ] Check the API directly at `http://localhost:8000/docs` — interactive API documentation

---

## 14. Glossary

| Term | Plain-Language Definition |
|---|---|
| **Elasticsearch** | A search engine that stores data in a way optimized for fast text searching, unlike databases which are optimized for structured data retrieval |
| **Index** | Elasticsearch's equivalent of a database table — the container that holds all product documents |
| **Document** | A single product record stored in the index |
| **Analyzer** | A text processing pipeline that determines how text is broken into searchable tokens |
| **Tokenizer** | The first step of an analyzer — splits text into individual words |
| **Stemming** | Reducing words to their root form — "towels" → "towel", "running" → "run" — so different forms of the same word all match |
| **Edge n-gram** | A technique that generates prefixes of words — "towels" produces "to", "tow", "towe", "towel", "towels" — enabling autocomplete |
| **Stop words** | Common words like "the", "is", "and" that are removed during analysis because they add noise, not meaning |
| **Fuzzy matching** | Allowing searches to tolerate typos — "towls" matches "towels" by allowing 1–2 character differences |
| **Relevance score** | A number computed by Elasticsearch indicating how well a document matches the search query — higher scores appear first in results |
| **Aggregation** | A counting/grouping operation on search results — similar to SQL's GROUP BY — used to count products per category |
| **Keyword field** | A field stored as an exact value with no text processing — used for category names where exact matching is needed |
| **Bool query** | A query that combines multiple conditions — "must" (required), "should" (optional bonus), "filter" (required, no scoring) |
| **Boost** | A multiplier that increases the importance of a particular search condition — "boost: 5" means "5× more important for ranking" |
| **Pagination** | Splitting results across numbered pages (20 results per page) so users can browse through large result sets |
| **Debouncing** | Waiting for the user to pause typing before sending a search request — prevents flooding the server with requests on every keystroke |
| **Shard** | A subdivision of an index for parallel processing — with 100,000 documents, a single shard is optimal |
| **Replica** | A backup copy of a shard on another server — not used in our single-server setup |
| **Docker** | A platform that runs software in isolated containers — Elasticsearch runs inside a Docker container |
| **FastAPI** | A modern Python web framework used to build the API server |
| **msearch** | Multi-search — Elasticsearch's ability to execute multiple queries in a single network call, reducing latency |

---

*End of Document — Pepagora Elasticsearch-Powered Product Search System — Version 2.0*
