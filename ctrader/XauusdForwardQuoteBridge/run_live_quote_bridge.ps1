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

$isolated = $null
if ([Environment]::GetEnvironmentVariable('XAUUSD_ISOLATED_CONFIGURATION', 'Process') -or
    [Environment]::GetEnvironmentVariable('XAUUSD_ISOLATED_CONFIGURATION_SHA256', 'Process')) {
    . (Join-Path $moduleRoot 'scripts\control_center_common.ps1')
    $isolated = Get-IsolatedRuntimeConfiguration
    $allowedCodeRoots = @([string]$isolated.runtime_root)
    if ($BuildOnly) { $allowedCodeRoots += [string]$isolated.source_root }
    if ([IO.Path]::GetFullPath($moduleRoot) -notin $allowedCodeRoots) {
        throw 'ISOLATED_QUOTE_CODE_CONTEXT_MISMATCH'
    }
    $expectedConfig = Join-Path ([string]$isolated.repository_root) '.local\config'
    if ($ConfigRoot -and -not ([IO.Path]::GetFullPath($ConfigRoot)).Equals(
            $expectedConfig, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'ISOLATED_QUOTE_CONFIG_CONTEXT_MISMATCH'
    }
    $ConfigRoot = $expectedConfig
    if (-not $BuildOnly) {
      foreach ($binding in @(
        @{Parameter='CliPath'; Key='CTRADER_CLI_PATH'},
        @{Parameter='SecretRoot'; Key='CTRADER_SECRET_ROOT'}
      )) {
        $declared = [string](Get-UserEnvironmentValue -Name $binding.Key)
        if (-not $declared -or -not [IO.Path]::IsPathRooted($declared)) {
            throw 'ISOLATED_QUOTE_CREDENTIAL_PATH_REQUIRED'
        }
        $declared = [IO.Path]::GetFullPath($declared)
        if (-not $declared.StartsWith(([string]$isolated.owned_root + '\'),
                [StringComparison]::OrdinalIgnoreCase)) {
            throw 'ISOLATED_QUOTE_CREDENTIAL_PATH_OUTSIDE_ROOT'
        }
        $supplied = [string](Get-Variable -Name $binding.Parameter -ValueOnly)
        if ($supplied -and -not ([IO.Path]::GetFullPath($supplied)).Equals(
                $declared, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'ISOLATED_QUOTE_CREDENTIAL_CONTEXT_MISMATCH'
        }
        Assert-IsolatedConfigurationPath -Path $declared
        Set-Variable -Name $binding.Parameter -Value $declared
      }
      foreach ($required in @($CliPath, (Join-Path $SecretRoot 'ctid.txt'),
            (Join-Path $SecretRoot 'account.txt'), (Join-Path $SecretRoot 'ctrader-cli.pwd'))) {
        Assert-IsolatedConfigurationPath -Path $required
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw 'ISOLATED_QUOTE_CREDENTIAL_FILE_MISSING'
        }
      }
    }
    Assert-IsolatedConfigurationPath -Path $ConfigRoot
}

if (-not $BuildOnly) {
    if ($isolated) {
        $authorityRoot = Join-Path ([string]$isolated.runtime_root) '.local\forward'
    } else {
        $profileRoot = [Environment]::GetFolderPath('UserProfile')
        $authorityRoot = [System.IO.Path]::GetFullPath((Join-Path $profileRoot (
            'XAUUSD-Forecaster-runtime\.local\forward'
        )))
    }
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
    if ($isolated) { Assert-IsolatedConfigurationPath -Path $OutputDirectory }
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
