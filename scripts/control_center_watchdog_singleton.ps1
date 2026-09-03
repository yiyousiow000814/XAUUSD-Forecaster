# Canonical machine-wide Watchdog ownership contract. Dot-sourced by
# xauusd_control_center.ps1. Do not execute this file directly.

$watchdogOwnerReceiptMaximumBytes = 16384
$script:watchdogOwnershipContext = $null

function Get-WatchdogSha256TextHex {
    param([Parameter(Mandatory = $true)][string]$Value)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Value)
        return ([BitConverter]::ToString($algorithm.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
    } finally { $algorithm.Dispose() }
}

function Get-WatchdogCanonicalPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [IO.Path]::GetFullPath($Path).TrimEnd('\').ToLowerInvariant()
}

function Get-WatchdogSingletonDescriptor {
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $runtime = Get-WatchdogCanonicalPath -Path $moduleRoot
    $repository = Get-WatchdogCanonicalPath -Path $repositoryRoot
    $identity = @(
        $watchdogSingletonContractVersion
        $sid
        $runtime
        $repository
    ) -join "`n"
    $identityHash = Get-WatchdogSha256TextHex -Value $identity
    [pscustomobject]@{
        contract_version = $watchdogSingletonContractVersion
        user_sid = $sid
        runtime_root_hash = Get-WatchdogSha256TextHex -Value $runtime
        repository_root_hash = Get-WatchdogSha256TextHex -Value $repository
        mutex_identity_hash = $identityHash
        mutex_name = "Global\XAUUSD_Forecaster_Watchdog_$identityHash"
    }
}

function Read-WatchdogOwnerReceipt {
    if (-not (Test-Path -LiteralPath $watchdogOwnerReceiptPath -PathType Leaf)) {
        return $null
    }
    $file = Get-Item -LiteralPath $watchdogOwnerReceiptPath -ErrorAction Stop
    if ($file.Length -le 0 -or $file.Length -gt $watchdogOwnerReceiptMaximumBytes) {
        throw 'WATCHDOG_OWNER_RECEIPT_INVALID'
    }
    try {
        return Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
    } catch { throw 'WATCHDOG_OWNER_RECEIPT_INVALID' }
}

function Test-WatchdogOwnerReceiptShape {
    param(
        [Parameter(Mandatory = $true)][object]$Receipt,
        [Parameter(Mandatory = $true)][object]$Descriptor
    )
    return [bool](
        [string]$Receipt.schema_version -eq 'watchdog-owner-v2' -and
        [string]$Receipt.instance_id -match '^[0-9a-f]{32}$' -and
        [int]$Receipt.process_id -gt 0 -and
        -not [string]::IsNullOrWhiteSpace([string]$Receipt.process_start_token) -and
        [int]$Receipt.launcher_pid -gt 0 -and
        -not [string]::IsNullOrWhiteSpace([string]$Receipt.launcher_start_token) -and
        [string]$Receipt.user_sid -eq [string]$Descriptor.user_sid -and
        [string]$Receipt.runtime_root_hash -eq [string]$Descriptor.runtime_root_hash -and
        [string]$Receipt.repository_root_hash -eq [string]$Descriptor.repository_root_hash -and
        [string]$Receipt.mutex_identity_hash -eq [string]$Descriptor.mutex_identity_hash -and
        [string]$Receipt.installed_control_revision -match '^[0-9a-f]{40}$' -and
        [string]$Receipt.bundle_digest -match '^[0-9a-f]{64}$' -and
        [string]$Receipt.mode -in @('ACTIVE', 'QUIESCED_INSTALL')
    )
}

function Get-WatchdogOwnerReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $canonical = @(
        [string]$Receipt.schema_version
        [string]$Receipt.instance_id
        [string]$Receipt.process_id
        [string]$Receipt.process_start_token
        [string]$Receipt.launcher_pid
        [string]$Receipt.launcher_start_token
        [string]$Receipt.user_sid
        [string]$Receipt.runtime_root_hash
        [string]$Receipt.repository_root_hash
        [string]$Receipt.mutex_identity_hash
        [string]$Receipt.installed_control_revision
        [string]$Receipt.bundle_digest
        [string]$Receipt.acquired_at
        [string]$Receipt.mode
        [string]$Receipt.install_transaction_id
    ) -join "`n"
    return Get-WatchdogSha256TextHex -Value $canonical
}

function Write-WatchdogOwnerReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $descriptor = Get-WatchdogSingletonDescriptor
    if (-not (Test-WatchdogOwnerReceiptShape -Receipt $Receipt -Descriptor $descriptor)) {
        throw 'WATCHDOG_OWNER_RECEIPT_INVALID'
    }
    $json = $Receipt | ConvertTo-Json -Depth 6
    if ([Text.UTF8Encoding]::new($false).GetByteCount($json) -gt
        $watchdogOwnerReceiptMaximumBytes) {
        throw 'WATCHDOG_OWNER_RECEIPT_TOO_LARGE'
    }
    Write-ReleaseEvidenceUtf8Atomic -Path $watchdogOwnerReceiptPath -Content $json
    $written = Read-WatchdogOwnerReceipt
    if (-not (Test-WatchdogOwnerReceiptShape -Receipt $written -Descriptor $descriptor) -or
        [string]$written.instance_id -ne [string]$Receipt.instance_id -or
        [int]$written.process_id -ne [int]$Receipt.process_id -or
        -not (Test-ControlPlaneStartTokenEqual -Left $written.process_start_token -Right $Receipt.process_start_token)) {
        throw 'WATCHDOG_OWNER_RECEIPT_READBACK_MISMATCH'
    }
    return $written
}

function Test-WatchdogCanonicalLauncherIdentity {
    param([Parameter(Mandatory = $true)][object]$Identity)
    if (-not $Identity -or [string]$Identity.name -ne 'wscript.exe') { return $false }
    $expected = Join-Path $repositoryRoot '.local\runtime-control\xauusd_watchdog_launcher.vbs'
    return [bool]($Identity.command_line -and
        $Identity.command_line.Contains($expected) -and
        $Identity.command_line.Contains($moduleRoot) -and
        $Identity.command_line.Contains($repositoryRoot))
}

function Get-WatchdogProcessTreeSnapshot {
    param([Parameter(Mandatory = $true)][object]$RootIdentity)
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $identities = @($RootIdentity)
    $frontier = @([int]$RootIdentity.process_id)
    while ($frontier.Count -gt 0) {
        $next = @()
        foreach ($parentId in $frontier) {
            foreach ($child in @($all | Where-Object { [int]$_.ParentProcessId -eq $parentId })) {
                $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$child.ProcessId)
                if ($identity) { $identities += $identity; $next += [int]$child.ProcessId }
            }
        }
        $frontier = $next
    }
    return @($identities)
}

function Stop-WatchdogExactProcessTree {
    param([Parameter(Mandatory = $true)][object]$RootIdentity)
    $snapshot = @(Get-WatchdogProcessTreeSnapshot -RootIdentity $RootIdentity)
    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    $killer = Start-Process -FilePath $taskkill -ArgumentList @(
        '/PID', [string]$RootIdentity.process_id, '/T', '/F'
    ) -WindowStyle Hidden -PassThru
    $taskkillCompleted = $killer.WaitForExit(15000)
    if (-not $taskkillCompleted) {
        try { $killer.Kill(); $killer.WaitForExit(2000) | Out-Null } catch {}
    }
    $needsFallback = -not $taskkillCompleted -or
        ($killer.HasExited -and $killer.ExitCode -ne 0)
    if ($needsFallback) {
        [Array]::Reverse($snapshot)
        foreach ($expected in $snapshot) {
            $current = Get-ControlPlaneProcessIdentity -ProcessId ([int]$expected.process_id)
            if ($current -and (Test-ControlPlaneStartTokenEqual `
                    -Left $current.process_start_token -Right $expected.process_start_token)) {
                Stop-Process -Id ([int]$current.process_id) -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(10)
    do {
        $remaining = @($snapshot | Where-Object {
            $current = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.process_id)
            $current -and (Test-ControlPlaneStartTokenEqual `
                -Left $current.process_start_token -Right $_.process_start_token)
        })
        if ($remaining.Count -eq 0) { return 'TERMINATED' }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw 'WATCHDOG_TERMINATION_UNRESOLVED'
}

function Enter-WatchdogSingletonOwnership {
    param([string]$InstallTransactionId = '')
    $descriptor = Get-WatchdogSingletonDescriptor
    try { $mutex = [Threading.Mutex]::new($false, [string]$descriptor.mutex_name) }
    catch { throw 'WATCHDOG_SINGLETON_MUTEX_UNAVAILABLE' }
    $acquired = $false
    $abandoned = $false
    try {
        try { $acquired = $mutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $acquired = $true; $abandoned = $true }
        if (-not $acquired) {
            $mutex.Dispose()
            return [pscustomobject]@{ acquired = $false; status = 'DUPLICATE_OWNER' }
        }
        $priorReceipt = Read-WatchdogOwnerReceipt
        if ($priorReceipt) {
            if (-not (Test-WatchdogOwnerReceiptShape -Receipt $priorReceipt `
                    -Descriptor $descriptor)) {
                throw 'WATCHDOG_ABANDONED_OWNER_RECEIPT_INVALID'
            }
            $priorProcess = Get-ControlPlaneProcessIdentity `
                -ProcessId ([int]$priorReceipt.process_id)
            if ($priorProcess -and (Test-ControlPlaneStartTokenEqual `
                    -Left $priorProcess.process_start_token `
                    -Right $priorReceipt.process_start_token)) {
                throw 'WATCHDOG_SINGLETON_OWNER_CONTRADICTION'
            }
        }
        $process = Get-ControlPlaneProcessIdentity -ProcessId $PID
        if (-not $process) { throw 'WATCHDOG_PROCESS_IDENTITY_REQUIRED' }
        $launcher = Get-ControlPlaneProcessIdentity -ProcessId ([int]$process.parent_process_id)
        if (-not (Test-WatchdogCanonicalLauncherIdentity -Identity $launcher)) {
            throw 'WATCHDOG_CANONICAL_LAUNCHER_REQUIRED'
        }
        $bundle = Assert-ActiveControlBundle
        $receipt = [pscustomobject][ordered]@{
            schema_version = 'watchdog-owner-v2'
            instance_id = [guid]::NewGuid().ToString('N')
            process_id = [int]$process.process_id
            process_start_token = [string]$process.process_start_token
            launcher_pid = [int]$launcher.process_id
            launcher_start_token = [string]$launcher.process_start_token
            user_sid = [string]$descriptor.user_sid
            runtime_root_hash = [string]$descriptor.runtime_root_hash
            repository_root_hash = [string]$descriptor.repository_root_hash
            mutex_identity_hash = [string]$descriptor.mutex_identity_hash
            installed_control_revision = [string]$bundle.source_revision
            bundle_digest = [string]$bundle.bundle_digest
            acquired_at = [DateTimeOffset]::UtcNow.ToString('o')
            mode = if ($InstallTransactionId) { 'QUIESCED_INSTALL' } else { 'ACTIVE' }
            install_transaction_id = if ($InstallTransactionId) { $InstallTransactionId } else { $null }
        }
        $receipt = Write-WatchdogOwnerReceipt -Receipt $receipt
        $context = [pscustomobject]@{
            acquired = $true
            status = if ($abandoned) { 'ABANDONED_OWNER_RECOVERED' } else { 'ACQUIRED' }
            mutex = $mutex
            descriptor = $descriptor
            receipt = $receipt
            receipt_digest = Get-WatchdogOwnerReceiptDigest -Receipt $receipt
        }
        $script:watchdogOwnershipContext = $context
        return $context
    } catch {
        if ($acquired) { try { $mutex.ReleaseMutex() } catch {} }
        $mutex.Dispose()
        throw
    }
}

function Update-WatchdogSingletonMode {
    param(
        [ValidateSet('ACTIVE', 'QUIESCED_INSTALL')][string]$Mode,
        [string]$InstallTransactionId = ''
    )
    $context = $script:watchdogOwnershipContext
    if (-not $context -or -not $context.acquired) { throw 'WATCHDOG_SINGLETON_NOT_OWNED' }
    $context.receipt.mode = $Mode
    $context.receipt.install_transaction_id = if ($InstallTransactionId) { $InstallTransactionId } else { $null }
    $context.receipt = Write-WatchdogOwnerReceipt -Receipt $context.receipt
    $context.receipt_digest = Get-WatchdogOwnerReceiptDigest -Receipt $context.receipt
}

function Exit-WatchdogSingletonOwnership {
    param([AllowNull()][object]$Context = $script:watchdogOwnershipContext)
    if (-not $Context -or -not $Context.acquired) { return }
    try {
        try {
            $current = Read-WatchdogOwnerReceipt
            if ($current -and [string]$current.instance_id -eq [string]$Context.receipt.instance_id) {
                Remove-Item -LiteralPath $watchdogOwnerReceiptPath -Force -ErrorAction Stop
            }
        } catch {}
        try { $Context.mutex.ReleaseMutex() } catch {}
    } finally {
        $Context.mutex.Dispose()
        if ($script:watchdogOwnershipContext -eq $Context) {
            $script:watchdogOwnershipContext = $null
        }
    }
}
