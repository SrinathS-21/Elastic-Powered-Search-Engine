# Pepagora Search Functionality Approach (Updated Submission Document)

Version date: 2026-04-14

## Document Status

- Status: Updated and current.
- Updated on: 2026-04-14.
- Primary decision document: `DOCUMENTATION.md`.
- Implementation tracker (operational details): `IMPLEMENTATION_TRACKER.md`.

## What Was Updated in This Revision

1. Reorganized document flow for easier reading:
- Executive summary -> objective/scope -> use case -> approach -> cost -> challenges -> technical deep dive.

2. Kept the document business-focused:
- Removed tracker-level operational details from this file.
- Preserved implementation tracker in separate file.

3. Updated challenge statements to reflect real issues faced:
- Kept only practical relevance/quality issues from implementation and rollout.

4. Kept AWS-only costing and scaling plan:
- Baseline and stress allocation with clear justification and budget envelope.

## 1. Executive Summary

This document explains the implemented search and category-mapping approach for Post Buy Request.

The design is intentionally:

- Lexical-first for speed, stability, and explainability.
- Reliability-aware to handle sparse and ambiguous keyword clusters.
- Fallback-enabled (semantic cluster and product vote) only when confidence is weak.
- Audit-friendly, with explicit confidence, decision, margin, lanes used, and evidence in API responses.

Decision supported by this document:

- Approve production use of the lexical-first, reliability-aware mapping design with controlled fallback lanes and AWS baseline resource allocation.

## 1.1 Business Objective and Scope

This section provides the formal objective and scope statement for governance and sign-off.

| Area | Definition |
|---|---|
| Primary objective | Convert user intent text (typed query or selected suggestion) into reliable category mapping. |
| Secondary objective | Provide high-quality autosuggestions with low noise and low latency. |
| In scope | Intent normalization, suggestion quality controls, confidence-based category mapping, conditional semantic fallback, product-vote fallback, telemetry, and operational guardrails. |
| Out of scope | Product ranking as the primary business output for this decision stage. |
| Current quality status | Latest baseline and canary validation set shows `failure_count=0` across critical, pair, and random checks. |

## 1.2 Use Case

This section explains what user problem is being solved and what success looks like in business terms.


### Use Case Summary

- A B2B buyer enters free-text intent (for example, `ss wire`, `color sorter`, `air compressor`) or selects an autosuggestion.
- The system maps that intent to the most relevant product category path.
- The UI returns one of three outcomes:
  - direct mapping (`auto_map`),
  - user-assisted confirmation (`confirm`),
  - alternatives (`options`) for ambiguous cases.

### Use Case Description

Business problem being solved:

- Buyer language is inconsistent (abbreviations, spelling noise, partial terms).
- Keyword clusters can be sparse and may map to multiple categories.
- A wrong direct mapping leads to poor browsing flow and lower conversion confidence.

Expected business outcomes:

- Faster buyer navigation to the correct catalog area.
- Explainable mapping decisions for product, support, and operations teams.
- Controlled behavior on ambiguous intent instead of forcing risky auto-decisions.

### Success Indicators for This Use Case

- High-quality suggestion acceptance by users.
- Lower wrong-auto-map incidents.
- Stable latency at peak hours.
- Better top-1 or top-3 category acceptance in validation checks.

## 1.3 Approach

This section explains why the architecture is designed this way and how it was implemented.

### Selected Approach

The implemented approach is intentionally multi-lane and reliability-aware:

1. Lexical-first mapping and suggestions for speed and explainability.
2. Reliability weighting using support (`product_count`) and ambiguity (`category_count`).
3. Conditional semantic-cluster fallback only when lexical confidence is weak.
4. Product-vote fallback as final safety net when confidence remains below trigger threshold.
5. Governance and telemetry controls so rollout can be monitored, tuned, and rolled back safely.

### Why This Setup Was Chosen

| Design Choice | Why We Selected It | Business Outcome |
|---|---|---|
| Lexical-first suggestion and mapping | Most user intent is resolved quickly with direct keyword evidence, which is easier to explain and audit. | Faster response and lower risk of unexpected category jumps. |
| Reliability-aware scoring | Data includes sparse and ambiguous clusters; support and ambiguity weighting prevents false confidence. | Fewer wrong auto-maps and better trust in confidence output. |
| Conditional semantic fallback | Semantic retrieval is useful but should not run blindly for every request. | Better accuracy for weak intents without unnecessary cost or latency. |
| Product-vote fallback as final safety net | Some difficult queries remain weak after lexical and semantic lanes. | Higher recovery for hard queries and fewer no-match outcomes. |
| Explicit decision states (`auto_map`, `confirm`, `options`) | Not every query should be forced into one confident decision. | Business can control user experience by confidence level. |
| Telemetry + guardrails | Rollout quality must remain measurable, auditable, and reversible. | Safer production adoption and clear quality governance. |

### Algorithms/Models We Used

| Component | Core Method / Model | Input Signals | Output Produced | Key Runtime Controls |
|---|---|---|---|---|
| Suggestion ranking | Multi-stage lexical ranking (`exact`, `prefix`, `ordered_phrase`, `ordered_tokens`, `contains`, `fuzzy`) | query text, canonical tokens, keyword fields (`keyword_name`, `variant_terms`, guarded `long_tail/head_terms`) | ranked suggestion list | `HEAD_TERMS_HARD_CAP`, per-doc term limits, stage gating (`<=4`) |
| Intent reliability scoring | Support-ambiguity reliability factor (`log1p(product_count)` + ambiguity penalty) | `product_count`, `category_count` | reliability-weighted vote contribution | `KEYWORD_P95_PRODUCT_COUNT`, `RELIABILITY_BETA` |
| Category decisioning | Weighted vote aggregation across lanes | lexical score, match signal, semantic vote, product vote | `top_category`, alternatives, confidence, margin | `AUTO_MAP_CONFIDENCE`, `AUTO_MAP_MARGIN`, `CONFIRM_MAP_CONFIDENCE` |
| Semantic retrieval lane | Dense vector kNN on cluster/product vectors | query embedding (`BGE`), index vectors (`keyword_vector_*`, `product_vector_*`) | semantic candidates for fallback/rerank | `SEMANTIC_CLUSTER_WEIGHT`, fallback triggers |
| Embedding model | `BAAI/bge-base-en-v1.5` (`768`-dim, normalized embeddings) | query/document text | vector representation for semantic matching | cache behavior (`lru_cache`), batching and device config |
| Confidence calibration | Runtime + learned isotonic calibration | raw confidence from vote scores, optional label-trained model | calibrated confidence for safer decisions | calibration toggles, model file validity checks |
| Synonym expansion | Data-driven synonym dictionary | configured synonym file + query text | expanded lexical query variants | governance scripts (`validate`, `review`, `apply`, `rollback`) |

Algorithm quality checks already covered in this implementation:

- deterministic ranking order for repeated inputs,
- guardrails against noisy head-term dominance,
- strict confidence gating before auto decisions,
- canary safety to prevent ranking behavior divergence.

### Implementation Steps Completed

1. Profiled indexed data quality (sparsity, ambiguity, outliers) to set safeguards.
2. Implemented lexical suggestion lane with strong denoise and head-term guardrails.
3. Implemented confidence-based mapping lane with explicit decision policy.
4. Added semantic and product-vote fallbacks behind thresholds.
5. Added Phase-3 canary, telemetry, and calibration controls.
6. Added synonym governance scripts (validate, review, apply, rollback).
7. Added observability and canary guard scripts for hold/promote/rollback decisions.
8. Validated with regression artifacts:
   - baseline (`100%`): `critical_checks=5`, `pair_checks=5`, `random_checks=40`, `failure_count=0`
   - canary (`30%`): `critical_checks=5`, `pair_checks=5`, `random_checks=40`, `failure_count=0`

## 1.4 Costing and Resource Allocation (AWS)

This section explains both the numbers and the reasoning behind the selected cloud setup.

The resource setup is designed to balance three goals:

- predictable search and mapping latency,
- controlled monthly spend for long-run operations,
- clean horizontal scaling during stress periods.

Budget envelope (monthly):

- Baseline run (right-sized): `$1,011.46`
- Optimal run (baseline + API scale-out): `$1,179.65`
- Sustained stress run (standard scale-out): `$1,376.64`
- Sustained stress run (with dedicated BGE inference tier): `$1,713.02`

Pricing reference method:

- Source: AWS public pricing offer file for `AmazonEC2`.
- Endpoint: `https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/us-east-1/index.json`
- Region used for snapshot: `us-east-1` (US East, N. Virginia).
- Snapshot date: `2026-04-14`.
- Currency: `USD`.
- Monthly conversion: `730` hours.
- Scope: EC2 on-demand Linux + EBS storage only (network/data transfer, NAT, ALB, snapshot growth, and other managed-service costs are excluded).

### BGE Hosting Requirement (Right-Sized)

Model in use:

- `BAAI/bge-base-en-v1.5` (`768`-dimension embeddings), loaded through `sentence-transformers`.

What this means operationally:

- BGE embedding generation is used in semantic/hybrid paths and mapping fallback logic, not on every pure lexical request.
- Query embedding has an in-process cache (`lru_cache`) that reduces repeated work for frequent terms.

Recommended hosting now (no over-engineering):

- Keep BGE inference inside the existing API tier (`2 x m6i.xlarge`) for current scale.
- Do not introduce a dedicated GPU tier at this stage.
- Keep one batch/quality worker (`1 x m6i.large`) for non-serving tasks.

When to split BGE into a dedicated inference tier:

- If embedding latency (`p95`) remains high under sustained load, or
- If API CPU pressure remains high after standard API horizontal scale-out.

Dedicated BGE tier (only when needed):

- Start with `1 x m6i.2xlarge`, then scale to `2` instances only if sustained demand requires it.

### Elasticsearch Hosting Requirement (Right-Sized)

Sizing basis (current data profile):

- Product index documents: `100,000`
- Keyword cluster index documents: `511,124`
- Dense vector search enabled for semantic and fallback behavior.

Recommended baseline cluster (production-safe, not over-engineered):

- `3 x m6i.xlarge` Elasticsearch nodes
- `3 x EBS gp3 (300 GB)` data volumes
- Roles combined (data + cluster management) to avoid unnecessary coordinator-only nodes at this stage

Why this is right-sized:

- Three nodes provide high availability and shard balance.
- `m6i.xlarge` provides enough memory/CPU headroom for current document counts and vector workloads.
- `gp3 300 GB` per node keeps storage cost controlled while maintaining sufficient growth headroom.

#### Live Price Snapshot (Compute and Disk)

| Resource | Unit Price | Monthly Equivalent |
|---|---:|---:|
| `m6i.large` (Linux, on-demand) | `$0.096/hour` | `$70.08/month` |
| `m6i.xlarge` (Linux, on-demand) | `$0.192/hour` | `$140.16/month` |
| `m6i.2xlarge` (Linux, on-demand) | `$0.384/hour` | `$280.32/month` |
| `m6i.4xlarge` (Linux, on-demand) | `$0.768/hour` | `$560.64/month` |
| `EBS gp3` storage | `$0.08/GB-month` | `$24.00/month` for `300 GB` |
| `EBS gp2` storage | `$0.10/GB-month` | `$30.00/month` for `300 GB` |
| `EBS io2` storage | `$0.125/GB-month` | `$37.50/month` for `300 GB` |

### Concurrency and Capacity Scenarios (Best, Optimal, Worst)

Purpose of this section:

- to communicate required cloud allocation to stakeholders first, then the concurrency that allocation can support,
- to make capacity planning resource-first (concurrency is a derived output, not an independent input),
- to support go-live scaling decisions with a clear cost-to-capacity mapping,
- not to claim final SLA certification until dedicated load testing is completed.

Planning method:

- Resource allocation is the primary input.
- Supported request rate is limited by the bottleneck layer in the allocated stack (API, Elasticsearch, or BGE when enabled).
- `RPS_capacity = min(API capacity, Elasticsearch capacity, BGE capacity when enabled)`
- `Estimated concurrency = ceil(RPS_capacity x P95 latency in seconds)`
- This means concurrency is derived from provisioned resources; it is not an independent assumption.
- Observed telemetry from controlled 100-row runs (`1.63-3.74 RPS`, median `2.34 RPS`) is used as baseline reference; scenario envelopes below are resource-scaled planning values.
- Final SLA lock still requires dedicated load testing.

| Scenario | Required Cloud Allocation | Capacity Driver | Supported RPS Envelope | P95 Target | Estimated In-Flight Concurrency | Monthly Cost (Live) |
|---|---|---|---:|---:|---:|---:|
| Best | `ES: 3 x m6i.xlarge + 3 x gp3(300 GB)`, `API: 2 x m6i.xlarge`, `Worker: 1 x m6i.large`, BGE in API tier | Baseline API serving capacity with lexical-dominant traffic | `5` | `300 ms` | `2` | `$1,011.46` |
| Optimal | Best + `1 x API m6i.xlarge` (total API = `3`) | Additional API parallelism for mixed lexical + semantic traffic | `12` | `350 ms` | `5` | `$1,179.65` |
| Worst (standard) | Optimal + `1 x ES m6i.xlarge` + `1 x gp3(300 GB)` (total ES data nodes = `4`) | Elasticsearch-side headroom for sustained semantic fallback and higher fan-out | `16` | `500 ms` | `8` | `$1,376.64` |
| Worst (heavy embedding) | Worst (standard) + dedicated BGE tier `1 x m6i.2xlarge` | Embedding isolation from API serving under sustained semantic pressure | `22` | `650 ms` | `15` | `$1,713.02` |

Relative pricing impact from live rates:

- Optimal vs Best: `+$168.19/month`
- Worst (standard) vs Best: `+$365.18/month`
- Worst (heavy embedding) vs Worst (standard): `+$336.38/month`

#### Recommended Long-Run Baseline Allocation (Right-Sized)

| Layer | Allocation | Justification | Monthly Cost |
|---|---|---|---:|
| Elasticsearch data compute | `3 x m6i.xlarge` | Three-node cluster for availability and shard balance without oversized compute. | `$420.48` |
| Elasticsearch data storage | `3 x EBS gp3 (300 GB)` | Balanced storage for current index size plus growth headroom. | `$72.00` |
| API serving tier (includes BGE inference) | `2 x m6i.xlarge` | Keeps request serving and current embedding workload simple and cost-efficient. | `$280.32` |
| Batch/quality worker | `1 x m6i.large` | Keeps calibration, reporting, and reindex tasks separated from serving path. | `$70.08` |
| Subtotal (compute + data disks) | N/A | Sum of baseline compute and storage resources. | `$842.88` |
| Platform overhead reserve (monitoring, transfer, backups, contingency @ 20%) | N/A | Safety reserve for non-instance platform costs and operational variance. | `$168.58` |
| Estimated monthly run cost | N/A | Baseline subtotal plus platform overhead reserve. | `$1,011.46` |

Note:

- Add a dedicated coordinator/ingest node later only if telemetry shows clear contention between ingestion and user-facing query latency.

#### Stress Handling Allocation (Scale-Out Add-On)

When sustained traffic or latency pressure is detected, add:

| Add-On | Why It Is Added |
|---|---|
| `+1 x m6i.xlarge` (ES data node) | Adds search/index capacity while preserving cluster balance. |
| `+1 x EBS gp3 (300 GB)` (ES data disk) | Keeps storage and shard placement balanced with the additional node. |
| `+1 x m6i.xlarge` (API horizontal scale) | Raises serving concurrency for higher request volume. |
| `+1 x m6i.2xlarge` (dedicated BGE inference tier, optional) | Added only if embedding latency remains high after API scale-out. |

Cost impact:

- Standard add-on subtotal (without dedicated BGE tier): `$304.32/month`
- Standard add-on with 20% overhead: `$365.18/month`
- Baseline + standard stress run: `$1,376.64/month`
- Optional dedicated BGE tier add-on: `+$336.38/month` (including 20% overhead)
- Baseline + stress run with dedicated BGE tier: `$1,713.02/month`

Operational scale triggers (recommended):

- API scale-out: `CPU > 65%` for 5 minutes or mapping `P95 latency > 350 ms`.
- ES scale-out: heap pressure sustained `> 70%` plus rising search queue depth.
- BGE dedicated tier activation: embedding `P95` remains high even after API horizontal scale-out.
- Scale-in: hold for cooldown period after metrics return below target bands.

## 1.5 Challenges and Solutions

This section summarizes real issues we faced, why they mattered, and how we resolved them.

| What We Faced | Why It Was a Problem | What We Changed | Current Result |
|---|---|---|---|
| Sparse and ambiguous keyword clusters | Same query could appear confident even when evidence was weak or split across categories. | Added reliability weighting using support (`product_count`) and ambiguity (`category_count`) penalties. | Mapping confidence is more realistic and wrong auto-maps are reduced. |
| Noisy head terms and truncated terms (for example `color t`) | Low-quality suggestions reached users and reduced trust in autocomplete. | Added head-term hard caps, per-document limits, evidence gates, and one-letter-tail suppression. | Suggestion list quality is stable and noisy partial terms are filtered out. |
| Intent drift in multi-token queries | Queries could drift into nearby but incorrect categories. | Added canonical token normalization and anchor/order constraints for stronger intent preservation. | Results stay aligned to the original user intent more consistently. |
| Canary rollout initially affected relevance behavior | Canary traffic produced different ranking behavior instead of only rollout visibility differences. | Decoupled canary from core scoring; canary now controls visibility and telemetry segmentation only. | Baseline and canary runs now show consistent relevance behavior. |
| Confidence model saturation with very small label set | Learned confidence could become overconfident and less reliable for decisions. | Added calibration validation rules and guidance for balanced label data before model reliance. | Confidence calibration is controlled; label set expansion is tracked as ongoing work. |

### Potential Future Challenges (Data-Driven Hypothetical Scenarios)

| Potential Scenario | Early Signal to Watch | Potential Impact | Recommended Preventive Action |
|---|---|---|---|
| Long-tail query growth with sparse support | Rising share of queries mapped from clusters with `product_count <= 1` | More `options/no_match` decisions and lower confidence stability | Expand long-tail labels and controlled synonym proposals; prioritize weak-intent query mining |
| Synonym over-expansion conflicts | Higher false positives after synonym updates | Suggestion drift and off-intent category mapping | Enforce governance review gates and run regression before synonym promotion |
| Category taxonomy updates upstream | New/renamed categories without synchronized keyword mappings | Incorrect breadcrumbs and stale mapping behavior | Add change-detection checks and reindex/relabel runbook for taxonomy updates |
| Semantic traffic spike during campaigns | Higher semantic fallback activation rate with rising embedding latency | API latency pressure and inconsistent user experience | Trigger API scale-out first, then enable dedicated BGE tier when thresholds are exceeded |
| Confidence drift from changing query mix | Margin collapse and rising low-confidence alerts over time | Unstable decision quality and higher manual confirmation load | Monthly calibration refresh with balanced labels and threshold revalidation |
| Data freshness lag in indexing pipelines | Delay between source catalog updates and searchable index state | Missing or outdated suggestions and categories | Track ingestion lag SLO and prioritize backfill catch-up automation |

### Suggested Validation Tests to Add for These Scenarios

1. Long-tail stability pack: add a curated weak-support query set and run before each release.
2. Synonym safety gate: run synonym governance review + full relevance regression before apply.
3. Campaign-load simulation: execute burst load profile with high semantic mix and verify P95 + fallback rates.
4. Calibration drift check: monitor confidence and margin distributions weekly against baseline windows.
5. Freshness check: validate index lag and sample correctness after each major ingest/backfill.

## 2. Data Used to Establish Search

This section clearly states what data is used and how each dataset contributes to search.

### 2.1 Upstream Source Data (Ingestion Inputs)

The Elasticsearch indices are built from MongoDB collections:

| Source Collection | Role in System |
|---|---|
| `liveproducts_v1` | Product master data for product index |
| `keyword_cluster` | Intent keyword clusters for suggestion and mapping lanes |
| `categories` | Category label resolution during indexing |
| `subcategories` | Sub-category label resolution during indexing |
| `productcategories` | Product-category label resolution during indexing |

### 2.2 Runtime Search Indices and Key Fields

#### A. Keyword Cluster Index (`pepagora_keyword_cluster`)

Used for:

- Autosuggest candidate generation
- Intent-to-category evidence aggregation

Key fields consumed at runtime:

| Field | Type | Used In |
|---|---|---|
| `keyword_name` | text | Primary lexical anchor for suggest and mapping |
| `variant_terms` | text | Secondary lexical candidate pool |
| `long_tail_terms` | text | Conditional lexical enrichment (strictly guarded) |
| `head_terms` | keyword | Guarded lexical source (outlier protected) |
| `product_count` | integer | Support strength for reliability weighting |
| `category_count` | integer | Ambiguity penalty in reliability weighting |
| `product_category_ids` | keyword[] | Category vote targets |
| `keyword_vector_longtail` | dense_vector | Semantic fallback lane |

#### B. Product Index (`pepagora_products`)

Used for:

- Product-name fallback suggestions
- Last-stage category vote fallback
- Category metadata hydration for response cards

Key fields consumed at runtime:

| Field | Type | Used In |
|---|---|---|
| `productName` | text | Product fallback suggestion source |
| `product_vector_main` | dense_vector | Product semantic retrieval for vote fallback |
| `product_vector_short` | dense_vector | Secondary semantic signal for vote fallback and rerank boost |
| `productCategory_id` | keyword | Vote bucket key for fallback |
| `category_name` | keyword | Breadcrumb rendering |
| `subCategory_name` | keyword | Breadcrumb rendering |
| `productCategory_name` | keyword | Breadcrumb rendering |

### 2.3 Data Quality Observations Used in Design

From indexed data profiling:

- Product index is high completeness and reliable for fallback voting.
- Keyword clusters are often sparse (`product_count` low in many docs).
- Many clusters are ambiguous (`category_count` > 1).
- Rare `head_terms` outliers are very large and can damage suggestion quality if not capped.

Design impact:

- Support smoothing and ambiguity penalty are mandatory.
- Head-term usage is guarded and capped.

### 2.4 Current Indexed Data Profile (Design Baseline)

The following profile numbers were used to define safeguards and thresholds:

| Metric | Value |
|---|---|
| Product index documents | `100,000` |
| Keyword cluster index documents | `511,124` |
| Product category-field completeness (sample) | `~99.3%` |
| Keyword clusters with `product_count <= 1` (sample) | `~54.3%` |
| Clusters with `category_count >= 3` (sample) | `~23.4%` |
| Head-term outlier docs (`head_terms > 1,000`) | `268` |
| Head-term outlier docs (`head_terms > 5,000`) | `22` |
| Head-term outlier docs (`head_terms > 10,000`) | `21` |

Why these metrics matter:

- High product completeness makes product-vote fallback dependable.
- Sparse cluster support requires support smoothing in reliability scoring.
- Category ambiguity requires explicit ambiguity penalties.
- Extreme head-term outliers justify strict term caps and guarded head-term use.

## 3. End-to-End Flow

```mermaid
flowchart TD
    A[User Input] --> B[Query Preprocessing]
    B --> C[Suggestion Lane - Lexical Only]
    C --> D{Selected suggestion exists?}
    D -->|Yes| E[Lexical Cluster Mapping]
    D -->|No| E

    E --> F{Top confidence >= confirm threshold?}
    F -->|Yes| G[Decision: auto_map or confirm]
    F -->|No and free-text| H[Semantic Cluster Fallback]
    F -->|No and selected suggestion| I[Skip Semantic]
    H --> J[Recompute category confidence]
    I --> J
    J --> K{Top confidence < product fallback trigger?}
    K -->|No| G
    K -->|Yes| L[Product Vector Retrieval]
    L --> M[Category Vote Aggregation]
    M --> G

    G --> N[Top category + alternatives + evidence]
```

## 4. Suggestion Lane (Lexical Fast Lane)

Endpoint:

- `GET /ui-api/suggestions`

### 4.1 Query Preprocessing

- Lowercase and token normalization.
- Remove connective noise words from significance logic.
- Build anchor token behavior for intent-preserving ranking.

### 4.2 Candidate Sources (Priority)

1. `keyword_name`
2. `variant_terms`
3. `long_tail_terms` (only with strong evidence)
4. `head_terms` (guarded mode only)
5. Product-name fallback from product index

### 4.3 Guardrails

- Skip `head_terms` when term-list size exceeds `HEAD_TERMS_HARD_CAP`.
- Truncate per document by configured limits:
  - `HEAD_TERMS_PER_DOC_LIMIT`
  - `VARIANT_TERMS_PER_DOC_LIMIT`
  - `LONG_TAIL_TERMS_PER_DOC_LIMIT`
- Remove low-signal noisy candidates.

### 4.4 Ranking Behavior

Ranking order is lexical stage-based and evidence-first:

- exact
- prefix
- ordered phrase
- ordered tokens
- weak contains
- fuzzy fallback

### 4.5 Exact Runtime Suggestion Pipeline (Implementation Detail)

Runtime function: `_fetch_keyword_suggestions(query, limit)`.

Execution sequence:

1. Build query context:
- `raw_tokens`
- `normalized_query`
- `intent_query`
- `phrase_candidates`
- `anchor_tokens`

2. Query keyword index (`pepagora_keyword_cluster`) with lexical `should` clauses over:
- `keyword_name`
- `variant_terms`
- `long_tail_terms`
- `head_terms`

3. Build candidate bucket using source priorities:
- `keyword_name`: priority `6`
- `variant_terms`: priority `5`
- `long_tail_terms`: priority `3`
- `head_terms`: priority `2`
- Product-name fallback (`pepagora_products.productName`): priority `1`

4. Apply guardrails per source:
- `variant_terms`: must pass strong evidence check.
- `long_tail_terms`: must pass strong evidence and stage must be `<= 4`.
- `head_terms`: only if doc head-term count `<= HEAD_TERMS_HARD_CAP`; each term must pass strong evidence and stage must be `<= 4`.

5. Deduplicate and rank with tie-break chain:
- stage quality
- token coverage
- first-token mismatch
- starts numeric
- first token position
- length delta
- source priority
- frequency
- score
- term length

6. Apply post-ranking denoise:
- suppress weak stage terms for multi-token queries when first token diverges.
- enforce prefix-order preference when query ends in connective noise token.
- optional anchor-token filter when enough high-signal suggestions remain.

7. Final selection:
- Prefer strong-stage items (stage `<= 4`) up to `limit`.
- Otherwise fill with best available ordered candidates.

### 4.6 Head-Term Handling (Plan vs Runtime)

Head terms are included by design, but only in guarded mode.

Head-term controls in runtime:

- Query-time contribution has lower weight than `keyword_name` and `variant_terms`.
- Per-document hard cap: skip all head terms if list size exceeds `HEAD_TERMS_HARD_CAP`.
- Per-document truncation: use only first `HEAD_TERMS_PER_DOC_LIMIT` unique head terms.
- Evidence gate: candidate must align strongly with current intent.
- Stage gate: candidate must stay in strong lexical stages (`<= 4`).

This preserves useful recall from head terms while preventing noisy takeover.

### 4.7 Incomplete-Term Suppression (Observed Production Case)

Observed issue:

- Suggestions such as `color t` or `stain g` appeared.

Root cause:

- These values existed in upstream indexed `head_terms`.
- Suggestion flow previously allowed truncated one-letter tail tokens to survive.

Implemented fix:

- Suggestion candidate is rejected when the last raw token is a single alphabetic character.

Effect:

- Incomplete terms are suppressed.
- Valid full terms such as `color t-shirts` remain eligible.

## 5. Mapping Lane (Category Intent)

Endpoints:

- `GET /ui-api/hierarchy`
- `GET /ui-api/map-category`

### 5.1 Notation

For cluster hit `i`:

- `p_i`: product support (`product_count`)
- `a_i`: ambiguity (`category_count`)
- `s_i`: normalized search score
- `m_i`: lexical match signal
- `w_lane`: lane weight (`1.0` lexical, semantic weight for semantic lane)

### 5.2 Reliability Factor

Reliability per cluster hit:

$$
R_i = \operatorname{clamp}\left(
\frac{\log(1+p_i)}{\log(1+P95)} \cdot \frac{1}{1+\beta(a_i-1)},
0.02,
1.25
\right)
$$

Where:

- `P95 = KEYWORD_P95_PRODUCT_COUNT`
- `beta = RELIABILITY_BETA`

Interpretation:

- Higher support increases trust.
- Higher ambiguity decreases trust.

### 5.3 Match Signal

`m_i` is derived from lexical quality over keyword_name, variants, long-tail, and (guarded) head terms, using:

- stage quality
- token overlap
- phrase bonus
- prefix bonus

with clamping to `[0, 1]`.

### 5.4 Document Vote Formula

Normalized search score:

$$
s_i = \frac{h_i}{\max_j(h_j)}
$$

Document vote:

$$
v_i = w_{lane} \cdot \left(0.55\,s_i + 0.45\,m_i\right) \cdot R_i
$$

If one cluster maps to multiple categories, vote is split equally across those categories.

### 5.5 Category Confidence

For category `k`:

$$
\mathrm{raw}_k = \sum_{i \in k} v_i
$$

$$
\mathrm{confidence}_k = \frac{\mathrm{raw}_k}{\sum_t \mathrm{raw}_t}
$$

Margin used for decisioning:

$$
\mathrm{margin} = \mathrm{confidence}_{top1} - \mathrm{confidence}_{top2}
$$

## 6. Thresholds and Decision Policy

Current runtime defaults:

| Parameter | Default |
|---|---|
| `AUTO_MAP_CONFIDENCE` | `0.72` |
| `AUTO_MAP_MARGIN` | `0.14` |
| `CONFIRM_MAP_CONFIDENCE` | `0.52` |
| `PRODUCT_FALLBACK_TRIGGER` | `0.42` |
| `SEMANTIC_CLUSTER_WEIGHT` | `0.62` |
| `PRODUCT_VOTE_WEIGHT` | `0.55` |
| `PRODUCT_MAIN_VOTE_SHARE` | `0.75` |
| `PRODUCT_SHORT_VOTE_SHARE` | `0.25` |
| `SHORT_VECTOR_RERANK_BOOST` | `0.18` |
| `KEYWORD_P95_PRODUCT_COUNT` | `17` |
| `RELIABILITY_BETA` | `0.35` |
| `KEYWORD_SUGGEST_DOCS` | `96` |
| `PHRASE_CANDIDATE_LIMIT` | `8` |

Decision rules:

| Decision | Rule |
|---|---|
| `auto_map` | `top_conf >= AUTO_MAP_CONFIDENCE` and `margin >= AUTO_MAP_MARGIN` |
| `confirm` | `top_conf >= CONFIRM_MAP_CONFIDENCE` and auto-map rule not met |
| `options` | otherwise show top alternatives |
| `no_match` | no usable evidence |

## 7. Fallback Control Flow

```mermaid
flowchart LR
    A[Lexical cluster mapping] --> B{Top confidence >= confirm threshold?}
    B -->|Yes| C[Return decision]
    B -->|No| D{Selected suggestion present?}
    D -->|Yes| E[Skip semantic]
    D -->|No| F[Run semantic cluster fallback]
    E --> G[Recompute confidence]
    F --> G
    G --> H{Top confidence < product fallback trigger?}
    H -->|No| C
    H -->|Yes| I[Run product vector retrieval]
    I --> J[Category vote aggregation]
    J --> C
```

Important safety behavior:

- Semantic cluster lane is not always on; it is conditional.
- Product fallback maps by aggregated category votes, not by a single nearest product.

### 7.1 Product Main vs Product Short Order

The product-vector order is deterministic.

#### A. Mapping flow (`/ui-api/hierarchy`, `/ui-api/map-category`)

Product vectors are only used after lexical and optional semantic-cluster lanes fail to reach required confidence:

1. Run lexical cluster mapping.
2. If confidence is weak and input is free-text, run semantic cluster fallback.
3. If confidence is still below `PRODUCT_FALLBACK_TRIGGER`, start product-vector fallback.
4. Fetch kNN from `product_vector_main`.
5. Fetch kNN from `product_vector_short`.
6. Merge both into category votes using configured shares:
  - `PRODUCT_MAIN_VOTE_SHARE`
  - `PRODUCT_SHORT_VOTE_SHARE`
7. If both are present, both contribute by normalized shares.
8. If only one lane has hits, that lane contributes 100% of the product fallback vote.

This means `product_vector_short` is not an earlier lane; it is used inside the final product-vote fallback stage.

### 7.2 Free-Text vs Selected-Suggestion Mapping Behavior

Mapping lane behavior differs by user input mode:

1. Selected suggestion mode (`selected` provided):
- Semantic cluster fallback is skipped.
- Decision is made from lexical lane plus product-vote fallback (only if needed).

2. Free-text mode (`selected` not provided):
- Semantic cluster fallback may run when lexical confidence is below `CONFIRM_MAP_CONFIDENCE`.
- Product-vote fallback may run if confidence remains below `PRODUCT_FALLBACK_TRIGGER`.

This distinction is intentional: selected suggestions are treated as stronger lexical intent anchors.

#### B. Search flow (`/search`)

For `semantic` and `hybrid` modes:

1. Primary retrieval uses `product_vector_main` as the main kNN field.
2. `product_vector_short` is then used as a rerank/boost overlay on returned hits.
3. Final score is base ES score plus short-vector boost component.

So, in search mode, main is primary retrieval and short is secondary rerank.

## 8. API Outputs and Explainability

### 8.1 Suggestions API

- `GET /ui-api/suggestions?q=...&limit=...`
- Returns suggestions, count, ranking order, and latency.

### 8.2 Hierarchy Mapping API

- `GET /ui-api/hierarchy?keyword=...&max_cards=...`
- Returns decision, confidence, margin, lanes used, cards, and matched-doc count.

### 8.3 Intent Mapping API

- `GET /ui-api/map-category?q=...&selected=...&max_cards=...`
- Returns:
  - decision (`auto_map`, `confirm`, `options`, `no_match`)
  - confidence and margin
  - top category and alternatives
  - semantic/product fallback usage indicators
  - active thresholds

Evidence fields returned to support auditability:

- `ranking_basis`
- `sample_keywords`
- `sample_products`
- `avg_product_support`
- `avg_category_ambiguity`
- `lanes_used`

### 8.4 UI Rendering Contract for Mapping Cards

UI behavior is now confidence-aware and non-congested.

1. Selected suggestion:
- Request mapping with `selected=<keyword>`.
- Show only top-1 mapped category card.

2. Free text:
- Request up to 3 cards.
- Render dynamically:
  - show 1 when only top candidate is strong.
  - show 2 when top-2 are both relevant and close.
  - show 3 only when third candidate is also sufficiently relevant.

3. Diagnostics:
- Default card view is compact.
- Detailed evidence is available in collapsible diagnostics.

4. Display-level confidence formatting:
- Confidence values are normalized to percentage for readability.

## 9. Why This Approach Fits Current Data

1. Sparse support is handled through logarithmic support smoothing.
2. Ambiguous clusters are penalized before voting impact increases.
3. Head-term outliers are hard-capped and truncated.
4. Semantic lane is controlled and used only when lexical confidence is weak.
5. Product lane is a final fallback with vote aggregation, reducing one-hit errors.

## 10. Governance and Tuning

Primary tuning knobs:

- Suggestion quality:
  - `HEAD_TERMS_HARD_CAP`
  - `HEAD_TERMS_PER_DOC_LIMIT`
  - `VARIANT_TERMS_PER_DOC_LIMIT`
  - `LONG_TAIL_TERMS_PER_DOC_LIMIT`
- Reliability and confidence:
  - `KEYWORD_P95_PRODUCT_COUNT`
  - `RELIABILITY_BETA`
  - `AUTO_MAP_CONFIDENCE`
  - `AUTO_MAP_MARGIN`
  - `CONFIRM_MAP_CONFIDENCE`
  - `PRODUCT_FALLBACK_TRIGGER`
- Fallback influence:
  - `SEMANTIC_CLUSTER_WEIGHT`
  - `PRODUCT_VOTE_WEIGHT`

Recommended rollout KPIs:

- Suggestion selection rate
- Auto-map rate
- Confirm-required rate
- Top-1 acceptance rate
- Top-3 success rate
- P50/P95 latency by endpoint
- Semantic fallback activation rate
- Product fallback activation rate

### 10.1 Threshold Tuning Playbook (Required Before Final Lock)

Tuning should follow a fixed order to avoid coupled regressions.

Recommended sequence:

1. Freeze suggestion quality controls first:
- `HEAD_TERMS_HARD_CAP`
- `HEAD_TERMS_PER_DOC_LIMIT`
- `VARIANT_TERMS_PER_DOC_LIMIT`
- `LONG_TAIL_TERMS_PER_DOC_LIMIT`

2. Tune decision thresholds second:
- `AUTO_MAP_CONFIDENCE`
- `AUTO_MAP_MARGIN`
- `CONFIRM_MAP_CONFIDENCE`
- `PRODUCT_FALLBACK_TRIGGER`

3. Tune lane influence third:
- `SEMANTIC_CLUSTER_WEIGHT`
- `PRODUCT_VOTE_WEIGHT`
- `PRODUCT_MAIN_VOTE_SHARE`
- `PRODUCT_SHORT_VOTE_SHARE`

4. Validate each tuning pass against:
- selected-suggestion scenarios
- ambiguous free-text scenarios
- low-support sparse-cluster scenarios

5. Accept a new threshold set only if all pass criteria improve or remain stable:
- lower wrong auto-map rate
- stable or improved top-1/top-3 acceptance
- no significant latency regression (P95)

## 11. Validation Checklist (No-Gap Verification)

Before release, confirm all checks below are true.

Suggestion lane:

- `head_terms` are included only with guards.
- truncated one-letter tails are suppressed.
- stage/frequency/source-priority ordering behaves deterministically.

Mapping lane:

- lexical lane always runs first.
- semantic lane runs only for weak free-text lexical confidence.
- product-vote lane runs only below `PRODUCT_FALLBACK_TRIGGER`.
- category cards include explainability fields.

Vector behavior:

- `product_vector_main` is primary retrieval in semantic/hybrid search.
- `product_vector_short` is rerank boost in search.
- product-vote fallback merges main and short by configured shares.

UI behavior:

- selected suggestion shows top-1 card.
- free text shows dynamic top-1/top-2/top-3 based on relevance.
- diagnostics are collapsible, not always expanded.

## 12. Conclusion

The implemented search design is data-grounded, reliable under sparse and ambiguous cluster behavior, and submission-ready for business and engineering review.

It provides:

- Clear architecture and flow control
- Formal, well-defined scoring equations
- Explicit thresholds and decisions
- Strong explainability and operational tuning controls
- Verified safeguards for noisy/incomplete suggestion terms
- Deterministic vector-lane order for both mapping and search

## 13. API Request and Response Examples

This section provides practical examples for QA, product, and business walkthroughs.

### 13.1 Suggestions API

Request:

```http
GET /ui-api/suggestions?q=color&limit=12
```

Expected response shape:

```json
{
  "query": "color",
  "suggestions": [
    "color pigments",
    "color powders",
    "color solutions",
    "color dye"
  ],
  "ranking_order": [
    "exact",
    "prefix",
    "ordered_phrase",
    "ordered_tokens",
    "weak_contains",
    "fuzzy_fallback"
  ],
  "count": 12,
  "latency_ms": 34.7
}
```

Validation notes:

- Incomplete tails such as `color t` or `stain g` should not appear.
- Head-term derived suggestions can appear only when guardrails are satisfied.

### 13.2 Mapping API (Free-Text)

Request:

```http
GET /ui-api/map-category?q=color sorter&max_cards=3
```

Expected response shape:

```json
{
  "query": "color sorter",
  "selected": null,
  "normalized_query": "color sorter",
  "decision": "options",
  "confidence": 0.41,
  "margin": 0.08,
  "needs_confirmation": false,
  "auto_mapped": false,
  "intent_query": "color sorter",
  "phrase_candidates": ["color sorter"],
  "top_category": {
    "product_category_id": "68a6...",
    "breadcrumb": "Food & Agriculture >> Processing Machinery >> Color Sorting",
    "confidence": 0.41
  },
  "cards": [
    {
      "product_category_id": "68a6...",
      "breadcrumb": "Food & Agriculture >> Processing Machinery >> Color Sorting",
      "count": 42,
      "correlation_pct": 41.0,
      "avg_token_coverage": 0.79,
      "ranking_basis": {
        "lexical_cluster_hits": 20,
        "semantic_cluster_hits": 14,
        "product_vote_hits": 8,
        "exact_hits": 20,
        "prefix_hits": 0,
        "token_and_hits": 0,
        "semantic_hits": 14
      },
      "sample_keywords": ["color sorter", "grain color sorter"],
      "sample_products": ["Automatic Grain Color Sorting Machine"],
      "confidence": 0.41,
      "avg_product_support": 5.2,
      "avg_category_ambiguity": 1.8,
      "reason": "support=5.20, ambiguity=1.80, lexical_hits=20, semantic_hits=14, product_votes=8"
    }
  ],
  "matched_clusters": 34,
  "product_vote_hits": 8,
  "lanes_used": ["lexical", "semantic", "product_vote"],
  "semantic_used": true,
  "product_fallback_used": true,
  "thresholds": {
    "auto_map": 0.72,
    "auto_map_margin": 0.14,
    "confirm": 0.52,
    "product_fallback": 0.42
  },
  "latency_ms": 186.3
}
```

Validation notes:

- Free-text can activate lexical, semantic, and product-vote lanes.
- UI should render 1/2/3 cards dynamically based on relevance, not always 3.

### 13.3 Mapping API (Selected Suggestion)

Request:

```http
GET /ui-api/map-category?q=color sorter&selected=color sorter&max_cards=1
```

Expected response behavior:

- Semantic fallback is skipped.
- `lanes_used` typically includes lexical and optional product_vote.
- UI should show only Top 1 mapped category card.

Expected response shape:

```json
{
  "query": "color sorter",
  "selected": "color sorter",
  "decision": "confirm",
  "lanes_used": ["lexical"],
  "semantic_used": false,
  "product_fallback_used": false,
  "cards": [
    {
      "breadcrumb": "Food & Agriculture >> Processing Machinery >> Color Sorting",
      "confidence": 0.67
    }
  ]
}
```

### 13.4 Search API (Hybrid Mode)

Request:

```http
GET /search?q=ss%20wire&page=1&mode=hybrid
```

Expected response shape:

```json
{
  "query": "ss wire",
  "total": 81,
  "page": 1,
  "page_size": 20,
  "pages": 5,
  "hits": [
    {
      "id": "abc123",
      "productName": "Industrial Stainless Steel SS Wire",
      "productDescription": "...",
      "category_name": "Industrial Supplies",
      "subCategory_name": "Wire",
      "productCategory_name": "Stainless Steel Wire",
      "score": 17.4331,
      "score_raw": 17.1904,
      "short_vector_boost": 1.3481,
      "confidence": 100.0
    }
  ],
  "facets": {
    "category": [
      {"name": "Industrial Supplies", "count": 51}
    ],
    "sub_category": [],
    "prod_category": []
  },
  "latency_ms": 492.1,
  "suggestion": null,
  "synonym_expanded": null,
  "search_mode": "hybrid",
  "semantic_short_used": true,
  "suppliers": []
}
```

Validation notes:

- `product_vector_main` is the primary kNN retrieval lane.
- `product_vector_short` contributes as rerank/boost in semantic or hybrid modes.

### 13.5 Quick QA Matrix

Use this matrix for rapid acceptance checks:

| Scenario | Expected decision | Expected lanes | UI cards |
|---|---|---|---|
| Clear selected suggestion | `auto_map` or `confirm` | lexical (+ optional product_vote) | 1 |
| Ambiguous free-text | `options` | lexical -> semantic -> optional product_vote | 2 or 3 if relevant |
| Weak sparse query | `options` or `no_match` | lexical -> semantic -> product_vote | 1 to 3 depending on relevance |
| Misspelled keyword | suggestions recover intent | lexical suggest lane | suggestion list only |

---

Document owner: Search Engineering

Implementation source: src/main.py

### Operational Commands (PowerShell)

```powershell
# Start API
./scripts/run_api.ps1

# Product indexing
./elasticsearch_indexing_service/start_product.ps1 create-index --recreate
./elasticsearch_indexing_service/start_product.ps1 backfill --batch-size 192 --published-only

# Keyword indexing
./elasticsearch_indexing_service/start_keyword.ps1 create-index --recreate
./elasticsearch_indexing_service/start_keyword.ps1 backfill --batch-size 400

# Benchmark
./scripts/run_benchmark.ps1 -QuerySet compact -Modes "keyword,semantic,hybrid"
```

