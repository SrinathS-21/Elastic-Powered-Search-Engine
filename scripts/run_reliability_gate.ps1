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

function Load-DotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return
    }

    Get-Content -Path $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }

        $parts = $line -split "=", 2
        if ($parts.Count -ne 2) {
            return
        }

        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if (-not $name) {
            return
        }

        if (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'"))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }

        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$envFile = Join-Path $repoRoot ".env"
Load-DotEnv -Path $envFile

$python = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

& $python "./scripts/reliability_gate.py" `
    --random-samples $RandomSamples `
    --baseline-canary-percent $BaselineCanaryPercent `
    --canary-percent $CanaryPercent `
    --output $Output
