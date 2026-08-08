param(
    [string]$Symbol = 'XAUUSD',
    [string]$OutputDirectory = '',
    [string]$CliPath = '',
    [string]$SecretRoot = '',
    [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$moduleRoot = Split-Path (Split-Path $projectRoot -Parent) -Parent
$project = Join-Path $projectRoot 'XauusdForwardQuoteBridge.csproj'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $moduleRoot '.local\forward\quotes'
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$source = Get-Content -LiteralPath (Join-Path $projectRoot 'XauusdForwardQuoteBridge.cs') -Raw
$forbidden = @('ExecuteMarketOrder', 'PlaceLimitOrder', 'PlaceStopOrder', 'ModifyPosition', 'ClosePosition')
foreach ($token in $forbidden) {
    if ($source.Contains($token)) {
        throw "Quote bridge safety gate rejected forbidden order API token: $token"
    }
}

dotnet build $project -c Release -p:AlgoPublish=false
if ($LASTEXITCODE -ne 0) {
    throw "Quote bridge build failed with exit code $LASTEXITCODE"
}

$artifact = Join-Path $projectRoot 'bin\Release\net6.0\XauusdForwardQuoteBridge.algo'
if (-not (Test-Path -LiteralPath $artifact)) {
    throw "Built Algo artifact not found: $artifact"
}
if ($BuildOnly) {
    Write-Host "Build-only safety check passed: $artifact"
    exit 0
}

if ([string]::IsNullOrWhiteSpace($CliPath)) {
    $CliPath = [Environment]::GetEnvironmentVariable('CTRADER_CLI_PATH', 'User')
}
if ([string]::IsNullOrWhiteSpace($SecretRoot)) {
    $SecretRoot = [Environment]::GetEnvironmentVariable('CTRADER_SECRET_ROOT', 'User')
}

$localConfigRoot = Join-Path $moduleRoot '.local\config'
if ([string]::IsNullOrWhiteSpace($CliPath)) {
    $cliPathFile = Join-Path $localConfigRoot 'windows_cli_path.txt'
    if (Test-Path -LiteralPath $cliPathFile) {
        $CliPath = (Get-Content -LiteralPath $cliPathFile -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($SecretRoot)) {
    $secretPathFile = Join-Path $localConfigRoot 'windows_secret_path.txt'
    if (Test-Path -LiteralPath $secretPathFile) {
        $SecretRoot = (Get-Content -LiteralPath $secretPathFile -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($CliPath) -or [string]::IsNullOrWhiteSpace($SecretRoot)) {
    throw 'Set user-level CTRADER_CLI_PATH and CTRADER_SECRET_ROOT, or provide the matching parameters.'
}

$CliPath = [System.IO.Path]::GetFullPath($CliPath)
$SecretRoot = [System.IO.Path]::GetFullPath($SecretRoot)
foreach ($required in @($CliPath, (Join-Path $SecretRoot 'ctid.txt'), (Join-Path $SecretRoot 'account.txt'), (Join-Path $SecretRoot 'ctrader-cli.pwd'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required local cTrader file not found: $required"
    }
}

$ctid = (Get-Content -LiteralPath (Join-Path $SecretRoot 'ctid.txt') -Raw).Trim()
$account = (Get-Content -LiteralPath (Join-Path $SecretRoot 'account.txt') -Raw).Trim()
$arguments = @(
    'run',
    $artifact,
    '--ctid', $ctid,
    '--pwd-file', (Join-Path $SecretRoot 'ctrader-cli.pwd'),
    '--account', $account,
    '--symbol', $Symbol,
    '--period', 'm1',
    '--full-access',
    '--exit-on-stop',
    "--OutputDirectory=$OutputDirectory",
    "--ExpectedSymbol=$Symbol",
    '--FlushIntervalSeconds=1'
)

Write-Host "Starting read-only XAUUSD quote bridge. No order API exists in the Algo."
Write-Host "Output directory: $OutputDirectory"
& $CliPath @arguments
if ($LASTEXITCODE -ne 0) {
    throw "cTrader CLI quote bridge exited with code $LASTEXITCODE"
}
