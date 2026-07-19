param(
    [string]$Keyword,
    [string]$SeedUrl,

    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
    [string]$BatchId,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 1000)]
    [int]$MaxPages,

    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 100000)]
    [int]$MaxItems,

    [ValidateSet("command", "api")]
    [string]$YingdaoMode = "command",

    [string]$YingdaoRunnerPath = $env:YINGDAO_RUNNER_PATH,
    [string]$YingdaoAppFile = $env:YINGDAO_APP_FILE,
    [string]$YingdaoAccountName = $env:YINGDAO_ACCOUNT_NAME,
    [string]$YingdaoRobotUuid = $env:YINGDAO_ROBOT_UUID,
    [string]$BrowserChannel = "msedge",
    [string]$BrowserExecutable,
    [double]$YingdaoTimeout = 600,
    [switch]$Headful
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $OutputRoot "archive\jd_product\$BatchId\manifest.json"
$inputCsvPath = Join-Path $OutputRoot "discovery\jd_product_urls_$BatchId.csv"
$rawCsvPath = Join-Path $OutputRoot "inbox\jd_product_${BatchId}_raw.csv"
$canResumeWithoutYingdao = (Test-Path -LiteralPath $manifestPath) -or (
    (Test-Path -LiteralPath $inputCsvPath) -and
    (Test-Path -LiteralPath $rawCsvPath)
)

if ([string]::IsNullOrWhiteSpace($Keyword) -eq [string]::IsNullOrWhiteSpace($SeedUrl)) {
    [Console]::Error.WriteLine("Exactly one of Keyword or SeedUrl is required.")
    exit 20
}

if ($YingdaoMode -eq "command") {
    if ([string]::IsNullOrWhiteSpace($YingdaoRunnerPath)) {
        $YingdaoRunnerPath = "D:\ShadowBot\ShadowBot.exe"
    }
    if (-not $canResumeWithoutYingdao -and [string]::IsNullOrWhiteSpace($YingdaoAppFile)) {
        [Console]::Error.WriteLine(
            "Command mode requires YingdaoAppFile or YINGDAO_APP_FILE."
        )
        exit 30
    }
}

$arguments = @(
    "run",
    "--project", "services/data-ops",
    "talonmart-jd-pipeline",
    "--batch-id", $BatchId,
    "--output-root", $OutputRoot,
    "--max-pages", $MaxPages,
    "--max-items", $MaxItems,
    "--browser-channel", $BrowserChannel,
    "--yingdao-mode", $YingdaoMode,
    "--yingdao-timeout", $YingdaoTimeout
)

if (-not [string]::IsNullOrWhiteSpace($Keyword)) {
    $arguments += @("--keyword", $Keyword)
}
else {
    $arguments += @("--seed-url", $SeedUrl)
}
if (-not [string]::IsNullOrWhiteSpace($BrowserExecutable)) {
    $arguments += @("--browser-executable", $BrowserExecutable)
}
if ($Headful) {
    $arguments += "--headful"
}
if ($YingdaoMode -eq "command") {
    if (-not [string]::IsNullOrWhiteSpace($YingdaoRunnerPath)) {
        $arguments += @("--yingdao-runner-path", $YingdaoRunnerPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($YingdaoAppFile)) {
        $arguments += @("--yingdao-app-file", $YingdaoAppFile)
    }
}
else {
    $arguments += @(
        "--yingdao-account-name", $YingdaoAccountName,
        "--yingdao-robot-uuid", $YingdaoRobotUuid
    )
}

Push-Location $repoRoot
try {
    & uv @arguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
