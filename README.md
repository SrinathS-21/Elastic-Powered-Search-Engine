# Pepagora Search Service

Production-ready FastAPI service for category mapping and suggestion workflows, with Elasticsearch/OpenSearch backend support and operational quality scripts.

## What Runs in Production

- App entrypoint: `src/main.py`
- Public runtime routes: `src/routers/ui.py` (`/` and `/ui-api/*`)
- Static UI: `ui/`
- Core service logic: `src/services/` and `src/core/`

`src/routers/search.py` and `src/routers/quality.py` are intentionally unmounted by default.

## Repository Structure

| Path | Purpose |
|---|---|
| `src/main.py` | FastAPI app setup, middleware, router mounting |
| `src/core/` | Environment config, backend clients, embedding client, lifecycle |
| `src/services/` | Search, mapping, benchmark, synonym logic |
| `src/routers/` | API route definitions (UI routes are production-mounted) |
| `scripts/` | Regression, reliability, governance, benchmark tooling |
| `resources/` | Synonyms and calibration artifacts |
| `runtime/` | Generated telemetry and reliability outputs |
| `ui/` | Static web pages served by API |
| `Notebooks/` | Migration and exploratory notebooks (non-runtime) |

## Local Run

1. Configure environment variables in `.env`.
2. Install dependencies from `src/requirements.txt`.
3. Start API using PowerShell helper:

```powershell
./scripts/run_api.ps1
```

## Production Environment Notes

- Required host/backend variables are validated in `src/core/config.py`.
- CORS is configurable with `CORS_ALLOW_ORIGINS` (comma-separated). Default remains `*` for backward compatibility.
- FastAPI docs/openapi endpoints are disabled in production app configuration.

## Documentation Contract (Production)

Use this repository documentation with clear ownership:

- `README.md`: quick onboarding, repository layout, and deploy checks.
- `DOCUMENTATION.md`: architecture decisions, runtime boundaries, and release gates.
- `scripts/README.md`: operational runbook for benchmarks, reliability, and one-time scripts.

To avoid drift, keep only markdown files as source-of-truth and avoid duplicate generated mirrors in version control.

## Validation Before Deploy

```powershell
& ".\.venv\Scripts\python.exe" -m compileall src scripts
& ".\.venv\Scripts\python.exe" -m pip check
```

## Operational Scripts

See `scripts/README.md` for:

- benchmark and regression execution
- canary and reliability gate checks
- synonym governance workflow
- observability guardrails

## Primary Documentation

- Decision + architecture: `DOCUMENTATION.md`
- Operational script usage: `scripts/README.md`

## Decisions To Keep Codebase Clear

These are the remaining owner-level decisions:

1. Should unmounted routers (`src/routers/search.py`, `src/routers/quality.py`) remain in `src/routers/` or move to `internal/archive`?
2. Should notebooks stay in main release branches or move to a research branch/workflow?
3. What is the retention window for one-time migration scripts before archival/removal?
4. Confirm production `CORS_ALLOW_ORIGINS` allowlist values per environment.
