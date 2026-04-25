# Pepagora API Documentation (Postman)

This document is intentionally scoped to the **PBR (Post Buy Request) Quick Post** flow only.
It documents the **two APIs used by the PBR quick-post UI**: **Suggestions** and **Map Category**.

## Base URL

- Local default: `http://127.0.0.1:8000`

## Backend Override (Important)

Most API routes support backend override via:

- Query param: `backend=elasticsearch|opensearch`
- Header: `x-search-backend: elasticsearch|opensearch`

Response headers include selected backend:

- `x-search-backend`
- `x-search-backend-requested` (if requested)
- `x-search-backend-available` (if requested)
- `x-search-backend-fallback-from`, `x-search-backend-fallback-to` (if fallback happened)

---

## PBR (Post Buy Request) — Quick Post APIs (Suggestions + Mapping)

The “Post Buy Request” quick-post UI uses **only these two APIs**:

- **Step A**: fetch suggestions for the buyer’s typed product name
- **Step B**: map the (typed or selected) product name to the best category (top 1–3 cards)

Both are `GET` endpoints (payload is sent via query params, not a JSON body).

---

### 3.4.1 Suggestions (PBR Step A)

- **Method**: `GET`
- **Path**: `/ui-api/suggestions`
- **Query params**
  - **`q`** (string, optional): buyer typed text (can be empty)
  - **`limit`** (int, optional, default `12`, min `3`, max `30`)
- **Optional backend override**
  - Query param: `backend=elasticsearch|opensearch`
  - Header: `x-search-backend: elasticsearch|opensearch`

Example request:

`GET {{baseUrl}}/ui-api/suggestions?q=steel%20bottle&limit=8&backend=opensearch`

Expected response body (example):

```json
{
  "query": "steel bottle",
  "suggestions": [
    "steel bottle",
    "stainless steel bottle",
    "steel water bottle",
    "steel bottle 1 litre"
  ],
  "ranking_order": [
    "exact",
    "prefix",
    "ordered_phrase",
    "ordered_tokens",
    "weak_contains",
    "fuzzy_fallback"
  ],
  "count": 4,
  "latency_ms": 12.4
}
```

---

### 3.4.2 Map Category (PBR Step B)

- **Method**: `GET`
- **Path**: `/ui-api/map-category`
- **Query params**
  - **`q`** (string, required, min length `2`): typed product name
  - **`selected`** (string, optional): if the buyer clicked a suggestion, pass that here
  - **`max_cards`** (int, optional, default `3`, min `1`, max `6`)
- **Optional backend override**
  - Query param: `backend=elasticsearch|opensearch`
  - Header: `x-search-backend: elasticsearch|opensearch`

Example request (typed only):

`GET {{baseUrl}}/ui-api/map-category?q=steel%20bottle&max_cards=3&backend=opensearch`

Example request (buyer selected a suggestion):

`GET {{baseUrl}}/ui-api/map-category?q=steel%20bottle&selected=stainless%20steel%20bottle&max_cards=3&backend=opensearch`

Expected response body (example):

```json
{
  "query": "steel bottle",
  "selected": "stainless steel bottle",
  "normalized_query": "stainless steel bottle",
  "decision": "confirm",
  "confidence": 0.8421,
  "margin": 0.214,
  "needs_confirmation": true,
  "auto_mapped": false,
  "intent_query": "stainless steel bottle",
  "phrase_candidates": ["steel bottle", "stainless steel bottle"],
  "top_category": {
    "product_category_id": "PCAT_12345",
    "breadcrumb": "Kitchen & Dining >> Bottles >> Stainless Steel Bottles",
    "count": 22,
    "correlation_pct": 78.3,
    "avg_token_coverage": 0.712,
    "ranking_basis": {
      "lexical_cluster_hits": 10,
      "semantic_cluster_hits": 6,
      "product_vote_hits": 6,
      "cluster_hits": 16,
      "total_evidence_hits": 22,
      "exact_hits": 10,
      "prefix_hits": 0,
      "token_and_hits": 0,
      "semantic_hits": 6
    },
    "lane_scores": { "lexical": 2.4123, "semantic": 1.1144, "product_vote": 0.8831 },
    "lane_score_pct": { "lexical": 54.8, "semantic": 25.3, "product_vote": 19.9 },
    "sample_products": ["Insulated Steel Water Bottle", "Stainless Steel Bottle 1L"],
    "sample_keywords": ["steel bottle", "stainless steel bottle"],
    "confidence": 0.8421,
    "confidence_raw": 0.783,
    "confidence_calibrated_heuristic": 0.821,
    "confidence_calibrated_learned": null,
    "confidence_model_used": false,
    "avg_product_support": 114.2,
    "avg_category_ambiguity": 1.4,
    "reason": "conf_raw=0.783, conf_cal=0.842, conf_heur=0.821, conf_model=off, support=114.20, ambiguity=1.40, lexical_hits=10, semantic_hits=6, product_votes=6"
  },
  "cards": [
    {
      "product_category_id": "PCAT_12345",
      "breadcrumb": "Kitchen & Dining >> Bottles >> Stainless Steel Bottles",
      "count": 22,
      "correlation_pct": 78.3,
      "avg_token_coverage": 0.712,
      "ranking_basis": {
        "lexical_cluster_hits": 10,
        "semantic_cluster_hits": 6,
        "product_vote_hits": 6,
        "cluster_hits": 16,
        "total_evidence_hits": 22,
        "exact_hits": 10,
        "prefix_hits": 0,
        "token_and_hits": 0,
        "semantic_hits": 6
      },
      "lane_scores": { "lexical": 2.4123, "semantic": 1.1144, "product_vote": 0.8831 },
      "lane_score_pct": { "lexical": 54.8, "semantic": 25.3, "product_vote": 19.9 },
      "sample_products": ["Insulated Steel Water Bottle", "Stainless Steel Bottle 1L"],
      "sample_keywords": ["steel bottle", "stainless steel bottle"],
      "confidence": 0.8421,
      "confidence_raw": 0.783,
      "confidence_calibrated_heuristic": 0.821,
      "confidence_calibrated_learned": null,
      "confidence_model_used": false,
      "avg_product_support": 114.2,
      "avg_category_ambiguity": 1.4,
      "reason": "conf_raw=0.783, conf_cal=0.842, conf_heur=0.821, conf_model=off, support=114.20, ambiguity=1.40, lexical_hits=10, semantic_hits=6, product_votes=6"
    }
  ],
  "matched_clusters": 16,
  "product_vote_hits": 12,
  "lanes_used": ["lexical", "semantic", "product_vote"],
  "semantic_used": true,
  "product_fallback_used": true,
  "phase3_active": true,
  "alerts": []
}
```

---

## Quick Postman Setup

1. Create an environment variable:
   - `baseUrl = http://127.0.0.1:8000`
2. Use URLs like:
   - `{{baseUrl}}/ui-api/suggestions?q=steel%20bottle&limit=8&backend=opensearch`
   - `{{baseUrl}}/ui-api/map-category?q=steel%20bottle&max_cards=3&backend=opensearch`
3. Optional header for backend switching:
   - `x-search-backend: opensearch`
