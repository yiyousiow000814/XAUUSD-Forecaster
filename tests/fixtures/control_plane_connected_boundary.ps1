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
    if ([string]$Uri -cin @('http://127.0.0.1:8765/api/health',
            'http://127.0.0.1:8765/api/status', 'http://127.0.0.1:8765/api/critical-status') -and
        $Method -ieq 'Get' -and -not $Body -and $config.values.LOCAL_API_BASE_URL) {
        $base = [Uri]$config.values.LOCAL_API_BASE_URL
        if ($base.AbsolutePath -cne '/' -or $base.Query -or $base.Fragment) {
            throw 'CONNECTED_NETWORK_TARGET_UNDECLARED'
        }
        $target = [Uri]($base.GetLeftPart([UriPartial]::Authority) + $target.AbsolutePath)
        $PSBoundParameters['Uri'] = $target
    }
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
    if ([string]$Uri -cin @('http://127.0.0.1:8765/api/health',
            'http://127.0.0.1:8765/api/status', 'http://127.0.0.1:8765/api/critical-status') -and
        $Method -ieq 'Get' -and -not $Body -and $config.values.LOCAL_API_BASE_URL) {
        $base = [Uri]$config.values.LOCAL_API_BASE_URL
        if ($base.AbsolutePath -cne '/' -or $base.Query -or $base.Fragment) {
            throw 'CONNECTED_NETWORK_TARGET_UNDECLARED'
        }
        $target = [Uri]($base.GetLeftPart([UriPartial]::Authority) + $target.AbsolutePath)
        $PSBoundParameters['Uri'] = $target
    }
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
function Get-AvailableLoopbackPort {
    $config = Get-IsolatedRuntimeConfiguration
    $port = 0
    if (-not [int]::TryParse([string]$config.values.PREFLIGHT_API_PORT, [ref]$port) -or
        $port -notin @($config.loopback_ports) -or $port -eq 8765) {
        throw 'CONNECTED_PREFLIGHT_PORT_UNDECLARED'
    }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port)
    try { $listener.Start(); return $port } finally { $listener.Stop() }
}
function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,
        $WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError)
    if ($args.Count) { throw 'CONNECTED_PROCESS_START_UNDECLARED' }
    $config = Get-IsolatedRuntimeConfiguration
    $expectedExecutable = Join-Path ([Environment]::SystemDirectory) 'wscript.exe'
    $controlRoot = Join-Path ([string]$config.repository_root) '.local\runtime-control'
    $expectedArguments = '"{0}" "{1}" "{2}" "{3}"' -f (
        Join-Path $controlRoot 'xauusd_watchdog_launcher.vbs'), (
        Join-Path $controlRoot 'xauusd_control_center.ps1'), $config.runtime_root, $config.repository_root
    # Match the entire actual Start-WatchdogReplacement command, including its
    # optional transaction UUID; no ignored UNC, unquoted or trailing argument.
    $pattern = '^' + [regex]::Escape($expectedArguments) + '( "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")?$'
    if ([string]$FilePath -ceq $expectedExecutable -and [string]$ArgumentList -cmatch $pattern -and
        -not $WorkingDirectory -and -not $RedirectStandardOutput -and -not $RedirectStandardError) {
        Assert-IsolatedConfigurationPath -Path $controlRoot
        return Microsoft.PowerShell.Management\Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WindowStyle Hidden -PassThru:$PassThru
    }
    $preflight = $false
    $contract = $null
    $targetRevision = [string]$config.values.TARGET_SOURCE_REVISION
    $stageRoot = Join-Path ([string]$config.repository_root) ('.local\runtime-preflight\' + $targetRevision)
    if ($targetRevision -cmatch '^[0-9a-f]{40}$' -and [string]$WorkingDirectory -ceq $stageRoot -and
        [string]$FilePath -ceq [string]$config.values.PYTHON_EXECUTABLE -and $PassThru) {
        $state = Join-Path ([string]$config.runtime_root) '.local\preflight'
        $raw = @((Join-Path $stageRoot 'scripts\run_dashboard_api.py'), '--state-root', $state,
            '--runtime-role', 'preflight', '--database', (Join-Path $state 'forward-evidence.sqlite3'),
            '--host', '127.0.0.1', '--port', [string]$config.values.PREFLIGHT_API_PORT)
        $actual = @($ArgumentList)
        $equal = $raw.Count -eq $actual.Count
        for ($i=0; $equal -and $i -lt $raw.Count; $i++) {
            if ([string]$actual[$i] -cne [string]$raw[$i]) { $equal=$false }
        }
        if (-not $equal -or [int]$config.values.PREFLIGHT_API_PORT -notin @($config.loopback_ports) -or
            [int]$config.values.PREFLIGHT_API_PORT -eq 8765) { throw 'CONNECTED_PREFLIGHT_START_UNDECLARED' }
        $preflight = $true
        $contract = [pscustomobject]@{Key='preflight';Kind='Python';CodeRoot=$stageRoot;ScriptPath=$raw[0]}
    }
    if ($WindowStyle -cne 'Hidden' -or (-not $preflight -and
        [string]$WorkingDirectory -cne [string]$config.runtime_root)) {
        throw 'CONNECTED_PROCESS_START_UNDECLARED'
    }
    $matches = @()
    foreach ($serviceContract in $(if ($preflight) { @() } else { @($services) })) {
        if ([string]$serviceContract.CodeRoot -cne [string]$config.runtime_root -or
            $serviceContract.Key -cnotin @('quote','collector','annotator','api','sync','broadcast')) { continue }
        $raw = @([string]$serviceContract.ScriptPath) + @($serviceContract.Arguments)
        $executable = 'python'
        if ($serviceContract.Kind -ceq 'PowerShell') {
            $executable = 'powershell.exe'
            $raw = @('-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File') + $raw
        }
        if ([string]$FilePath -cne $executable) { continue }
        $expected = @($raw | ForEach-Object { ConvertTo-NativeProcessArgument -Argument ([string]$_) })
        $actual = @($ArgumentList)
        if ($expected.Count -ne $actual.Count) { continue }
        $equal = $true
        for ($i=0; $i -lt $expected.Count; $i++) {
            if ([string]$actual[$i] -cne [string]$expected[$i]) { $equal=$false; break }
        }
        if ($equal) { $matches += $serviceContract }
    }
    if (-not $preflight) {
        if ($matches.Count -ne 1) { throw 'CONNECTED_PROCESS_START_UNDECLARED' }
        $contract = $matches[0]
    }
    $logDirectory = Join-Path ([string]$config.runtime_root) '.local\forward\logs'
    $leaf = [IO.Path]::GetFileName([string]$RedirectStandardOutput)
    if ([IO.Path]::GetDirectoryName([string]$RedirectStandardOutput) -cne $logDirectory -or
        ($preflight -and $leaf -cne 'runtime-preflight.stdout.log') -or
        (-not $preflight -and $leaf -cnotmatch ('^control-' + [regex]::Escape($contract.Key) + '-[0-9]{8}-[0-9]{6}\.stdout\.log$')) -or
        [string]$RedirectStandardError -cne ([string]$RedirectStandardOutput).Replace('.stdout.log','.stderr.log')) {
        throw 'CONNECTED_PROCESS_LOG_UNDECLARED'
    }
    foreach ($path in @($WorkingDirectory,$contract.ScriptPath,$RedirectStandardOutput,$RedirectStandardError)) {
        Assert-IsolatedConfigurationPath -Path $path
    }
    $native = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
    if ($contract.Kind -ceq 'Python') {
        $native = [string]$config.values.PYTHON_EXECUTABLE
        $guard = [string]$config.values.PYTHON_STARTUP_GUARD
        if (-not $guard -or -not [IO.Path]::IsPathRooted($guard)) { throw 'CONNECTED_BUSINESS_GUARD_UNDECLARED' }
        $guard = [IO.Path]::GetFullPath($guard)
        if (-not $native -or -not [IO.Path]::IsPathRooted($native) -or
            [IO.Path]::GetFileName($native) -ine 'python.exe' -or
            -not $guard -or -not $guard.StartsWith(([string]$config.owned_root + '\'), [StringComparison]::OrdinalIgnoreCase) -or
            ([string]$env:PYTHONPATH).Split([IO.Path]::PathSeparator)[0] -cne $guard -or
            $env:XAUUSD_FIXTURE_CONFIGURATION -cne $env:XAUUSD_ISOLATED_CONFIGURATION) {
            throw 'CONNECTED_BUSINESS_GUARD_UNDECLARED'
        }
        Assert-IsolatedConfigurationPath -Path $guard
        # Generated guards already contain the configuration digest. Their
        # identities travel in the same parent launch environment, not inside
        # that configuration (which would create a hash dependency cycle).
        foreach ($entry in @(@{name='sitecustomize.py';key='XAUUSD_FIXTURE_STARTUP_GUARD_SHA256'},
                @{name='fixture_business_environment.py';key='XAUUSD_FIXTURE_BUSINESS_GUARD_SHA256'})) {
            $guardFile = Join-Path $guard $entry.name
            Assert-IsolatedConfigurationPath -Path $guardFile
            $expectedGuard = [Environment]::GetEnvironmentVariable($entry.key, 'Process')
            if ($expectedGuard -cnotmatch '^[0-9a-f]{64}$' -or -not (Test-Path -LiteralPath $guardFile -PathType Leaf) -or
                (Get-FileHash -LiteralPath $guardFile -Algorithm SHA256).Hash.ToLowerInvariant() -cne
                    $expectedGuard) { throw 'CONNECTED_BUSINESS_GUARD_UNDECLARED' }
        }
    }
    if ($contract.Key -ceq 'api') {
        $base = [Uri]$config.values.LOCAL_API_BASE_URL
        if (-not $base -or $base.Scheme -cne 'http' -or $base.Host -cne '127.0.0.1' -or
            $base.Port -notin @($config.loopback_ports) -or $base.Port -eq 8765 -or
            '--host' -cin @($contract.Arguments) -or '--port' -cin @($contract.Arguments)) {
            throw 'CONNECTED_API_BIND_UNDECLARED'
        }
        $ArgumentList = @($ArgumentList) + @('--host','127.0.0.1','--port',[string]$base.Port)
    }
    Microsoft.PowerShell.Management\Start-Process -FilePath $native -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru:$PassThru `
        -RedirectStandardOutput $RedirectStandardOutput -RedirectStandardError $RedirectStandardError
}
