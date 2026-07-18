param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,

    [Parameter(Mandatory = $true)]
    [string]$DatasetType,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$Encoding = "utf-8-sig",
    [string]$Delimiter = ",",
    [string]$SheetName = "0"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

Push-Location $repoRoot
try {
    uv run --project services/data-ops talonmart-data-ops --dataset-type $DatasetType --input-path $InputPath --output-root $OutputRoot --encoding $Encoding --delimiter $Delimiter --sheet-name $SheetName
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
