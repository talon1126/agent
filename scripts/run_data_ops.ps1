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

function Get-DataOpsHandoffPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$RuntimeRoot
    )

    if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
        throw "Input file does not exist: $SourcePath"
    }

    $resolvedSource = (Resolve-Path -LiteralPath $SourcePath).Path
    $inboxRoot = Join-Path $runtimeRoot "inbox"
    New-Item -ItemType Directory -Path $inboxRoot -Force | Out-Null
    $resolvedInbox = [IO.Path]::GetFullPath($inboxRoot)
    $inboxPrefix = $resolvedInbox.TrimEnd("\", "/") + [IO.Path]::DirectorySeparatorChar

    if ($resolvedSource.StartsWith(
        $inboxPrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        return $resolvedSource
    }

    $handoffPath = Join-Path $resolvedInbox ([IO.Path]::GetFileName($resolvedSource))
    if (Test-Path -LiteralPath $handoffPath) {
        throw "Inbox handoff already exists: $handoffPath"
    }
    Copy-Item -LiteralPath $resolvedSource -Destination $handoffPath
    return $handoffPath
}

function Invoke-DataOpsProcessing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$DatasetType,

        [Parameter(Mandatory = $true)]
        [string]$RuntimeRoot,

        [string]$Encoding = "utf-8-sig",
        [string]$Delimiter = ",",
        [string]$SheetName = "0"
    )

    $handoffPath = Get-DataOpsHandoffPath `
        -SourcePath $SourcePath `
        -RuntimeRoot $RuntimeRoot
    & uv run --project services/data-ops talonmart-data-ops `
        --dataset-type $DatasetType `
        --input-path $handoffPath `
        --output-root $RuntimeRoot `
        --encoding $Encoding `
        --delimiter $Delimiter `
        --sheet-name $SheetName
    if ($LASTEXITCODE -ne 0) {
        throw "$DatasetType processing failed with exit code $LASTEXITCODE"
    }
}

function Invoke-JdProductProcessing {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,

        [Parameter(Mandatory = $true)]
        [string]$RuntimeRoot,

        [string]$Encoding = "utf-8-sig",
        [string]$Delimiter = ",",
        [string]$SheetName = "0"
    )

    Invoke-DataOpsProcessing `
        -SourcePath $SourcePath `
        -DatasetType "jd_product" `
        -RuntimeRoot $RuntimeRoot `
        -Encoding $Encoding `
        -Delimiter $Delimiter `
        -SheetName $SheetName
}

$scriptExitCode = 0
Push-Location $repoRoot
try {
    $runtimeRoot = [IO.Path]::GetFullPath($OutputRoot)
    if ($DatasetType -eq "jd_product") {
        Invoke-JdProductProcessing `
            -SourcePath $InputPath `
            -RuntimeRoot $runtimeRoot `
            -Encoding $Encoding `
            -Delimiter $Delimiter `
            -SheetName $SheetName
    }
    else {
        Invoke-DataOpsProcessing `
            -SourcePath $InputPath `
            -DatasetType $DatasetType `
            -RuntimeRoot $runtimeRoot `
            -Encoding $Encoding `
            -Delimiter $Delimiter `
            -SheetName $SheetName
    }
}
catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $scriptExitCode = 1
}
finally {
    Pop-Location
}
exit $scriptExitCode
