param(
    [string]$KaynSdkPath = $env:KAYN_SDK_PATH
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$serviceRoot = Join-Path $repoRoot "services\ai-service"
$envFile = Join-Path $repoRoot ".env"

if ([string]::IsNullOrWhiteSpace($KaynSdkPath)) {
    $KaynSdkPath = Join-Path (Split-Path $repoRoot -Parent) "Kayn\packages\python-sdk"
}
$sdkRoot = (Resolve-Path -LiteralPath $KaynSdkPath).Path
$sdkProject = Join-Path $sdkRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $sdkProject -PathType Leaf)) {
    throw "Kayn SDK pyproject.toml was not found under: $sdkRoot"
}

Push-Location $serviceRoot
try {
    if (Test-Path -LiteralPath $envFile -PathType Leaf) {
        uv run --project . --env-file $envFile --with-editable $sdkRoot python -m app.kayn_target
    }
    else {
        uv run --project . --with-editable $sdkRoot python -m app.kayn_target
    }
}
finally {
    Pop-Location
}
