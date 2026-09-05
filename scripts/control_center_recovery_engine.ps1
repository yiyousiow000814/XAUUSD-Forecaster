# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
function Test-RecoveryShortObservationCycle {
    param(
        [Parameter(Mandatory = $true)][object]$ReleaseState,
        [Parameter(Mandatory = $true)][object]$RuntimeState
    )
    $transaction = $ReleaseState.transaction
    if (-not (Test-RecoveryShortObserveMode -Transaction $transaction)) {
        return [pscustomobject]@{ passed = $false; reason = "RECOVERY_OBSERVE_CONTRACT_INVALID" }
    }
    $target = $transaction.target
    $deployment = Get-CloudflareDeployment
    $active = Get-DeploymentVersion -Deployment $deployment -Percentage 100
    $runtime = Get-RuntimeCodeState
    if (-not $active -or -not $runtime -or
        [string]$active.version_id -cne [string]$target.worker_version_id -or
        [string]$runtime.applied_revision -cne [string]$target.windows_revision) {
        return [pscustomobject]@{ passed = $false; reason = "RECOVERY_POST_SWITCH_IDENTITY_MISMATCH" }
    }
    if (-not (Test-SingleProductionOwner)) {
        return [pscustomobject]@{ passed = $false; reason = "RECOVERY_SINGLE_OWNER_REQUIRED" }
    }
    if (-not (Test-CloudflareRollbackTarget -Target $transaction.previous)) {
        return [pscustomobject]@{ passed = $false; reason = "RECOVERY_ROLLBACK_READINESS_LOST" }
    }
    $refs = $transaction.evidence_receipt_refs
    $refNames = @($refs.PSObject.Properties | ForEach-Object { [string]$_.Name })
    $requiredNames = @($releaseEvidencePrerequisiteNodes | ForEach-Object { [string]$_ })
    if ($refNames.Count -ne $requiredNames.Count -or
        @($refNames | Where-Object { $_ -notin $requiredNames }).Count -gt 0) {
        return [pscustomobject]@{
            passed = $false; reason = "RECOVERY_EVIDENCE_REFERENCE_SET_INVALID"
        }
    }
    foreach ($node in $requiredNames) {
        $receipt = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
            -ValidationKey ([string]$target.validation_key) -Node $node
        if (-not $receipt -or
            [string]$receipt.receipt_digest -cne [string]$refs.$node -or
            [string]$receipt.source_identity.qualification_state -cnotin @("PASSED", "NOT_REQUIRED")) {
            return [pscustomobject]@{
                passed = $false; reason = "RECOVERY_EVIDENCE_INVALID:$node"
            }
        }
        if ($node -in @(
                "migration_live_lease", "candidate_placement",
                "access_provider_lease", "rollback_precheck"
            ) -and
            [string]$receipt.source_identity.qualification_state -cne "NOT_REQUIRED") {
            $expiry = [DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse(
                    [string]$receipt.source_identity.subject.expires_at,
                    [ref]$expiry) -or $expiry -le [DateTimeOffset]::UtcNow) {
                return [pscustomobject]@{
                    passed = $false; reason = "RECOVERY_EVIDENCE_LEASE_STALE:$node"
                }
            }
        }
    }
    return [pscustomobject]@{ passed = $true; reason = "RECOVERY_BOUNDED_CYCLE_PASSED" }
}

function Test-RuntimeObservation {
    $state = Get-RuntimeUpdateState
    if (-not $state -or [string]$state.update_status -ne "OBSERVING") {
        return $true
    }
    $revision = [string]$state.observing_revision
    $previousRevision = [string]$state.previous_revision
    $recordedFailure = [string]$state.observation_original_failure_reason
    if (-not [string]::IsNullOrWhiteSpace($recordedFailure)) {
        Invoke-RuntimeRollback -FailedRevision $revision `
            -PreviousRevision $previousRevision -Reason $recordedFailure | Out-Null
        return $false
    }
    $started = ConvertTo-ReleaseTimestampUtc -Value $state.observation_started_at
    $startedValid = $started -ne [DateTimeOffset]::MinValue
    if (-not $startedValid) {
        Invoke-RuntimeRollback -FailedRevision $revision `
            -PreviousRevision $previousRevision `
            -Reason "runtime observation state has an invalid start time" | Out-Null
        return $false
    }
    $failure = $null
    $healthBoundary = $started
    if ($state.observation_health_boundary_at) {
        $candidateBoundary = ConvertTo-ReleaseTimestampUtc `
            -Value $state.observation_health_boundary_at
        if ($candidateBoundary -ne [DateTimeOffset]::MinValue) {
            $healthBoundary = $candidateBoundary
        }
    }
    if (-not (Test-CodeReloadHealth -ReloadStarted $healthBoundary)) {
        $failure = "reload health check failed"
    }
    $readyAt = ConvertTo-ReleaseTimestampUtc -Value $state.observation_ready_at
    $readyValid = $readyAt -ne [DateTimeOffset]::MinValue
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
        $projectionBoundary = ConvertTo-ReleaseTimestampUtc `
            -Value $state.observation_projection_boundary_at
        if ($projectionBoundary -eq [DateTimeOffset]::MinValue) {
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
            Write-RuntimeUpdateState @{
                observation_original_failure_reason = $failure
                observation_original_failure_evidence = $state.observation_deferred_projection_evidence
                observation_original_failed_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
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
    if ([string]$state.observation_mode -in @("RECOVERY_HOTFIX", "RESTORE_LKG")) {
        $release = Get-ReleaseControlState
        $cycle = if ($release -and $release.transaction) {
            Test-RecoveryShortObservationCycle -ReleaseState $release -RuntimeState $state
        } else {
            [pscustomobject]@{ passed = $false; reason = "RECOVERY_TRANSACTION_UNAVAILABLE" }
        }
        if (-not $cycle.passed) {
            $failures = 1 + [int]$state.observation_consecutive_failures
            Write-RuntimeUpdateState @{ observation_consecutive_failures = $failures }
            if ($failures -ge 3) {
                Invoke-RuntimeRollback -FailedRevision $revision `
                    -PreviousRevision $previousRevision -Reason ([string]$cycle.reason) | Out-Null
                return $false
            }
            return $true
        }
        $cycles = 1 + [int]$state.observation_success_cycles
        Write-RuntimeUpdateState @{
            observation_success_cycles = $cycles
            observation_consecutive_failures = 0
        }
        if ($cycles -ge 2) {
            Write-RuntimeUpdateState @{
                update_status = "ACTIVE"
                activated_revision = $revision
                activated_at = [DateTimeOffset]::UtcNow.ToString("o")
                user_visible_failure = $false
                failure_message = $null
            }
            Complete-ReleaseRecovery
        }
        return $true
    }
    $decisionTimes = @(Get-RuntimeDecisionTimes)
    $lastDecision = [string]$state.observation_last_decision_time
    $cycles = [int]$state.observation_success_cycles
    $lastInstant = ConvertTo-ReleaseTimestampUtc -Value $lastDecision
    $lastValid = $lastInstant -ne [DateTimeOffset]::MinValue
    $referenceInstant = if ($lastValid) { $lastInstant } else { $started }
    $referenceCycle = [Math]::Floor($referenceInstant.ToUnixTimeSeconds() / 300)
    $newDecisions = @()
    foreach ($decisionTime in $decisionTimes) {
        $decisionInstant = ConvertTo-ReleaseTimestampUtc -Value $decisionTime
        if ($decisionInstant -eq [DateTimeOffset]::MinValue) { continue }
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
    $path = Join-Path $runtimeForwardRoot "quotes\market-session.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $session = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $observedAt = ConvertTo-ReleaseTimestampUtc -Value $session.observed_at
        if ($observedAt -eq [DateTimeOffset]::MinValue) { return $null }
        $now = [DateTimeOffset]::UtcNow
        if ($observedAt -gt $now.AddSeconds(5) -or
            ($now - $observedAt).TotalSeconds -gt 20) { return $null }
        $closesAt = ConvertTo-ReleaseTimestampUtc -Value $session.next_close_time
        $closesAtValid = $closesAt -ne [DateTimeOffset]::MinValue
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

function New-RuntimeRecoveryPlan {
    param(
        [Parameter(Mandatory = $true)][string]$StableRevision,
        [object]$ReleaseState,
        [Parameter(Mandatory = $true)][array]$ServiceContracts
    )
    if ($StableRevision -notmatch '^[0-9a-f]{40}$') {
        throw "RUNTIME_RECOVERY_STABLE_REVISION_REQUIRED"
    }
    $incident = Get-CollectorClockRecoveryContext
    if ($incident -and [string]$incident.broken_revision -cne $StableRevision) { $incident = $null }
    $incidentProcesses = if ($incident) {
        @(Get-ForecasterProcessSnapshot -RequireCompleteInventory)
    } else { @() }
    $running = @()
    $owners = [ordered]@{}
    $contracts = @()
    foreach ($service in $ServiceContracts) {
        if ([string]$service.Revision -ne $StableRevision -or
            [string]$service.CodeRoot -ne [System.IO.Path]::GetFullPath($moduleRoot)) {
            throw "RUNTIME_RECOVERY_CONTRACT_IDENTITY_MISMATCH:$($service.Key)"
        }
        $processes = @(if ($incident) {
            @($incidentProcesses | Where-Object { Test-ForecasterServiceProcess -Process $_ -Service $service })
        } else { @(Get-ForecasterProcesses -Service $service) })
        if ($incident -and $service.Key -eq 'collector' -and $processes.Count -ne 0) {
            throw 'COLLECTOR_RECOVERY_BASELINE_CHANGED'
        }
        if ($processes.Count -gt 1) {
            throw "RUNTIME_RECOVERY_MULTIPLE_SERVICE_OWNERS:$($service.Key)"
        }
        if ($processes.Count -eq 1) { $running += [string]$service.Key }
        $serviceArguments = @($service.Arguments | ForEach-Object { [string]$_ })
        if ($StableRevision -eq "783d25314b090dd7fbbf124777c3b8de517d2b85" -and
            [string]$service.Key -eq "quote" -and $processes.Count -eq 1) {
            $cliIndex = [Array]::IndexOf([object[]]$serviceArguments, "-CliPath")
            $secretIndex = [Array]::IndexOf([object[]]$serviceArguments, "-SecretRoot")
            if ($cliIndex -lt 0 -or $secretIndex -lt 0 -or
                $cliIndex + 1 -ge $serviceArguments.Count -or
                $secretIndex + 1 -ge $serviceArguments.Count) {
                throw "RUNTIME_RECOVERY_QUOTE_AUTHORITY_UNAVAILABLE"
            }
            $cliPath = [string]$serviceArguments[$cliIndex + 1]
            $secretRoot = [string]$serviceArguments[$secretIndex + 1]
            foreach ($required in @(
                $cliPath, (Join-Path $secretRoot "ctid.txt"),
                (Join-Path $secretRoot "account.txt"),
                (Join-Path $secretRoot "ctrader-cli.pwd")
            )) {
                if (-not (Test-Path -LiteralPath $required)) {
                    throw "RUNTIME_RECOVERY_QUOTE_AUTHORITY_UNAVAILABLE"
                }
            }
        }
        $owners[[string]$service.Key] = @($processes | ForEach-Object {
            $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId) `
                -RequireCompleteInventory:([bool]$incident)
            if (-not $identity -or -not [string]$identity.process_start_token) {
                throw "RUNTIME_RECOVERY_OWNER_IDENTITY_UNAVAILABLE:$($service.Key)"
            }
            [ordered]@{
                process_id = [int]$identity.process_id
                process_start_token = [string]$identity.process_start_token
            }
        })
        $contracts += [ordered]@{
            revision = [string]$service.Revision; code_root = [string]$service.CodeRoot
            key = [string]$service.Key; label = [string]$service.Label
            match = [string]$service.Match; kind = [string]$service.Kind
            script = [string]$service.Script; script_path = [string]$service.ScriptPath
            arguments = $serviceArguments
        }
    }
    foreach ($service in $ServiceContracts) {
        if ($incident -and [string]$service.Key -eq 'collector') { continue }
        if ((Test-ControlPlaneServiceOwnerRequired -Service $service -ReleaseState $ReleaseState) -and
            [string]$service.Key -notin $running) {
            throw "RUNTIME_RECOVERY_REQUIRED_OWNER_MISSING:$($service.Key)"
        }
    }
    $body = [ordered]@{
        schema = "runtime-recovery-plan-v1"; stable_revision = $StableRevision
        stable_worker_version = if ($ReleaseState -and $ReleaseState.stable) {
            [string]$ReleaseState.stable.worker_version_id
        } else { "NOT_RECORDED" }
        runtime_root = [System.IO.Path]::GetFullPath($moduleRoot)
        runtime_state_root = [System.IO.Path]::GetFullPath($runtimeForwardRoot)
        config_root = [System.IO.Path]::GetFullPath((Join-Path $repositoryLocalRoot "config"))
        running_service_keys = @($running | Sort-Object)
        process_baseline = $owners; service_contracts = $contracts
        rollback_target = $StableRevision
    }
    if ($incident) { $body['collector_clock_recovery'] = $incident }
    $json = $body | ConvertTo-Json -Depth 9 -Compress
    [pscustomobject]@{
        body = [pscustomobject]$body
        digest = Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
    }
}

function Assert-RuntimeRecoveryPlan {
    param([Parameter(Mandatory = $true)][object]$Plan)
    $json = $Plan.body | ConvertTo-Json -Depth 9 -Compress
    $digest = Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
    if ([string]$Plan.digest -ne $digest) { throw "RUNTIME_RECOVERY_PLAN_TAMPERED" }
    if ([string]$Plan.body.stable_revision -ne [string]$Plan.body.rollback_target -or
        [string]$Plan.body.runtime_root -ne [System.IO.Path]::GetFullPath($moduleRoot) -or
        [string]$Plan.body.runtime_state_root -ne [System.IO.Path]::GetFullPath($runtimeForwardRoot)) {
        throw "RUNTIME_RECOVERY_PLAN_AUTHORITY_MISMATCH"
    }
    if (@($Plan.body.service_contracts).Count -eq 0) {
        throw "RUNTIME_RECOVERY_PLAN_INCOMPLETE"
    }
    if ($Plan.body.collector_clock_recovery) {
        Assert-CollectorClockRecoveryContext -Context $Plan.body.collector_clock_recovery
        if ([string]$Plan.body.stable_revision -cne
            [string]$Plan.body.collector_clock_recovery.broken_revision -or
            'collector' -in @($Plan.body.running_service_keys)) {
            throw 'COLLECTOR_RECOVERY_PLAN_BASELINE_CONFLICT'
        }
    }
    return $true
}

function Convert-RecoveryPlanContracts {
    param([Parameter(Mandatory = $true)][object]$Plan)
    $null = Assert-RuntimeRecoveryPlan -Plan $Plan
    @($Plan.body.service_contracts | ForEach-Object {
        [pscustomobject]@{
            Revision=[string]$_.revision; CodeRoot=[string]$_.code_root
            Key=[string]$_.key; Label=[string]$_.label; Match=[string]$_.match
            Kind=[string]$_.kind; Script=[string]$_.script
            ScriptPath=[string]$_.script_path; Arguments=@($_.arguments)
        }
    })
}

function Restore-RuntimeRecoveryPlan {
    param([Parameter(Mandatory = $true)][object]$Plan)
    $contracts = @(Convert-RecoveryPlanContracts -Plan $Plan)
    $inventoryArguments = @{}
    if ($Plan.body.collector_clock_recovery) { $inventoryArguments.RequireCompleteInventory = $true }
    $revision = [string]$Plan.body.stable_revision
    Stop-All
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    do {
        $remaining = @($services | ForEach-Object { Get-ForecasterProcesses -Service $_ @inventoryArguments })
        if ($remaining.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if ($remaining.Count -ne 0) { throw "RUNTIME_RECOVERY_SERVICE_FENCE_FAILED" }
    & git -C $moduleRoot checkout --detach --force --quiet $revision 2>$null
    if ($LASTEXITCODE -ne 0 -or (Get-BusinessRuntimeRevision) -ne $revision) {
        throw "RUNTIME_RECOVERY_CHECKOUT_FAILED"
    }
    $script:services = $contracts
    foreach ($service in $contracts) {
        $expected = [string]$service.Key -in @($Plan.body.running_service_keys)
        $count = @(Get-ForecasterProcesses -Service $service @inventoryArguments).Count
        if ($expected -and $count -eq 0) {
            Start-ForecasterService -Service $service -SkipExistingCheck
        } elseif (-not $expected -and $count -ne 0) {
            throw "RUNTIME_RECOVERY_UNEXPECTED_OWNER:$($service.Key)"
        }
    }
    return $true
}

function Wait-RuntimeRecoveryPlanHealth {
    param(
        [Parameter(Mandatory = $true)][object]$Plan,
        [DateTimeOffset]$RecoveryStarted = [DateTimeOffset]::UtcNow
    )
    $contracts = @(Convert-RecoveryPlanContracts -Plan $Plan)
    $inventoryArguments = @{}
    if ($Plan.body.collector_clock_recovery) { $inventoryArguments.RequireCompleteInventory = $true }
    $runningKeys = @($Plan.body.running_service_keys | ForEach-Object { [string]$_ })
    $requiredReloadable = @($runningKeys | Where-Object { $_ -in $reloadableServiceKeys })
    $deadline = [DateTimeOffset]::UtcNow.Add($serviceStartupTimeout)
    do {
        Start-Sleep -Milliseconds 500
        $ownersHealthy = $true
        foreach ($service in $contracts) {
            $expectedCount = if ([string]$service.Key -in $runningKeys) { 1 } else { 0 }
            if (@(Get-ForecasterProcesses -Service $service @inventoryArguments).Count -ne $expectedCount) {
                $ownersHealthy = $false
                break
            }
        }
        $functionalHealthy = $ownersHealthy -and (
            $requiredReloadable.Count -eq 0 -or
            (Test-CodeReloadHealth -ReloadStarted $RecoveryStarted `
                -RequiredServiceKeys $requiredReloadable)
        )
        if ($functionalHealthy -and "quote" -in $runningKeys) {
            $quote = $contracts | Where-Object Key -eq "quote" | Select-Object -First 1
            $quoteProcesses = @(Get-ForecasterProcesses -Service $quote @inventoryArguments)
            $quoteState = Get-ServiceState -Service $quote -Processes $quoteProcesses
            $functionalHealthy = $quoteState -in @("LIVE", "MARKET CLOSED")
            if ($Plan.body.collector_clock_recovery -and -not (Get-BrokerMarketSession)) {
                $functionalHealthy = $false
            }
        }
    } while (-not $functionalHealthy -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $functionalHealthy) { throw "RUNTIME_RECOVERY_HEALTH_FAILED" }
    $baselineState = if ($Plan.body.collector_clock_recovery) { 'DEGRADED_RECOVERY_BASELINE' } else { 'HEALTHY' }
    Write-WatchdogEvent -Event $(if ($Plan.body.collector_clock_recovery) {
        'RUNTIME_RECOVERY_DEGRADED_BASELINE_RESTORED'
    } else { 'RUNTIME_RECOVERY_HEALTHY' }) -Service "all" `
        -State ([string]$Plan.body.stable_revision)
    return [pscustomobject]@{
        revision = [string]$Plan.body.stable_revision
        running_service_keys = @($runningKeys)
        recovered_at = [DateTimeOffset]::UtcNow.ToString("o")
        baseline_health = $baselineState
    }
}

function Initialize-RuntimeStateNativeProbe {
    if ("XauusdRuntimeStateNativeProbe" -as [type]) { return }
    $source = @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public sealed class XauusdRuntimeStateHandleInfo
{
    public int ProcessId;
    public long HandleValue;
    public uint GrantedAccess;
    public bool IsDirectory;
    public bool IsCurrentDirectory;
    public string Path;
    public int ProbeError;
}

public sealed class XauusdTopLevelWindowInfo
{
    public long HandleValue;
    public int ProcessId;
    public bool Visible;
    public string ClassName;
    public string Title;
}

public static class XauusdRuntimeStateNativeProbe
{
    private const int ProcessBasicInformation = 0;
    private const int ProcessHandleInformation = 51;
    private const int StatusInfoLengthMismatch = unchecked((int)0xC0000004);
    private const uint ProcessVmRead = 0x0010;
    private const uint ProcessDuplicateHandle = 0x0040;
    private const uint ProcessQueryInformation = 0x0400;
    private const uint DuplicateSameAccess = 0x00000002;
    private const uint FileTypeDisk = 0x0001;
    private const uint FileAttributeDirectory = 0x0010;
    private const uint OpenExisting = 3;
    private const uint FileFlagBackupSemantics = 0x02000000;
    private const uint WmClose = 0x0010;

    private delegate bool EnumWindowsCallback(IntPtr window, IntPtr parameter);

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessBasicInformationValue
    {
        public IntPtr Reserved1;
        public IntPtr PebBaseAddress;
        public IntPtr Reserved2_0;
        public IntPtr Reserved2_1;
        public IntPtr UniqueProcessId;
        public IntPtr InheritedFromUniqueProcessId;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct ProcessHandleEntry
    {
        public IntPtr HandleValue;
        public UIntPtr HandleCount;
        public UIntPtr PointerCount;
        public uint GrantedAccess;
        public uint ObjectTypeIndex;
        public uint HandleAttributes;
        public uint Reserved;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FileAttributeTagInfo
    {
        public uint FileAttributes;
        public uint ReparseTag;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint access, bool inherit, int processId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr handle);

    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool DuplicateHandle(
        IntPtr sourceProcess, IntPtr sourceHandle, IntPtr targetProcess,
        out IntPtr targetHandle, uint access, bool inherit, uint options);

    [DllImport("kernel32.dll")]
    private static extern uint GetFileType(IntPtr handle);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        IntPtr handle, StringBuilder path, uint length, uint flags);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool GetFileInformationByHandleEx(
        IntPtr handle, int informationClass, out FileAttributeTagInfo information,
        uint size);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadProcessMemory(
        IntPtr process, IntPtr address, byte[] buffer, int size, out IntPtr read);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsWow64Process(IntPtr process, out bool isWow64);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr CreateFile(
        string path, uint access, uint share, IntPtr security,
        uint creation, uint flags, IntPtr template);

    [DllImport("ntdll.dll")]
    private static extern int NtQueryInformationProcess(
        IntPtr process, int informationClass, IntPtr information,
        int informationLength, out int returnLength);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(
        EnumWindowsCallback callback, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(
        IntPtr window, out uint processId);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetClassName(
        IntPtr window, StringBuilder className, int capacity);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(
        IntPtr window, StringBuilder title, int capacity);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool PostMessage(
        IntPtr window, uint message, IntPtr wParam, IntPtr lParam);

    private static string NormalizePath(string path)
    {
        if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
            return @"\\" + path.Substring(8);
        if (path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase))
            return path.Substring(4);
        return path;
    }

    private static IntPtr ReadPointer(IntPtr process, IntPtr address)
    {
        byte[] bytes = new byte[IntPtr.Size];
        IntPtr read;
        if (!ReadProcessMemory(process, address, bytes, bytes.Length, out read) ||
            read.ToInt64() != bytes.Length)
            return IntPtr.Zero;
        return IntPtr.Size == 8
            ? new IntPtr(BitConverter.ToInt64(bytes, 0))
            : new IntPtr(BitConverter.ToInt32(bytes, 0));
    }

    private static IntPtr GetCurrentDirectoryHandle(IntPtr process)
    {
        bool isWow64;
        if (!IsWow64Process(process, out isWow64) || isWow64 || IntPtr.Size != 8)
            return IntPtr.Zero;
        IntPtr buffer = Marshal.AllocHGlobal(
            Marshal.SizeOf(typeof(ProcessBasicInformationValue)));
        ProcessBasicInformationValue basic;
        try
        {
            int returned;
            int status = NtQueryInformationProcess(
                process, ProcessBasicInformation, buffer,
                Marshal.SizeOf(typeof(ProcessBasicInformationValue)), out returned);
            if (status != 0) return IntPtr.Zero;
            basic = (ProcessBasicInformationValue)Marshal.PtrToStructure(
                buffer, typeof(ProcessBasicInformationValue));
        }
        finally { Marshal.FreeHGlobal(buffer); }
        if (basic.PebBaseAddress == IntPtr.Zero) return IntPtr.Zero;
        IntPtr parameters = ReadPointer(
            process, new IntPtr(basic.PebBaseAddress.ToInt64() + 0x20));
        if (parameters == IntPtr.Zero) return IntPtr.Zero;
        return ReadPointer(process, new IntPtr(parameters.ToInt64() + 0x48));
    }

    private static bool IsWithin(string path, string root)
    {
        return path.Equals(root, StringComparison.OrdinalIgnoreCase) ||
            path.StartsWith(root + System.IO.Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase);
    }

    public static XauusdRuntimeStateHandleInfo[] Query(
        int[] processIds, string stateTree, int maxHandlesPerProcess)
    {
        var results = new List<XauusdRuntimeStateHandleInfo>();
        string root = System.IO.Path.GetFullPath(stateTree)
            .TrimEnd(System.IO.Path.DirectorySeparatorChar);
        foreach (int processId in processIds)
        {
            IntPtr process = OpenProcess(
                ProcessVmRead | ProcessDuplicateHandle | ProcessQueryInformation,
                false, processId);
            if (process == IntPtr.Zero)
            {
                results.Add(new XauusdRuntimeStateHandleInfo {
                    ProcessId = processId, ProbeError = Marshal.GetLastWin32Error()
                });
                continue;
            }
            IntPtr buffer = IntPtr.Zero;
            try
            {
                IntPtr currentDirectoryHandle = GetCurrentDirectoryHandle(process);
                int size = 65536;
                int returned;
                int status;
                while (true)
                {
                    buffer = Marshal.AllocHGlobal(size);
                    status = NtQueryInformationProcess(
                        process, ProcessHandleInformation, buffer, size, out returned);
                    if (status == 0) break;
                    Marshal.FreeHGlobal(buffer);
                    buffer = IntPtr.Zero;
                    if (status != StatusInfoLengthMismatch || size >= 16777216)
                    {
                        results.Add(new XauusdRuntimeStateHandleInfo {
                            ProcessId = processId, ProbeError = status
                        });
                        break;
                    }
                    size = Math.Min(Math.Max(size * 2, returned + 4096), 16777216);
                }
                if (buffer == IntPtr.Zero) continue;
                ulong count = (ulong)Marshal.ReadInt64(buffer);
                if (count > (ulong)maxHandlesPerProcess)
                {
                    results.Add(new XauusdRuntimeStateHandleInfo {
                        ProcessId = processId, ProbeError = 234
                    });
                    continue;
                }
                int entrySize = Marshal.SizeOf(typeof(ProcessHandleEntry));
                long offset = IntPtr.Size * 2;
                for (ulong index = 0; index < count; index++)
                {
                    IntPtr entryAddress = new IntPtr(
                        buffer.ToInt64() + offset + (long)index * entrySize);
                    var entry = (ProcessHandleEntry)Marshal.PtrToStructure(
                        entryAddress, typeof(ProcessHandleEntry));
                    IntPtr duplicate;
                    if (!DuplicateHandle(
                        process, entry.HandleValue, GetCurrentProcess(), out duplicate,
                        0, false, DuplicateSameAccess))
                        continue;
                    try
                    {
                        if (GetFileType(duplicate) != FileTypeDisk) continue;
                        var path = new StringBuilder(32768);
                        uint length = GetFinalPathNameByHandle(
                            duplicate, path, (uint)path.Capacity, 0);
                        if (length == 0 || length >= path.Capacity) continue;
                        string normalized = NormalizePath(path.ToString());
                        if (!IsWithin(normalized, root)) continue;
                        FileAttributeTagInfo attributes;
                        bool isDirectory = GetFileInformationByHandleEx(
                            duplicate, 9, out attributes,
                            (uint)Marshal.SizeOf(typeof(FileAttributeTagInfo))) &&
                            (attributes.FileAttributes & FileAttributeDirectory) != 0;
                        results.Add(new XauusdRuntimeStateHandleInfo {
                            ProcessId = processId,
                            HandleValue = entry.HandleValue.ToInt64(),
                            GrantedAccess = entry.GrantedAccess,
                            IsDirectory = isDirectory,
                            IsCurrentDirectory = currentDirectoryHandle != IntPtr.Zero &&
                                entry.HandleValue == currentDirectoryHandle,
                            Path = normalized,
                            ProbeError = 0
                        });
                    }
                    finally { CloseHandle(duplicate); }
                }
            }
            finally
            {
                if (buffer != IntPtr.Zero) Marshal.FreeHGlobal(buffer);
                CloseHandle(process);
            }
        }
        return results.ToArray();
    }

    public static int ProbeDirectoryAccess(string path, uint access, uint share)
    {
        IntPtr handle = CreateFile(
            path, access, share, IntPtr.Zero, OpenExisting,
            FileFlagBackupSemantics, IntPtr.Zero);
        if (handle == new IntPtr(-1)) return Marshal.GetLastWin32Error();
        CloseHandle(handle);
        return 0;
    }

    public static XauusdTopLevelWindowInfo[] GetTopLevelWindows(int[] processIds)
    {
        var allowed = new HashSet<int>(processIds ?? new int[0]);
        var windows = new List<XauusdTopLevelWindowInfo>();
        EnumWindows(delegate(IntPtr window, IntPtr parameter) {
            uint processId;
            GetWindowThreadProcessId(window, out processId);
            if (!allowed.Contains((int)processId)) return true;
            var className = new StringBuilder(512);
            var title = new StringBuilder(2048);
            GetClassName(window, className, className.Capacity);
            GetWindowText(window, title, title.Capacity);
            windows.Add(new XauusdTopLevelWindowInfo {
                HandleValue = window.ToInt64(),
                ProcessId = (int)processId,
                Visible = IsWindowVisible(window),
                ClassName = className.ToString(),
                Title = title.ToString()
            });
            return true;
        }, IntPtr.Zero);
        return windows.ToArray();
    }

    public static int RequestGracefulClose(int[] processIds)
    {
        int requested = 0;
        foreach (var window in GetTopLevelWindows(processIds))
        {
            if (window.ClassName != "Shell_TrayWnd" &&
                window.ClassName != "CabinetWClass")
                continue;
            if (PostMessage(
                new IntPtr(window.HandleValue), WmClose,
                IntPtr.Zero, IntPtr.Zero))
                requested++;
        }
        return requested;
    }
}
'@
    Add-Type -TypeDefinition $source -ErrorAction Stop
}

function Get-RuntimeStateHolderInventory {
    param(
        [Parameter(Mandatory = $true)][string]$StateTree,
        [int[]]$ControlledProcessIds = @()
    )
    Initialize-RuntimeStateNativeProbe
    $root = [System.IO.Path]::GetFullPath($StateTree).TrimEnd(
        [System.IO.Path]::DirectorySeparatorChar
    )
    $sessionId = (Get-Process -Id $PID -ErrorAction Stop).SessionId
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object { [int]$_.SessionId -eq [int]$sessionId })
    if ($processes.Count -gt 512) { throw "RUNTIME_STATE_QUIESCENCE_TIMEOUT:PROCESS_BOUND" }
    $processById = @{}
    foreach ($process in $processes) { $processById[[int]$process.ProcessId] = $process }
    $controlled = @{}
    foreach ($processId in $ControlledProcessIds) { $controlled[[int]$processId] = $true }
    $native = @([XauusdRuntimeStateNativeProbe]::Query(
        [int[]]@($processes.ProcessId), $root, 100000
    ))
    $errors = @($native | Where-Object { [int]$_.ProbeError -ne 0 })
    $rawHolders = @($native | Where-Object { [int]$_.ProbeError -eq 0 } |
        ForEach-Object {
            $process = $processById[[int]$_.ProcessId]
            [pscustomobject]@{
                process_id = [int]$_.ProcessId
                process_name = if ($process) { [string]$process.Name } else { "exited" }
                controlled = [bool]$controlled.ContainsKey([int]$_.ProcessId)
                kind = if ([bool]$_.IsCurrentDirectory) { "PROCESS_CWD" } elseif (
                    [bool]$_.IsDirectory
                ) { "DIRECTORY" } else { "FILE" }
                path = [string]$_.Path
                handle = [long]$_.HandleValue
                granted_access = ("0x{0:x8}" -f [uint32]$_.GrantedAccess)
            }
        })
    $holders = @($rawHolders | Group-Object process_id,process_name,controlled,kind,path |
        ForEach-Object {
            $first = $_.Group[0]
            [pscustomobject]@{
                process_id = [int]$first.process_id
                process_name = [string]$first.process_name
                controlled = [bool]$first.controlled
                kind = [string]$first.kind
                path = [string]$first.path
                handle_count = $_.Count
                granted_access = @($_.Group.granted_access | Sort-Object -Unique) -join ","
            }
        } | Sort-Object process_id,path,kind)
    [pscustomobject]@{
        state_tree = $root
        inspected_process_count = $processes.Count
        holders = $holders
        probe_errors = @($errors | Select-Object -First 20 |
            ForEach-Object {
                $process = $processById[[int]$_.ProcessId]
                [pscustomobject]@{
                    process_id = [int]$_.ProcessId
                    process_name = if ($process) { [string]$process.Name } else { "exited" }
                    error = [int]$_.ProbeError
                }
            })
    }
}

function ConvertTo-RuntimeStateHolderDiagnostic {
    param([object[]]$Holders)
    $bounded = @($Holders | Select-Object -First 20 | ForEach-Object {
        [ordered]@{
            process_id = [int]$_.process_id
            process_name = [string]$_.process_name
            controlled = [bool]$_.controlled
            kind = [string]$_.kind
            path = [string]$_.path
            handle_count = [int]$_.handle_count
            granted_access = [string]$_.granted_access
        }
    })
    [ordered]@{
        schema = "runtime-state-holder-v1"
        holder_count = @($Holders).Count
        truncated = @($Holders).Count -gt $bounded.Count
        holders = $bounded
    } | ConvertTo-Json -Depth 5 -Compress
}

function Test-RuntimeStatePathContained {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$StateTree
    )
    try {
        $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar
        )
        $root = [System.IO.Path]::GetFullPath($StateTree).TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar
        )
        return $candidate.Equals(
            $root, [System.StringComparison]::OrdinalIgnoreCase
        ) -or $candidate.StartsWith(
            $root + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    } catch { return $false }
}

function ConvertFrom-ExplorerLocationUrl {
    param([string]$LocationUrl)
    if ([string]::IsNullOrWhiteSpace($LocationUrl)) { return $null }
    try {
        $uri = [Uri]$LocationUrl
        if (-not $uri.IsFile) { return $null }
        return [System.IO.Path]::GetFullPath($uri.LocalPath)
    } catch { return $null }
}

function Get-ExplorerShellWindowInventory {
    $shell = $null
    try {
        $shell = New-Object -ComObject Shell.Application
        return @($shell.Windows() | ForEach-Object {
            $applicationPath = [string]$_.FullName
            if ([System.IO.Path]::GetFileName($applicationPath) -ieq "explorer.exe") {
                [pscustomobject]@{
                    hwnd = [long]$_.HWND
                    busy = [bool]$_.Busy
                    location_name = [string]$_.LocationName
                    location_url = [string]$_.LocationURL
                    location_path = ConvertFrom-ExplorerLocationUrl `
                        -LocationUrl ([string]$_.LocationURL)
                    shell_window = $_
                }
            }
        })
    } finally {
        if ($shell -and [Runtime.InteropServices.Marshal]::IsComObject($shell)) {
            $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($shell)
        }
    }
}

function Close-ExplorerShellWindowsForStateTree {
    param([Parameter(Mandatory = $true)][string]$StateTree)
    $windows = @(Get-ExplorerShellWindowInventory)
    $closed = 0
    try {
        foreach ($window in $windows) {
            if (-not $window.location_path -or -not (
                Test-RuntimeStatePathContained -Path ([string]$window.location_path) `
                    -StateTree $StateTree
            )) { continue }
            $window.shell_window.Quit()
            $closed += 1
        }
    } finally {
        foreach ($window in $windows) {
            $owner = $window.shell_window
            if ($owner -and [Runtime.InteropServices.Marshal]::IsComObject($owner)) {
                $null = [Runtime.InteropServices.Marshal]::ReleaseComObject($owner)
            }
        }
    }
    return [pscustomobject]@{
        examined_count = $windows.Count
        closed_count = $closed
    }
}

function Get-ExplorerTopLevelWindowInventory {
    param([Parameter(Mandatory = $true)][int[]]$ProcessIds)
    Initialize-RuntimeStateNativeProbe
    return @([XauusdRuntimeStateNativeProbe]::GetTopLevelWindows($ProcessIds) |
        ForEach-Object {
            [pscustomobject]@{
                hwnd = [long]$_.HandleValue
                process_id = [int]$_.ProcessId
                visible = [bool]$_.Visible
                class_name = [string]$_.ClassName
                title = [string]$_.Title
            }
        })
}

function Test-ExplorerFileOperationActive {
    param([Parameter(Mandatory = $true)][int[]]$ProcessIds)
    $operations = @(Get-ExplorerTopLevelWindowInventory -ProcessIds $ProcessIds |
        Where-Object {
            $_.class_name -eq "OperationStatusWindow" -and (
                $_.visible -or (
                    -not [string]::IsNullOrWhiteSpace([string]$_.title) -and
                    [string]$_.title -notmatch '^\s*100%\s+complete\s*$'
                )
            )
        })
    return $operations.Count -ne 0
}

function Assert-ExplorerRuntimeStateHolderSet {
    param(
        [Parameter(Mandatory = $true)][object[]]$Holders,
        [Parameter(Mandatory = $true)][string]$StateTree
    )
    if ($Holders.Count -eq 0) { return $true }
    $invalid = @($Holders | Where-Object {
        [string]$_.process_name -ine "explorer.exe" -or
        [string]$_.kind -ne "DIRECTORY" -or
        -not (Test-RuntimeStatePathContained -Path ([string]$_.path) `
            -StateTree $StateTree)
    })
    if ($invalid.Count -ne 0) {
        throw ("RUNTIME_STATE_EXTERNAL_HOLDER_ACTIVE:" +
            (ConvertTo-RuntimeStateHolderDiagnostic -Holders $Holders))
    }
    return $true
}

function Get-ExternalRuntimeStateHolderInventory {
    param(
        [Parameter(Mandatory = $true)][string]$StateTree,
        [Parameter(Mandatory = $true)][object]$ProcessPlan
    )
    $null = Update-RuntimeProcessQuiescencePlan -Plan $ProcessPlan
    $controlledIds = @($ProcessPlan.entries | ForEach-Object { [int]$_.process_id })
    $inventory = Get-RuntimeStateHolderInventory -StateTree $StateTree `
        -ControlledProcessIds $controlledIds
    return [pscustomobject]@{
        inventory = $inventory
        external = @($inventory.holders | Where-Object { -not $_.controlled })
    }
}

function Request-ExplorerGracefulShutdown {
    param([Parameter(Mandatory = $true)][int[]]$ProcessIds)
    Initialize-RuntimeStateNativeProbe
    return [XauusdRuntimeStateNativeProbe]::RequestGracefulClose($ProcessIds)
}

function Stop-VerifiedExplorerProcesses {
    param([Parameter(Mandatory = $true)][object[]]$Identities)
    foreach ($identity in $Identities) {
        $current = Get-ControlPlaneProcessIdentity -ProcessId ([int]$identity.process_id)
        if (-not $current -or [string]$current.name -ine "explorer.exe" -or
            -not (Test-ControlPlaneStartTokenEqual `
                -Left ([string]$current.process_start_token) `
                -Right ([string]$identity.process_start_token))) {
            throw "RUNTIME_STATE_EXPLORER_IDENTITY_CHANGED"
        }
        Stop-Process -Id ([int]$identity.process_id) -Force -ErrorAction Stop
    }
}

function Start-ExplorerShell {
    $explorer = Join-Path $env:WINDIR "explorer.exe"
    Start-Process -FilePath $explorer | Out-Null
}

function Test-ExplorerShellHealthy {
    Initialize-RuntimeStateNativeProbe
    $sessionId = (Get-Process -Id $PID -ErrorAction Stop).SessionId
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='explorer.exe'" |
        Where-Object { [int]$_.SessionId -eq [int]$sessionId })
    if ($processes.Count -eq 0) { return $false }
    return @(Get-ExplorerTopLevelWindowInventory `
        -ProcessIds @($processes.ProcessId) | Where-Object {
            $_.class_name -eq "Shell_TrayWnd"
        }).Count -ne 0
}

function Wait-ExplorerShellHealthy {
    param([TimeSpan]$Timeout = [TimeSpan]::FromSeconds(15))
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    do {
        if (Test-ExplorerShellHealthy) { return $true }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "RUNTIME_STATE_EXPLORER_SHELL_RESTART_FAILED"
}

function Repair-ExplorerRuntimeStateHolders {
    param(
        [Parameter(Mandatory = $true)][string]$StateTree,
        [Parameter(Mandatory = $true)][object]$ProcessPlan,
        [Parameter(Mandatory = $true)][object[]]$InitialHolders
    )
    $null = Assert-ExplorerRuntimeStateHolderSet `
        -Holders $InitialHolders -StateTree $StateTree
    $processIds = @($InitialHolders.process_id | ForEach-Object { [int]$_ } |
        Sort-Object -Unique)

    $windowResult = Close-ExplorerShellWindowsForStateTree -StateTree $StateTree
    Start-Sleep -Milliseconds 750
    $observed = Get-ExternalRuntimeStateHolderInventory `
        -StateTree $StateTree -ProcessPlan $ProcessPlan
    if ($observed.external.Count -eq 0) {
        return [pscustomobject]@{
            repaired = $true
            method = "MATCHING_WINDOW_CLOSED"
            closed_window_count = [int]$windowResult.closed_count
            shell_restarted = $false
            inventory = $observed.inventory
        }
    }

    $null = Assert-ExplorerRuntimeStateHolderSet `
        -Holders $observed.external -StateTree $StateTree
    $processIds = @($observed.external.process_id | ForEach-Object { [int]$_ } |
        Sort-Object -Unique)
    if (Test-ExplorerFileOperationActive -ProcessIds $processIds) {
        throw "RUNTIME_STATE_EXPLORER_FILE_OPERATION_ACTIVE"
    }
    $identities = @($processIds | ForEach-Object {
        $identity = Get-ControlPlaneProcessIdentity -ProcessId $_
        if (-not $identity -or [string]$identity.name -ine "explorer.exe") {
            throw "RUNTIME_STATE_EXPLORER_IDENTITY_UNAVAILABLE"
        }
        $identity
    })

    $null = Request-ExplorerGracefulShutdown -ProcessIds $processIds
    Start-Sleep -Seconds 2
    $observed = Get-ExternalRuntimeStateHolderInventory `
        -StateTree $StateTree -ProcessPlan $ProcessPlan
    if ($observed.external.Count -eq 0) {
        if (-not (Test-ExplorerShellHealthy)) {
            Start-ExplorerShell
            $null = Wait-ExplorerShellHealthy
        }
        return [pscustomobject]@{
            repaired = $true
            method = "GRACEFUL_SHELL_CLOSE"
            closed_window_count = [int]$windowResult.closed_count
            shell_restarted = $true
            inventory = $observed.inventory
        }
    }

    $null = Assert-ExplorerRuntimeStateHolderSet `
        -Holders $observed.external -StateTree $StateTree
    $remainingIds = @($observed.external.process_id | ForEach-Object { [int]$_ } |
        Sort-Object -Unique)
    if (@(Compare-Object $processIds $remainingIds).Count -ne 0) {
        throw "RUNTIME_STATE_EXPLORER_HOLDER_IDENTITY_CHANGED"
    }
    if (Test-ExplorerFileOperationActive -ProcessIds $remainingIds) {
        throw "RUNTIME_STATE_EXPLORER_FILE_OPERATION_ACTIVE"
    }
    try {
        Stop-VerifiedExplorerProcesses -Identities $identities
    } finally {
        Start-ExplorerShell
    }
    $null = Wait-ExplorerShellHealthy
    Start-Sleep -Seconds 1
    $observed = Get-ExternalRuntimeStateHolderInventory `
        -StateTree $StateTree -ProcessPlan $ProcessPlan
    if ($observed.external.Count -ne 0) {
        $null = Assert-ExplorerRuntimeStateHolderSet `
            -Holders $observed.external -StateTree $StateTree
        throw ("RUNTIME_STATE_EXPLORER_HOLDER_PERSISTED:" +
            (ConvertTo-RuntimeStateHolderDiagnostic -Holders $observed.external))
    }
    return [pscustomobject]@{
        repaired = $true
        method = "CONTROLLED_SHELL_RESTART"
        closed_window_count = [int]$windowResult.closed_count
        shell_restarted = $true
        inventory = $observed.inventory
    }
}

function Assert-RuntimeStatePermissions {
    param([Parameter(Mandatory = $true)][string]$StateTree)
    Initialize-RuntimeStateNativeProbe
    $tree = [System.IO.Path]::GetFullPath($StateTree)
    $parent = [System.IO.Path]::GetFullPath((Split-Path -Parent $tree))
    $shareAll = [uint32]7
    $deleteError = [XauusdRuntimeStateNativeProbe]::ProbeDirectoryAccess(
        $tree, [uint32]65536, $shareAll
    )
    $deleteChildError = [XauusdRuntimeStateNativeProbe]::ProbeDirectoryAccess(
        $parent, [uint32]64, $shareAll
    )
    $sharingViolation = 32
    if (($deleteError -ne 0 -and $deleteError -ne $sharingViolation) -or
        ($deleteChildError -ne 0 -and $deleteChildError -ne $sharingViolation)) {
        throw ("RUNTIME_STATE_PERMISSION_DENIED:" + ([ordered]@{
            state_tree_delete_error = [int]$deleteError
            parent_delete_child_error = [int]$deleteChildError
        } | ConvertTo-Json -Compress))
    }
    $probe = Join-Path $parent (".__runtime-access-{0}" -f
        [Guid]::NewGuid().ToString("N"))
    $moved = "$probe-moved"
    if ([System.IO.Path]::GetDirectoryName($probe) -ne $parent -or
        [System.IO.Path]::GetDirectoryName($moved) -ne $parent) {
        throw "RUNTIME_STATE_PERMISSION_DENIED:PROBE_PATH"
    }
    try {
        [System.IO.Directory]::CreateDirectory($probe) | Out-Null
        [System.IO.Directory]::Move($probe, $moved)
        [System.IO.Directory]::Delete($moved, $false)
    } catch {
        throw "RUNTIME_STATE_PERMISSION_DENIED:$($_.Exception.GetBaseException().Message)"
    } finally {
        if ([System.IO.Directory]::Exists($probe)) {
            [System.IO.Directory]::Delete($probe, $false)
        }
        if ([System.IO.Directory]::Exists($moved)) {
            [System.IO.Directory]::Delete($moved, $false)
        }
    }
    return [pscustomobject]@{
        state_tree_delete = $deleteError -eq 0
        parent_delete_child = $deleteChildError -eq 0
        sharing_violation_deferred_to_holder_inventory =
            $deleteError -eq $sharingViolation -or $deleteChildError -eq $sharingViolation
        sibling_create_rename_delete = $true
    }
}

function New-RuntimeProcessQuiescencePlan {
    param([Parameter(Mandatory = $true)][object[]]$ServiceContracts)
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $rootById = @{}
    foreach ($service in $ServiceContracts) {
        foreach ($process in @($snapshot | Where-Object {
            Test-ForecasterServiceProcess -Process $_ -Service $service
        })) {
            $rootById[[int]$process.ProcessId] = [string]$service.Key
        }
    }
    $entries = @{}
    foreach ($rootId in @($rootById.Keys)) {
        $pending = @([pscustomobject]@{ process_id=[int]$rootId; depth=0 })
        while ($pending.Count -gt 0) {
            $current = $pending[0]
            $pending = @($pending | Select-Object -Skip 1)
            $process = $snapshot | Where-Object ProcessId -eq $current.process_id |
                Select-Object -First 1
            if (-not $process) { continue }
            if (-not $entries.ContainsKey([int]$process.ProcessId)) {
                $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$process.ProcessId)
                if (-not $identity) { throw "RUNTIME_STATE_PROCESS_IDENTITY_UNAVAILABLE" }
                $waitHandle = $null
                try { $waitHandle = [System.Diagnostics.Process]::GetProcessById(
                    [int]$process.ProcessId
                ) } catch {}
                $entries[[int]$process.ProcessId] = [pscustomobject]@{
                    process_id = [int]$process.ProcessId
                    parent_process_id = [int]$process.ParentProcessId
                    process_start_token = [string]$identity.process_start_token
                    process_name = [string]$process.Name
                    service_key = [string]$rootById[[int]$rootId]
                    depth = [int]$current.depth
                    wait_handle = $waitHandle
                }
            }
            foreach ($child in @($snapshot | Where-Object {
                [int]$_.ParentProcessId -eq [int]$process.ProcessId
            })) {
                $pending += [pscustomobject]@{
                    process_id = [int]$child.ProcessId
                    depth = [int]$current.depth + 1
                }
            }
        }
    }
    [pscustomobject]@{
        captured_at = [DateTimeOffset]::UtcNow.ToString("o")
        entries = @($entries.Values | Sort-Object depth -Descending)
    }
}

function Update-RuntimeProcessQuiescencePlan {
    param([Parameter(Mandatory = $true)][object]$Plan)
    $entries = @{}
    foreach ($entry in @($Plan.entries)) {
        $entries[[int]$entry.process_id] = $entry
    }
    $snapshot = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $changed = $true
    while ($changed) {
        $changed = $false
        foreach ($process in $snapshot) {
            $processId = [int]$process.ProcessId
            $parentId = [int]$process.ParentProcessId
            if ($entries.ContainsKey($processId) -or
                -not $entries.ContainsKey($parentId)) { continue }
            $parent = $entries[$parentId]
            $identity = Get-ControlPlaneProcessIdentity -ProcessId $processId
            if (-not $identity) { continue }
            $waitHandle = $null
            try {
                $waitHandle = [System.Diagnostics.Process]::GetProcessById($processId)
            } catch {}
            $entries[$processId] = [pscustomobject]@{
                process_id = $processId
                parent_process_id = $parentId
                process_start_token = [string]$identity.process_start_token
                process_name = [string]$process.Name
                service_key = [string]$parent.service_key
                depth = [int]$parent.depth + 1
                wait_handle = $waitHandle
            }
            $changed = $true
        }
    }
    $Plan.entries = @($entries.Values | Sort-Object depth -Descending)
    return $Plan
}

function Stop-CapturedRuntimeProcessIdentity {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$Entry.process_id)
    if (-not $identity -or [string]$identity.process_start_token -ne
        [string]$Entry.process_start_token) { return }
    Stop-Process -Id ([int]$Entry.process_id) -ErrorAction SilentlyContinue
}

function Stop-RuntimeProcessQuiescencePlan {
    param(
        [Parameter(Mandatory = $true)][object]$Plan,
        [TimeSpan]$Timeout = [TimeSpan]::FromSeconds(30)
    )
    $null = Update-RuntimeProcessQuiescencePlan -Plan $Plan
    # Stop the exact captured roots first so they cannot spawn another child
    # while the already-captured descendants are being fenced.
    foreach ($entry in @($Plan.entries | Where-Object depth -eq 0)) {
        Stop-CapturedRuntimeProcessIdentity -Entry $entry
    }
    # A child created immediately before its root terminated still retains the
    # captured parent PID. Include that last bounded generation before waiting.
    $null = Update-RuntimeProcessQuiescencePlan -Plan $Plan
    foreach ($entry in @($Plan.entries | Sort-Object depth -Descending)) {
        Stop-CapturedRuntimeProcessIdentity -Entry $entry
    }
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    foreach ($entry in @($Plan.entries)) {
        $remaining = [int][Math]::Max(0, (
            $deadline - [DateTimeOffset]::UtcNow
        ).TotalMilliseconds)
        if ($entry.wait_handle) {
            try { $null = $entry.wait_handle.WaitForExit($remaining) } catch {}
        }
    }
    $active = @($Plan.entries | Where-Object {
        $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.process_id)
        $identity -and [string]$identity.process_start_token -eq
            [string]$_.process_start_token
    })
    if ($active.Count -ne 0) {
        throw ("RUNTIME_STATE_QUIESCENCE_TIMEOUT:" + (@($active |
            Select-Object -First 20 process_id,process_name,service_key) |
            ConvertTo-Json -Compress))
    }
    return $true
}

function Assert-NoExternalRuntimeStateHolders {
    param(
        [Parameter(Mandatory = $true)][string]$StateTree,
        [Parameter(Mandatory = $true)][object]$ProcessPlan
    )
    $observed = Get-ExternalRuntimeStateHolderInventory `
        -StateTree $StateTree -ProcessPlan $ProcessPlan
    $inventory = $observed.inventory
    $external = @($observed.external)
    if ($external.Count -ne 0) {
        $repair = Repair-ExplorerRuntimeStateHolders -StateTree $StateTree `
            -ProcessPlan $ProcessPlan -InitialHolders $external
        $inventory = $repair.inventory
        $inventory | Add-Member -NotePropertyName explorer_repair -NotePropertyValue `
            ([pscustomobject]@{
                method = [string]$repair.method
                closed_window_count = [int]$repair.closed_window_count
                shell_restarted = [bool]$repair.shell_restarted
            }) -Force
    }
    return $inventory
}

function Wait-RuntimeStateTreeQuiesced {
    param(
        [Parameter(Mandatory = $true)][string]$StateTree,
        [int[]]$ControlledProcessIds = @(),
        [TimeSpan]$Timeout = [TimeSpan]::FromSeconds(30)
    )
    if (-not (Test-Path -LiteralPath $StateTree)) { return $true }
    $null = Assert-RuntimeStatePermissions -StateTree $StateTree
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    $lastError = ""
    $lastHolders = @()
    do {
        $probePath = "$StateTree.quiescence-probe"
        try {
            $inventory = Get-RuntimeStateHolderInventory -StateTree $StateTree `
                -ControlledProcessIds $ControlledProcessIds
            $lastHolders = @($inventory.holders)
            $external = @($lastHolders | Where-Object { -not $_.controlled })
            if ($external.Count -ne 0) {
                throw ("RUNTIME_STATE_EXTERNAL_HOLDER_ACTIVE:" +
                    (ConvertTo-RuntimeStateHolderDiagnostic -Holders $external))
            }
            if ($lastHolders.Count -ne 0) {
                $lastError = "controlled holders remain"
                Start-Sleep -Milliseconds 250
                continue
            }
            if (Test-Path -LiteralPath $probePath) {
                throw "RUNTIME_STATE_QUIESCENCE_TIMEOUT:STALE_PROBE_PATH"
            }
            [System.IO.Directory]::Move($StateTree, $probePath)
            [System.IO.Directory]::Move($probePath, $StateTree)
            return [pscustomobject]@{
                quiesced = $true
                holder_count = 0
                rename_capable = $true
                verified_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
        } catch {
            $lastError = $_.Exception.Message
            if ($lastError.StartsWith("RUNTIME_STATE_EXTERNAL_HOLDER_ACTIVE:")) {
                throw $lastError
            }
            if ((Test-Path -LiteralPath $probePath) -and
                -not (Test-Path -LiteralPath $StateTree)) {
                try { [System.IO.Directory]::Move($probePath, $StateTree) } catch {}
            }
            Start-Sleep -Milliseconds 250
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if ($lastHolders.Count -ne 0) {
        $diagnostic = ConvertTo-RuntimeStateHolderDiagnostic -Holders $lastHolders
        $code = if (@($lastHolders | Where-Object kind -eq "PROCESS_CWD").Count) {
            "RUNTIME_STATE_PROCESS_CWD_ACTIVE"
        } elseif (@($lastHolders | Where-Object kind -eq "DIRECTORY").Count) {
            "RUNTIME_STATE_DIRECTORY_HANDLE_ACTIVE"
        } else { "RUNTIME_STATE_FILE_HANDLE_ACTIVE" }
        throw "${code}:$diagnostic"
    }
    throw "RUNTIME_STATE_QUIESCENCE_TIMEOUT:$lastError"
}

function Assert-RuntimeMigrationFailurePoint {
    param([string]$FailurePhase, [string]$CurrentPhase)
    if ($FailurePhase -and $FailurePhase -eq $CurrentPhase) {
        throw "INJECTED_RUNTIME_MIGRATION_FAILURE:$CurrentPhase"
    }
}

function Assert-LegacyRuntimeStateTopology {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeLocal,
        [Parameter(Mandatory = $true)][string]$SourceLocal
    )
    $runtimePath = [System.IO.Path]::GetFullPath($RuntimeLocal)
    $sourcePath = [System.IO.Path]::GetFullPath($SourceLocal)
    if (-not (Test-Path -LiteralPath $runtimePath)) {
        throw "RUNTIME_STATE_TOPOLOGY_LEGACY_JUNCTION_REQUIRED"
    }
    $runtimeItem = Get-Item -LiteralPath $runtimePath -Force
    if (-not ($runtimeItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        throw "RUNTIME_STATE_TOPOLOGY_LEGACY_JUNCTION_REQUIRED"
    }
    $targets = @($runtimeItem.Target)
    if ($targets.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$targets[0])) {
        throw "RUNTIME_STATE_TOPOLOGY_TARGET_UNAVAILABLE"
    }
    $declaredTarget = [string]$targets[0]
    $targetPath = if ([System.IO.Path]::IsPathRooted($declaredTarget)) {
        [System.IO.Path]::GetFullPath($declaredTarget)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path `
            (Split-Path -Parent $runtimePath) $declaredTarget))
    }
    if (-not $targetPath.Equals(
        $sourcePath, [System.StringComparison]::OrdinalIgnoreCase
    )) { throw "RUNTIME_STATE_TOPOLOGY_TARGET_MISMATCH" }
    $sourceForward = Join-Path $sourcePath "forward"
    if (-not (Test-Path -LiteralPath $sourceForward -PathType Container)) {
        throw "RUNTIME_STATE_TOPOLOGY_FORWARD_REQUIRED"
    }
    return [pscustomobject]@{
        runtime_local = $runtimePath
        source_local = $sourcePath
        source_forward = [System.IO.Path]::GetFullPath($sourceForward)
        target = $targetPath
        kind = "LEGACY_JUNCTION"
    }
}

function Convert-LegacyRuntimeLocalJunction {
    param(
        [Parameter(Mandatory = $true)][string]$RuntimeLocal,
        [Parameter(Mandatory = $true)][string]$SourceLocal,
        [string]$FailurePhase = ""
    )
    $runtimePath = [System.IO.Path]::GetFullPath($RuntimeLocal)
    $sourcePath = [System.IO.Path]::GetFullPath($SourceLocal)
    if (-not (Test-Path -LiteralPath $runtimePath)) {
        New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
        return [pscustomobject]@{ migrated = $false; state_root = $runtimePath }
    }
    $runtimeItem = Get-Item -LiteralPath $runtimePath -Force
    if (-not ($runtimeItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
        if (-not $runtimeItem.PSIsContainer) {
            throw "Runtime state root is not a directory: $runtimePath"
        }
        return [pscustomobject]@{ migrated = $false; state_root = $runtimePath }
    }
    $targets = @($runtimeItem.Target)
    if ($targets.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$targets[0])) {
        throw "Legacy runtime state link target is unavailable."
    }
    $declaredTarget = [string]$targets[0]
    $targetPath = if ([System.IO.Path]::IsPathRooted($declaredTarget)) {
        [System.IO.Path]::GetFullPath($declaredTarget)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $runtimePath) $declaredTarget))
    }
    if (-not $targetPath.Equals($sourcePath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Runtime state link does not target the authorized repository local root."
    }

    $sourceForward = Join-Path $sourcePath "forward"
    $migrationRoot = Join-Path (Split-Path -Parent $runtimePath) (
        ".runtime-state-migration-{0}" -f [Guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $migrationRoot | Out-Null
    try {
        if (Test-Path -LiteralPath $sourceForward) {
            Move-Item -LiteralPath $sourceForward `
                -Destination (Join-Path $migrationRoot "forward")
        }
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "AFTER_STATE_STAGED"
        # Windows PowerShell 5.1 can throw an internal NullReferenceException
        # when Remove-Item targets a directory junction. Directory.Delete on
        # the already verified link removes only the junction, never its target.
        [System.IO.Directory]::Delete($runtimePath)
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "AFTER_JUNCTION_REMOVAL"
        Move-Item -LiteralPath $migrationRoot -Destination $runtimePath
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "AFTER_RUNTIME_ROOT_CREATION"
        $migrated = Get-Item -LiteralPath $runtimePath -Force
        if ($migrated.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Runtime state root remained a reparse point after migration."
        }
        return [pscustomobject]@{ migrated = $true; state_root = $runtimePath }
    } catch {
        if (Test-Path -LiteralPath $migrationRoot) {
            $stagedForward = Join-Path $migrationRoot "forward"
            if ((Test-Path -LiteralPath $stagedForward) -and
                -not (Test-Path -LiteralPath $sourceForward)) {
                Move-Item -LiteralPath $stagedForward -Destination $sourceForward
            }
            Remove-Item -LiteralPath $migrationRoot -Force -Recurse
        }
        if (-not (Test-Path -LiteralPath $runtimePath)) {
            New-Item -ItemType Junction -Path $runtimePath -Target $sourcePath | Out-Null
        }
        throw
    }
}

function Enter-RuntimeStateMigrationLock {
    if (Test-Path -LiteralPath $runtimeStateMigrationLockPath) { return $false }
    try {
        New-Item -ItemType Directory -Path $runtimeStateMigrationLockPath `
            -ErrorAction Stop | Out-Null
        $owner = Get-ControlPlaneProcessIdentity -ProcessId $PID
        if (-not $owner) { throw "RUNTIME_STATE_MIGRATION_OWNER_REQUIRED" }
        $ownerRecord = [pscustomobject]@{
            owner_pid = $PID
            owner_process_start_token = [string]$owner.process_start_token
            acquired_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        Write-ControlCenterJsonAtomic `
            -Path (Join-Path $runtimeStateMigrationLockPath "owner.json") `
            -Value $ownerRecord -Depth 4
        $script:runtimeStateMigrationLockHeld = $true
        return $true
    } catch {
        Remove-Item -LiteralPath $runtimeStateMigrationLockPath -Recurse -Force `
            -ErrorAction SilentlyContinue
        return $false
    }
}

function Exit-RuntimeStateMigrationLock {
    if ($script:runtimeStateMigrationLockHeld -and
        (Test-Path -LiteralPath $runtimeStateMigrationLockPath)) {
        Remove-Item -LiteralPath $runtimeStateMigrationLockPath -Recurse -Force `
            -ErrorAction SilentlyContinue
    }
    $script:runtimeStateMigrationLockHeld = $false
}

function Invoke-RuntimeStateRootMigration {
    param(
        [ValidateSet("","BEFORE_WATCHDOG_SUSPENSION","AFTER_WATCHDOG_SUSPENSION",
            "AFTER_STOP_ALL","AFTER_STATE_STAGED","AFTER_JUNCTION_REMOVAL",
            "AFTER_RUNTIME_ROOT_CREATION","DURING_STABLE_RESTART",
            "AFTER_PARTIAL_RESTART","BEFORE_WATCHDOG_HANDOFF",
            "DURING_HEALTH_VERIFICATION")]
        [string]$FailurePhase = "",
        [switch]$PreflightOnly
    )
    $bundle = Assert-ControlCenterProcessIdentity
    $runtime = [System.IO.Path]::GetFullPath($moduleRoot)
    $source = [System.IO.Path]::GetFullPath($repositoryRoot)
    if ($runtime.Equals($source, [System.StringComparison]::OrdinalIgnoreCase) -or
        $runtime.StartsWith(
            $source + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "RUNTIME_STATE_ROOT_MUST_BE_SEPARATE_FROM_CODE_CHECKOUT"
    }
    if (-not (Enter-RuntimeStateMigrationLock)) {
        throw "RUNTIME_STATE_MIGRATION_ALREADY_ACTIVE"
    }

    $supervisionState = $null
    $oldWatchdog = $null
    $watchdogStopped = $false
    $migrationCompleted = $false
    $servicesStopped = $false
    $recoveryPlan = $null
    $processPlan = $null
    try {
        $release = Get-ReleaseControlState
        if (($release -and $release.transaction) -or
            (Test-Path -LiteralPath $releaseLockPath)) {
            throw "RUNTIME_STATE_MIGRATION_BLOCKED_BY_RELEASE_TRANSACTION"
        }
        $owners = @(Get-VerifiedWatchdogOwners)
        if ($owners.Count -ne 1) {
            throw "RUNTIME_STATE_MIGRATION_EXACTLY_ONE_WATCHDOG_REQUIRED"
        }
        $oldWatchdog = $owners[0]
        $null = Assert-CurrentWatchdogHeartbeat -Owner $oldWatchdog `
            -ExpectedRevision ([string]$bundle.source_revision)
        $before = Get-ControlPlaneIsolationSnapshot
        Assert-ControlPlaneIsolationBaseline -Snapshot $before -ReleaseState $release
        $stableRevision = [string]$before.business_runtime_revision
        if ($stableRevision -notmatch '^[0-9a-f]{40}$') {
            throw "RUNTIME_STATE_MIGRATION_STABLE_REVISION_REQUIRED"
        }
        $recoveryPlan = New-RuntimeRecoveryPlan -StableRevision $stableRevision `
            -ReleaseState $release -ServiceContracts @($services)
        $null = Assert-RuntimeRecoveryPlan -Plan $recoveryPlan
        $topology = Assert-LegacyRuntimeStateTopology `
            -RuntimeLocal $runtimeLocalRoot -SourceLocal $repositoryLocalRoot
        $sourceForward = [string]$topology.source_forward
        $null = Assert-RuntimeStatePermissions -StateTree $sourceForward
        $processPlan = New-RuntimeProcessQuiescencePlan -ServiceContracts @($services)
        $initialInventory = Assert-NoExternalRuntimeStateHolders `
            -StateTree $sourceForward -ProcessPlan $processPlan
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "BEFORE_WATCHDOG_SUSPENSION"

        $supervisionState = Suspend-ControlPlaneSupervision
        Wait-ControlPlaneGuardQuiesced
        Stop-VerifiedWatchdogOwner -Identity $oldWatchdog
        $watchdogStopped = $true
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "AFTER_WATCHDOG_SUSPENSION"

        # Recheck after fencing the watchdog. The migration lock prevents every
        # normal release entrypoint from acquiring its transaction lock.
        $releaseAfterFence = Get-ReleaseControlState
        if (($releaseAfterFence -and $releaseAfterFence.transaction) -or
            (Test-Path -LiteralPath $releaseLockPath)) {
            throw "RUNTIME_STATE_MIGRATION_RELEASE_TRANSACTION_APPEARED"
        }

        $null = Stop-RuntimeProcessQuiescencePlan -Plan $processPlan
        $servicesStopped = $true
        $quiescence = Wait-RuntimeStateTreeQuiesced -StateTree $sourceForward `
            -ControlledProcessIds @($processPlan.entries.process_id)
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "AFTER_STOP_ALL"

        if ($PreflightOnly) {
            $recoveryStarted = [DateTimeOffset]::UtcNow
            $null = Restore-RuntimeRecoveryPlan -Plan $recoveryPlan
            $recovered = Wait-RuntimeRecoveryPlanHealth -Plan $recoveryPlan `
                -RecoveryStarted $recoveryStarted
            $servicesStopped = $false
            $releaseAfterPreflight = Get-ReleaseControlState
            $stateHashAfterPreflight = if (Test-Path -LiteralPath $releaseControlStatePath) {
                Get-Sha256Hex -LiteralPath $releaseControlStatePath
            } else { $null }
            $historyHashAfterPreflight = if (Test-Path -LiteralPath $releaseHistoryPath) {
                Get-Sha256Hex -LiteralPath $releaseHistoryPath
            } else { $null }
            if ([string]$before.release_state_hash -ne [string]$stateHashAfterPreflight -or
                [string]$before.release_history_hash -ne [string]$historyHashAfterPreflight -or
                ($releaseAfterPreflight -and $releaseAfterPreflight.transaction)) {
                throw "RUNTIME_STATE_PREFLIGHT_CHANGED_RELEASE_STATE"
            }
            $null = Start-WatchdogReplacement -PassThru
            $newWatchdog = Wait-VerifiedWatchdogHandoff `
                -ExpectedRevision ([string]$bundle.source_revision) `
                -PreviousIdentity $oldWatchdog
            $watchdogStopped = $false
            Restore-ControlPlaneSupervision -State $supervisionState
            return [pscustomobject]@{
                preflight = "PASSED"
                topology = [string]$topology.kind
                state_tree = $sourceForward
                permission_checked = $true
                initial_controlled_holder_count = @($initialInventory.holders).Count
                final_holder_count = [int]$quiescence.holder_count
                rename_capable = [bool]$quiescence.rename_capable
                explorer_repair = $initialInventory.explorer_repair
                recovered_revision = [string]$recovered.revision
                watchdog_process_id = [int]$newWatchdog.process_id
            }
        }

        $result = Convert-LegacyRuntimeLocalJunction `
            -RuntimeLocal $runtimeLocalRoot -SourceLocal $repositoryLocalRoot `
            -FailurePhase $FailurePhase
        $runtimeItem = Get-Item -LiteralPath $runtimeLocalRoot -Force
        if ($runtimeItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "RUNTIME_STATE_MIGRATION_REPARSE_POINT_REMAINED"
        }
        if (Test-Path -LiteralPath (Join-Path $repositoryLocalRoot "forward")) {
            throw "RUNTIME_STATE_MIGRATION_CODE_ROOT_STATE_REMAINED"
        }
        $migrationCompleted = $true

        $reloadStarted = [DateTimeOffset]::UtcNow
        $startedCount = 0
        foreach ($service in $services) {
            if (@($before.services.($service.Key)).Count -eq 1) {
                if ($FailurePhase -eq "DURING_STABLE_RESTART" -and $startedCount -eq 0) {
                    throw "INJECTED_RUNTIME_MIGRATION_FAILURE:DURING_STABLE_RESTART"
                }
                Start-ForecasterService -Service $service -SkipExistingCheck
                $startedCount += 1
                if ($FailurePhase -eq "AFTER_PARTIAL_RESTART" -and $startedCount -eq 1) {
                    throw "INJECTED_RUNTIME_MIGRATION_FAILURE:AFTER_PARTIAL_RESTART"
                }
            }
        }
        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "DURING_HEALTH_VERIFICATION"
        $deadline = [DateTimeOffset]::UtcNow.Add($serviceStartupTimeout)
        do {
            Start-Sleep -Milliseconds 500
            $healthy = Test-CodeReloadHealth -ReloadStarted $reloadStarted
        } while (-not $healthy -and [DateTimeOffset]::UtcNow -lt $deadline)
        if (-not $healthy) {
            throw "RUNTIME_STATE_MIGRATION_SERVICE_HEALTH_FAILED"
        }
        foreach ($service in $services) {
            $expectedOwners = @($before.services.($service.Key)).Count
            if (@(Get-ForecasterProcesses -Service $service).Count -ne $expectedOwners) {
                throw "RUNTIME_STATE_MIGRATION_SERVICE_OWNER_MISMATCH:$($service.Key)"
            }
        }
        if ([string](Get-CodeRevision) -ne $stableRevision) {
            throw "RUNTIME_STATE_MIGRATION_CHANGED_STABLE_REVISION"
        }
        $releaseAfter = Get-ReleaseControlState
        $currentReleaseStateHash = if (Test-Path -LiteralPath $releaseControlStatePath) {
            Get-Sha256Hex -LiteralPath $releaseControlStatePath
        } else { $null }
        $currentReleaseHistoryHash = if (Test-Path -LiteralPath $releaseHistoryPath) {
            Get-Sha256Hex -LiteralPath $releaseHistoryPath
        } else { $null }
        if ([string]$before.release_state_hash -ne [string]$currentReleaseStateHash -or
            [string]$before.release_history_hash -ne [string]$currentReleaseHistoryHash -or
            ($releaseAfter -and $releaseAfter.transaction)) {
            throw "RUNTIME_STATE_MIGRATION_CHANGED_RELEASE_STATE"
        }
        Write-RuntimeUpdateState @{
            state_root_migrated = [bool]$result.migrated
            state_root_migrated_at = [DateTimeOffset]::UtcNow.ToString("o")
            state_root = $runtimeLocalRoot
            preserved_revision = $stableRevision
        }

        Assert-RuntimeMigrationFailurePoint -FailurePhase $FailurePhase `
            -CurrentPhase "BEFORE_WATCHDOG_HANDOFF"
        $null = Start-WatchdogReplacement -PassThru
        $newWatchdog = Wait-VerifiedWatchdogHandoff `
            -ExpectedRevision ([string]$bundle.source_revision) `
            -PreviousIdentity $oldWatchdog
        $watchdogStopped = $false
        Restore-ControlPlaneSupervision -State $supervisionState
        return [pscustomobject]@{
            migrated = [bool]$result.migrated
            runtime_state_root = $runtimeLocalRoot
            preserved_revision = $stableRevision
            explorer_repair = $initialInventory.explorer_repair
            watchdog_process_id = [int]$newWatchdog.process_id
        }
    } catch {
        $failure = $_.Exception.Message
        if ($servicesStopped -and $recoveryPlan) {
            try {
                $recoveryStarted = [DateTimeOffset]::UtcNow
                $null = Restore-RuntimeRecoveryPlan -Plan $recoveryPlan
                $null = Wait-RuntimeRecoveryPlanHealth -Plan $recoveryPlan `
                    -RecoveryStarted $recoveryStarted
            } catch {
                $failure = "${failure};RUNTIME_RECOVERY_FAILED:$($_.Exception.Message)"
            }
        }
        if ($watchdogStopped) {
            try { $null = Start-WatchdogReplacement -PassThru } catch {}
        }
        try { Restore-ControlPlaneSupervision -State $supervisionState } catch {}
        throw $failure
    } finally {
        Exit-RuntimeStateMigrationLock
    }
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
    $releaseBeforeInstall = Get-ReleaseControlState
    if ($releaseBeforeInstall -and $releaseBeforeInstall.transaction) {
        throw "Runtime state ownership cannot change during a release transaction."
    }
    $revisionRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
        -Arguments @("-C", $source, "rev-parse", "HEAD")
    $revision = ([string]$revisionRead.stdout).Trim()
    if ($revisionRead.exit_code -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') {
        throw "Cannot resolve the verified development revision."
    }
    if (Test-Path -LiteralPath $runtime) {
        $insideRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $runtime, "rev-parse", "--is-inside-work-tree")
        $inside = ([string]$insideRead.stdout).Trim()
        if ($insideRead.exit_code -ne 0 -or $inside -ne "true") {
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
    Stop-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Stop-All
    $stateRootResult = Convert-LegacyRuntimeLocalJunction `
        -RuntimeLocal $runtimeLocal -SourceLocal $sourceLocal
    Write-RuntimeUpdateState @{
        bootstrap_revision = $revision
        installed_at = [DateTimeOffset]::UtcNow.ToString("o")
        state_root_migrated = [bool]$stateRootResult.migrated
    }
    $controlRoot = Join-Path $sourceLocal "runtime-control"
    Sync-StableRuntimeControlFiles -SourceRoot $runtime -ControlRoot $controlRoot
    $stableScript = Join-Path $controlRoot "xauusd_control_center.ps1"

    Register-AutoStartTask -ControlScript $stableScript `
        -RuntimePath $runtime -SourceRepository $source
    [pscustomobject]@{
        runtime_root = $runtime
        state_root = $runtimeLocal
        installed_revision = $revision
        control_script = $stableScript
    }
}
