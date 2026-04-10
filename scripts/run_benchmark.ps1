param(
    [ValidateSet("default", "compact")]
    [string]$QuerySet = "compact",
    [string]$Modes = "keyword,semantic,hybrid",
    [int]$TopN = 3,
    [double]$RelevanceThreshold = 0.5,
    [ValidateSet("summary", "full")]
    [string]$Output = "summary"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$defaultSynonymsFile = Join-Path $repoRoot "config/synonyms.json"
if (-not $env:B2B_SYNONYMS_FILE -and (Test-Path $defaultSynonymsFile)) {
    $env:B2B_SYNONYMS_FILE = $defaultSynonymsFile
}

$python = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python "scripts/benchmark_runner.py" `
    --query-set $QuerySet `
    --modes $Modes `
    --top-n $TopN `
    --relevance-threshold $RelevanceThreshold `
    --output $Output
