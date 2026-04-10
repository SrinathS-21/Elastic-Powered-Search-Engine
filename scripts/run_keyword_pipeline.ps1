param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("create-index", "install-assets", "promote-alias", "backfill")]
    [string]$Command,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PipelineArgs
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

$args = @("-m", "src.pipelines.keyword_indexing_pipeline", $Command)
if ($PipelineArgs) {
    $args += $PipelineArgs
}

& $python @args
