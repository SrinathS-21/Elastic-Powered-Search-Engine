param(
    [switch]$Reload,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$defaultSynonymsFile = Join-Path $repoRoot "resources/synonyms.json"
if (-not $env:B2B_SYNONYMS_FILE -and (Test-Path $defaultSynonymsFile)) {
    $env:B2B_SYNONYMS_FILE = $defaultSynonymsFile
}

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
        if (-not $line) {
            return
        }
        if ($line.StartsWith("#")) {
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

        # Explicitly override existing process-level values so stale keys do not leak into uvicorn.
        [System.Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$envFile = Join-Path $repoRoot ".env"
Load-DotEnv -Path $envFile

if ([string]::IsNullOrWhiteSpace("$env:APP_HOST")) {
    throw "Missing APP_HOST in .env. Host must be loaded from .env only."
}
$resolvedBindHost = "$env:APP_HOST".Trim()

if ([string]::IsNullOrWhiteSpace("$env:APP_PORT")) {
    throw "Missing APP_PORT in .env. Port must be loaded from .env only."
}

$portCandidate = "$env:APP_PORT".Trim()
$parsedPort = 0
if (-not [int]::TryParse($portCandidate, [ref]$parsedPort)) {
    throw "Invalid APP_PORT value '$portCandidate'. Use an integer between 1 and 65535."
}
$resolvedPort = $parsedPort

if (($resolvedPort -lt 1) -or ($resolvedPort -gt 65535)) {
    throw "Invalid port '$resolvedPort'. Use an integer between 1 and 65535."
}

[System.Environment]::SetEnvironmentVariable("APP_HOST", $resolvedBindHost, "Process")
[System.Environment]::SetEnvironmentVariable("APP_PORT", $resolvedPort.ToString(), "Process")

if ($Reload -and $NoReload) {
    throw "Use either -Reload or -NoReload, not both."
}

$useReload = $false
if ($Reload) {
    $useReload = $true
}

$modeLabel = if ($useReload) { "reload enabled" } else { "no reload" }
Write-Host "Starting API on $resolvedBindHost`:$resolvedPort ($modeLabel, loaded from .env)."

$python = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
}

$uvicornCmd = @(
    "-m", "uvicorn", "src.main:app",
    "--host", $resolvedBindHost,
    "--port", $resolvedPort.ToString()
)

# NOTE: Do NOT pass --env-file here. Environment variables are already loaded
# into the process by Load-DotEnv above. Passing --env-file again causes uvicorn
# to overwrite process env on its own thread, which can interfere with Ctrl+C
# signal propagation on Windows (signals arrive before env is fully re-applied).
if ($useReload) {
    $uvicornCmd += "--reload"
}

& $python @uvicornCmd

