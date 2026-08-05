param(
    [string]$Symbol = 'XAUUSD',
    [string]$OutputDirectory = '',
    [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$moduleRoot = Split-Path (Split-Path $projectRoot -Parent) -Parent
$repositoryRoot = Split-Path (Split-Path $moduleRoot -Parent) -Parent
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

$cliPathFile = Join-Path $repositoryRoot 'src\ctrader\windows_cli_path.txt'
$secretPathFile = Join-Path $repositoryRoot 'src\ctrader\windows_secret_path.txt'
$cli = (Get-Content -LiteralPath $cliPathFile -Raw).Trim()
$secretRoot = (Get-Content -LiteralPath $secretPathFile -Raw).Trim()
foreach ($required in @($cli, (Join-Path $secretRoot 'ctid.txt'), (Join-Path $secretRoot 'account.txt'), (Join-Path $secretRoot 'ctrader-cli.pwd'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required local cTrader file not found: $required"
    }
}

$ctid = (Get-Content -LiteralPath (Join-Path $secretRoot 'ctid.txt') -Raw).Trim()
$account = (Get-Content -LiteralPath (Join-Path $secretRoot 'account.txt') -Raw).Trim()
$arguments = @(
    'run',
    $artifact,
    '--ctid', $ctid,
    '--pwd-file', (Join-Path $secretRoot 'ctrader-cli.pwd'),
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
& $cli @arguments
if ($LASTEXITCODE -ne 0) {
    throw "cTrader CLI quote bridge exited with code $LASTEXITCODE"
}
