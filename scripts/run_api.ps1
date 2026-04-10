param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$NoReload
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

$uvicornCmd = @(
    "-m", "uvicorn", "src.main:app",
    "--host", $BindHost,
    "--port", $Port.ToString()
)

if (-not $NoReload) {
    $uvicornCmd += "--reload"
}

& $python @uvicornCmd
