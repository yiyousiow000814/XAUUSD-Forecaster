# Reviewed external boundary for the connected isolated lifecycle only.
# No lifecycle predicate, transaction, ownership or Observe implementation is replaced.
# Unknown provider operations fail before a native command or network request.
function Invoke-GitHubChecksRead {
    param([string]$Revision)
    $config = Get-IsolatedRuntimeConfiguration
    if ($Revision -cne [string]$config.values.TARGET_SOURCE_REVISION -or
        -not $config.values.GITHUB_CHECK_RUNS_JSON) { throw 'CONNECTED_GITHUB_REQUEST_UNDECLARED' }
    [pscustomobject]@{exit_code=0;stdout=[string]$config.values.GITHUB_CHECK_RUNS_JSON;
        stdout_lines=@([string]$config.values.GITHUB_CHECK_RUNS_JSON);stderr='';stderr_lines=@()}
}
function Invoke-WranglerJson {
    param([string[]]$Arguments)
    $config = Get-IsolatedRuntimeConfiguration
    $requests = [string]$config.values.WRANGLER_READ_RESPONSES_JSON | ConvertFrom-ReleaseControlJson
    $key = $Arguments | ConvertTo-Json -Compress
    $matched = @($requests | Where-Object { ($_.arguments | ConvertTo-Json -Compress) -ceq $key })
    if ($matched.Count -ne 1) { throw 'CONNECTED_WRANGLER_REQUEST_UNDECLARED' }
    return $matched[0].response
}
function Invoke-WranglerDeploymentCommand {
    param([string[]]$Arguments)
    throw 'CONNECTED_DEPLOYMENT_REQUEST_UNDECLARED'
}
function Invoke-WebRequest {
    param($Uri,$Method='Get',$Headers,$Body,$ContentType,$TimeoutSec=15,[switch]$UseBasicParsing)
    $config = Get-IsolatedRuntimeConfiguration
    $target = [Uri]$Uri
    if ($target.Scheme -cne 'http' -or $target.Host -cnotin @('127.0.0.1','localhost','::1') -or
        $target.Port -notin @($config.loopback_ports) -or $target.Port -eq 8765 -or $target.UserInfo) {
        throw 'CONNECTED_NETWORK_TARGET_UNDECLARED'
    }
    Microsoft.PowerShell.Utility\Invoke-WebRequest @PSBoundParameters -MaximumRedirection 0
}
function Invoke-RestMethod {
    param($Uri,$Method='Get',$Headers,$Body,$ContentType,$TimeoutSec=15)
    $config = Get-IsolatedRuntimeConfiguration
    $target = [Uri]$Uri
    if ($target.Scheme -cne 'http' -or $target.Host -cnotin @('127.0.0.1','localhost','::1') -or
        $target.Port -notin @($config.loopback_ports) -or $target.Port -eq 8765 -or $target.UserInfo) {
        throw 'CONNECTED_NETWORK_TARGET_UNDECLARED'
    }
    Microsoft.PowerShell.Utility\Invoke-RestMethod @PSBoundParameters -MaximumRedirection 0
}
function Get-ScheduledTask {
    param($TaskName,$TaskPath)
    $config = Get-IsolatedRuntimeConfiguration
    $prefix = 'XAUUSD-Contract-' + $config.fixture_id
    if ($TaskName -cnotin @($prefix+'-Main',$prefix+'-Guard') -or
        ($TaskPath -and $TaskPath -cne $config.task_namespace)) { throw 'CONNECTED_TASK_UNDECLARED' }
    [pscustomobject]@{TaskName=$TaskName;TaskPath=$config.task_namespace;State='Ready';Settings=[pscustomobject]@{Enabled=$true}}
}
function Start-ScheduledTask { throw 'CONNECTED_TASK_START_UNDECLARED' }
function Stop-ScheduledTask { param($TaskName,$TaskPath); $null = Get-ScheduledTask @PSBoundParameters }
function Enable-ScheduledTask { throw 'CONNECTED_TASK_ENABLE_UNDECLARED' }
function Disable-ScheduledTask { throw 'CONNECTED_TASK_DISABLE_UNDECLARED' }
function Register-ScheduledTask { throw 'CONNECTED_TASK_REGISTER_UNDECLARED' }
function Unregister-ScheduledTask { throw 'CONNECTED_TASK_UNREGISTER_UNDECLARED' }
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru)
    $config = Get-IsolatedRuntimeConfiguration
    $expectedExecutable = Join-Path ([Environment]::SystemDirectory) 'wscript.exe'
    $controlRoot = Join-Path ([string]$config.repository_root) '.local\runtime-control'
    $expectedArguments = '"{0}" "{1}" "{2}" "{3}"' -f (
        Join-Path $controlRoot 'xauusd_watchdog_launcher.vbs'), (
        Join-Path $controlRoot 'xauusd_control_center.ps1'), $config.runtime_root, $config.repository_root
    # Match the entire actual Start-WatchdogReplacement command, including its
    # optional transaction UUID; no ignored UNC, unquoted or trailing argument.
    $pattern = '^' + [regex]::Escape($expectedArguments) + '( "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")?$'
    if ([string]$FilePath -cne $expectedExecutable -or [string]$ArgumentList -cnotmatch $pattern) {
        throw 'CONNECTED_PROCESS_START_UNDECLARED'
    }
    Assert-IsolatedConfigurationPath -Path $controlRoot
    Microsoft.PowerShell.Management\Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WindowStyle Hidden -PassThru:$PassThru
}
