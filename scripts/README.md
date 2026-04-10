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
