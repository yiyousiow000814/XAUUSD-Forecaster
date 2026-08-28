param(
    [string]$Symbol = 'XAUUSD',
    [string]$StateRoot = '',
    [string]$OutputDirectory = '',
    [string]$CliPath = '',
    [string]$SecretRoot = '',
    [string]$ConfigRoot = '',
    [switch]$BuildOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$moduleRoot = Split-Path (Split-Path $projectRoot -Parent) -Parent
$project = Join-Path $projectRoot 'XauusdForwardQuoteBridge.csproj'

if (-not $BuildOnly) {
    $authorityRoot = [Environment]::GetEnvironmentVariable('XAUUSD_RUNTIME_STATE_ROOT')
    if ([string]::IsNullOrWhiteSpace($authorityRoot)) {
        throw 'XAUUSD_RUNTIME_STATE_ROOT is required for the production quote bridge.'
    }
    $authorityRoot = [System.IO.Path]::GetFullPath($authorityRoot)
    if ([string]::IsNullOrWhiteSpace($StateRoot)) {
        throw 'StateRoot is required for the production quote bridge.'
    }
    $StateRoot = [System.IO.Path]::GetFullPath($StateRoot)
    if (-not $StateRoot.Equals(
            $authorityRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw 'StateRoot does not match the launcher authority.'
    }
    $StateRoot = $authorityRoot
    $expectedOutput = Join-Path $StateRoot 'quotes'
    if (-not [string]::IsNullOrWhiteSpace($OutputDirectory) -and
        -not ([System.IO.Path]::GetFullPath($OutputDirectory)).Equals(
            $expectedOutput, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "OutputDirectory must be $expectedOutput"
    }
    $OutputDirectory = $expectedOutput
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
}

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

if ([string]::IsNullOrWhiteSpace($ConfigRoot)) {
    $ConfigRoot = Join-Path $moduleRoot '.local\config'
}
$localConfigRoot = [System.IO.Path]::GetFullPath($ConfigRoot)
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
