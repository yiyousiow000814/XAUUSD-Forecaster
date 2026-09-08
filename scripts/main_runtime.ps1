# Single-checkout runtime. The service registry owns commands; this controller
# owns process lifetime. Only the maintenance caller changes source identity.
Set-StrictMode -Version Latest

function ConvertTo-RuntimeArgument {
    param([AllowEmptyString()][string]$Value)
    '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Get-RuntimeFileHash {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $hasher.Dispose() }
}

function Invoke-RuntimeNative {
    param([string]$FilePath, [string[]]$Arguments, [string]$WorkingDirectory, [int]$TimeoutMilliseconds = 60000)
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.WorkingDirectory = $WorkingDirectory
    $start.Arguments = ($Arguments | ForEach-Object { ConvertTo-RuntimeArgument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    try {
        $null = $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            & taskkill.exe /PID $process.Id /T /F 2>&1 | Out-Null
            throw "Native command timed out: $([IO.Path]::GetFileName($FilePath))"
        }
        [pscustomobject]@{ exit_code = $process.ExitCode; stdout = $stdout.GetAwaiter().GetResult(); stderr = $stderr.GetAwaiter().GetResult() }
    } finally { $process.Dispose() }
}

function Invoke-RuntimeGit {
    param([string[]]$Arguments)
    $result = Invoke-RuntimeNative -FilePath 'git.exe' -Arguments (@('-C', $script:RuntimeRoot) + $Arguments) -WorkingDirectory $script:RuntimeRoot
    if ($result.exit_code -ne 0) { throw "Git operation failed: $($Arguments[0])" }
    $result.stdout.Trim()
}

function Write-RuntimeJson {
    param([string]$Path, [object]$Value)
    $temporary = "$Path.$PID.tmp"
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 12), [Text.UTF8Encoding]::new($false))
    try {
        for ($attempt = 0; $attempt -lt 50; $attempt++) {
            try {
                if ([IO.File]::Exists($Path)) { [IO.File]::Replace($temporary, $Path, [NullString]::Value) }
                else { [IO.File]::Move($temporary, $Path) }
                break
            } catch {
                $nativeError = $_.Exception.GetBaseException().HResult -band 65535
                if ($nativeError -notin @(5, 32, 33) -or $attempt -eq 49) { throw }
                Start-Sleep -Milliseconds 20
            }
        }
    } finally {
        if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
    }
}

function Read-RuntimeJson {
    param([string]$Path)
    $stream = [IO.FileStream]::new($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
    $reader = $null
    try {
        $reader = [IO.StreamReader]::new($stream, [Text.UTF8Encoding]::new($false, $true))
        $reader.ReadToEnd() | ConvertFrom-Json
    } finally {
        if ($reader) { $reader.Dispose() } else { $stream.Dispose() }
    }
}

function Get-RuntimeSetting {
    param([string]$Name)
    [Environment]::GetEnvironmentVariable($Name, 'User')
}

function Assert-RuntimeWriterIsolation {
    # Read-only inventory. Takeover must disable old tasks and stop their
    # publication owners explicitly; this assertion never stops them itself.
    foreach ($process in @(Get-CimInstance Win32_Process)) {
        $command = [string]$process.CommandLine
        if ($command -match 'xauusd_(control_center|watchdog_guard)\.ps1' -and
            ($command.IndexOf($script:RuntimeRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
             $command.IndexOf($script:RepositoryRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
            throw 'Old runtime/publication owner is still running; takeover is required'
        }
    }
    foreach ($task in @(Get-CimInstance -Namespace 'Root/Microsoft/Windows/TaskScheduler' -ClassName MSFT_ScheduledTask -Filter "TaskName LIKE 'XAUUSD-Forecaster%'") ) {
        foreach ($action in @($task.Actions)) {
            $arguments = [string]$action.Arguments
            if ($task.State -ne 1 -and $arguments -match 'xauusd_(control_center|watchdog_guard|watchdog_launcher)' -and
                ($arguments.IndexOf($script:RuntimeRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                 $arguments.IndexOf($script:RepositoryRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                throw 'Old runtime/publication task remains enabled; takeover is required'
            }
        }
    }
}

function Get-RuntimeServices {
    $contract = Get-Content -LiteralPath (Join-Path $script:RuntimeRoot 'scripts/windows-service-launch-contract.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($contract.schema_version -ne 'windows-service-launch-contract-v1') { throw 'Unknown service registry' }
    $services = @()
    foreach ($service in $contract.services) {
        if ($service.key -eq 'broadcast' -and ((Get-RuntimeSetting 'AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED') -ne '1' -or -not (Get-RuntimeSetting 'LIVE_BROADCAST_PUBLISH_TOKEN'))) { continue }
        $arguments = @($service.arguments | ForEach-Object {
            ([string]$_).Replace('{runtime_forward_root}', $script:ForwardRoot).Replace('{repository_config_root}', (Join-Path $script:RepositoryRoot '.local/config'))
        })
        $path = [IO.Path]::GetFullPath((Join-Path $script:RuntimeRoot $service.script))
        if (-not $path.StartsWith($script:RuntimeRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Service path escapes runtime' }
        $services += [pscustomobject]@{ Key = $service.key; Kind = $service.kind; ScriptPath = $path; Arguments = $arguments }
    }
    return $services
}

function Get-RuntimeServiceProcesses {
    param([object]$Service)
    # The retained production owner used unquoted paths when they had no
    # spaces. Recognize both real launch forms, with exact argument boundaries.
    $escaped = [regex]::Escape($Service.ScriptPath)
    $pattern = '(?<!\S)(?:"' + $escaped + '"|' + $escaped + ')(?!\S)'
    @(Get-CimInstance Win32_Process | Where-Object {
        if ($_.Name -notin @('python.exe', 'pythonw.exe', 'powershell.exe') -or -not $_.CommandLine) { return $false }
        $match = [regex]::Match($_.CommandLine, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if (-not $match.Success) { return $false }
        $prefix = $_.CommandLine.Substring(0, $match.Index).Trim()
        if ($_.Name -in @('python.exe', 'pythonw.exe')) {
            # Python service scripts are the first argument after the executable.
            return $prefix -match '^(?:"[^"]+"|[^\s"]+)$'
        }
        return $prefix -match '^(?:"[^"]+"|[^\s"]+)(?:\s+(?:"[^"]+"|[^\s"]+))*\s+(?:"-File"|-File)$' -and
            $prefix -notmatch '(?i)(?:^|\s)"?-(?:Command|EncodedCommand)"?(?:\s|$)'
    })
}

function Stop-RuntimeService {
    param([object]$Service)
    foreach ($process in @(Get-RuntimeServiceProcesses $Service)) {
        # Holding the process handle prevents PID reuse while taskkill stops
        # the owned tree, including the quote bridge's native child.
        try { $handle = [Diagnostics.Process]::GetProcessById([int]$process.ProcessId); $null = $handle.Handle }
        catch { continue }
        try {
            if ([Math]::Abs(($handle.StartTime.ToUniversalTime() - $process.CreationDate.ToUniversalTime()).TotalMilliseconds) -gt 1) { continue }
            & taskkill.exe /PID $process.ProcessId /T /F 2>&1 | Out-Null
            if (-not $handle.WaitForExit(15000)) { throw "Service did not stop: $($Service.Key)" }
        } finally { $handle.Dispose() }
    }
}

function Start-RuntimeService {
    param([object]$Service)
    if (@(Get-RuntimeServiceProcesses $Service).Count) { return }
    $names = switch ($Service.Key) {
        'collector' { 'BLS_API_KEY', 'BEA_API_KEY', 'FRED_API_KEY', 'EIA_API_KEY' }
        'annotator' { 'GEMINI_API_KEY', 'GEMINI_API_KEYS' }
        'api' { 'GEMINI_API_KEY', 'GEMINI_API_KEYS', 'DASHBOARD_OPERATOR_BRIDGE_TOKEN' }
        'sync' { 'DASHBOARD_OPERATOR_BRIDGE_TOKEN', 'SITES_BYPASS_TOKEN', 'CLOUDFLARE_INGEST_URL', 'CLOUDFLARE_INGEST_TOKEN' }
        'broadcast' { 'AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED', 'LIVE_BROADCAST_PUBLISH_TOKEN' }
        default { @() }
    }
    $saved = @{}
    try {
        foreach ($name in $names) {
            $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            $value = Get-RuntimeSetting $name
            if (-not $value -and $Service.Key -eq 'collector') {
                $secretPath = Join-Path $script:RepositoryRoot '.local/secrets/collector-keys.json'
                if (Test-Path -LiteralPath $secretPath) {
                    $secrets = Get-Content -LiteralPath $secretPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    $property = $secrets.PSObject.Properties[$name]
                    if ($property) { $value = [string]$property.Value }
                }
            }
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
        $executable = 'python.exe'
        $arguments = @($Service.ScriptPath) + @($Service.Arguments)
        if ($Service.Kind -eq 'PowerShell') {
            $executable = 'powershell.exe'
            $arguments = @('-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', $Service.ScriptPath) + @($Service.Arguments)
        }
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $log = Join-Path $script:LogRoot "$($Service.Key)-$stamp"
        $quoted = @($arguments | ForEach-Object { ConvertTo-RuntimeArgument ([string]$_) })
        $process = Start-Process -FilePath $executable -ArgumentList $quoted -WorkingDirectory $script:RuntimeRoot -WindowStyle Hidden -RedirectStandardOutput "$log.stdout.log" -RedirectStandardError "$log.stderr.log" -PassThru
        $process.Dispose()
    } finally {
        foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
}

function Set-RuntimeRevision {
    param([object[]]$Services, [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$Revision)
    # The maintenance caller supplies a fixed, locally available source identity.
    $target = Invoke-RuntimeGit -Arguments @('rev-parse', ($Revision + '^{commit}'))
    $current = Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD')
    $dependencyPath = Join-Path $script:RuntimeRoot 'pyproject.toml'
    $installedPath = Join-Path $script:ForwardRoot 'main-installed-dependencies.sha256'
    $installed = if (Test-Path -LiteralPath $installedPath) { [IO.File]::ReadAllText($installedPath).Trim() } else { '' }
    $dependencyHash = (Get-RuntimeFileHash $dependencyPath)
    if ($target -eq $current -and $installed -eq $dependencyHash) { return $false }
    if (Invoke-RuntimeGit -Arguments @('status', '--porcelain', '--untracked-files=normal')) { throw 'Runtime checkout has local changes; preserved without updating' }
    foreach ($service in $Services) { Stop-RuntimeService $service }
    # Never reset/clean the checkout or overwrite ignored persistent state.
    if ($target -ne $current) { $null = Invoke-RuntimeGit -Arguments @('checkout', '--detach', '--no-overwrite-ignore', $target) }
    $dependencyHash = (Get-RuntimeFileHash $dependencyPath)
    if ($installed -ne $dependencyHash) {
        $result = Invoke-RuntimeNative -FilePath 'python.exe' -Arguments @('-m', 'pip', 'install', '-e', '.') -WorkingDirectory $script:RuntimeRoot -TimeoutMilliseconds 180000
        [IO.File]::WriteAllText((Join-Path $script:LogRoot 'dependency-update.log'), $result.stdout + $result.stderr)
        if ($result.exit_code -ne 0) { throw 'Dependency update failed; see dependency-update.log' }
        [IO.File]::WriteAllText($installedPath, $dependencyHash)
    }
    return $true
}

function Invoke-MainRuntime {
    $hash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($script:RuntimeRoot.ToLowerInvariant()))).Replace('-', '')
    $mutex = [Threading.Mutex]::new($false, "Global\XauusdMainRuntime-$hash")
    $owned = $false
    $services = @()
    try {
        try { $owned = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $owned = $true }
        if (-not $owned) { return 0 }
        Assert-RuntimeWriterIsolation
        $services = @(Get-RuntimeServices)
        $loadedRevision = Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD')
        $nextStart = [DateTime]::MinValue
        $errorText = $null
        while ($true) {
            $desired = 'stopped'
            if (Test-Path -LiteralPath $script:DesiredPath) {
                $desired = (Read-RuntimeJson $script:DesiredPath).state
            }
            if ($desired -notin @('running', 'stopped', 'maintenance')) { throw 'Invalid runtime command' }
            if ((Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD')) -ne $loadedRevision) { return 75 }
            if ($desired -eq 'stopped') {
                $nextStart = [DateTime]::MinValue
                foreach ($service in $services) { Stop-RuntimeService $service }
            } elseif ($desired -eq 'running') {
                if ([DateTime]::UtcNow -ge $nextStart) {
                    foreach ($service in $services) { Start-RuntimeService $service }
                    $nextStart = [DateTime]::UtcNow.AddSeconds(60)
                }
            }
            $processes = @{}
            foreach ($service in $services) { $processes[$service.Key] = @((Get-RuntimeServiceProcesses $service) | ForEach-Object { $_.ProcessId }) }
            Write-RuntimeJson $script:StatusPath @{ state = $desired; source_revision = (Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD')); updated_at = [DateTime]::UtcNow.ToString('o'); controller_pid = $PID; services = $processes; error = $errorText }
            Start-Sleep -Seconds 5
        }
    } finally {
        if ($owned) {
            foreach ($service in $services) { Stop-RuntimeService $service }
            $mutex.ReleaseMutex()
        }
        $mutex.Dispose()
    }
}
