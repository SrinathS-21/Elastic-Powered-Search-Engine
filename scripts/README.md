# Scripts

Standardized PowerShell entrypoints for local development.

## Data-driven synonyms

Set `B2B_SYNONYMS_FILE` to a JSON file path so API expansion and ES analyzers load synonyms from data instead of built-in seed lists.
Recommended location: `config/synonyms.json`.
If this file exists, `run_api.ps1`, `run_benchmark.ps1`, `run_product_pipeline.ps1`, and `run_keyword_pipeline.ps1` auto-export `B2B_SYNONYMS_FILE` when it is not already set.

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
./scripts/run_product_pipeline.ps1 install-assets
./scripts/run_keyword_pipeline.ps1 install-assets
```

Note: Without reindex/backfill, analyzer changes on newly created mappings/templates will not retroactively rewrite existing indexed tokens. Runtime query synonym expansion still works now.

## Start API

```powershell
./scripts/run_api.ps1
./scripts/run_api.ps1 -BindHost 0.0.0.0 -Port 8000
./scripts/run_api.ps1 -NoReload
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

```powershell
./scripts/run_product_pipeline.ps1 create-index --recreate
./scripts/run_product_pipeline.ps1 create-index --use-aliases --index-name pepagora_products-000002
./scripts/run_product_pipeline.ps1 install-assets
./scripts/run_product_pipeline.ps1 backfill --batch-size 192 --index-name pepagora_products-000002 --published-only
./scripts/run_product_pipeline.ps1 promote-alias --index-name pepagora_products-000002
./scripts/run_product_pipeline.ps1 backfill --batch-size 192 --published-only
./scripts/run_product_pipeline.ps1 backfill --batch-size 192 --use-write-alias --published-only
./scripts/run_product_pipeline.ps1 backfill --batch-size 192 --limit 500
```

## Keyword Index Pipeline

```powershell
./scripts/run_keyword_pipeline.ps1 create-index --recreate
./scripts/run_keyword_pipeline.ps1 create-index --use-aliases --index-name pepagora_keyword_cluster-000002
./scripts/run_keyword_pipeline.ps1 install-assets
./scripts/run_keyword_pipeline.ps1 backfill --batch-size 400 --index-name pepagora_keyword_cluster-000002
./scripts/run_keyword_pipeline.ps1 promote-alias --index-name pepagora_keyword_cluster-000002
./scripts/run_keyword_pipeline.ps1 backfill --batch-size 400
./scripts/run_keyword_pipeline.ps1 backfill --batch-size 400 --use-write-alias
./scripts/run_keyword_pipeline.ps1 backfill --batch-size 400 --limit 1000
```

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
$env:MAPPING_TELEMETRY_FILE = "logs/mapping_telemetry.jsonl"
```

Run regression and then summarize telemetry:

```powershell
python ./scripts/relevance_regression.py --random-samples 40 --output full
python ./scripts/mapping_telemetry_report.py --file logs/mapping_telemetry.jsonl --output full
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

Prepare labels in JSONL format (`config/mapping_calibration_labels.jsonl`). Supported row styles:

- Query-derived labels: `query` + `expected_category_id` or `expected_any`/`expected_all`/`banned_any`
- Direct labels: `raw_confidence` + `is_correct`

Use the sample file as a template:

```powershell
Get-Content ./config/mapping_calibration_labels.sample.jsonl
```

Train and write the model artifact:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/train_mapping_calibration.py --labels config/mapping_calibration_labels.sample.jsonl --output config/mapping_confidence_calibration.json --output-mode full
```

Run verification with learned calibration enabled:

```powershell
$env:MAPPING_ENABLE_LEARNED_CONFIDENCE_CALIBRATION = "true"
$env:MAPPING_CONFIDENCE_MODEL_FILE = "config/mapping_confidence_calibration.json"
$env:MAPPING_TELEMETRY_FILE = "logs/mapping_telemetry_phase3_complete.jsonl"
& ".\.venv\Scripts\python.exe" ./scripts/relevance_regression.py --random-samples 40 --output full
& ".\.venv\Scripts\python.exe" ./scripts/mapping_telemetry_report.py --file logs/mapping_telemetry_phase3_complete.jsonl --output full
```

Recommended: keep a balanced label set (both correct and incorrect outcomes) so learned confidence does not saturate too aggressively.

## Phase 4 Synonym Governance

Validate current synonym rules:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py validate --synonyms config/synonyms.json --output full
```

Review a proposal (without applying changes):

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py review-proposal --synonyms config/synonyms.json --proposal config/synonym_proposal.sample.json --output full
```

Apply a reviewed proposal (auto-snapshots current file first):

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py apply-proposal --synonyms config/synonyms.json --proposal config/synonym_proposal.sample.json --output full
```

Rollback to latest snapshot:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/synonym_governance.py rollback --synonyms config/synonyms.json --history-dir config/synonyms_history --output full
```

## Phase 5 Production Observability

Guard telemetry against alert/decision-rate SLOs:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/observability_guard.py --telemetry logs/mapping_telemetry_phase3_complete.jsonl --expected-canary-percent 100 --output full
& ".\.venv\Scripts\python.exe" ./scripts/observability_guard.py --telemetry logs/mapping_telemetry_phase3_complete_30.jsonl --baseline logs/mapping_telemetry_phase3_complete.jsonl --expected-canary-percent 30 --output full
```

## Phase 6 Canary Rollout Guard

Generate baseline/canary regression artifacts and evaluate rollout action:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/canary_guard.py --baseline-telemetry logs/mapping_telemetry_phase3_complete.jsonl --canary-telemetry logs/mapping_telemetry_phase3_complete_30.jsonl --baseline-regression logs/regression_baseline_100.json --canary-regression logs/regression_canary_30.json
```

Exit codes:

- `0`: promote
- `2`: hold
- `1`: rollback

## Phase 7 Continuous Quality Loop

Generate periodic quality status and next actions:

```powershell
& ".\.venv\Scripts\python.exe" ./scripts/continuous_quality_report.py --telemetry logs/mapping_telemetry_phase3_complete.jsonl --regression logs/regression_baseline_100.json --calibration-model config/mapping_confidence_calibration.json --output full
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
& ".\.venv\Scripts\python.exe" ./scripts/reliability_gate.py --random-samples 40 --baseline-canary-percent 100 --canary-percent 30 --output summary --write-report logs/reliability_gate_report.json
```

Gate output includes:

- overall pass/fail
- failed step list
- rollout action (`promote`/`hold`/`rollback`)
- artifact paths for baseline/canary regression + telemetry
