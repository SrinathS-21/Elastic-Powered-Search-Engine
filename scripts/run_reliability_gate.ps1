param(
    [int]$RandomSamples = 40,
    [int]$BaselineCanaryPercent = 100,
    [int]$CanaryPercent = 30,
    [ValidateSet("summary", "full")]
    [string]$Output = "summary"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python "./scripts/reliability_gate.py" `
    --random-samples $RandomSamples `
    --baseline-canary-percent $BaselineCanaryPercent `
    --canary-percent $CanaryPercent `
    --output $Output
