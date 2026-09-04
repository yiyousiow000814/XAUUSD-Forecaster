param(
    [Parameter(Mandatory = $true)][string]$TaskName,
    [Parameter(Mandatory = $true)][string]$HeartbeatPath,
    [Parameter(Mandatory = $true)][string]$OwnerReceiptPath,
    [Parameter(Mandatory = $true)][string]$ControlScript,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [int]$MaxAgeSeconds = 120
)

$ErrorActionPreference = "Stop"
$contractVersion = 'watchdog-machine-singleton-v2'
$maximumReceiptBytes = 16384

function Get-GuardSha256TextHex {
    param([Parameter(Mandatory = $true)][string]$Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return ([BitConverter]::ToString($algorithm.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
    } finally { $algorithm.Dispose() }
}

function Get-GuardProcessIdentity {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    [pscustomobject]@{
        process_id = [int]$process.ProcessId
        parent_process_id = [int]$process.ParentProcessId
        process_start_token = ([DateTimeOffset]$process.CreationDate).ToUniversalTime().ToString('o')
        name = [string]$process.Name
        command_line = [string]$process.CommandLine
    }
}

function Test-GuardStartTokenEqual {
    param([object]$Left, [object]$Right)
    $leftTime = [DateTimeOffset]::MinValue
    $rightTime = [DateTimeOffset]::MinValue
    return [DateTimeOffset]::TryParse([string]$Left, [ref]$leftTime) -and
        [DateTimeOffset]::TryParse([string]$Right, [ref]$rightTime) -and
        $leftTime.UtcTicks -eq $rightTime.UtcTicks
}

function Get-GuardDescriptor {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $runtime = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\').ToLowerInvariant()
    $repository = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\').ToLowerInvariant()
    $identity = @($contractVersion, $sid, $runtime, $repository) -join "`n"
    [pscustomobject]@{
        user_sid = $sid
        runtime_root_hash = Get-GuardSha256TextHex -Value $runtime
        repository_root_hash = Get-GuardSha256TextHex -Value $repository
        mutex_identity_hash = Get-GuardSha256TextHex -Value $identity
    }
}

function Get-GuardOwnerReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $canonical = @(
        [string]$Receipt.schema_version, [string]$Receipt.instance_id,
        [string]$Receipt.process_id, [string]$Receipt.process_start_token,
        [string]$Receipt.launcher_pid, [string]$Receipt.launcher_start_token,
        [string]$Receipt.user_sid, [string]$Receipt.runtime_root_hash,
        [string]$Receipt.repository_root_hash, [string]$Receipt.mutex_identity_hash,
        [string]$Receipt.installed_control_revision, [string]$Receipt.bundle_digest,
        [string]$Receipt.acquired_at, [string]$Receipt.mode,
        [string]$Receipt.install_transaction_id
    ) -join "`n"
    return Get-GuardSha256TextHex -Value $canonical
}

function Read-GuardJson {
    param([Parameter(Mandatory = $true)][string]$Path, [int]$MaximumBytes = 16384)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $file = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($file.Length -le 0 -or $file.Length -gt $MaximumBytes) { throw 'WATCHDOG_GUARD_JSON_INVALID' }
    try { return Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { throw 'WATCHDOG_GUARD_JSON_INVALID' }
}

function Get-GuardWatchdogProcesses {
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq 'powershell.exe' -and $_.CommandLine -and
                $_.CommandLine.Contains($ControlScript) -and
                $_.CommandLine -match '(?i)-Action\s+Watchdog' -and
                $_.CommandLine.Contains($RuntimeRoot) -and
                $_.CommandLine.Contains($RepositoryRoot)
            } | ForEach-Object { Get-GuardProcessIdentity -ProcessId ([int]$_.ProcessId) }
    )
}

function Get-VerifiedGuardOwner {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $descriptor = Get-GuardDescriptor
    if ([string]$Receipt.schema_version -ne 'watchdog-owner-v2' -or
        [string]$Receipt.instance_id -notmatch '^[0-9a-f]{32}$' -or
        [string]$Receipt.user_sid -ne [string]$descriptor.user_sid -or
        [string]$Receipt.runtime_root_hash -ne [string]$descriptor.runtime_root_hash -or
        [string]$Receipt.repository_root_hash -ne [string]$descriptor.repository_root_hash -or
        [string]$Receipt.mutex_identity_hash -ne [string]$descriptor.mutex_identity_hash -or
        [string]$Receipt.installed_control_revision -notmatch '^[0-9a-f]{40}$' -or
        [string]$Receipt.bundle_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$Receipt.mode -notin @('ACTIVE', 'QUIESCED_INSTALL')) {
        throw 'WATCHDOG_OWNER_RECEIPT_INVALID'
    }
    $owner = Get-GuardProcessIdentity -ProcessId ([int]$Receipt.process_id)
    if (-not $owner -or $owner.name -ne 'powershell.exe' -or
        -not (Test-GuardStartTokenEqual $owner.process_start_token $Receipt.process_start_token) -or
        -not $owner.command_line.Contains($ControlScript) -or
        -not $owner.command_line.Contains($RuntimeRoot) -or
        -not $owner.command_line.Contains($RepositoryRoot)) { return $null }
    $launcher = Get-GuardProcessIdentity -ProcessId ([int]$Receipt.launcher_pid)
    $expectedLauncher = Join-Path (Split-Path -Parent $ControlScript) 'xauusd_watchdog_launcher.vbs'
    if (-not $launcher -or $launcher.name -ne 'wscript.exe' -or
        -not (Test-GuardStartTokenEqual $launcher.process_start_token $Receipt.launcher_start_token) -or
        -not $launcher.command_line.Contains($expectedLauncher) -or
        -not $launcher.command_line.Contains($RuntimeRoot) -or
        -not $launcher.command_line.Contains($RepositoryRoot)) {
        throw 'WATCHDOG_LAUNCHER_IDENTITY_MISMATCH'
    }
    $owner | Add-Member -NotePropertyName launcher_process_id `
        -NotePropertyValue ([int]$launcher.process_id) -Force
    $owner | Add-Member -NotePropertyName launcher_start_token `
        -NotePropertyValue ([string]$launcher.process_start_token) -Force
    return $owner
}

function Stop-GuardVerifiedOwner {
    param([Parameter(Mandatory = $true)][object]$Owner)
    $powershell = Join-Path $PSHOME 'powershell.exe'
    if (-not (Test-Path -LiteralPath $powershell)) { $powershell = 'powershell.exe' }
    $process = Start-Process -FilePath $powershell -ArgumentList @(
        '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $ControlScript), '-Action', 'TerminateWatchdogOwner',
        '-RuntimeRoot', ('"{0}"' -f $RuntimeRoot),
        '-RepositoryRoot', ('"{0}"' -f $RepositoryRoot),
        '-TargetProcessId', [string]$Owner.process_id,
        '-TargetProcessStartToken', ('"{0}"' -f $Owner.process_start_token)
    ) -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(55000) -or $process.ExitCode -ne 0) {
        throw 'WATCHDOG_TERMINATION_UNRESOLVED'
    }
}

function Invoke-WatchdogGuard {
    $receipt = Read-GuardJson -Path $OwnerReceiptPath -MaximumBytes $maximumReceiptBytes
    $processes = @(Get-GuardWatchdogProcesses)
    if ($processes.Count -gt 1) { throw 'WATCHDOG_MULTIPLE_OWNERS' }
    $owner = if ($receipt) { Get-VerifiedGuardOwner -Receipt $receipt } else { $null }
    if ($processes.Count -eq 1 -and (-not $owner -or
        [int]$processes[0].process_id -ne [int]$owner.process_id)) {
        throw 'WATCHDOG_OWNER_IDENTITY_UNRESOLVED'
    }
    if ($owner) {
        $heartbeat = Read-GuardJson -Path $HeartbeatPath
        $observedAt = [DateTimeOffset]::MinValue
        $now = [DateTimeOffset]::UtcNow
        $healthy = $heartbeat -and
            [string]$heartbeat.instance_id -eq [string]$receipt.instance_id -and
            [string]$heartbeat.mutex_identity_hash -eq [string]$receipt.mutex_identity_hash -and
            [string]$heartbeat.owner_receipt_digest -eq
                (Get-GuardOwnerReceiptDigest -Receipt $receipt) -and
            [string]$heartbeat.control_bundle_revision -eq
                [string]$receipt.installed_control_revision -and
            [bool]$heartbeat.control_bundle_exact_revision -and
            [bool]$heartbeat.control_bundle_hash_verified -and
            [string]$heartbeat.supervision_mode -eq $(if (
                [string]$receipt.mode -eq 'QUIESCED_INSTALL') { 'QUIESCED' } else { 'ACTIVE' }) -and
            [string]$heartbeat.install_transaction_id -eq
                [string]$receipt.install_transaction_id -and
            [int]$heartbeat.process_id -eq [int]$receipt.process_id -and
            (Test-GuardStartTokenEqual $heartbeat.process_start_token $receipt.process_start_token) -and
            [DateTimeOffset]::TryParse([string]$heartbeat.observed_at, [ref]$observedAt) -and
            $observedAt -le $now.AddSeconds(30) -and
            ($now - $observedAt).TotalSeconds -le $MaxAgeSeconds
        if ($healthy) { return $false }
        Stop-GuardVerifiedOwner -Owner $owner
    } elseif ($receipt) {
        Remove-Item -LiteralPath $OwnerReceiptPath -Force -ErrorAction Stop
    }
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $TaskName
    return $true
}

if ($MyInvocation.InvocationName -ne '.') { Invoke-WatchdogGuard | Out-Null }
