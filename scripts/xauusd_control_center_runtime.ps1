# Extracted from the latest-main Control Center.
# Owner: runtime supervision and Control Plane.

function Get-UserEnvironmentValue {
    param([Parameter(Mandatory = $true)][string]$Name)
    [Environment]::GetEnvironmentVariable($Name, "User")
}

function Get-CollectorSecret {
    param([Parameter(Mandatory = $true)][string]$Name)
    $userValue = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($userValue) { return $userValue.Trim() }
    if (-not (Test-Path -LiteralPath $collectorSecretsPath)) { return "" }
    try {
        $secrets = Get-Content -LiteralPath $collectorSecretsPath -Raw | ConvertFrom-Json
        $property = $secrets.PSObject.Properties[$Name]
        if ($property -and $property.Value) { return ([string]$property.Value).Trim() }
    } catch {
        return ""
    }
    return ""
}

function Get-ForecasterProcessSnapshot {
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("python.exe", "powershell.exe") })
}

function Test-ForecasterServiceProcess {
    param([object]$Process, [pscustomobject]$Service)
    if (-not $Process.CommandLine) { return $false }
    if ($Service.Kind -eq "Python") {
        return $Process.Name -eq "python.exe" -and
            $Process.CommandLine.Contains($Service.Match)
    }
    if ($Service.Kind -eq "PowerShell") {
        return $Process.Name -eq "powershell.exe" -and
            $Process.CommandLine -match ('(?i)-File\s+"?[^"\r\n]*{0}' -f
                [regex]::Escape($Service.Match))
    }
    return $false
}

function Get-ForecasterProcesses {
    param([pscustomobject]$Service)
    @(Get-ForecasterProcessSnapshot |
        Where-Object { Test-ForecasterServiceProcess -Process $_ -Service $Service })
}

function Get-CodeRevision {
    try {
        $revision = (& git -C $moduleRoot rev-parse HEAD 2>$null).Trim()
        if ($LASTEXITCODE -eq 0 -and $revision -match '^[0-9a-f]{40}$') {
            return $revision
        }
    } catch {}
    return $null
}

function Get-RuntimeUpdateState {
    if (-not (Test-Path -LiteralPath $runtimeUpdateStatePath)) { return $null }
    try {
        Get-Content -LiteralPath $runtimeUpdateStatePath -Raw | ConvertFrom-Json
    } catch { $null }
}

function Write-RuntimeUpdateState {
    param([hashtable]$Values)
    $current = @{}
    $prior = Get-RuntimeUpdateState
    if ($prior) {
        foreach ($property in $prior.PSObject.Properties) {
            $current[$property.Name] = $property.Value
        }
    }
    foreach ($key in $Values.Keys) { $current[$key] = $Values[$key] }
    $directory = Split-Path -Parent $runtimeUpdateStatePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$runtimeUpdateStatePath.tmp"
    $current | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $runtimeUpdateStatePath -Force
}

function Get-RuntimeCodeState {
    if (-not (Test-Path -LiteralPath $runtimeCodeStatePath)) { return $null }
    try {
        return Get-Content -LiteralPath $runtimeCodeStatePath -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Write-RuntimeCodeState {
    param([string]$Revision)
    $directory = Split-Path -Parent $runtimeCodeStatePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$runtimeCodeStatePath.tmp"
    $servicePids = @{}
    foreach ($service in @($services | Where-Object { $_.Key -in $reloadableServiceKeys })) {
        $servicePids[$service.Key] = @(
            Get-ForecasterProcesses $service | ForEach-Object { $_.ProcessId }
        )
    }
    [pscustomobject]@{
        applied_revision = $Revision
        applied_at = [DateTimeOffset]::UtcNow.ToString("o")
        service_pids = $servicePids
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $runtimeCodeStatePath -Force
}

function Get-RuntimeHeartbeat {
    param(
        [string]$Path,
        [string]$ServiceName,
        [string[]]$AllowedStates = @("RUNNING")
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $heartbeat = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $lastSuccess = [DateTimeOffset]::MinValue
        if ([string]$heartbeat.service -ne $ServiceName -or
            [string]$heartbeat.state -notin $AllowedStates -or
            -not [DateTimeOffset]::TryParse(
                [string]$heartbeat.last_success, [ref]$lastSuccess
            )) { return $null }
        return [pscustomobject]@{
            LastSuccess = $lastSuccess
            State = [string]$heartbeat.state
        }
    } catch { return $null }
}

function Get-ServiceProcessStartedAt {
    param([array]$Processes)
    try {
        $process = Get-Process -Id $Processes[0].ProcessId -ErrorAction Stop
        return [DateTimeOffset]$process.StartTime.ToUniversalTime()
    } catch { return [DateTimeOffset]::MinValue }
}

function Test-CodeReloadHealth {
    param(
        [DateTimeOffset]$ReloadStarted,
        [string[]]$AllowedWorkerStates = @("STARTING", "RUNNING"),
        [string[]]$RequiredServiceKeys = $reloadableServiceKeys
    )
    foreach ($service in @($services | Where-Object { $_.Key -in $RequiredServiceKeys })) {
        if (@(Get-ForecasterProcesses $service).Count -eq 0) { return $false }
    }
    foreach ($heartbeatSpec in @(
        @("collector", "collector-status.json"),
        @("annotator", "news-annotator-status.json")
    )) {
        # Collector reconciliation can temporarily keep the annotator waiting
        # on SQLite during a coordinated reload.  A fresh STARTING heartbeat
        # proves either candidate process launched; the subsequent observation
        # boundary still requires real decision cycles and rolls back a stuck
        # startup.
        $heartbeat = Get-RuntimeHeartbeat `
            -Path (Join-Path $moduleRoot ".local\forward\$($heartbeatSpec[1])") `
            -ServiceName $heartbeatSpec[0] -AllowedStates $AllowedWorkerStates
        if (-not $heartbeat -or $heartbeat.LastSuccess -lt $ReloadStarted) {
            return $false
        }
    }
    if ("api" -in $RequiredServiceKeys) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2
            if ($response.StatusCode -ne 200) { return $false }
        } catch { return $false }
    }
    if ("sync" -notin $RequiredServiceKeys) { return $true }
    $statusFile = Join-Path $moduleRoot ".local\forward\dashboard-sync-status.json"
    if (-not (Test-Path -LiteralPath $statusFile)) { return $false }
    try {
        $syncStatus = Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
        $lastAttempt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse(
            [string]$syncStatus.last_attempt, [ref]$lastAttempt
        )) { return $false }
        return (
            $lastAttempt -ge $ReloadStarted -and
            [string]$syncStatus.status -in @("OK", "DEGRADED")
        )
    } catch { return $false }
}

function Restart-CodeReloadableServices {
    param([string]$Revision, [string[]]$DeferredServiceKeys = @())
    $targets = @($services | Where-Object { $_.Key -in $reloadableServiceKeys })
    $immediateTargets = @($targets | Where-Object { $_.Key -notin $DeferredServiceKeys })
    $reloadStarted = [DateTimeOffset]::UtcNow
    Write-WatchdogEvent -Event "CODE_REVISION_RELOAD_STARTED" `
        -Service "collector,annotator,api,sync" -State $Revision
    foreach ($service in $targets) { Stop-ForecasterService $service }
    Start-Sleep -Milliseconds 800
    foreach ($service in $immediateTargets) {
        Start-ForecasterService $service -SkipExistingCheck
    }
    $deadline = [DateTimeOffset]::UtcNow.Add($codeReloadTimeout)
    do {
        Start-Sleep -Milliseconds 500
        Write-WatchdogHeartbeat
        $healthy = Test-CodeReloadHealth -ReloadStarted $reloadStarted `
            -RequiredServiceKeys @($immediateTargets.Key)
    } while (-not $healthy -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $healthy) { throw "Code revision reload failed functional health checks." }
    Write-WatchdogEvent -Event "CODE_REVISION_RELOAD_HEALTHY" `
        -Service "collector,annotator,api,sync" -State $Revision
    $broadcastService = $services | Where-Object Key -eq "broadcast" | Select-Object -First 1
    if ($broadcastService -and (Test-BroadcastPublisherEnabled)) {
        Stop-ForecasterService $broadcastService
        if (-not [string]::IsNullOrWhiteSpace(
            (Get-BroadcastPublisherToken)
        )) {
            try {
                Start-ForecasterService $broadcastService -SkipExistingCheck
                Write-WatchdogEvent -Event "BROADCAST_PUBLISHER_RESTARTED" `
                    -Service "broadcast" -State $Revision
            } catch {
                Write-WatchdogEvent -Event "BROADCAST_PUBLISHER_DEGRADED" `
                    -Service "broadcast" -State (
                        Protect-PreflightDiagnosticText $_.Exception.Message
                    )
            }
        }
    }
    return $reloadStarted
}

function Complete-DeferredServiceReload {
    param([DateTimeOffset]$ReloadStarted, [string[]]$DeferredServiceKeys)
    foreach ($service in @($services | Where-Object { $_.Key -in $DeferredServiceKeys })) {
        Start-ForecasterService $service -SkipExistingCheck
    }
    $deadline = [DateTimeOffset]::UtcNow.Add($codeReloadTimeout)
    do {
        Start-Sleep -Milliseconds 500
        Write-WatchdogHeartbeat
        $healthy = Test-CodeReloadHealth -ReloadStarted $ReloadStarted
    } while (-not $healthy -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $healthy) { throw "Deferred release services failed functional health checks." }
}

function Invoke-RuntimeCandidateActivation {
    param([string]$Revision, [string]$PreviousRevision)
    $reloadStarted = Restart-CodeReloadableServices -Revision $Revision
    Start-RuntimeObservation -Revision $Revision `
        -PreviousRevision $PreviousRevision -HealthBoundary $reloadStarted
    # Observation is durable before applied_revision. A watchdog restart in
    # between repeats safe work instead of silently skipping validation.
    Write-RuntimeCodeState -Revision $Revision
    Write-WatchdogEvent -Event "CODE_REVISION_RELOAD_APPLIED" `
        -Service "collector,annotator,api,sync" -State $Revision
}

function Get-RuntimeDecisionTimes {
    try {
        $status = Invoke-RestMethod -Method Get `
            -Uri "http://127.0.0.1:8765/api/status" -TimeoutSec 10
        return @(
            $status.recent_decisions |
                Where-Object { $_.decision_time } |
                ForEach-Object { [string]$_.decision_time }
        )
    } catch {}
    return @()
}

function Get-LatestRuntimeDecisionTime {
    $times = @(Get-RuntimeDecisionTimes)
    if ($times.Count -gt 0) { return [string]$times[0] }
    return $null
}

function Test-CurrentProductionShape {
    try {
        $python = (Get-Command python.exe -ErrorAction Stop).Source
        $arguments = @(
            (Join-Path $moduleRoot "scripts\check_production_shape.py"),
            "--status-url", "http://127.0.0.1:8765/api/critical-status",
            "--allow-pending-generation-decision"
        )
        $result = @(& $python @arguments 2>&1)
        $exitCode = $LASTEXITCODE
        $resultText = ($result | ForEach-Object { [string]$_ }) -join "`n"
        if ($exitCode -eq 75) {
            try {
                $payload = $resultText | ConvertFrom-Json -ErrorAction Stop
                $code = [string]$payload.error_code
                if ($code) { return "DEFERRED:$code" }
            } catch {}
            return "DEFERRED:STATUS_SNAPSHOT_REFRESH_IN_PROGRESS"
        }
        if ($exitCode -ne 0) { return "production shape rejected: $resultText" }
        return $null
    } catch {
        return $_.Exception.Message
    }
}

function Start-RuntimeObservation {
    param(
        [string]$Revision,
        [string]$PreviousRevision,
        [DateTimeOffset]$HealthBoundary = [DateTimeOffset]::UtcNow,
        [ValidateSet("PROMOTE", "REVERSE")][string]$Mode = "PROMOTE",
        [array]$DeferredProjectionObligations = @(),
        [string]$ValidationKey = "",
        [DateTimeOffset]$ProjectionBoundary = [DateTimeOffset]::UtcNow
    )
    $latestDecision = Get-LatestRuntimeDecisionTime
    Write-RuntimeUpdateState @{
        update_status = "OBSERVING"
        observing_revision = $Revision
        previous_revision = $PreviousRevision
        observation_started_at = [DateTimeOffset]::UtcNow.ToString("o")
        observation_ready_at = $null
        observation_health_boundary_at = $HealthBoundary.ToString("o")
        observation_last_decision_time = $latestDecision
        observation_success_cycles = 0
        observation_consecutive_failures = 0
        observation_mode = $Mode
        observation_validation_key = if ($ValidationKey) { $ValidationKey } else { $null }
        observation_deferred_projection_obligations = @($DeferredProjectionObligations)
        observation_deferred_projection_state = if (@($DeferredProjectionObligations).Count) {
            "PENDING"
        } else { "NOT_REQUIRED" }
        observation_projection_boundary_at = $ProjectionBoundary.ToString("o")
        user_visible_failure = $false
        failure_message = $null
    }
    Write-WatchdogEvent -Event "RUNTIME_OBSERVATION_STARTED" `
        -Service "all" -State "$Revision cycles=$runtimeObservationCycles"
}

function Invoke-RuntimeRollback {
    param([string]$FailedRevision, [string]$PreviousRevision, [string]$Reason)
    $releaseLockAcquiredHere = $false
    $releaseBeforeRollback = Get-ReleaseControlState
    if ($releaseBeforeRollback -and $releaseBeforeRollback.transaction -and
        -not $script:releaseTransactionLockHeld) {
        if (-not (Enter-ReleaseTransactionLock)) { return $false }
        $releaseLockAcquiredHere = $true
    }
    try {
        if (-not $PreviousRevision -or $PreviousRevision -notmatch '^[0-9a-f]{40}$') {
            throw "previous revision is unavailable"
        }
        & git -C $moduleRoot checkout --detach --force --quiet $PreviousRevision 2>$null
        if ($LASTEXITCODE -ne 0) { throw "cannot restore previous revision" }
        Restart-CodeReloadableServices -Revision $PreviousRevision
        Write-RuntimeCodeState -Revision $PreviousRevision
        Write-RuntimeUpdateFailure -Revision $FailedRevision -Status "ROLLED_BACK" `
            -Message "Candidate observation failed and the previous version was restored: $Reason"
        Write-WatchdogEvent -Event "RUNTIME_ROLLBACK_APPLIED" `
            -Service "all" -State $PreviousRevision
        $releaseState = Get-ReleaseControlState
        if ($releaseState -and $releaseState.transaction -and
            [string]$releaseState.transaction.type -eq "PROMOTE") {
            $priorValidation = $releaseState.candidate.validation
            $observationEvidence = Get-RuntimeUpdateState
            $prior = $releaseState.transaction.previous
            Invoke-CloudflareDeployment `
                -StableVersionId ([string]$prior.worker_version_id) `
                -Message "automatic release reverse $([string]$releaseState.transaction.id)"
            $releaseState.candidate.validation_state = "FAILED"
            $releaseState.candidate.validation = [pscustomobject]@{
                key = [string]$releaseState.candidate.validation_key
                error = "OBSERVATION_FAILED"
                reason = $Reason
                prior_validation = $priorValidation
                deferred_projection_evidence = if ($observationEvidence) {
                    $observationEvidence.observation_deferred_projection_evidence
                } else { $null }
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            $releaseState.transaction = $null
            $releaseState.deployment_status = "READY"
            $releaseState.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $releaseState
            Write-ReleaseHistory -Event "PROMOTION_REVERSED" -Release $prior `
                -Detail @{ reason = $Reason }
        } elseif ($releaseState -and $releaseState.transaction -and
            [string]$releaseState.transaction.type -eq "REVERSE") {
            $prior = $releaseState.transaction.previous
            Invoke-CloudflareDeployment `
                -StableVersionId ([string]$prior.worker_version_id) `
                -Message "failed reverse recovery $([string]$releaseState.transaction.id)"
            $releaseState.transaction = $null
            $releaseState.deployment_status = "RECOVERY_REQUIRED"
            $releaseState.drift = [pscustomobject]@{
                code = "REVERSE_OBSERVATION_FAILED"
                reason = $Reason
                observed_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            $releaseState.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $releaseState
            Write-ReleaseHistory -Event "REVERSE_OBSERVATION_FAILED" -Release $prior `
                -Detail @{ reason = $Reason }
        }
        return $true
    } catch {
        Write-RuntimeUpdateFailure -Revision $FailedRevision -Status "ROLLBACK_FAILED" `
            -Message "Candidate observation and automatic rollback both failed; inspect local services: $Reason; $($_.Exception.Message)"
        return $false
    } finally {
        if ($releaseLockAcquiredHere) { Exit-ReleaseTransactionLock }
    }
}

function Test-CloudflareReleasePlacement {
    param([object]$Stable, [object]$Candidate = $null)
    $deployment = Get-CloudflareDeployment
    $stablePlacement = @($deployment.versions | Where-Object {
        [string]$_.version_id -eq [string]$Stable.worker_version_id -and
        [double]$_.percentage -eq 100
    }).Count -eq 1
    if (-not $stablePlacement) { return $false }
    if ($Candidate) {
        return @($deployment.versions | Where-Object {
            [string]$_.version_id -eq [string]$Candidate.worker_version_id -and
            [double]$_.percentage -eq 0
        }).Count -eq 1
    }
    return $true
}

function Test-CloudflareRollbackTarget {
    param([Parameter(Mandatory = $true)][object]$Target)
    if (-not $Target.worker_version_id) { return $false }
    $known = @(Get-CloudflareVersions | Where-Object {
        [string]$_.id -eq [string]$Target.worker_version_id
    })
    if ($known.Count -ne 1) { return $false }
    $deployment = Get-CloudflareDeployment
    return @($deployment.versions | Where-Object {
        [string]$_.version_id -eq [string]$Target.worker_version_id -and
        [double]$_.percentage -in @(0, 100)
    }).Count -eq 1
}

function Start-ReleasePromotion {
    if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or $state.transaction) { throw "Release state is not ready." }
        $candidate = $state.candidate
        if (-not $candidate -or [string]$candidate.validation_state -ne "PASSED") {
            throw "Candidate has not passed validation."
        }
        if ([string]$candidate.artifact_kind -ne $productionCandidateArtifactKind) {
            throw "Preview and unknown artifacts cannot be promoted."
        }
        Assert-ActiveControlBundle
        if (-not (Test-ProductionCandidateProvenance -Candidate $candidate)) {
            throw "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED"
        }
        if ([string]$candidate.validation.key -ne [string]$candidate.validation_key -or
            [string]$candidate.validation_key -ne
                "$([string]$candidate.worker_version_id):$([string]$candidate.git_sha)") {
            throw "Candidate validation does not belong to this release."
        }
        if ([string]$candidate.git_sha -ne [string]$candidate.windows_revision -or
            [string]$candidate.compatibility_state -ne "PASSED") {
            throw "Worker and Windows compatibility is not authorized."
        }
        if ([string]$state.deployment_status -ne "READY") {
            throw "Release control is not deployment-ready."
        }
        if (-not (Test-CloudflareReleasePlacement -Stable $state.stable -Candidate $candidate)) {
            throw "Cloudflare Stable/Candidate placement drifted."
        }
        if (-not (Test-CloudflareRollbackTarget -Target $state.stable)) {
            throw "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        }
        $runtime = Get-RuntimeCodeState
        if (-not $runtime -or [string]$runtime.applied_revision -ne [string]$state.stable.windows_revision) {
            throw "Windows Stable revision drifted."
        }
        if (-not (Test-SingleProductionOwner)) { throw "Exactly one Windows production owner is required." }
        $deferredObligations = @(
            $candidate.validation.data_parity.deferred_obligations |
                Where-Object { $null -ne $_ }
        )
        foreach ($obligation in $deferredObligations) {
            if ([string]$obligation.state -ne
                    "DEFERRED_TO_POST_CUTOVER_OBSERVATION" -or
                [string]$obligation.validation_key -ne [string]$candidate.validation_key -or
                [string]$obligation.required_producer_revision -ne
                    [string]$candidate.windows_revision -or
                [string]$obligation.route -notin $candidateOnlyProjectionRoutes) {
                throw "DEFERRED_PROJECTION_OBLIGATION_INVALID"
            }
        }
        $transaction = [pscustomobject]@{
            id = [guid]::NewGuid().ToString()
            type = "PROMOTE"
            phase = "PRECHECK"
            target = $candidate
            previous = $state.stable
            deferred_projection_obligations = $deferredObligations
            started_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $state.transaction = $transaction
        $state.deployment_status = "PROMOTING"
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "PROMOTION_STARTED" -Release $candidate

        if (-not (Update-RuntimeCheckout -Revision ([string]$candidate.windows_revision))) {
            throw "Candidate Windows checkout failed."
        }
        $state = Get-ReleaseControlState
        $state.transaction.phase = "CUTOVER"
        Write-ReleaseControlState -State $state
        $reloadStarted = Restart-CodeReloadableServices `
            -Revision ([string]$candidate.windows_revision) `
            -DeferredServiceKeys @("sync")
        Invoke-CloudflareDeployment `
            -StableVersionId ([string]$candidate.worker_version_id) `
            -CandidateVersionId ([string]$state.stable.worker_version_id) `
            -Message "promote release $([string]$state.transaction.id)"
        $projectionBoundary = [DateTimeOffset]::UtcNow
        Complete-DeferredServiceReload -ReloadStarted $reloadStarted `
            -DeferredServiceKeys @("sync")
        if ($deferredObligations.Count -gt 0) {
            Start-RuntimeObservation -Revision ([string]$candidate.windows_revision) `
                -PreviousRevision ([string]$state.stable.windows_revision) `
                -HealthBoundary $reloadStarted `
                -DeferredProjectionObligations $deferredObligations `
                -ValidationKey ([string]$candidate.validation_key) `
                -ProjectionBoundary $projectionBoundary
        } else {
            Start-RuntimeObservation -Revision ([string]$candidate.windows_revision) `
                -PreviousRevision ([string]$state.stable.windows_revision) `
                -HealthBoundary $reloadStarted
        }
        Write-RuntimeCodeState -Revision ([string]$candidate.windows_revision)
        Write-WatchdogEvent -Event "CODE_REVISION_RELOAD_APPLIED" `
            -Service "collector,annotator,api,sync" `
            -State ([string]$candidate.windows_revision)
        $state = Get-ReleaseControlState
        $state.transaction.phase = "OBSERVING"
        $state.deployment_status = "OBSERVING"
        Write-ReleaseControlState -State $state
        return $true
    } catch {
        $failure = $_.Exception.Message
        $state = Get-ReleaseControlState
        if ($state -and $state.transaction -and [string]$state.transaction.type -eq "PROMOTE") {
            Invoke-RuntimeRollback `
                -FailedRevision ([string]$state.transaction.target.windows_revision) `
                -PreviousRevision ([string]$state.transaction.previous.windows_revision) `
                -Reason $failure | Out-Null
        }
        throw
    } finally { Exit-ReleaseTransactionLock }
}

function Complete-ReleasePromotion {
    $releaseLockAcquiredHere = $false
    if (-not $script:releaseTransactionLockHeld) {
        if (-not (Enter-ReleaseTransactionLock)) { return }
        $releaseLockAcquiredHere = $true
    }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.transaction -or
            [string]$state.transaction.type -ne "PROMOTE" -or
            [string]$state.transaction.phase -ne "OBSERVING") { return }
        $target = $state.transaction.target
        $previous = $state.transaction.previous
        $deferred = @($state.transaction.deferred_projection_obligations |
            Where-Object { $null -ne $_ })
        if ($deferred.Count -gt 0) {
            $runtimeObservation = Get-RuntimeUpdateState
            if (-not $runtimeObservation -or
                [string]$runtimeObservation.observation_validation_key -ne
                    [string]$target.validation_key -or
                [string]$runtimeObservation.observation_deferred_projection_state -ne
                    "PASSED") {
                return
            }
        }
        $state.stable = $target
        $state.previous_stable = $previous
        $state.previous_stable_rollback_eligible = Test-CloudflareRollbackTarget -Target $previous
        $state.previous_stable_rollback_reason = if ($state.previous_stable_rollback_eligible) {
            $null
        } else { "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE" }
        $state.candidate = $state.queued_candidate
        $state.queued_candidate = $null
        $state.transaction = $null
        $state.deployment_status = "READY"
        $state.drift = $null
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "STABLE_COMMITTED" -Release $target
    } finally {
        if ($releaseLockAcquiredHere) { Exit-ReleaseTransactionLock }
    }
}

function Invoke-ReleaseWindowsRestore {
    param([Parameter(Mandatory = $true)][string]$Revision)
    & git -C $moduleRoot checkout --detach --force --quiet $Revision 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot restore Windows revision." }
    Restart-CodeReloadableServices -Revision $Revision | Out-Null
    Write-RuntimeCodeState -Revision $Revision
}

function Invoke-ReverseStable {
    if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or $state.transaction -or -not $state.previous_stable) {
            throw "Previous Stable is unavailable."
        }
        Assert-ActiveControlBundle
        if (-not (Test-CloudflareRollbackTarget -Target $state.previous_stable)) {
            throw "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        }
        if (-not (Test-SingleProductionOwner)) { throw "Exactly one Windows production owner is required." }
        $current = $state.stable
        $target = $state.previous_stable
        $state.transaction = [pscustomobject]@{
            id = [guid]::NewGuid().ToString()
            type = "REVERSE"
            phase = "REVERSING"
            target = $target
            previous = $current
            started_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $state.deployment_status = "REVERSING"
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "REVERSE_STARTED" -Release $target
        $syncService = $services | Where-Object Key -eq "sync" | Select-Object -First 1
        if ($syncService) { Stop-ForecasterService $syncService }
        Invoke-CloudflareDeployment `
            -StableVersionId ([string]$target.worker_version_id) `
            -Message "reverse stable $([string]$state.transaction.id)"
        Invoke-ReleaseWindowsRestore -Revision ([string]$target.windows_revision)
        if (-not (Test-SingleProductionOwner)) { throw "Production owner uniqueness failed after Reverse." }
        Start-RuntimeObservation -Revision ([string]$target.windows_revision) `
            -PreviousRevision ([string]$current.windows_revision) -Mode "REVERSE"
        $state = Get-ReleaseControlState
        $state.transaction.phase = "REVERSE_OBSERVING"
        $state.deployment_status = "REVERSE_OBSERVING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "REVERSE_OBSERVATION_STARTED" -Release $target
        return $true
    } catch {
        $state = Get-ReleaseControlState
        if ($state) {
            $state.deployment_status = "RECOVERY_REQUIRED"
            $state.drift = [pscustomobject]@{
                code = "REVERSE_INCOMPLETE"
                observed_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
        }
        throw
    } finally { Exit-ReleaseTransactionLock }
}

function Complete-ReleaseReverse {
    $releaseLockAcquiredHere = $false
    if (-not $script:releaseTransactionLockHeld) {
        if (-not (Enter-ReleaseTransactionLock)) { return }
        $releaseLockAcquiredHere = $true
    }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.transaction -or
            [string]$state.transaction.type -ne "REVERSE" -or
            [string]$state.transaction.phase -ne "REVERSE_OBSERVING") { return }
        $target = $state.transaction.target
        $current = $state.transaction.previous
        $state.stable = $target
        $state.previous_stable = $current
        $state.previous_stable_rollback_eligible = Test-CloudflareRollbackTarget -Target $current
        $state.previous_stable_rollback_reason = if ($state.previous_stable_rollback_eligible) {
            $null
        } else { "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE" }
        $state.transaction = $null
        $state.deployment_status = "READY"
        $state.drift = $null
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "STABLE_REVERSED" -Release $target
    } finally {
        if ($releaseLockAcquiredHere) { Exit-ReleaseTransactionLock }
    }
}

function Reconcile-ReleaseControlState {
    $state = Get-ReleaseControlState
    if (-not $state) { return $null }
    $deployment = Get-CloudflareDeployment
    $runtime = Get-RuntimeCodeState
    $observedWorker = [string](Get-DeploymentVersion -Deployment $deployment -Percentage 100).version_id
    $observedWindows = if ($runtime) { [string]$runtime.applied_revision } else { "" }
    if ($state.transaction) {
        $target = $state.transaction.target
        $targetObserved = (
            $observedWorker -eq [string]$target.worker_version_id -and
            $observedWindows -eq [string]$target.windows_revision
        )
        if ([string]$state.transaction.type -eq "REVERSE" -and $targetObserved) {
            if ([string]$state.transaction.phase -eq "REVERSING") {
                Start-RuntimeObservation -Revision ([string]$target.windows_revision) `
                    -PreviousRevision ([string]$state.transaction.previous.windows_revision) `
                    -Mode "REVERSE"
                $state.transaction.phase = "REVERSE_OBSERVING"
                $state.deployment_status = "REVERSE_OBSERVING"
                Write-ReleaseControlState -State $state
                return $state
            }
            if ([string]$state.transaction.phase -eq "REVERSE_OBSERVING") {
                $observation = Get-RuntimeUpdateState
                if ($observation -and [string]$observation.update_status -eq "ACTIVE" -and
                    [string]$observation.activated_revision -eq [string]$target.windows_revision) {
                    Complete-ReleaseReverse
                    return Get-ReleaseControlState
                }
                if ($observation -and [string]$observation.update_status -eq "OBSERVING" -and
                    [string]$observation.observing_revision -eq [string]$target.windows_revision) {
                    Test-RuntimeObservation | Out-Null
                    return Get-ReleaseControlState
                }
            }
        }
        if ([string]$state.transaction.phase -eq "OBSERVING" -and $targetObserved) {
            $observation = Get-RuntimeUpdateState
            if ($observation -and [string]$observation.update_status -eq "ACTIVE" -and
                [string]$observation.activated_revision -eq [string]$target.windows_revision) {
                Complete-ReleasePromotion
                return Get-ReleaseControlState
            }
            if ($observation -and [string]$observation.update_status -eq "OBSERVING" -and
                [string]$observation.observing_revision -eq [string]$target.windows_revision) {
                Test-RuntimeObservation | Out-Null
                return Get-ReleaseControlState
            }
            $state.deployment_status = "RECOVERY_REQUIRED"
            $state.drift = [pscustomobject]@{
                code = "OBSERVATION_STATE_MISSING_OR_MISMATCHED"
                worker_version_id = $observedWorker
                windows_revision = $observedWindows
                observed_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $state
        }
        $state.deployment_status = "RECOVERY_REQUIRED"
        $state.drift = [pscustomobject]@{
            code = "INCOMPLETE_RELEASE_TRANSACTION"
            phase = [string]$state.transaction.phase
            worker_version_id = $observedWorker
            windows_revision = $observedWindows
            observed_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        Write-ReleaseControlState -State $state
        return $state
    }
    if ($observedWorker -ne [string]$state.stable.worker_version_id -or
        $observedWindows -ne [string]$state.stable.windows_revision) {
        $state.deployment_status = "DEPLOYMENT_DRIFT"
        $state.drift = [pscustomobject]@{
            code = "STABLE_IDENTITY_MISMATCH"
            worker_version_id = $observedWorker
            windows_revision = $observedWindows
            observed_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
    } else {
        $state.deployment_status = "READY"
        $state.drift = $null
    }
    if ($state.previous_stable) {
        $state.previous_stable_rollback_eligible =
            Test-CloudflareRollbackTarget -Target $state.previous_stable
        $state.previous_stable_rollback_reason = if ($state.previous_stable_rollback_eligible) {
            $null
        } else { "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE" }
    } else {
        $state.previous_stable_rollback_eligible = $false
        $state.previous_stable_rollback_reason = "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
    }
    Write-ReleaseControlState -State $state
    return $state
}

function Test-DeferredProjectionObligations {
    param(
        [Parameter(Mandatory = $true)][array]$Obligations,
        [Parameter(Mandatory = $true)][object]$Target,
        [Parameter(Mandatory = $true)][DateTimeOffset]$RequiredAfter,
        [Parameter(Mandatory = $true)][string]$ValidationKey
    )
    if ($ValidationKey -ne [string]$Target.validation_key) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "DEFERRED_PROJECTION_VALIDATION_KEY_MISMATCH"
        }
    }
    $routes = @($Obligations | ForEach-Object { [string]$_.route })
    if ($routes.Count -eq 0) {
        return [pscustomobject]@{ state = "PASSED"; reason = "NOT_REQUIRED"; routes = @() }
    }
    if (@($routes | Where-Object { $_ -notin $candidateOnlyProjectionRoutes }).Count -gt 0 -or
        @($routes | Select-Object -Unique).Count -ne $routes.Count) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "DEFERRED_PROJECTION_ROUTE_NOT_ALLOWED"
        }
    }
    $python = (Get-Command python.exe -ErrorAction Stop).Source
    $arguments = @(
        (Join-Path $moduleRoot "scripts\check_deferred_projection_parity.py"),
        "--version-id", ([string]$Target.worker_version_id),
        "--git-sha", ([string]$Target.git_sha),
        "--producer-revision", ([string]$Target.windows_revision),
        "--required-after", $RequiredAfter.ToString("o")
    )
    foreach ($route in $routes) { $arguments += @("--route", $route) }
    $output = @(& $python @arguments 2>&1)
    try {
        return (($output | ForEach-Object { [string]$_ }) -join "`n") |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        return [pscustomobject]@{
            state = "FAILED"; reason = "DEFERRED_PROJECTION_EVIDENCE_INVALID"
            diagnostic = Protect-PreflightDiagnosticText ($output -join "`n")
        }
    }
}

function Test-RuntimeObservation {
    $state = Get-RuntimeUpdateState
    if (-not $state -or [string]$state.update_status -ne "OBSERVING") {
        return $true
    }
    $revision = [string]$state.observing_revision
    $previousRevision = [string]$state.previous_revision
    $started = [DateTimeOffset]::MinValue
    $startedValid = [DateTimeOffset]::TryParse(
        [string]$state.observation_started_at, [ref]$started
    )
    if (-not $startedValid) {
        Invoke-RuntimeRollback -FailedRevision $revision `
            -PreviousRevision $previousRevision `
            -Reason "runtime observation state has an invalid start time" | Out-Null
        return $false
    }
    $failure = $null
    $healthBoundary = $started
    if ($state.observation_health_boundary_at) {
        $candidateBoundary = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse(
            [string]$state.observation_health_boundary_at, [ref]$candidateBoundary
        )) {
            $healthBoundary = $candidateBoundary
        }
    }
    if (-not (Test-CodeReloadHealth -ReloadStarted $healthBoundary)) {
        $failure = "reload health check failed"
    }
    $readyAt = [DateTimeOffset]::MinValue
    $readyValid = $state.observation_ready_at -and [DateTimeOffset]::TryParse(
        [string]$state.observation_ready_at, [ref]$readyAt
    )
    if (-not $failure -and -not $readyValid) {
        if (Test-CodeReloadHealth $healthBoundary @("RUNNING")) {
            $readyAt = [DateTimeOffset]::UtcNow
            $readyValid = $true
            Write-RuntimeUpdateState @{
                observation_ready_at = $readyAt.ToString("o")
                observation_consecutive_failures = 0
            }
        } elseif (([DateTimeOffset]::UtcNow - $healthBoundary) -ge $serviceStartupTimeout) {
            Invoke-RuntimeRollback -FailedRevision $revision `
                -PreviousRevision $previousRevision `
                -Reason "workers did not finish startup" | Out-Null
            return $false
        } else {
            Write-RuntimeUpdateState @{ observation_consecutive_failures = 0 }
            return $true
        }
    }
    $deferredObligations = @($state.observation_deferred_projection_obligations |
        Where-Object { $null -ne $_ })
    if (-not $failure -and $deferredObligations.Count -gt 0 -and
        [string]$state.observation_deferred_projection_state -ne "PASSED") {
        $projectionBoundary = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse(
            [string]$state.observation_projection_boundary_at,
            [ref]$projectionBoundary
        )) {
            $failure = "DEFERRED_PROJECTION_BOUNDARY_INVALID"
        } else {
            $release = Get-ReleaseControlState
            $target = if ($release -and $release.transaction) {
                $release.transaction.target
            } else { $null }
            if (-not $target) {
                $failure = "DEFERRED_PROJECTION_TARGET_UNAVAILABLE"
            } else {
                $projection = Test-DeferredProjectionObligations `
                    -Obligations $deferredObligations -Target $target `
                    -RequiredAfter $projectionBoundary `
                    -ValidationKey ([string]$state.observation_validation_key)
                if ([string]$projection.state -eq "PASSED") {
                    Write-RuntimeUpdateState @{
                        observation_deferred_projection_state = "PASSED"
                        observation_deferred_projection_evidence = $projection
                        observation_deferred_projection_passed_at = [DateTimeOffset]::UtcNow.ToString("o")
                    }
                } elseif ([string]$projection.state -eq "FAILED") {
                    $failure = [string]$projection.reason
                } elseif (([DateTimeOffset]::UtcNow - $projectionBoundary) -ge
                        $runtimeObservationTimeout) {
                    $failure = "DEFERRED_PROJECTION_OBSERVATION_TIMEOUT"
                } else {
                    Write-RuntimeUpdateState @{
                        observation_deferred_projection_state = "PENDING"
                        observation_deferred_projection_evidence = $projection
                        observation_consecutive_failures = 0
                    }
                    return $true
                }
            }
        }
    }
    if (-not $failure) {
        if (Test-BroadcastPublisherEnabled) {
            $broadcastLive = Test-BroadcastLiveDeliveryReadiness `
                -ExpectedRevision $revision
            if (-not $broadcastLive.passed) {
                $failure = [string]$broadcastLive.reason
            }
        }
    }
    if (-not $failure) {
        $shapeResult = Test-CurrentProductionShape
        if ($shapeResult -and [string]$shapeResult -like "DEFERRED:*") {
            $deferredCode = ([string]$shapeResult).Substring("DEFERRED:".Length)
            if ([string]$state.observation_deferred_code -ne $deferredCode) {
                Write-WatchdogEvent -Event "RUNTIME_OBSERVATION_DEFERRED" `
                    -Service "api" -State $deferredCode
            }
            Write-RuntimeUpdateState @{
                observation_deferred_code = $deferredCode
                observation_deferred_at = [DateTimeOffset]::UtcNow.ToString("o")
                observation_consecutive_failures = 0
            }
            return $true
        }
        $failure = $shapeResult
    }
    if ($failure) {
        $failures = 1 + [int]$state.observation_consecutive_failures
        Write-RuntimeUpdateState @{ observation_consecutive_failures = $failures }
        if ($failures -ge 3) {
            Invoke-RuntimeRollback -FailedRevision $revision `
                -PreviousRevision $previousRevision -Reason $failure | Out-Null
            return $false
        }
        return $true
    }
    if ($state.observation_deferred_code) {
        Write-WatchdogEvent -Event "RUNTIME_OBSERVATION_RESUMED" `
            -Service "api" -State ([string]$state.observation_deferred_code)
        Write-RuntimeUpdateState @{
            observation_deferred_code = $null
            observation_deferred_at = $null
        }
    }
    $decisionTimes = @(Get-RuntimeDecisionTimes)
    $lastDecision = [string]$state.observation_last_decision_time
    $cycles = [int]$state.observation_success_cycles
    $lastInstant = [DateTimeOffset]::MinValue
    $lastValid = [DateTimeOffset]::TryParse($lastDecision, [ref]$lastInstant)
    $referenceInstant = if ($lastValid) { $lastInstant } else { $started }
    $referenceCycle = [Math]::Floor($referenceInstant.ToUnixTimeSeconds() / 300)
    $newDecisions = @()
    foreach ($decisionTime in $decisionTimes) {
        $decisionInstant = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse(
            [string]$decisionTime, [ref]$decisionInstant
        )) { continue }
        $decisionCycle = [Math]::Floor($decisionInstant.ToUnixTimeSeconds() / 300)
        if ($decisionInstant -gt $referenceInstant -and
            $decisionCycle -gt $referenceCycle) {
            $newDecisions += [pscustomobject]@{
                Cycle = $decisionCycle
                Instant = $decisionInstant
                Value = [string]$decisionTime
            }
        }
    }
    $newCycles = @($newDecisions | Sort-Object Cycle -Unique)
    if ($newCycles.Count -gt 0) {
        $latestDecision = ($newDecisions | Sort-Object Instant -Descending |
            Select-Object -First 1).Value
        $cycles += $newCycles.Count
        Write-RuntimeUpdateState @{
            observation_last_decision_time = $latestDecision
            observation_success_cycles = $cycles
            observation_consecutive_failures = 0
        }
    } else {
        Write-RuntimeUpdateState @{ observation_consecutive_failures = 0 }
    }
    if ($cycles -ge $runtimeObservationCycles) {
        Write-RuntimeUpdateState @{
            update_status = "ACTIVE"
            activated_revision = $revision
            activated_at = [DateTimeOffset]::UtcNow.ToString("o")
            user_visible_failure = $false
            failure_message = $null
        }
        Write-WatchdogEvent -Event "RUNTIME_OBSERVATION_PASSED" `
            -Service "all" -State "$revision cycles=$cycles"
        if ([string]$state.observation_mode -eq "REVERSE") {
            Complete-ReleaseReverse
        } else {
            Complete-ReleasePromotion
        }
        return $true
    }
    if (([DateTimeOffset]::UtcNow - $readyAt) -ge $runtimeObservationTimeout) {
        if (Test-RuntimeObservationMarketPause) {
            # Time without an eligible 30-minute decision does not consume the
            # two-cycle observation window, including the pre-close boundary.
            Write-RuntimeUpdateState @{
                observation_ready_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
        } else {
            Invoke-RuntimeRollback -FailedRevision $revision `
                -PreviousRevision $previousRevision `
                -Reason "two complete five-minute decision cycles were not observed" | Out-Null
            return $false
        }
    }
    return $true
}

function Test-ExpectedWeeklyMarketClosure {
    $eastern = [System.TimeZoneInfo]::FindSystemTimeZoneById("Eastern Standard Time")
    $newYork = [System.TimeZoneInfo]::ConvertTime([DateTimeOffset]::UtcNow, $eastern)
    if ($newYork.DayOfWeek -eq [DayOfWeek]::Saturday) { return $true }
    if ($newYork.DayOfWeek -eq [DayOfWeek]::Friday -and
        $newYork.TimeOfDay -ge [TimeSpan]::FromHours(17)) { return $true }
    if ($newYork.DayOfWeek -eq [DayOfWeek]::Sunday -and
        $newYork.TimeOfDay -lt [TimeSpan]::FromHours(18)) { return $true }
    return $false
}

function Get-BrokerMarketSession {
    $path = Join-Path $moduleRoot ".local\forward\quotes\market-session.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $session = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $observedAt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse(
            [string]$session.observed_at, [ref]$observedAt
        )) { return $null }
        $now = [DateTimeOffset]::UtcNow
        if ($observedAt -gt $now.AddSeconds(5) -or
            ($now - $observedAt).TotalSeconds -gt 20) { return $null }
        $closesAt = [DateTimeOffset]::MinValue
        $closesAtValid = [DateTimeOffset]::TryParse(
            [string]$session.next_close_time, [ref]$closesAt
        )
        return [pscustomobject]@{
            ObservedAt = $observedAt
            IsOpen = $session.is_open -eq $true
            ClosesAt = if ($closesAtValid) { $closesAt } else { $null }
        }
    } catch { return $null }
}

function Test-RuntimeObservationMarketPause {
    $session = Get-BrokerMarketSession
    if ($session) {
        if (-not $session.IsOpen) { return $true }
        if ($session.ClosesAt -and
            $session.ClosesAt -gt [DateTimeOffset]::UtcNow -and
            ($session.ClosesAt - [DateTimeOffset]::UtcNow) -le $runtimeDecisionHorizon) {
            return $true
        }
        return $false
    }
    try {
        $status = Invoke-RestMethod -Method Get `
            -Uri "http://127.0.0.1:8765/api/status" -TimeoutSec 5
        return [string]$status.system.market_session -in @(
            "CLOSED", "WEEKLY_CLOSED"
        )
    } catch { return $false }
}

function Get-ServiceState {
    param(
        [pscustomobject]$Service,
        [array]$Processes
    )
    if ($Service.Key -eq "broadcast") {
        if (-not (Test-BroadcastPublisherEnabled)) { return "DISABLED" }
        if ([string]::IsNullOrWhiteSpace((Get-BroadcastPublisherToken))) {
            return "NOT_CONFIGURED"
        }
        if ($Processes.Count -eq 0) { return "DEGRADED" }
        $statusPath = Join-Path $moduleRoot ".local\forward\live-broadcast-publisher-status.json"
        if (-not (Test-Path -LiteralPath $statusPath)) { return "DEGRADED" }
        try {
            $publisher = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
            $lastSuccess = [DateTimeOffset]::MinValue
            $fresh = [DateTimeOffset]::TryParse(
                [string]$publisher.last_success, [ref]$lastSuccess
            ) -and ([DateTimeOffset]::UtcNow - $lastSuccess) -le $broadcastFreshnessThreshold
            if ([string]$publisher.state -eq "RUNNING" -and $fresh) { return "RUNNING" }
        } catch {}
        return "DEGRADED"
    }

    if ($Processes.Count -eq 0) { return "STOPPED" }

    if ($Service.Key -in @("collector", "annotator")) {
        $statusName = if ($Service.Key -eq "collector") {
            "collector-status.json"
        } else { "news-annotator-status.json" }
        $heartbeat = Get-RuntimeHeartbeat `
            -Path (Join-Path $moduleRoot ".local\forward\$statusName") `
            -ServiceName $Service.Key `
            -AllowedStates @("RUNNING", "STARTING")
        $startedAt = Get-ServiceProcessStartedAt -Processes $Processes
        if ($heartbeat -and
            $heartbeat.State -eq "RUNNING" -and
            $heartbeat.LastSuccess -ge $startedAt -and
            ([DateTimeOffset]::UtcNow - $heartbeat.LastSuccess).TotalSeconds -le 300) {
            return "RUNNING"
        }
        if ($heartbeat -and
            $heartbeat.State -eq "STARTING" -and
            $heartbeat.LastSuccess -ge $startedAt -and
            $startedAt -ne [DateTimeOffset]::MinValue -and
            ([DateTimeOffset]::UtcNow - $startedAt) -le $serviceStartupTimeout) {
            return "STARTING"
        }
        if ($startedAt -ne [DateTimeOffset]::MinValue -and
            ([DateTimeOffset]::UtcNow - $startedAt) -le $codeReloadTimeout) {
            return "STARTING"
        }
        return "$($Service.Key.ToUpper()) STALE"
    }

    if ($Service.Key -eq "quote") {
        $brokerSession = Get-BrokerMarketSession
        if ($brokerSession -and -not $brokerSession.IsOpen) { return "MARKET CLOSED" }
        $quoteRoot = Join-Path $moduleRoot ".local\forward\quotes"
        $latestQuote = Get-ChildItem -LiteralPath $quoteRoot -Filter "*.jsonl" `
            -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -eq $latestQuote -or ((Get-Date) - $latestQuote.LastWriteTime).TotalSeconds -gt 60) {
            if (Test-ExpectedWeeklyMarketClosure) { return "MARKET CLOSED" }
            return "DATA STALE"
        }
        if (-not $brokerSession) { return "SESSION STALE" }
        return "LIVE"
    }

    if ($Service.Key -eq "api") {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2
            if ($response.StatusCode -eq 200) { return "API OK" }
            return "API ERROR"
        } catch {
            return "API ERROR"
        }
    }

    if ($Service.Key -eq "sync") {
        $statusFile = Join-Path $moduleRoot ".local\forward\dashboard-sync-status.json"
        if (-not (Test-Path -LiteralPath $statusFile)) { return "STARTING" }
        try {
            $syncStatus = Get-Content -LiteralPath $statusFile -Raw | ConvertFrom-Json
            $lastSuccess = if ($syncStatus.last_success) {
                [DateTimeOffset]::Parse($syncStatus.last_success)
            } else { $null }
            $lastAttempt = if ($syncStatus.last_attempt) {
                [DateTimeOffset]::Parse($syncStatus.last_attempt)
            } else { $null }
            if ($syncStatus.last_error -and $lastAttempt -and (
                -not $lastSuccess -or $lastAttempt -gt $lastSuccess
            )) { return "SYNC ERROR" }
            if ($lastSuccess -and $syncStatus.status -eq "DEGRADED" -and (
                [DateTimeOffset]::UtcNow - $lastSuccess
            ).TotalSeconds -le 120) { return "SYNC DEGRADED" }
            if ($lastSuccess -and (
                [DateTimeOffset]::UtcNow - $lastSuccess
            ).TotalSeconds -le 120) { return "SYNC OK" }
            return "SYNC STALE"
        } catch {
            return "SYNC ERROR"
        }
    }

    return "RUNNING"
}

function Get-ForecasterStatus {
    $snapshot = @(Get-ForecasterProcessSnapshot)
    foreach ($service in $services) {
        $processes = @($snapshot | Where-Object {
            Test-ForecasterServiceProcess -Process $_ -Service $service
        })
        [pscustomobject]@{
            Key = $service.Key
            Component = $service.Label
            State = Get-ServiceState -Service $service -Processes $processes
            Pids = ($processes.ProcessId -join ",")
        }
    }
}

function Start-ForecasterService {
    param(
        [pscustomobject]$Service,
        [switch]$SkipExistingCheck
    )
    if (-not $SkipExistingCheck -and @(Get-ForecasterProcesses $Service).Count -gt 0) {
        return
    }
    if ($Service.Key -eq "broadcast") {
        if (-not (Test-BroadcastPublisherEnabled)) {
            throw "Live Broadcast Publisher is DISABLED."
        }
        $publisherToken = Get-BroadcastPublisherToken
        if ([string]::IsNullOrWhiteSpace($publisherToken)) {
            throw "Live Broadcast Publisher is NOT_CONFIGURED."
        }
        $env:AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED = "1"
        $env:LIVE_BROADCAST_PUBLISH_TOKEN = $publisherToken
    }
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    if ($Service.Key -in @("annotator", "api")) {
        $env:GEMINI_API_KEY = [Environment]::GetEnvironmentVariable("GEMINI_API_KEY", "User")
        $env:GEMINI_API_KEYS = [Environment]::GetEnvironmentVariable("GEMINI_API_KEYS", "User")
    }
    if ($Service.Key -in @("api", "sync")) {
        $env:DASHBOARD_OPERATOR_BRIDGE_TOKEN = Get-UserEnvironmentValue `
            -Name "DASHBOARD_OPERATOR_BRIDGE_TOKEN"
    }
    if ($Service.Key -eq "collector") {
        $env:BLS_API_KEY = Get-CollectorSecret -Name "BLS_API_KEY"
        $env:BEA_API_KEY = Get-CollectorSecret -Name "BEA_API_KEY"
        $env:FRED_API_KEY = Get-CollectorSecret -Name "FRED_API_KEY"
        $env:EIA_API_KEY = Get-CollectorSecret -Name "EIA_API_KEY"
    }
    if ($Service.Key -eq "sync") {
        $env:SITES_BYPASS_TOKEN = [Environment]::GetEnvironmentVariable(
            "SITES_BYPASS_TOKEN", "User"
        )
        $env:CLOUDFLARE_INGEST_URL = [Environment]::GetEnvironmentVariable(
            "CLOUDFLARE_INGEST_URL", "User"
        )
        $env:CLOUDFLARE_INGEST_TOKEN = [Environment]::GetEnvironmentVariable(
            "CLOUDFLARE_INGEST_TOKEN", "User"
        )
    }
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $stdout = Join-Path $logRoot ("control-{0}-{1}.stdout.log" -f $Service.Key, $stamp)
    $stderr = Join-Path $logRoot ("control-{0}-{1}.stderr.log" -f $Service.Key, $stamp)
    if ($Service.Kind -eq "PowerShell") {
        $scriptPath = Join-Path $moduleRoot $Service.Script
        $arguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $scriptPath)
        ) + @($Service.Arguments)
        Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
            -WorkingDirectory $moduleRoot -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    } else {
        $arguments = @($Service.Script) + @($Service.Arguments)
        Start-Process -FilePath "python" -ArgumentList $arguments `
            -WorkingDirectory $moduleRoot -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    }
}

function Stop-ForecasterProcessTree {
    param([int]$ProcessId)
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    function Stop-Children {
        param([int]$ParentId)
        foreach ($child in @($snapshot | Where-Object ParentProcessId -eq $ParentId)) {
            Stop-Children -ParentId $child.ProcessId
            Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        }
    }
    Stop-Children -ParentId $ProcessId
    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
}

function Stop-ForecasterService {
    param([pscustomobject]$Service)
    foreach ($process in (Get-ForecasterProcesses $Service)) {
        Stop-ForecasterProcessTree -ProcessId $process.ProcessId
    }
}

function Start-All {
    $status = @(Get-ForecasterStatus)
    foreach ($service in $services) {
        if ($service.Key -eq "broadcast" -and -not (Test-BroadcastPublisherEnabled)) {
            continue
        }
        $row = $status | Where-Object Key -eq $service.Key
        if ($row.State -eq "STOPPED" -or
            ($service.Key -eq "broadcast" -and $row.State -eq "DEGRADED")) {
            Start-ForecasterService $service -SkipExistingCheck
        }
    }
}

function Stop-All {
    $snapshot = @(Get-ForecasterProcessSnapshot)
    foreach ($process in $snapshot) {
        $owned = $false
        foreach ($service in $services) {
            if (Test-ForecasterServiceProcess -Process $process -Service $service) {
                $owned = $true
                break
            }
        }
        if ($owned) { Stop-ForecasterProcessTree -ProcessId $process.ProcessId }
    }
}

function Restart-All {
    Stop-All
    Start-Sleep -Milliseconds 800
    Start-All
}

function Write-WatchdogEvent {
    param([string]$Event, [string]$Service, [string]$State)
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    [pscustomobject]@{
        time = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        service = $Service
        state = $State
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $watchdogLog -Encoding UTF8
}

function Get-RuntimeControlBundleIdentity {
    Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $PSScriptRoot
}

function Assert-ActiveControlBundle {
    $identity = Get-RuntimeControlBundleIdentity
    if (-not $identity) { throw "CONTROL_BUNDLE_HASH_VERIFICATION_FAILED" }
    if (-not [bool]$identity.exact_revision -or
        [string]$identity.source_revision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
    return $identity
}

function Test-ControlCenterChildIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$CurrentScriptPath,
        [Parameter(Mandatory = $true)][string]$InstalledScriptPath,
        [Parameter(Mandatory = $true)][string]$CurrentRevision,
        [string]$ExpectedScriptPath = "",
        [string]$ExpectedRevision = ""
    )
    if ([bool]$ExpectedScriptPath -ne [bool]$ExpectedRevision) { return $false }
    if (-not [string]::Equals(
        [System.IO.Path]::GetFullPath($CurrentScriptPath),
        [System.IO.Path]::GetFullPath($InstalledScriptPath),
        [StringComparison]::OrdinalIgnoreCase
    )) { return $false }
    if ($ExpectedScriptPath -and (-not [string]::Equals(
        [System.IO.Path]::GetFullPath($CurrentScriptPath),
        [System.IO.Path]::GetFullPath($ExpectedScriptPath),
        [StringComparison]::OrdinalIgnoreCase
    ))) { return $false }
    return (-not $ExpectedRevision -or $CurrentRevision -eq $ExpectedRevision)
}

function Assert-ControlCenterProcessIdentity {
    param(
        [string]$ExpectedScriptPath = "",
        [string]$ExpectedRevision = ""
    )
    $identity = Assert-ActiveControlBundle
    $currentScript = [System.IO.Path]::GetFullPath($PSCommandPath)
    $installedScript = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_control_center.ps1"))
    if (-not (Test-ControlCenterChildIdentity `
        -CurrentScriptPath $currentScript -InstalledScriptPath $installedScript `
        -CurrentRevision ([string]$identity.source_revision) `
        -ExpectedScriptPath $ExpectedScriptPath `
        -ExpectedRevision $ExpectedRevision)) {
        throw "CONTROL_CENTER_SCRIPT_OR_REVISION_MISMATCH"
    }
    return $identity
}

function Write-WatchdogHeartbeat {
    $directory = Split-Path -Parent $watchdogHeartbeatPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$watchdogHeartbeatPath.tmp"
    $controlBundle = Get-RuntimeControlBundleIdentity
    $processIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
    [pscustomobject]@{
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
        process_id = $PID
        process_start_token = if ($processIdentity) {
            [string]$processIdentity.process_start_token
        } else { $null }
        revision = Get-CodeRevision
        control_bundle_revision = if ($controlBundle) {
            [string]$controlBundle.source_revision
        } else { $null }
        control_bundle_exact_revision = [bool]($controlBundle -and $controlBundle.exact_revision)
        control_bundle_hash_verified = [bool]$controlBundle
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $watchdogHeartbeatPath -Force
}

function Get-ControlPlaneProcessIdentity {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" `
        -ErrorAction SilentlyContinue
    if (-not $process) { return $null }
    $created = [DateTimeOffset]$process.CreationDate
    [pscustomobject]@{
        process_id = [int]$process.ProcessId
        parent_process_id = [int]$process.ParentProcessId
        process_start_token = $created.ToUniversalTime().ToString("o")
        name = [string]$process.Name
        command_line = [string]$process.CommandLine
    }
}

function Get-VerifiedWatchdogOwners {
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $controlScript = Join-Path $controlRoot "xauusd_control_center.ps1"
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -eq "powershell.exe" -and $_.CommandLine -and
                $_.CommandLine.Contains($controlScript) -and
                $_.CommandLine -match '(?i)-Action\s+Watchdog' -and
                $_.CommandLine.Contains($moduleRoot) -and
                $_.CommandLine.Contains($repositoryRoot)
            } | ForEach-Object {
                $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId)
                $launcher = Get-ControlPlaneProcessIdentity `
                    -ProcessId ([int]$identity.parent_process_id)
                $expectedLauncher = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
                if (-not $launcher -or $launcher.name -ne "wscript.exe" -or
                    -not $launcher.command_line.Contains($expectedLauncher) -or
                    -not $launcher.command_line.Contains($moduleRoot) -or
                    -not $launcher.command_line.Contains($repositoryRoot)) {
                    return
                }
                $identity | Add-Member -NotePropertyName launcher_identity `
                    -NotePropertyValue $launcher
                $identity
            }
    )
}

function Get-VerifiedControlCenterGuiOwners {
    $controlScript = Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_control_center.ps1"
    $guiLauncher = Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_control_center_launcher.vbs"
    @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.CommandLine -and (
                    ($_.Name -eq "powershell.exe" -and
                     $_.CommandLine.Contains($controlScript) -and
                     $_.CommandLine -match '(?i)-Action\s+Gui') -or
                    ($_.Name -eq "wscript.exe" -and
                     $_.CommandLine.Contains($guiLauncher) -and
                     $_.CommandLine.Contains($moduleRoot) -and
                     $_.CommandLine.Contains($repositoryRoot))
                )
            } | ForEach-Object {
                Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId)
            }
    )
}

function Assert-CurrentWatchdogHeartbeat {
    param(
        [Parameter(Mandatory = $true)][object]$Owner,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision
    )
    if (-not (Test-Path -LiteralPath $watchdogHeartbeatPath)) {
        throw "CONTROL_PLANE_CURRENT_WATCHDOG_HEARTBEAT_MISSING"
    }
    try {
        $heartbeat = Get-Content -LiteralPath $watchdogHeartbeatPath -Raw |
            ConvertFrom-Json
        $observedAt = [DateTimeOffset]::Parse([string]$heartbeat.observed_at)
    } catch {
        throw "CONTROL_PLANE_CURRENT_WATCHDOG_HEARTBEAT_INVALID"
    }
    if (($observedAt -gt [DateTimeOffset]::UtcNow.AddSeconds(30)) -or
        ([DateTimeOffset]::UtcNow - $observedAt).TotalSeconds -gt 120 -or
        [int]$heartbeat.process_id -ne [int]$Owner.process_id -or
        ([string]$heartbeat.process_start_token -and
         [string]$heartbeat.process_start_token -ne [string]$Owner.process_start_token) -or
        [string]$heartbeat.control_bundle_revision -ne $ExpectedRevision -or
        -not [bool]$heartbeat.control_bundle_exact_revision -or
        -not [bool]$heartbeat.control_bundle_hash_verified) {
        throw "CONTROL_PLANE_CURRENT_WATCHDOG_HEARTBEAT_MISMATCH"
    }
    return $heartbeat
}

function Get-ControlPlaneIsolationSnapshot {
    $serviceIdentities = [ordered]@{}
    foreach ($service in $services) {
        $serviceIdentities[$service.Key] = @(
            Get-ForecasterProcesses -Service $service | ForEach-Object {
                Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId)
            }
        )
    }
    [pscustomobject]@{
        business_runtime_revision = Get-CodeRevision
        services = [pscustomobject]$serviceIdentities
        release_state_hash = if (Test-Path -LiteralPath $releaseControlStatePath) {
            Get-Sha256Hex -LiteralPath $releaseControlStatePath
        } else { $null }
        release_history_hash = if (Test-Path -LiteralPath $releaseHistoryPath) {
            Get-Sha256Hex -LiteralPath $releaseHistoryPath
        } else { $null }
    }
}

function Assert-ControlPlaneIsolationSnapshot {
    param(
        [Parameter(Mandatory = $true)][object]$Before,
        [Parameter(Mandatory = $true)][object]$After
    )
    if ([string]$Before.business_runtime_revision -ne
        [string]$After.business_runtime_revision) {
        throw "CONTROL_PLANE_INSTALL_CHANGED_BUSINESS_RUNTIME"
    }
    if ([string]$Before.release_state_hash -ne [string]$After.release_state_hash -or
        [string]$Before.release_history_hash -ne [string]$After.release_history_hash) {
        throw "CONTROL_PLANE_INSTALL_CHANGED_RELEASE_STATE"
    }
    foreach ($service in $services) {
        $beforeProcesses = @($Before.services.($service.Key))
        $afterProcesses = @($After.services.($service.Key))
        $required = Test-ControlPlaneServiceOwnerRequired -Service $service
        if ((-not $required) -and
            ($beforeProcesses.Count -ne 0 -or $afterProcesses.Count -ne 0)) {
            throw "CONTROL_PLANE_UNEXPECTED_SERVICE_OWNER_$($service.Key.ToUpperInvariant())"
        }
        if ($required -and ($beforeProcesses.Count -ne 1 -or
            $afterProcesses.Count -ne 1 -or
            [int]$beforeProcesses[0].process_id -ne [int]$afterProcesses[0].process_id -or
            [string]$beforeProcesses[0].process_start_token -ne
                [string]$afterProcesses[0].process_start_token)) {
            throw "CONTROL_PLANE_INSTALL_CHANGED_SERVICE_$($service.Key.ToUpperInvariant())"
        }
    }
}

function Write-ControlPlaneInstallState {
    param([Parameter(Mandatory = $true)][hashtable]$Values)
    $current = @{}
    if (Test-Path -LiteralPath $controlPlaneInstallStatePath) {
        try {
            $prior = Get-Content -LiteralPath $controlPlaneInstallStatePath -Raw |
                ConvertFrom-Json
            foreach ($property in $prior.PSObject.Properties) {
                $current[$property.Name] = $property.Value
            }
        } catch {}
    }
    foreach ($key in $Values.Keys) { $current[$key] = $Values[$key] }
    $directory = Split-Path -Parent $controlPlaneInstallStatePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$controlPlaneInstallStatePath.tmp"
    $current | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $controlPlaneInstallStatePath -Force
}

function Suspend-ControlPlaneSupervision {
    $state = @{}
    foreach ($name in @($guardTaskName, $taskName)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) { throw "CONTROL_PLANE_SCHEDULED_TASK_MISSING:$name" }
        $state[$name] = [bool]$task.Settings.Enabled
    }
    try {
        foreach ($name in @($guardTaskName, $taskName)) {
            Disable-ScheduledTask -TaskName $name | Out-Null
            if ($name -eq $guardTaskName) {
                Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Restore-ControlPlaneSupervision -State $state
        throw
    }
    return $state
}

function Restore-ControlPlaneSupervision {
    param([hashtable]$State)
    if (-not $State) { return }
    foreach ($name in @($taskName, $guardTaskName)) {
        if ([bool]$State[$name]) { Enable-ScheduledTask -TaskName $name | Out-Null }
    }
}

function Wait-ControlPlaneGuardQuiesced {
    param([TimeSpan]$Timeout = ([TimeSpan]::FromSeconds(15)))
    $guardScript = Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_watchdog_guard.ps1"
    $guardLauncher = Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_watchdog_guard_launcher.vbs"
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    do {
        $owners = @(
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.CommandLine -and
                    ($_.CommandLine.Contains($guardScript) -or
                     $_.CommandLine.Contains($guardLauncher))
                }
        )
        if ($owners.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "CONTROL_PLANE_GUARD_DID_NOT_QUIESCE"
}

function Stop-VerifiedWatchdogOwner {
    param([Parameter(Mandatory = $true)][object]$Identity)
    $current = Get-ControlPlaneProcessIdentity -ProcessId ([int]$Identity.process_id)
    if (-not $current -or
        [string]$current.process_start_token -ne [string]$Identity.process_start_token -or
        $current.name -ne "powershell.exe" -or
        $current.command_line -notmatch '(?i)-Action\s+Watchdog') {
        throw "CONTROL_PLANE_WATCHDOG_IDENTITY_CHANGED"
    }
    Stop-Process -Id ([int]$current.process_id) -Force
    Wait-Process -Id ([int]$current.process_id) -Timeout 15 -ErrorAction SilentlyContinue
    if (Get-Process -Id ([int]$current.process_id) -ErrorAction SilentlyContinue) {
        throw "CONTROL_PLANE_WATCHDOG_STOP_FAILED"
    }
    $expectedLauncherIdentity = $Identity.launcher_identity
    $launcher = Get-ControlPlaneProcessIdentity `
        -ProcessId ([int]$expectedLauncherIdentity.process_id)
    if ($launcher -and $launcher.name -eq "wscript.exe") {
        $expectedLauncher = Join-Path $repositoryRoot `
            ".local\runtime-control\xauusd_watchdog_launcher.vbs"
        if ([string]$launcher.process_start_token -ne
                [string]$expectedLauncherIdentity.process_start_token -or
            -not $launcher.command_line.Contains($expectedLauncher) -or
            -not $launcher.command_line.Contains($moduleRoot) -or
            -not $launcher.command_line.Contains($repositoryRoot)) {
            throw "CONTROL_PLANE_LAUNCHER_IDENTITY_MISMATCH"
        }
        Stop-Process -Id ([int]$launcher.process_id) -Force -ErrorAction SilentlyContinue
    }
}

function Start-WatchdogReplacement {
    param([switch]$PassThru)
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $controlScript = Join-Path $controlRoot "xauusd_control_center.ps1"
    $launcher = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
    if (-not (Test-Path -LiteralPath $controlScript) -or
        -not (Test-Path -LiteralPath $launcher)) {
        throw "Updated watchdog control files are unavailable."
    }
    $wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
    $arguments = '"{0}" "{1}" "{2}" "{3}"' -f `
        $launcher, $controlScript, $moduleRoot, $repositoryRoot
    $process = Start-Process -FilePath $wscript -ArgumentList $arguments `
        -WindowStyle Hidden -PassThru
    if ($PassThru) { return $process }
}

function Wait-VerifiedWatchdogHandoff {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][object]$PreviousIdentity,
        [TimeSpan]$Timeout = ([TimeSpan]::FromSeconds(90))
    )
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    do {
        Start-Sleep -Milliseconds 250
        $heartbeat = $null
        try {
            if (Test-Path -LiteralPath $watchdogHeartbeatPath) {
                $heartbeat = Get-Content -LiteralPath $watchdogHeartbeatPath -Raw |
                    ConvertFrom-Json
            }
        } catch {}
        if (-not $heartbeat -or
            [string]$heartbeat.control_bundle_revision -ne $ExpectedRevision -or
            -not [bool]$heartbeat.control_bundle_exact_revision -or
            -not [bool]$heartbeat.control_bundle_hash_verified -or
            [string]$heartbeat.process_start_token -eq "") { continue }
        $owners = @(Get-VerifiedWatchdogOwners)
        if ($owners.Count -ne 1) { continue }
        $owner = $owners[0]
        if ([int]$owner.process_id -ne [int]$heartbeat.process_id -or
            [string]$owner.process_start_token -ne
                [string]$heartbeat.process_start_token -or
            ([int]$owner.process_id -eq [int]$PreviousIdentity.process_id -and
             [string]$owner.process_start_token -eq
                [string]$PreviousIdentity.process_start_token)) { continue }
        return $owner
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"
}

function Invoke-ControlPlaneInstall {
    param(
        [Parameter(Mandatory = $true)][string]$VerifiedSourceRoot,
        [Parameter(Mandatory = $true)][string]$TargetRevision
    )
    if ($TargetRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $currentBundle = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $controlRoot
    if (-not $currentBundle) { throw "CONTROL_BUNDLE_CURRENT_VERIFICATION_FAILED" }
    if (@(Get-VerifiedControlCenterGuiOwners).Count -ne 0) {
        throw "CONTROL_CENTER_GUI_MUST_BE_CLOSED"
    }
    $release = Get-ReleaseControlState
    if (($release -and $release.transaction) -or
        (Test-Path -LiteralPath $releaseLockPath)) {
        throw "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"
    }
    $oldOwners = @(Get-VerifiedWatchdogOwners)
    if ($oldOwners.Count -ne 1) {
        throw "CONTROL_PLANE_EXACTLY_ONE_WATCHDOG_REQUIRED"
    }
    $oldOwner = $oldOwners[0]
    $oldHeartbeat = Assert-CurrentWatchdogHeartbeat -Owner $oldOwner `
        -ExpectedRevision ([string]$currentBundle.source_revision)
    $isolationBefore = Get-ControlPlaneIsolationSnapshot
    foreach ($service in $services) {
        $owners = @($isolationBefore.services.($service.Key))
        $required = Test-ControlPlaneServiceOwnerRequired -Service $service
        if ($required -and $owners.Count -ne 1) {
            throw "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:$($service.Key)"
        }
        if ((-not $required) -and $owners.Count -ne 0) {
            throw "CONTROL_PLANE_UNEXPECTED_SERVICE_OWNER:$($service.Key)"
        }
    }

    $controlParent = Split-Path -Parent $controlRoot
    $transactionId = [guid]::NewGuid().ToString("N")
    $stageRoot = Join-Path $controlParent (".cps-{0}" -f $transactionId)
    $backupRoot = Join-Path $controlParent (".cpb-{0}" -f $transactionId)
    $supervisionState = $null
    $releaseLockHeld = $false
    $oldStopped = $false
    $bundleInstalled = $false
    $newOwner = $null
    $startedAt = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ControlPlaneInstallState @{
        transaction_id = $transactionId
        target_revision = $TargetRevision
        previous_revision = [string]$currentBundle.source_revision
        started_at = $startedAt
        completed_at = $null
        phase = "PRECHECK"
        old_watchdog_identity = $oldOwner
        old_watchdog_heartbeat = $oldHeartbeat
        new_watchdog_identity = $null
        bundle_hash_verified = $false
        rollback_result = $null
        isolation_before = $isolationBefore
        isolation_after = $null
    }
    try {
        if (-not (Enter-ReleaseTransactionLock)) {
            throw "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"
        }
        $releaseLockHeld = $true
        $staged = New-VerifiedRuntimeControlBundleStage `
            -SourceRoot $VerifiedSourceRoot -SourceRevision $TargetRevision `
            -StageRoot $stageRoot -RequireImmutableSource
        if (-not $staged) { throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED" }
        Write-ControlPlaneInstallState @{
            phase = "QUIESCE_CONTROL_SUPERVISION"
            bundle_hash_verified = $true
        }
        $supervisionState = Suspend-ControlPlaneSupervision
        Wait-ControlPlaneGuardQuiesced
        # Revalidate the complete stage before the first destructive process action.
        if (-not (Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $stageRoot)) {
            throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED"
        }
        Write-ControlPlaneInstallState @{ phase = "STOP_OLD_WATCHDOG" }
        Stop-VerifiedWatchdogOwner -Identity $oldOwner
        $oldStopped = $true
        if (@(Get-VerifiedWatchdogOwners).Count -ne 0) {
            throw "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"
        }
        Write-ControlPlaneInstallState @{ phase = "INSTALL_BUNDLE" }
        $installed = Install-VerifiedRuntimeControlBundleStage `
            -StageRoot $stageRoot -ControlRoot $controlRoot -BackupRoot $backupRoot
        $bundleInstalled = $true
        if ([string]$installed.source_revision -ne $TargetRevision) {
            throw "CONTROL_BUNDLE_INSTALLED_REVISION_MISMATCH"
        }
        Write-ControlPlaneInstallState @{ phase = "START_NEW_WATCHDOG" }
        $null = Start-WatchdogReplacement -PassThru
        Write-ControlPlaneInstallState @{ phase = "VERIFY_NEW_HEARTBEAT" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner
        $isolationAfter = Get-ControlPlaneIsolationSnapshot
        Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore -After $isolationAfter
        Restore-ControlPlaneSupervision -State $supervisionState
        $supervisionState = $null
        Write-ControlPlaneInstallState @{
            phase = "COMMITTED"
            completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            new_watchdog_identity = $newOwner
            rollback_result = "NOT_REQUIRED"
            isolation_after = $isolationAfter
        }
        return [pscustomobject]@{
            status = "COMMITTED"
            previous_revision = [string]$currentBundle.source_revision
            target_revision = $TargetRevision
            old_watchdog_identity = $oldOwner
            new_watchdog_identity = $newOwner
            business_runtime_revision = [string]$isolationBefore.business_runtime_revision
            bundle_hash_verified = $true
        }
    } catch {
        $failure = $_.Exception.Message
        $rollbackResult = "NOT_REQUIRED"
        try {
            if ($oldStopped) {
                foreach ($owner in @(Get-VerifiedWatchdogOwners)) {
                    Stop-VerifiedWatchdogOwner -Identity $owner
                }
                if ($bundleInstalled) {
                    $null = Restore-RuntimeControlBundleBackup `
                        -BackupRoot $backupRoot -ControlRoot $controlRoot
                }
                $null = Start-WatchdogReplacement -PassThru
                $restoredOwner = Wait-VerifiedWatchdogHandoff `
                    -ExpectedRevision ([string]$currentBundle.source_revision) `
                    -PreviousIdentity $oldOwner
                $isolationAfter = Get-ControlPlaneIsolationSnapshot
                Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
                    -After $isolationAfter
                $rollbackResult = "ROLLED_BACK"
                $newOwner = $restoredOwner
            }
        } catch {
            $rollbackResult = "ROLLBACK_FAILED: $($_.Exception.Message)"
        }
        try {
            Restore-ControlPlaneSupervision -State $supervisionState
        } catch {
            $rollbackResult = "ROLLBACK_FAILED: supervision restore: $($_.Exception.Message)"
        }
        Write-ControlPlaneInstallState @{
            phase = if ($rollbackResult -eq "ROLLED_BACK") { "ROLLED_BACK" } else { "FAILED" }
            completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            new_watchdog_identity = $newOwner
            rollback_result = $rollbackResult
            failure = $failure
            isolation_after = if ($oldStopped) { $isolationAfter } else { $null }
        }
        throw "CONTROL_PLANE_INSTALL_FAILED: $failure; $rollbackResult"
    } finally {
        if ($releaseLockHeld) { Exit-ReleaseTransactionLock }
        foreach ($path in @($stageRoot, $backupRoot)) {
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Recurse -Force
            }
        }
    }
}

function Test-WatchdogRecoverySuppressed {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceKey,
        [Parameter(Mandatory = $true)][string]$ServiceState,
        [object]$ReleaseState
    )
    return [bool]($ServiceKey -eq "sync" -and $ServiceState -eq "STOPPED" -and
        (Test-CoordinatedMigrationSyncHold -ReleaseState $ReleaseState))
}

function Invoke-ForecasterWatchdog {
    $failureCounts = @{}
    $lastRestart = @{}
    foreach ($service in $services) {
        $failureCounts[$service.Key] = 0
        $lastRestart[$service.Key] = [DateTimeOffset]::MinValue
    }
    $watchdogRevisionAtStart = Get-CodeRevision
    Ensure-WatchdogGuardTask
    Write-WatchdogEvent -Event "WATCHDOG_STARTED" -Service "all" -State "MONITORING"
    while ($true) {
        Write-WatchdogHeartbeat
        try {
            $currentRevision = Get-CodeRevision
            # Git/main and Worker version movement only discover Candidate work.
            # Production checkout and traffic remain Stable until local Promote.
            Start-CandidateDiscovery
            $observationHealthy = Test-RuntimeObservation
            $currentRevision = Get-CodeRevision
            if ($currentRevision -ne $watchdogRevisionAtStart -and
                (-not $observationHealthy -or -not (Get-ReleaseControlState).transaction)) {
                # Only an explicit Promote/Reverse may change the checkout. Once
                # that durable transaction finishes, hand supervision to its
                # matching control bundle.
                Start-WatchdogReplacement
                return 0
            }
            $status = @(Get-ForecasterStatus)
            $releaseState = Get-ReleaseControlState
            foreach ($service in $services) {
                $row = $status | Where-Object Key -eq $service.Key
                if (Test-WatchdogRecoverySuppressed -ServiceKey $service.Key `
                    -ServiceState $row.State -ReleaseState $releaseState) {
                    $failureCounts[$service.Key] = 0
                    continue
                }
                $unhealthy = if ($service.Key -eq "broadcast") {
                    (Test-BroadcastPublisherEnabled) -and $row.State -eq "DEGRADED"
                } else {
                    $row.State -in @(
                        "STOPPED", "DATA STALE", "API ERROR", "SYNC ERROR", "SYNC STALE",
                        "COLLECTOR STALE", "ANNOTATOR STALE", "SESSION STALE"
                    )
                }
                if (-not $unhealthy) {
                    $failureCounts[$service.Key] = 0
                    continue
                }
                $failureCounts[$service.Key] += 1
                $requiredFailures = if ($row.State -eq "STOPPED") { 1 } else { 3 }
                $cooldownSeconds = if ($service.Key -eq "quote") { 900 } else { 120 }
                $sinceRestart = ([DateTimeOffset]::UtcNow - $lastRestart[$service.Key]).TotalSeconds
                if ($failureCounts[$service.Key] -lt $requiredFailures -or
                    $sinceRestart -lt $cooldownSeconds) {
                    continue
                }
                Write-WatchdogEvent -Event "AUTO_RECOVERY_STARTED" `
                    -Service $service.Key -State $row.State
                Stop-ForecasterService $service
                Start-Sleep -Milliseconds 600
                Start-ForecasterService $service -SkipExistingCheck
                $lastRestart[$service.Key] = [DateTimeOffset]::UtcNow
                $failureCounts[$service.Key] = 0
                Write-WatchdogEvent -Event "AUTO_RECOVERY_LAUNCHED" `
                    -Service $service.Key -State $row.State
            }
        } catch {
            Write-WatchdogEvent -Event "WATCHDOG_CHECK_ERROR" `
                -Service "all" -State $_.Exception.Message
        }
        Write-WatchdogHeartbeat
        Start-Sleep -Seconds 30
    }
}

function Test-AutoStart {
    $null -ne (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) -and
        $null -ne (Get-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue)
}

function Register-AutoStartTask {
    param(
        [string]$ControlScript,
        [string]$RuntimePath,
        [string]$SourceRepository
    )
    $controlRoot = Split-Path -Parent $ControlScript
    $launcherPath = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
    if (-not (Test-Path -LiteralPath $launcherPath)) {
        throw "Missing windowless watchdog launcher: $launcherPath"
    }
    $wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
    $taskArguments = '"{0}" "{1}" "{2}" "{3}"' -f `
        $launcherPath, $ControlScript, $RuntimePath, $SourceRepository
    $taskAction = New-ScheduledTaskAction -Execute $wscript -Argument $taskArguments
    $taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal `
        -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
        -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName $taskName -Action $taskAction `
        -Trigger $taskTrigger -Principal $principal -Settings $settings -Force | Out-Null

    Register-WatchdogGuardTask -ControlScript $ControlScript -Principal $principal
    Start-ScheduledTask -TaskName $taskName
}

function Register-WatchdogGuardTask {
    param(
        [string]$ControlScript,
        [object]$Principal
    )
    $controlRoot = Split-Path -Parent $ControlScript
    $guardPath = Join-Path $controlRoot "xauusd_watchdog_guard.ps1"
    $launcherPath = Join-Path $controlRoot "xauusd_watchdog_guard_launcher.vbs"
    if (-not (Test-Path -LiteralPath $guardPath)) {
        throw "Missing watchdog guard: $guardPath"
    }
    if (-not (Test-Path -LiteralPath $launcherPath)) {
        throw "Missing windowless watchdog guard launcher: $launcherPath"
    }
    $wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
    $guardArguments = '"{0}" "{1}" "{2}" "{3}"' -f `
        $launcherPath, $guardPath, $taskName, $watchdogHeartbeatPath
    $guardAction = New-ScheduledTaskAction -Execute $wscript -Argument $guardArguments
    $guardTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
        -RepetitionInterval (New-TimeSpan -Minutes 2)
    $guardSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $guardTaskName -Action $guardAction `
        -Trigger $guardTrigger -Principal $principal -Settings $guardSettings -Force | Out-Null
}

function Ensure-WatchdogGuardTask {
    if ($null -ne (Get-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue)) {
        return
    }
    try {
        $principal = New-ScheduledTaskPrincipal `
            -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
            -LogonType Interactive -RunLevel Limited
        Register-WatchdogGuardTask -ControlScript $PSCommandPath -Principal $principal
        Write-WatchdogEvent -Event "WATCHDOG_GUARD_REGISTERED" `
            -Service "watchdog" -State "MONITORING"
    } catch {
        Write-WatchdogEvent -Event "WATCHDOG_GUARD_REGISTRATION_ERROR" `
            -Service "watchdog" -State $_.Exception.Message
    }
}

function Enable-AutoStart {
    Register-AutoStartTask -ControlScript $PSCommandPath `
        -RuntimePath $moduleRoot -SourceRepository $repositoryRoot
}

function Install-ProductionRuntime {
    $source = [System.IO.Path]::GetFullPath($repositoryRoot)
    $runtime = if ($RuntimeRoot) {
        [System.IO.Path]::GetFullPath($RuntimeRoot)
    } else {
        Join-Path (Split-Path -Parent $source) "XAUUSD-Forecaster-runtime"
    }
    $sameCheckout = $runtime.Equals($source, [System.StringComparison]::OrdinalIgnoreCase)
    $insideCheckout = $runtime.StartsWith(
        $source + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
    if ($sameCheckout -or $insideCheckout) {
        throw "RuntimeRoot must be separate from the development checkout."
    }
    $revision = (& git -C $source rev-parse HEAD 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') {
        throw "Cannot resolve the verified development revision."
    }
    if (Test-Path -LiteralPath $runtime) {
        $inside = (& git -C $runtime rev-parse --is-inside-work-tree 2>$null).Trim()
        if ($LASTEXITCODE -ne 0 -or $inside -ne "true") {
            throw "Existing RuntimeRoot is not a Git worktree: $runtime"
        }
        & git -C $runtime checkout --detach --force --quiet $revision 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Cannot update runtime worktree." }
    } else {
        & git -C $source worktree add --detach --quiet $runtime $revision 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Cannot create runtime worktree." }
    }
    $runtimeLocal = Join-Path $runtime ".local"
    $sourceLocal = Join-Path $source ".local"
    New-Item -ItemType Directory -Path $sourceLocal -Force | Out-Null
    if (-not (Test-Path -LiteralPath $runtimeLocal)) {
        New-Item -ItemType Junction -Path $runtimeLocal -Target $sourceLocal | Out-Null
    }
    Write-RuntimeUpdateState @{
        bootstrap_revision = $revision
        installed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $controlRoot = Join-Path $sourceLocal "runtime-control"
    Sync-StableRuntimeControlFiles -SourceRoot $runtime -ControlRoot $controlRoot
    $stableScript = Join-Path $controlRoot "xauusd_control_center.ps1"

    Stop-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Stop-All
    Register-AutoStartTask -ControlScript $stableScript `
        -RuntimePath $runtime -SourceRepository $source
    [pscustomobject]@{
        runtime_root = $runtime
        state_root = $sourceLocal
        installed_revision = $revision
        control_script = $stableScript
    }
}

function Disable-AutoStart {
    Unregister-ScheduledTask -TaskName $guardTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}

function Repair-WindowsTime {
    $command = "Set-Service W32Time -StartupType Automatic; Start-Service W32Time; w32tm /resync /force"
    try {
        $process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command
        )
        if ($process.ExitCode -ne 0) {
            throw "Administrator command exited with code $($process.ExitCode)"
        }
        [System.Windows.Forms.MessageBox]::Show(
            "Windows Time has been started and a resync was requested. Quote clock drift should decrease over the next few updates.",
            "Clock Repair Requested"
        ) | Out-Null
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            "Clock repair could not be completed: $($_.Exception.Message)",
            "Clock Repair Failed"
        ) | Out-Null
    }
}

function Install-ControlShortcut {
    param([string]$ShortcutPath = "")
    $desktop = [Environment]::GetFolderPath("Desktop")
    if (-not $ShortcutPath) {
        $ShortcutPath = Join-Path $desktop "XAUUSD Forecaster Control Center.lnk"
    }
    $launcherPath = Join-Path $PSScriptRoot "xauusd_control_center_launcher.vbs"
    if (-not (Test-Path -LiteralPath $launcherPath)) {
        throw "Verified Control Center GUI launcher is missing."
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $shortcut.Arguments = '"{0}" "{1}" "{2}"' -f `
        $launcherPath, $moduleRoot, $repositoryRoot
    $shortcut.WorkingDirectory = $moduleRoot
    $shortcut.Description = "Start, stop, inspect, and configure XAUUSD Forecaster"
    $shortcut.Save()
    return $ShortcutPath
}

function Test-BroadcastPublisherEnabled {
    [string](Get-UserEnvironmentValue -Name "AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED") -eq "1"
}

function Get-BroadcastPublisherToken {
    [string](Get-UserEnvironmentValue -Name "LIVE_BROADCAST_PUBLISH_TOKEN")
}

function Test-ControlPlaneServiceOwnerRequired {
    param([Parameter(Mandatory = $true)][object]$Service)
    if ([string]$Service.Key -eq "broadcast") {
        return [bool](Test-BroadcastPublisherEnabled)
    }
    return $true
}
