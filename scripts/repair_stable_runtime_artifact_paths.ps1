param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$ExpectedRevision,
    [Parameter(Mandatory = $true)][string]$ReceiptPath,
    [switch]$PreMutationPlanOnly
)

$ErrorActionPreference = "Stop"
$repairSourceRoot = Split-Path -Parent $PSScriptRoot
$controlScript = Join-Path $PSScriptRoot "xauusd_control_center.ps1"
$migrationScript = Join-Path $PSScriptRoot "migrate_runtime_artifact_paths.py"
$runtimePath = [IO.Path]::GetFullPath($RuntimeRoot)
$repositoryPath = [IO.Path]::GetFullPath($RepositoryRoot)
$database = Join-Path $runtimePath ".local\forward\forward-evidence.sqlite3"
$receiptFullPath = [IO.Path]::GetFullPath($ReceiptPath)
$runtimeForwardPath = Join-Path $runtimePath ".local\forward"
$completionPath = [IO.Path]::ChangeExtension($receiptFullPath, ".completion.json")

$null = . $controlScript -Action CodeRevision -RuntimeRoot $runtimePath `
    -RepositoryRoot $repositoryPath
if (-not (Test-RuntimeStatePathContained -Path $receiptFullPath `
        -StateTree $runtimeForwardPath) -or
    [IO.Path]::GetExtension($receiptFullPath) -ne ".json") {
    throw "ARTIFACT_REPAIR_RECEIPT_PATH_INVALID"
}
$ReceiptPath = $receiptFullPath

function Invoke-ArtifactMigrationProcess {
    param([ValidateSet("plan", "apply", "verify", "rollback")][string]$MigrationAction)
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $result = Invoke-Utf8NativeProcess -FilePath $python -Arguments @(
        $migrationScript,
        "--database", $database,
        "--runtime-root", $runtimePath,
        "--receipt", $ReceiptPath,
        "--action", $MigrationAction
    )
    if ($result.exit_code -ne 0) {
        $diagnostic = @($result.stderr_lines + $result.stdout_lines) -join "`n"
        throw "ARTIFACT_PATH_MIGRATION_$($MigrationAction.ToUpperInvariant())_FAILED: $diagnostic"
    }
    return [string]$result.stdout
}

function Wait-StableServiceState {
    param(
        [Parameter(Mandatory = $true)][object]$Service,
        [Parameter(Mandatory = $true)][string]$ExpectedState,
        [TimeSpan]$Timeout = ([TimeSpan]::FromMinutes(15))
    )
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    do {
        $owners = @(Get-ForecasterProcesses -Service $Service)
        $state = Get-ServiceState -Service $Service -Processes $owners
        if ($owners.Count -eq 1 -and $state -eq $ExpectedState) {
            return $owners[0]
        }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "STABLE_SERVICE_HEALTH_TIMEOUT:$($Service.Key):$state"
}

if ($ExpectedRevision -notmatch '^[0-9a-f]{40}$') {
    throw "ARTIFACT_REPAIR_EXACT_REVISION_REQUIRED"
}
$repairSourceRevision = (& git.exe -C $repairSourceRoot rev-parse HEAD 2>$null).Trim()
$repairOriginMain = (& git.exe -C $repairSourceRoot rev-parse origin/main 2>$null).Trim()
if ($LASTEXITCODE -ne 0 -or $repairSourceRevision -ne $ExpectedRevision -or
    $repairOriginMain -ne $ExpectedRevision) {
    throw "ARTIFACT_REPAIR_EXACT_MAIN_REQUIRED"
}
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "ARTIFACT_REPAIR_DATABASE_MISSING"
}
if ($PreMutationPlanOnly) {
    $null = Invoke-ArtifactMigrationProcess -MigrationAction plan
    [pscustomobject]@{
        schema = "xauusd.stable-artifact-repair-premutation-plan.v1"
        status = "PLANNED"
        repair_source_root = [IO.Path]::GetFullPath($repairSourceRoot)
        source_revision = $repairSourceRevision
        origin_main = $repairOriginMain
        runtime_root = $runtimePath
        repository_root = $repositoryPath
        working_directory = [IO.Path]::GetFullPath((Get-Location).Path)
        receipt = $ReceiptPath
    } | ConvertTo-Json -Depth 4 -Compress
    exit 0
}

$release = Get-ReleaseControlState
if (($release -and $release.transaction) -or (Test-Path -LiteralPath $releaseLockPath)) {
    throw "ARTIFACT_REPAIR_RELEASE_TRANSACTION_ACTIVE"
}
$controlRoot = Join-Path $repositoryPath ".local\runtime-control"
$oldBundle = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $controlRoot
if (-not $oldBundle) { throw "ARTIFACT_REPAIR_OLD_CONTROL_BUNDLE_UNVERIFIED" }
$oldWatchdogs = @(Get-VerifiedWatchdogOwners)
if ($oldWatchdogs.Count -ne 1) {
    throw "ARTIFACT_REPAIR_EXACTLY_ONE_WATCHDOG_REQUIRED"
}
$oldWatchdog = $oldWatchdogs[0]
$null = Assert-CurrentWatchdogHeartbeat -Owner $oldWatchdog `
    -ExpectedRevision ([string]$oldBundle.source_revision)
$baseline = Get-ControlPlaneIsolationSnapshot
$collector = $services | Where-Object Key -eq "collector"
$annotator = $services | Where-Object Key -eq "annotator"
$api = $services | Where-Object Key -eq "api"
$supervision = $null
$lockHeld = $false
$watchdogStopped = $false
$migrationApplied = $false
$collectorStarted = $false
$annotatorStarted = $false
$apiStarted = $false
try {
    if (-not (Enter-ReleaseTransactionLock)) {
        throw "ARTIFACT_REPAIR_RELEASE_TRANSACTION_ACTIVE"
    }
    $lockHeld = $true
    $supervision = Suspend-ControlPlaneSupervision
    Wait-ControlPlaneGuardQuiesced
    Stop-VerifiedWatchdogOwner -Identity $oldWatchdog
    $watchdogStopped = $true
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (@(Get-VerifiedWatchdogOwners).Count -ne 0) {
        throw "ARTIFACT_REPAIR_WATCHDOG_FENCE_FAILED"
    }
    Stop-ForecasterService -Service $collector
    Stop-ForecasterService -Service $annotator
    Stop-ForecasterService -Service $api
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    while ((@(Get-ForecasterProcesses -Service $collector).Count -ne 0 -or
            @(Get-ForecasterProcesses -Service $annotator).Count -ne 0 -or
            @(Get-ForecasterProcesses -Service $api).Count -ne 0) -and
           [DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 250
    }
    if (@(Get-ForecasterProcesses -Service $collector).Count -ne 0 -or
        @(Get-ForecasterProcesses -Service $annotator).Count -ne 0 -or
        @(Get-ForecasterProcesses -Service $api).Count -ne 0) {
        throw "ARTIFACT_REPAIR_SQLITE_WRITER_FENCE_FAILED"
    }
    $null = Invoke-ArtifactMigrationProcess -MigrationAction plan
    $null = Invoke-ArtifactMigrationProcess -MigrationAction apply
    $migrationApplied = $true
    $null = Invoke-ArtifactMigrationProcess -MigrationAction verify

    Start-ForecasterService -Service $collector -SkipExistingCheck
    $collectorStarted = $true
    $collectorOwner = Wait-StableServiceState -Service $collector `
        -ExpectedState "RUNNING"
    Start-ForecasterService -Service $annotator -SkipExistingCheck
    $annotatorStarted = $true
    $annotatorOwner = Wait-StableServiceState -Service $annotator `
        -ExpectedState "RUNNING"
    Start-ForecasterService -Service $api -SkipExistingCheck
    $apiStarted = $true
    $apiOwner = Wait-StableServiceState -Service $api `
        -ExpectedState "RUNNING"
    $health = Invoke-WebRequest -UseBasicParsing `
        -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw "ARTIFACT_REPAIR_API_UNHEALTHY" }

    $null = Start-WatchdogReplacement -PassThru
    $restoredWatchdog = Wait-VerifiedWatchdogHandoff `
        -ExpectedRevision ([string]$oldBundle.source_revision) `
        -PreviousIdentity $oldWatchdog
    $watchdogStopped = $false
    Restore-ControlPlaneSupervision -State $supervision
    $supervision = $null
    [pscustomobject]@{
        schema = "xauusd.stable-artifact-repair-completion.v1"
        completed_at = [DateTimeOffset]::UtcNow.ToString("o")
        exact_main_revision = $ExpectedRevision
        stable_windows_revision = [string]$baseline.business_runtime_revision
        control_bundle_revision = [string]$oldBundle.source_revision
        migration_receipt = [IO.Path]::GetFullPath($ReceiptPath)
        migration_receipt_sha256 = Get-Sha256Hex -LiteralPath $ReceiptPath
        collector_process_id = [int]$collectorOwner.ProcessId
        annotator_process_id = [int]$annotatorOwner.ProcessId
        api_process_id = [int]$apiOwner.ProcessId
        watchdog_process_id = [int]$restoredWatchdog.process_id
        api_health = "OK"
        supervision_restored = $true
    } | ConvertTo-Json -Depth 6 | Set-Content `
        -LiteralPath $completionPath -Encoding UTF8
    Get-Content -LiteralPath $completionPath -Raw -Encoding UTF8
} catch {
    $failure = $_.Exception.Message
    try {
        if ($collectorStarted) { Stop-ForecasterService -Service $collector }
        if ($annotatorStarted) { Stop-ForecasterService -Service $annotator }
        if ($apiStarted) { Stop-ForecasterService -Service $api }
        if ($migrationApplied) {
            $null = Invoke-ArtifactMigrationProcess -MigrationAction rollback
        }
        if (@(Get-ForecasterProcesses -Service $collector).Count -eq 0) {
            Start-ForecasterService -Service $collector -SkipExistingCheck
        }
        if (@(Get-ForecasterProcesses -Service $annotator).Count -eq 0) {
            Start-ForecasterService -Service $annotator -SkipExistingCheck
        }
        if (@(Get-ForecasterProcesses -Service $api).Count -eq 0) {
            Start-ForecasterService -Service $api -SkipExistingCheck
        }
        if ($watchdogStopped -and @(Get-VerifiedWatchdogOwners).Count -eq 0) {
            $null = Start-WatchdogReplacement -PassThru
            $null = Wait-VerifiedWatchdogHandoff `
                -ExpectedRevision ([string]$oldBundle.source_revision) `
                -PreviousIdentity $oldWatchdog
        }
        Restore-ControlPlaneSupervision -State $supervision
    } catch {
        throw "ARTIFACT_REPAIR_FAILED:$failure; RECOVERY_FAILED:$($_.Exception.Message)"
    }
    throw "ARTIFACT_REPAIR_FAILED:$failure; RECOVERED_PREVIOUS_SUPERVISION"
} finally {
    if ($lockHeld) { Exit-ReleaseTransactionLock }
}
