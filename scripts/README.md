# Scripts

Standardized PowerShell entrypoints for local development.

## Script Roles (Production Clarity)

- Runtime start: `run_api.ps1`
- Benchmark/regression: `run_benchmark.ps1`, `benchmark_runner.py`, `relevance_regression.py`
- Rollout safety: `canary_guard.py`, `observability_guard.py`, `reliability_gate.py`, `run_reliability_gate.ps1`
- Mapping quality/calibration: `mapping_telemetry_report.py`, `train_mapping_calibration.py`, `user_situation_validation.py`
- Synonym governance: `synonym_governance.py`
- One-time migration/patch operations: `ingest_live_fields.py`

Keep one-time migration scripts out of steady-state deployment jobs.

## Data-driven synonyms

Set `B2B_SYNONYMS_FILE` to a JSON file path so API expansion and ES analyzers load synonyms from data instead of built-in seed lists.
Recommended locations:
- `resources/synonyms.json` for API and benchmark scripts.
- `elasticsearch_indexing_service/config/synonyms.json` for Elasticsearch indexing runners.
If these files exist, `run_api.ps1` and `run_benchmark.ps1` auto-export `B2B_SYNONYMS_FILE` when it is not already set.

Example file content:

```json
{
	"synonyms": {
		"ac": "air conditioner",
		"ss": "stainless steel"
	}
}
```

## No-reindex mode

If reindex/backfill is too expensive right now, you can still use data-driven runtime synonym expansion immediately:

```powershell
./scripts/run_api.ps1
```

Optional (template/analyzer assets only, no reindex):

```powershell
./elasticsearch_indexing_service/start_product.ps1 install-assets
./elasticsearch_indexing_service/start_keyword.ps1 install-assets
```

Note: Without reindex/backfill, analyzer changes on newly created mappings/templates will not retroactively rewrite existing indexed tokens. Runtime query synonym expansion still works now.

## Start API

`run_api.ps1` loads host/port from `.env` only. No CLI host/port overrides and no fallback defaults.

Frontend pages resolve API base URL from `/ui-api/runtime-config`, which is sourced from `.env` values.
Optional override for frontend/public URL: `UI_API_BASE_URL`.

Recommended `.env` keys:

```text
APP_SCHEME=http
APP_HOST=127.0.0.1
APP_PORT=8000
EMBEDDING_API_URL=http://127.0.0.1:8001
# Required service URLs
ES_HOST=http://localhost:9200
MONGO_URI=mongodb://localhost:27017/admin
ES_PORT=9200
# Optional when frontend/public URL must differ from APP_SCHEME/APP_HOST/APP_PORT
# UI_API_BASE_URL=http://127.0.0.1:8000
```

```powershell
./scripts/run_api.ps1
./scripts/run_api.ps1 -Reload
# Backward-compatible alias; same as default no-reload mode
./scripts/run_api.ps1 -NoReload
```

## Embedding API Client

This repo only calls the external embedding API through `EMBEDDING_API_URL`.
It does not host or train the embedding model locally.

Required environment key:

```text
EMBEDDING_API_URL=http://127.0.0.1:8001
```

Optional supplier enrichment toggle (default is disabled):

If your deployment has only product and keyword indices, keep supplier enrichment disabled.

```powershell
$env:SUPPLIER_ENRICHMENT_ENABLED = "false"
```

Enable it only when supplier index is available:

```powershell
$env:SUPPLIER_ENRICHMENT_ENABLED = "true"
$env:ES_SUPPLIER_INDEX = "pepagora_suppliers"
```

## Product Index Pipeline

> **External repo:** These commands run through the `elasticsearch_indexing_service` repository (separate from this repo).
> Clone and configure it first, then run from its root directory.

These Elasticsearch commands prefer `elasticsearch_indexing_service/.env`.

```powershell
./elasticsearch_indexing_service/start_product.ps1 create-index --recreate
./elasticsearch_indexing_service/start_product.ps1 create-index --use-aliases --index-name pepagora_products-000002
./elasticsearch_indexing_service/start_product.ps1 install-assets
./elasticsearch_indexing_service/start_product.ps1 backfill --batch-size 192 --index-name pepagora_products-000002 --published-only
./elasticsearch_indexing_service/start_product.ps1 promote-alias --index-name pepagora_products-000002
./elasticsearch_indexing_service/start_product.ps1 backfill --batch-size 192 --published-only
./elasticsearch_indexing_service/start_product.ps1 backfill --batch-size 192 --use-write-alias --published-only
./elasticsearch_indexing_service/start_product.ps1 backfill --batch-size 192 --limit 500
```

## Keyword Index Pipeline

> **External repo:** Same `elasticsearch_indexing_service` repository as above.

These Elasticsearch commands prefer `elasticsearch_indexing_service/.env`.

```powershell
./elasticsearch_indexing_service/start_keyword.ps1 create-index --recreate
./elasticsearch_indexing_service/start_keyword.ps1 create-index --use-aliases --index-name pepagora_keyword_cluster-000002
./elasticsearch_indexing_service/start_keyword.ps1 install-assets
./elasticsearch_indexing_service/start_keyword.ps1 backfill --batch-size 400 --index-name pepagora_keyword_cluster-000002
./elasticsearch_indexing_service/start_keyword.ps1 promote-alias --index-name pepagora_keyword_cluster-000002
./elasticsearch_indexing_service/start_keyword.ps1 backfill --batch-size 400
./elasticsearch_indexing_service/start_keyword.ps1 backfill --batch-size 400 --use-write-alias
./elasticsearch_indexing_service/start_keyword.ps1 backfill --batch-size 400 --limit 1000
```

## OpenSearch-Only Pipelines

> **External repo:** These commands run through the `opensearch_indexing_service` repository (separate from this repo).
> Clone and configure it first, then run from its root directory.

Use these entrypoints when you want index creation/backfill behavior that is explicit for OpenSearch (`knn_vector` mappings, OpenSearch client, and OpenSearch alias promotion flow).

Product pipeline (OpenSearch):

```powershell
./opensearch_indexing_service/start_product.ps1 show-schema --output-file config/opensearch_product_schema_v1.json
./opensearch_indexing_service/start_product.ps1 install-assets
./opensearch_indexing_service/start_product.ps1 create-index --use-aliases --index-name pepagora_products_os-000001
./opensearch_indexing_service/start_product.ps1 backfill --batch-size 192 --index-name pepagora_products_os-000001 --published-only
./opensearch_indexing_service/start_product.ps1 promote-alias --index-name pepagora_products_os-000001
```

Keyword pipeline (OpenSearch):

```powershell
./opensearch_indexing_service/start_keyword.ps1 show-schema --output-file config/opensearch_keyword_schema_v1.json
./opensearch_indexing_service/start_keyword.ps1 install-assets
./opensearch_indexing_service/start_keyword.ps1 create-index --use-aliases --index-name pepagora_keyword_cluster_os-000001
./opensearch_indexing_service/start_keyword.ps1 backfill --batch-size 400 --index-name pepagora_keyword_cluster_os-000001
./opensearch_indexing_service/start_keyword.ps1 promote-alias --index-name pepagora_keyword_cluster_os-000001
```

Schema guardrails notebook (OpenSearch analyzers/mappings):

> **Note:** `notebooks/opensearch_schema_guardrails.ipynb` is maintained in the `opensearch_indexing_service` repository, not this repo.
> Open and run it from there to validate normalizers, analyzers, strict mappings, vector field types, and core index settings against live OpenSearch indices.

Optional OpenSearch tuning env keys:

```text
OPENSEARCH_PRODUCT_INDEX=pepagora_products
OPENSEARCH_KEYWORD_INDEX=pepagora_keyword_cluster
OPENSEARCH_VECTOR_SPACE_TYPE=cosinesimil
OPENSEARCH_VECTOR_EF_SEARCH=120
OPENSEARCH_VECTOR_EF_CONSTRUCTION=128
OPENSEARCH_VECTOR_M=24
```

## One-Time Live Field Ingestion (Migration)

Use when you need to backfill `liveUrl`, `showcase`, `createdBy`, and `businessOf` from Mongo into OpenSearch documents.

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/ingest_live_fields.py --dry-run
& ".\.venv\Scripts\python.exe" ./scripts/ingest_live_fields.py --batch-size 500
```

This script is migration-oriented and should not be part of recurring runtime cron unless explicitly required.

## Benchmark

```powershell
./scripts/run_benchmark.ps1
./scripts/run_benchmark.ps1 -QuerySet default -Modes "keyword,semantic,hybrid" -Output full
```

## Relevance Regression (No Reindex)

```powershell
python ./scripts/relevance_regression.py --random-samples 20 --output summary
python ./scripts/relevance_regression.py --random-samples 40 --output full
```

## Phase 3 Safety + Telemetry

Phase 3 adds canary rollout, runtime safety switches, and telemetry logs for threshold tuning.
Canary percentage controls rollout visibility (`phase3_active`) and telemetry segmentation only; core ranking/scoring behavior remains consistent when feature switches are enabled.

Useful environment variables:

```powershell
$env:MAPPING_PHASE3_CANARY_PERCENT = "100"
$env:MAPPING_ENABLE_CONFIDENCE_CALIBRATION = "true"
$env:MAPPING_ENABLE_SEMANTIC_FALLBACK = "true"
$env:MAPPING_ENABLE_PRODUCT_FALLBACK = "true"
$env:MAPPING_TELEMETRY_ENABLED = "true"
$env:MAPPING_TELEMETRY_FILE = "runtime/mapping_telemetry.jsonl"
```

Run regression and then summarize telemetry:

```powershell
python ./scripts/relevance_regression.py --random-samples 40 --output full
python ./scripts/mapping_telemetry_report.py --file runtime/mapping_telemetry.jsonl --output full
```

Optional threshold tuning controls:

```powershell
$env:CONFIRM_MAP_CONFIDENCE = "0.45"
$env:AUTO_MAP_CONFIDENCE = "0.70"
$env:AUTO_MAP_MARGIN = "0.10"
$env:MAPPING_ALERT_LOW_CONFIDENCE_THRESHOLD = "0.40"
$env:MAPPING_ALERT_LOW_MARGIN_THRESHOLD = "0.04"
$env:MAPPING_ALERT_PRODUCT_DOMINANCE_RATIO = "0.60"
```

## Learned Confidence Calibration (Phase 3)

Prepare labels in JSONL format (`resources/mapping_calibration_labels.jsonl`). Supported row styles:

- Query-derived labels: `query` + `expected_category_id` or `expected_any`/`expected_all`/`banned_any`
- Direct labels: `raw_confidence` + `is_correct`

Use the sample file as a template:

```powershell
Get-Content ./resources/mapping_calibration_labels.sample.jsonl
```

Train and write the model artifact:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/train_mapping_calibration.py --labels resources/mapping_calibration_labels.sample.jsonl --output resources/mapping_confidence_calibration.json --output-mode full
```

Run verification with learned calibration enabled:

```powershell
$env:MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION = "true"
$env:MAPPING_CONFIDENCE_MODEL_FILE = "resources/mapping_confidence_calibration.json"
$env:MAPPING_TELEMETRY_FILE = "runtime/mapping_telemetry_phase3_complete.jsonl"
& ".\.venv\Scripts\python.exe" ./scripts/relevance_regression.py --random-samples 40 --output full
& ".\.venv\Scripts\python.exe" ./scripts/mapping_telemetry_report.py --file runtime/mapping_telemetry_phase3_complete.jsonl --output full
```

Recommended: keep a balanced label set (both correct and incorrect outcomes) so learned confidence does not saturate too aggressively.

## Phase 4 Synonym Governance

Validate current synonym rules:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py validate --synonyms resources/synonyms.json --output full
```

Review a proposal (without applying changes):

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py review-proposal --synonyms resources/synonyms.json --proposal resources/synonym_proposal.sample.json --output full
```

Apply a reviewed proposal (auto-snapshots current file first):

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py apply-proposal --synonyms resources/synonyms.json --proposal resources/synonym_proposal.sample.json --output full
```

Rollback to latest snapshot:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py rollback --synonyms resources/synonyms.json --history-dir resources/synonyms_history --output full
```

## Phase 5 Production Observability

Guard telemetry against alert/decision-rate SLOs:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/observability_guard.py --telemetry runtime/mapping_telemetry_phase3_complete.jsonl --expected-canary-percent 100 --output full
& ".\.venv\Scripts\python.exe" ./scripts/observability_guard.py --telemetry runtime/mapping_telemetry_phase3_complete_30.jsonl --baseline runtime/mapping_telemetry_phase3_complete.jsonl --expected-canary-percent 30 --output full
```

## Phase 6 Canary Rollout Guard

Generate baseline/canary regression artifacts and evaluate rollout action:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/canary_guard.py --baseline-telemetry runtime/mapping_telemetry_phase3_complete.jsonl --canary-telemetry runtime/mapping_telemetry_phase3_complete_30.jsonl --baseline-regression runtime/regression_baseline_100.json --canary-regression runtime/regression_canary_30.json
```

Exit codes:

- `0`: promote
- `2`: hold
- `1`: rollback

## Phase 7 Continuous Quality Loop

Generate periodic quality status and next actions:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/continuous_quality_report.py --telemetry runtime/mapping_telemetry_phase3_complete.jsonl --regression runtime/regression_baseline_100.json --calibration-model resources/mapping_confidence_calibration.json --output full
```

## User Situation Validation

Run an explicit situation matrix (exact intent, abbreviation, typo, demographic, selected suggestion, short ambiguous query, out-of-domain safety):

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/user_situation_validation.py --output full
```

## One-Command Reliability Gate

Run all core reliability checks in sequence (Phase 4-7 tooling + user-situation coverage) and produce one rollout decision:

```powershell
./scripts/run_reliability_gate.ps1
./scripts/run_reliability_gate.ps1 -RandomSamples 40 -BaselineCanaryPercent 100 -CanaryPercent 30 -Output full
```

Direct Python entrypoint (writes consolidated report artifact):

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/reliability_gate.py --random-samples 40 --baseline-canary-percent 100 --canary-percent 30 --output summary --write-report runtime/reliability_gate_report.json
```

Gate output includes:

- overall pass/fail
- failed step list
- rollout action (`promote`/`hold`/`rollback`)
- artifact paths for baseline/canary regression + telemetry

