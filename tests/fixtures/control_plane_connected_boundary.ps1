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
    if ($config.values.WORKER_PLACEMENT_FILE -and
        ($Arguments | ConvertTo-Json -Compress) -ceq '["deployments","status","--name","aurum-signal-room"]') {
        $path = Join-Path ([string]$config.owned_root) 'worker-placement.json'
        if ([string]$config.values.WORKER_PLACEMENT_FILE -cne $path) { throw 'CONNECTED_PLACEMENT_AUTHORITY_INVALID' }
        Assert-IsolatedConfigurationPath -Path $path
        if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or (Get-Item -LiteralPath $path).Length -gt 8192) {
            throw 'CONNECTED_PLACEMENT_UNAVAILABLE'
        }
        $placement = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
        $ids = @([string]$config.values.STABLE_WORKER_VERSION, [string]$config.values.TARGET_WORKER_VERSION)
        $owners = @($placement.versions)
        if ($ids[0] -ceq $ids[1] -or $owners.Count -notin @(1,2) -or
            @($owners | Where-Object { [string]$_.version_id -cnotin $ids -or $_.percentage -notin @(0,100) }).Count -or
            @($owners | Where-Object { $_.percentage -eq 100 }).Count -ne 1 -or
            @($owners.version_id | Select-Object -Unique).Count -ne $owners.Count) {
            throw 'CONNECTED_PLACEMENT_IDENTITY_INVALID'
        }
        return $placement
    }
    if ($Arguments.Count -eq 6 -and
        ($Arguments[0..4] | ConvertTo-Json -Compress) -ceq '["d1","execute","DB","--remote","--command"]' -and
        $config.values.D1_READ_COMMANDS_JSON -and $config.values.WORKER_LOOPBACK_BASE_URL) {
        $queries = [string]$config.values.D1_READ_COMMANDS_JSON | ConvertFrom-ReleaseControlJson
        $query = @($queries | Where-Object { [string]$_.sql -ceq $Arguments[5] -and $_.key -cin @('ledger','capabilities') })
        if ($query.Count -ne 1) { throw 'CONNECTED_D1_QUERY_UNDECLARED' }
        return Invoke-RestMethod -Uri (([string]$config.values.WORKER_LOOPBACK_BASE_URL) + '/fixture/d1/' + $query[0].key)
    }
    $requests = [string]$config.values.WRANGLER_READ_RESPONSES_JSON | ConvertFrom-ReleaseControlJson
    $key = $Arguments | ConvertTo-Json -Compress
    $matched = @($requests | Where-Object { ($_.arguments | ConvertTo-Json -Compress) -ceq $key })
    if ($matched.Count -ne 1) { throw 'CONNECTED_WRANGLER_REQUEST_UNDECLARED' }
    return $matched[0].response
}
function Invoke-WranglerDeploymentCommand {
    param([string[]]$Arguments)
    $config = Get-IsolatedRuntimeConfiguration
    if (-not $config.values.WORKER_PLACEMENT_FILE -or $Arguments.Count -notin @(8,9) -or
        $Arguments[0] -cne 'versions' -or $Arguments[1] -cne 'deploy' -or
        ($Arguments[($Arguments.Count-5)..($Arguments.Count-2)] | ConvertTo-Json -Compress) -cne
            '["--name","aurum-signal-room","--yes","--message"]') { throw 'CONNECTED_DEPLOYMENT_REQUEST_UNDECLARED' }
    $stable = [string]$config.values.STABLE_WORKER_VERSION
    $candidate = [string]$config.values.TARGET_WORKER_VERSION
    if ($stable -cnotmatch '^[0-9a-f-]{36}$' -or $candidate -cnotmatch '^[0-9a-f-]{36}$' -or $stable -ceq $candidate) {
        throw 'CONNECTED_DEPLOYMENT_IDENTITY_INVALID'
    }
    $prior = Invoke-WranglerJson -Arguments @('deployments','status','--name','aurum-signal-room')
    $specifications = @($Arguments[2..($Arguments.Count-6)])
    $message = $Arguments[-1]
    $stage = $specifications.Count -eq 2 -and $specifications[0] -ceq ($stable+'@100') -and
        $specifications[1] -ceq ($candidate+'@0') -and
        $message -ceq ('stage release candidate '+$candidate+':'+[string]$config.values.TARGET_SOURCE_REVISION)
    $promote = $specifications.Count -eq 2 -and $specifications[0] -ceq ($candidate+'@100') -and
        $specifications[1] -ceq ($stable+'@0') -and $message -cmatch '^promote release [0-9a-f-]{36}$'
    $restore = $specifications.Count -eq 1 -and $specifications[0] -ceq ($stable+'@100') -and
        $message -cmatch '^(restore committed lkg|reverse stable|automatic reverse recovery|automatic rollback) [0-9a-f-]{36}$'
    if (-not ($stage -or $promote -or $restore)) { throw 'CONNECTED_DEPLOYMENT_REQUEST_UNDECLARED' }
    if (($stage -or $promote) -and @($prior.versions | Where-Object {
            $_.version_id -ceq $stable -and $_.percentage -eq 100 }).Count -ne 1) {
        throw 'CONNECTED_DEPLOYMENT_PRIOR_OWNER_MISMATCH'
    }
    if ($promote -and @($prior.versions | Where-Object {
            $_.version_id -ceq $candidate -and $_.percentage -eq 0 }).Count -ne 1) {
        throw 'CONNECTED_DEPLOYMENT_CANDIDATE_NOT_STAGED'
    }
    $placement = [pscustomobject]@{id=('isolated-'+[guid]::NewGuid().ToString('N'));source='isolated_fixture';strategy='percentage';
        versions=@($specifications | ForEach-Object {
            $parts = $_.Split('@'); [pscustomobject]@{version_id=$parts[0];percentage=[int]$parts[1]}
        })}
    Write-ControlCenterJsonAtomic -Path ([string]$config.values.WORKER_PLACEMENT_FILE) -Value $placement -Depth 5
    [pscustomobject]@{exit_code=0;output=@('ISOLATED_PROVIDER_PLACEMENT_UPDATED')}
}
function Invoke-WebRequest {
    param($Uri,$Method='Get',$Headers,$Body,$ContentType,$TimeoutSec=15,[switch]$UseBasicParsing)
    $config = Get-IsolatedRuntimeConfiguration
    $target = [Uri]$Uri
    if ($config.values.PROVIDER_HTTP_REQUESTS_JSON -and $target.Scheme -ceq 'https' -and
        -not $target.UserInfo -and -not $target.Fragment) {
        $declarations = [string]$config.values.PROVIDER_HTTP_REQUESTS_JSON | ConvertFrom-ReleaseControlJson
        $matches = @($declarations | Where-Object {
            $_.origin -ceq $target.GetLeftPart([UriPartial]::Authority) -and $_.method -ieq $Method -and
            $_.path_query -ceq $target.PathAndQuery
        })
        if ($matches.Count -ne 1) { throw 'CONNECTED_NETWORK_TARGET_UNDECLARED' }
        $base = [Uri]$config.values.WORKER_LOOPBACK_BASE_URL
        if ($base.Scheme -cne 'http' -or $base.Host -cne '127.0.0.1' -or
            $base.Port -notin @($config.loopback_ports) -or $base.Port -eq 8765 -or
            $base.AbsolutePath -cne '/' -or $base.Query -or $base.Fragment -or $base.UserInfo) {
            throw 'CONNECTED_NETWORK_TARGET_UNDECLARED'
        }
        $forwardHeaders = @{}
        if ($Headers) { foreach($name in $Headers.Keys) { $forwardHeaders[$name]=$Headers[$name] } }
        if ($forwardHeaders.ContainsKey('X-Fixture-Requested-Origin')) { throw 'CONNECTED_NETWORK_HEADER_UNDECLARED' }
        $forwardHeaders['X-Fixture-Requested-Origin']=$target.GetLeftPart([UriPartial]::Authority)
        $target = [Uri]($base.GetLeftPart([UriPartial]::Authority) + $target.PathAndQuery)
        $PSBoundParameters['Uri']=$target
        $PSBoundParameters['Headers']=$forwardHeaders
    }
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
    # Same exact URI authority for both real caller families, with no network
    # fallback. All declared REST resources in this fixture return JSON bytes.
    $response = Invoke-WebRequest @PSBoundParameters -UseBasicParsing
    return ($response.Content | ConvertFrom-ReleaseControlJson)
}
function Get-ScheduledTask {
    [CmdletBinding()]
    param($TaskName,$TaskPath)
    $config = Get-IsolatedRuntimeConfiguration
    $prefix = 'XAUUSD-Contract-' + $config.fixture_id
    if ($TaskName -cnotin @(($prefix+'-Main'),($prefix+'-Guard')) -or
        ($TaskPath -and $TaskPath -cne $config.task_namespace)) { throw 'CONNECTED_TASK_UNDECLARED' }
    $path = Join-Path ([string]$config.owned_root) ('scheduler-' + $TaskName + '.json')
    Assert-IsolatedConfigurationPath -Path $path
    if (-not (Test-Path -LiteralPath $path -PathType Leaf) -or
        (Get-Item -LiteralPath $path).Length -gt 4096) { throw 'CONNECTED_TASK_STATE_UNDECLARED' }
    $task = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
    if ($task.TaskName -cne $TaskName -or $task.TaskPath -cne $config.task_namespace -or
        $task.Settings.Enabled -isnot [bool] -or $task.State -cnotin @('Ready','Disabled')) {
        throw 'CONNECTED_TASK_STATE_UNDECLARED'
    }
    return $task
}
function Start-ScheduledTask { throw 'CONNECTED_TASK_START_UNDECLARED' }
function Stop-ScheduledTask {
    [CmdletBinding()]
    param($TaskName,$TaskPath)
    # No scheduler-launched process exists in this declared fixture. The actual
    # installer still inventories/waits for real guard and Watchdog processes.
    $null = Get-ScheduledTask @PSBoundParameters
}
function Enable-ScheduledTask {
    [CmdletBinding()]
    param($TaskName,$TaskPath)
    $task = Get-ScheduledTask @PSBoundParameters
    $task.Settings.Enabled = $true; $task.State = 'Ready'
    $path = Join-Path ([string](Get-IsolatedRuntimeConfiguration).owned_root) ('scheduler-' + $TaskName + '.json')
    Write-ControlCenterJsonAtomic -Path $path -Value $task -Depth 5
    return Get-ScheduledTask @PSBoundParameters
}
function Disable-ScheduledTask {
    [CmdletBinding()]
    param($TaskName,$TaskPath)
    $task = Get-ScheduledTask @PSBoundParameters
    $task.Settings.Enabled = $false; $task.State = 'Disabled'
    $path = Join-Path ([string](Get-IsolatedRuntimeConfiguration).owned_root) ('scheduler-' + $TaskName + '.json')
    Write-ControlCenterJsonAtomic -Path $path -Value $task -Depth 5
    return Get-ScheduledTask @PSBoundParameters
}
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
    # optional installer GUID (N format); no ignored UNC, unquoted or trailing
    # argument. No-transaction restart remains the same exact four paths.
    $pattern = '^' + [regex]::Escape($expectedArguments) + '( "[0-9a-fA-F]{32}")?$'
    if ([string]$FilePath -ieq $expectedExecutable -and [string]$ArgumentList -cmatch $pattern -and
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
