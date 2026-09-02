# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
function Set-CandidateRepositoryPending {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$Operation,
        [int]$ExitCode = 1,
        [string]$Diagnostic = "",
        [bool]$WindowsPassed = $false
    )
    $state = Get-ReleaseControlState
    if (-not $state -or -not (Test-ReleaseIdentity $state.candidate $Candidate)) {
        return $false
    }
    $priorState = [string]$state.candidate.validation_state
    $priorReason = [string]$state.candidate.validation.reason
    $windowsState = if ($WindowsPassed -or
        [string]$state.candidate.validation.windows -eq "PASSED") {
        "PASSED"
    } else { "NOT_RUN" }
    $state.candidate.validation_state = "CHECKS_PENDING"
    $state.candidate.validation = [pscustomobject]@{
        key = [string]$Candidate.validation_key
        repository = "PENDING"
        repository_retryable = $true
        windows = $windowsState
        cloudflare = "NOT_RUN"
        reason = $Reason
        operation = $Operation
        exit_code = $ExitCode
        diagnostic = Protect-PreflightDiagnosticText $Diagnostic
        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    if ($priorState -ne "CHECKS_PENDING" -or $priorReason -ne $Reason) {
        Write-ReleaseHistory -Event "CANDIDATE_REPOSITORY_PENDING" `
            -Release $state.candidate -Detail @{
                reason = $Reason
                operation = $Operation
                exit_code = $ExitCode
                diagnostic = Protect-PreflightDiagnosticText $Diagnostic
                retryable = $true
            }
    }
    return $true
}

function Test-BroadcastServiceReadiness {
    param([Parameter(Mandatory = $true)][string]$CandidateRevision)
    $compatibleRevision = [string]$env:AURUM_LIVE_BROADCAST_COMPATIBLE_REVISION
    $token = Get-BroadcastPublisherToken
    if ([string]::IsNullOrWhiteSpace($token)) {
        return [pscustomobject]@{
            state = "BROADCAST_PENDING"; passed = $false; retryable = $true
            reason = "BROADCAST_PUBLISH_TOKEN_NOT_CONFIGURED"
            authority = $broadcastHealthUrl
        }
    }
    try {
        $health = Invoke-RestMethod -Method Get -Uri $broadcastHealthUrl -TimeoutSec 15
        if ([string]$health.service -ne "aurum-live-broadcast") {
            return [pscustomobject]@{
                state = "FAILED"; passed = $false; retryable = $false
                reason = "BROADCAST_AUTHORITY_MISMATCH"
                authority = $broadcastHealthUrl
            }
        }
        if ([string]$health.schema_version -ne "PUBLIC_LIVE_V1") {
            return [pscustomobject]@{
                state = "FAILED"; passed = $false; retryable = $false
                reason = "BROADCAST_SCHEMA_INCOMPATIBLE"
                authority = $broadcastHealthUrl
                schema_version = [string]$health.schema_version
            }
        }
        $revisionAccepted = (
            [string]$health.code_revision -eq $CandidateRevision -or
            (-not [string]::IsNullOrWhiteSpace($compatibleRevision) -and
                [string]$health.code_revision -eq $compatibleRevision)
        )
        if (-not [bool]$health.binding_ready -or -not $revisionAccepted) {
            return [pscustomobject]@{
                state = "BROADCAST_BLOCKED"; passed = $false; retryable = $true
                reason = "BROADCAST_PLATFORM_NOT_READY"
                authority = $broadcastHealthUrl
                code_revision = [string]$health.code_revision
                binding_ready = [bool]$health.binding_ready
                revision_accepted = $revisionAccepted
            }
        }
        $dryRunState = [ordered]@{
            schema_version = "PUBLIC_LIVE_V1"; sequence = 1
            generated_at = [DateTimeOffset]::UtcNow.ToString("o")
            source_revision = $CandidateRevision; market_session = "DATA_UNAVAILABLE"
            freshness = @{ online = $false; state = "STALE" }
            quote = @{
                bid = 0.0; ask = 0.0; spread = 0.0
                source_received_time = [DateTimeOffset]::UtcNow.ToString("o")
            }
            forecast = @{
                model_identity = $null; model_version = $null
                recommended_action = "WAIT"; prediction_status = "DRY_RUN"
                ev_long_u5 = $null; ev_short_u5 = $null; interval_width = $null
                decision_time = $null; signal_expiry_seconds = 20
                forecast_horizon_seconds = 1800; directional_bias = "NEUTRAL"
                frozen_record = $false
            }
            health = @{ status = "DRY_RUN"; alerts = @() }
        }
        $headers = @{ Authorization = "Bearer $token" }
        $dryRun = Invoke-RestMethod -Method Post -Uri $broadcastPublishDryRunUrl `
            -Headers $headers -ContentType "application/json" `
            -Body ($dryRunState | ConvertTo-Json -Depth 8 -Compress) -TimeoutSec 15
        $passed = [bool]$dryRun.valid -and [bool]$dryRun.dry_run -and
            [string]$dryRun.schema_version -eq "PUBLIC_LIVE_V1"
        return [pscustomobject]@{
            state = if ($passed) { "PASSED" } else { "BROADCAST_BLOCKED" }
            passed = $passed
            retryable = -not $passed
            reason = if ($passed) { "PASSED" } else { "BROADCAST_DRY_RUN_REJECTED" }
            authority = $broadcastHealthUrl
            schema_version = [string]$health.schema_version
            code_revision = [string]$health.code_revision
            binding_ready = [bool]$health.binding_ready
            revision_accepted = $revisionAccepted
            dry_run_valid = $passed
            dry_run_storage_mutation = $false
            dry_run_broadcast = $false
        }
    } catch {
        return [pscustomobject]@{
            state = "BROADCAST_BLOCKED"; passed = $false; retryable = $true
            reason = "BROADCAST_HEALTH_PROBE_FAILED"
            authority = $broadcastHealthUrl
            error = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Test-BroadcastLiveDeliveryReadiness {
    param([Parameter(Mandatory = $true)][string]$ExpectedRevision)
    try {
        $health = Invoke-RestMethod -Method Get -Uri $broadcastHealthUrl -TimeoutSec 15
        $publishedAt = ConvertTo-ReleaseTimestampUtc `
            -Value $health.latest_published_at
        $publishedValid = $publishedAt -ne [DateTimeOffset]::MinValue
        $age = if ($publishedValid) {
            [DateTimeOffset]::UtcNow - $publishedAt
        } else { [TimeSpan]::MaxValue }
        $publisherService = $services | Where-Object Key -eq "broadcast" | Select-Object -First 1
        $processes = if ($publisherService) { @(Get-ForecasterProcesses $publisherService) } else { @() }
        $publisherState = if ($publisherService) {
            Get-ServiceState -Service $publisherService -Processes $processes
        } else { "NOT_CONFIGURED" }
        $passed = (
            [string]$health.service -eq "aurum-live-broadcast" -and
            [string]$health.schema_version -eq "PUBLIC_LIVE_V1" -and
            [bool]$health.binding_ready -and [bool]$health.latest_available -and
            [string]$health.latest_source_revision -eq $ExpectedRevision -and
            $publishedValid -and $age -ge [TimeSpan]::Zero -and
            $age -le $broadcastFreshnessThreshold -and
            [string]$publisherState -eq "RUNNING"
        )
        return [pscustomobject]@{
            state = if ($passed) { "PASSED" } else { "BROADCAST_LIVE_BLOCKED" }
            passed = $passed; reason = if ($passed) { "PASSED" } else { "BROADCAST_LIVE_NOT_READY" }
            latest_sequence = $health.latest_sequence
            latest_generated_at = [string]$health.latest_generated_at
            latest_published_at = [string]$health.latest_published_at
            latest_source_revision = [string]$health.latest_source_revision
            freshness_threshold_seconds = [int]$broadcastFreshnessThreshold.TotalSeconds
            publisher_state = [string]$publisherState
        }
    } catch {
        return [pscustomobject]@{
            state = "BROADCAST_LIVE_BLOCKED"; passed = $false
            reason = "BROADCAST_LIVE_PROBE_FAILED"
            error = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Invoke-AutomaticCandidateValidation {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $state = Get-ReleaseControlState
    if (-not $state -or -not (Test-ReleaseIdentity $state.candidate $Candidate)) { return $false }
    $priorValidationState = [string]$state.candidate.validation_state
    $windowsAlreadyPassed = [bool](
        [string]$state.candidate.validation.key -eq [string]$Candidate.validation_key -and
        $priorValidationState -in @(
            "NEW", "CHECKS_BLOCKED", "CHECKS_PENDING", "PLATFORM_PENDING"
        ) -and
        [string]$state.candidate.validation.windows -eq "PASSED"
    )
    $startingValidationState = [string]$state.candidate.validation_state
    $startingValidationReason = if ($state.candidate.validation) {
        [string]$state.candidate.validation.reason
    } else { "" }
    $state.candidate.validation_state = "STAGING"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    try {
        if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind) {
            throw "Only a PRODUCTION_CANDIDATE artifact can enter validation."
        }
        $script:lastRepositoryValidationResult = $null
        if (-not (Test-ProductionCandidateProvenance -Candidate $Candidate)) {
            $repositoryResult = $script:lastRepositoryValidationResult
            if ($repositoryResult -and
                [string]$repositoryResult.state -eq "REPOSITORY_PENDING") {
                Set-CandidateRepositoryPending -Candidate $Candidate `
                    -Reason ([string]$repositoryResult.reason) `
                    -Operation ([string]$repositoryResult.operation) `
                    -ExitCode ([int]$repositoryResult.exit_code) `
                    -Diagnostic ([string]$repositoryResult.diagnostic) `
                    -WindowsPassed $windowsAlreadyPassed
                return $false
            }
            $repositoryFailure = if ($repositoryResult -and $repositoryResult.reason) {
                [string]$repositoryResult.reason
            } else { "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED" }
            if ($repositoryResult -and $repositoryResult.diagnostic) {
                $repositoryFailure += ": " +
                    (Protect-PreflightDiagnosticText $repositoryResult.diagnostic)
            }
            throw $repositoryFailure
        }
        $state.candidate.validation_state = "TESTING"
        Write-ReleaseControlState -State $state
        if (-not $windowsAlreadyPassed -and
            -not (Invoke-ProductionShapePreflight -Revision ([string]$Candidate.windows_revision))) {
            throw "Isolated Windows preflight failed."
        }
        $script:lastGitHubChecksResult = $null
        $checks = Test-RequiredGitHubChecks -Revision ([string]$Candidate.git_sha)
        $checksResult = $script:lastGitHubChecksResult
        if ($checks -eq "REPOSITORY_PENDING") {
            Set-CandidateRepositoryPending -Candidate $Candidate `
                -Reason $(if ($checksResult -and $checksResult.reason) {
                    [string]$checksResult.reason
                } else { "GITHUB_TEMPORARILY_UNAVAILABLE" }) `
                -Operation "GITHUB_CHECKS_API" `
                -ExitCode $(if ($checksResult) { [int]$checksResult.exit_code } else { 1 }) `
                -Diagnostic $(if ($checksResult) {
                    [string]$checksResult.diagnostic
                } else { "" }) `
                -WindowsPassed $true
            return $false
        }
        if ($checks -eq "FAILED") {
            throw $(if ($checksResult -and $checksResult.reason) {
                [string]$checksResult.reason
            } else { "GITHUB_CHECKS_ACCESS_FAILED" })
        }
        if ($checks -eq "CHECKS_BLOCKED") {
            $state.candidate.validation_state = "CHECKS_BLOCKED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "FAILED"
                repository_retryable = $true
                windows = "PASSED"
                cloudflare = "NOT_RUN"
                reason = "REQUIRED_GITHUB_CHECKS_BLOCKED"
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            if ($priorValidationState -ne "CHECKS_BLOCKED") {
                Write-ReleaseHistory -Event "CANDIDATE_CHECKS_BLOCKED" `
                    -Release $state.candidate -Detail @{ retryable = $true }
            }
            return $false
        }
        if ($checks -eq "PENDING") {
            $state.candidate.validation_state = "CHECKS_PENDING"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PENDING"
                repository_retryable = $true
                windows = "PASSED"
                cloudflare = "NOT_RUN"
                reason = "REQUIRED_GITHUB_CHECKS_PENDING"
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            try {
                $null = Invoke-CandidateAccessQualificationReuse
                return $true
            } catch {
                Write-ReleaseHistory -Event "CANDIDATE_ACCESS_QUALIFICATION_REUSE_UNAVAILABLE" `
                    -Release $state.candidate -Detail @{
                        reason = Protect-PreflightDiagnosticText $_.Exception.Message
                    }
            }
            return $false
        }
        if ($priorValidationState -in @("CHECKS_BLOCKED", "CHECKS_PENDING")) {
            Write-ReleaseHistory -Event "CANDIDATE_CHECKS_RECOVERED" `
                -Release $state.candidate -Detail @{
                    exact_sha = [string]$Candidate.git_sha
                    prior_reason = [string]$Candidate.validation.reason
                }
        }
        $changed = @(Get-CandidateChangedFiles `
            -StableRevision ([string]$state.stable.git_sha) `
            -CandidateRevision ([string]$Candidate.git_sha))
        $broadcastRequired = [bool](@($changed | Where-Object {
            [string]$_ -match '^(broadcast/|web/app/_lib/live-broadcast\.ts$)'
        }).Count -gt 0)
        $broadcast = [pscustomobject]@{
            state = "NOT_REQUIRED"; passed = $true; reason = "NOT_REQUIRED"
        }
        if ($broadcastRequired) {
            $broadcast = Test-BroadcastServiceReadiness `
                -CandidateRevision ([string]$Candidate.git_sha)
            if (-not $broadcast.passed) {
                $state.candidate.validation_state = [string]$broadcast.state
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                    broadcast = $broadcast
                    reason = [string]$broadcast.reason
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                $event = if ([bool]$broadcast.retryable) {
                    "CANDIDATE_BROADCAST_BLOCKED"
                } else { "CANDIDATE_FAILED" }
                if ($startingValidationState -ne [string]$broadcast.state -or
                    $startingValidationReason -ne [string]$broadcast.reason) {
                    Write-ReleaseHistory -Event $event -Release $state.candidate `
                        -Detail @{
                            reason = [string]$broadcast.reason
                            broadcast = $broadcast
                        }
                }
                return $false
            }
            if ($startingValidationState -in @("BROADCAST_PENDING", "BROADCAST_BLOCKED")) {
                Write-ReleaseHistory -Event "CANDIDATE_BROADCAST_RECOVERED" `
                    -Release $state.candidate -Detail @{ broadcast = $broadcast }
            }
        }
        $compatibility = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
        if ([string]$compatibility.state -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
            $accepted = [bool]($state.candidate.migration_acceptance -and
                [string]$state.candidate.migration_acceptance.validation_key -eq
                    [string]$Candidate.validation_key)
            if ($accepted) {
                try {
                    $qualification = Ensure-CoordinatedMigrationQualification `
                        -Candidate $Candidate -Stable $state.stable `
                        -MigrationFiles @($compatibility.files)
                    if ([string]$qualification.root_receipt_digest -ne
                        [string]$state.candidate.migration_acceptance.receipt_digest) {
                        throw "MIGRATION_RECEIPT_AUTHORITY_MISMATCH"
                    }
                    $state.candidate.compatibility_state =
                        "COORDINATED_STORAGE_MIGRATION_PASSED"
                } catch {
                    $state.candidate.compatibility_state = "REVIEW_REQUIRED"
                    $state.candidate.validation_state = "REVIEW_REQUIRED"
                    $state.candidate.validation = [pscustomobject]@{
                        key = [string]$Candidate.validation_key
                        repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                        reason = "COORDINATED_STORAGE_MIGRATION_EVIDENCE_INVALID"
                        migration_reason = Protect-PreflightDiagnosticText $_.Exception.Message
                        review_files = @($compatibility.files)
                        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                    }
                    Write-ReleaseControlState -State $state
                    return $false
                }
            } else {
                $state.candidate.compatibility_state = "REVIEW_REQUIRED"
                $state.candidate.validation_state = "REVIEW_REQUIRED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = "PENDING"
                    reason = "COORDINATED_STORAGE_MIGRATION_REQUIRED"
                    review_files = @($compatibility.files)
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                return $false
            }
        }
        if ([string]$compatibility.state -eq "PLATFORM_CONFIG_REVIEW_REQUIRED") {
            $approved = [bool]($state.candidate.compatibility_approval -and
                [string]$state.candidate.compatibility_approval.validation_key -eq
                    [string]$Candidate.validation_key)
            if (-not $approved) {
                $resourcesVerified = Test-CandidatePlatformResources `
                    -Stable $state.stable -Candidate $Candidate
                $state.candidate.compatibility_state = "REVIEW_REQUIRED"
                $state.candidate.validation_state = "REVIEW_REQUIRED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                    reason = if ($resourcesVerified) {
                        "PLATFORM_CONFIG_REVIEW_REQUIRED"
                    } else { "PLATFORM_RESOURCE_VERIFICATION_FAILED" }
                    review_files = @($compatibility.files)
                    resources_verified = $resourcesVerified
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                Write-ReleaseHistory -Event "CANDIDATE_COMPATIBILITY_REVIEW_REQUIRED" `
                    -Release $state.candidate -Detail @{
                        validation_key = [string]$Candidate.validation_key
                        files = @($compatibility.files)
                        resources_verified = $resourcesVerified
                    }
                return $false
            }
        }
        $routePlan = Get-CandidateRouteValidationPlan -ChangedFiles $changed `
            -Revision ([string]$Candidate.git_sha)
        $cpuRoutePlan = Get-CandidateRouteValidationPlan -ChangedFiles $changed `
            -Revision ([string]$Candidate.git_sha) -AllCpuRoutes
        $cpuRoutePlan.static_assets = @($routePlan.static_assets)
        $workerChanged = [bool]$routePlan.worker_cpu_required
        $cloudflareChanged = [bool]($routePlan.requires_validation -or
            @($cpuRoutePlan.worker_reads).Count -gt 0 -or @($cpuRoutePlan.worker_writes).Count -gt 0)
        Set-CloudflareCandidatePointer -Stable $state.stable -Candidate $Candidate
        $placementPropagation = Wait-CandidatePlacementPropagation -Candidate $Candidate
        if (-not $placementPropagation.passed) {
            $state.candidate.validation_state = "PLATFORM_PENDING"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                compatibility = [string]$state.candidate.compatibility_state
                reason = "CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
                placement_propagation = $placementPropagation
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            Write-ReleaseHistory -Event "CANDIDATE_PLATFORM_PENDING" `
                -Release $state.candidate -Detail @{
                    reason = "CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
                    retryable = $true
                }
            return $false
        }
        $cloudflare = [pscustomobject]@{ passed = $true; routes = @(); cpu_evidence = "NOT_REQUIRED" }
        if ($cloudflareChanged) {
            if ($priorValidationState -eq "PLATFORM_PENDING" -and
                [string]$state.candidate.validation.observability_diagnostic -eq
                    "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING") {
                $legacyRun = [string]$state.candidate.validation.validation_run
                if ($legacyRun -match '^[0-9a-fA-F-]{36}$') {
                    Add-WorkerCpuLedgerEvent -ValidationRun $legacyRun `
                        -Event "LEGACY_PROVIDER_INCOMPLETE_FORENSIC_CLOSED" `
                        -Detail ([pscustomobject]@{
                            prior_policy="exact-provider-universe"
                            reinterpreted=$false; further_polling=$false
                        })
                }
                Write-ReleaseHistory -Event "LEGACY_WORKER_CPU_PROVIDER_RUN_CLOSED" `
                    -Release $state.candidate -Detail @{
                        validation_run=$legacyRun; preserved_forensic=$true
                        reinterpreted=$false; further_polling=$false
                    }
            }
            $resumePlatformOnly = [bool](
                $priorValidationState -eq "PLATFORM_PENDING" -and
                [string]$state.candidate.validation.key -eq [string]$Candidate.validation_key -and
                (Test-RetryableObservabilityDiagnostic `
                    -Diagnostic ([string]$state.candidate.validation.observability_diagnostic))
            )
            $cloudflare = if ($resumePlatformOnly) {
                Resume-CandidateWorkerPlatformEvidence -Candidate $Candidate `
                    -Validation $state.candidate.validation
            } else {
                Invoke-CandidateWorkerValidation -Candidate $Candidate `
                    -RoutePlan $cpuRoutePlan
            }
            if (-not $cloudflare.passed) {
                $failedRoutes = @($cloudflare.routes | Where-Object { -not $_.passed })
                $passedRoutes = @($cloudflare.routes | Where-Object { $_.passed })
                $firstFailedRoute = $failedRoutes | Select-Object -First 1
                $firstFailure = if ($firstFailedRoute.first_failure) {
                    $firstFailedRoute.first_failure
                } else {
                    $firstFailedRoute.PSObject.Copy()
                }
                $state.candidate.validation_state = "FAILED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = "FAILED"
                    compatibility = [string]$state.candidate.compatibility_state
                    reason = "DIRECTED_WORKER_VALIDATION_FAILED"
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan
                    routes = $cloudflare.routes
                    routes_tested = [int]($passedRoutes.Count + $failedRoutes.Count)
                    routes_passed = [int]$passedRoutes.Count
                    routes_failed = [int]$failedRoutes.Count
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_worker_invocations = $cloudflare.static_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    first_failure = $firstFailure
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "NOT_RUN" }
                    worker_failures = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_evidence = "NOT_RUN"
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                Write-ReleaseHistory -Event "CANDIDATE_FAILED" `
                    -Release $state.candidate -Detail @{
                        reason = "DIRECTED_WORKER_VALIDATION_FAILED"
                        validation_run = $cloudflare.validation_run
                    }
                return $false
            }
            if (-not $cloudflare.cpu_evidence) {
                $diagnostic = if ($cloudflare.observability_diagnostic) {
                    [string]$cloudflare.observability_diagnostic
                } else { "PLATFORM_CPU_EVIDENCE_REQUIRED" }
                $telemetryPending = Test-RetryableObservabilityDiagnostic `
                    -Diagnostic $diagnostic
                if ($telemetryPending) {
                    $state.candidate.validation_state = "PLATFORM_PENDING"
                } else {
                    $state.candidate.validation_state = "FAILED"
                }
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = if ($telemetryPending) { "PENDING" } else { "FAILED" }
                    reason = $diagnostic
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan
                    routes = $cloudflare.routes
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    observability_credential_source = $cloudflare.observability_credential_source
                    observability_diagnostic = $diagnostic
                    telemetry_window_from = $cloudflare.telemetry_window_from
                    telemetry_window_to = $cloudflare.telemetry_window_to
                    expected_requests = @($cloudflare.expected_requests)
                    cpu_route_plan = $cpuRoutePlan
                    worker_qualification = $cloudflare.worker_qualification
                    directed_request_ledger = $cloudflare.directed_request_ledger
                    cpu_qualification_mode = $cloudflare.cpu_qualification_mode
                    static_worker_invocations = $cloudflare.static_worker_invocations
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "DIAGNOSTIC_UNAVAILABLE" }
                    worker_failures = [pscustomobject]@{ state = "DIAGNOSTIC_UNAVAILABLE" }
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                if ($telemetryPending) {
                    Write-ReleaseHistory -Event "CANDIDATE_PLATFORM_PENDING" `
                        -Release $state.candidate -Detail @{
                            reason = $diagnostic
                            retryable = $true
                        }
                } else {
                    Write-ReleaseHistory -Event "CANDIDATE_FAILED" `
                        -Release $state.candidate -Detail @{
                            reason = $diagnostic
                            validation_run = $cloudflare.validation_run
                        }
                }
                return $false
            }
            if ([string]$cloudflare.cpu_evidence.gate_state -eq "REVIEW_REQUIRED") {
                $state.candidate.validation_state = "REVIEW_REQUIRED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"
                    cloudflare = "REVIEW_REQUIRED"
                    reason = "WORKER_CPU_HEADROOM_REVIEW_REQUIRED"
                    route_plan = $routePlan; routes = $cloudflare.routes
                    cpu_route_plan = $cpuRoutePlan
                    validation_run = $cloudflare.validation_run
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    expected_requests = @($cloudflare.expected_requests)
                    telemetry_window_from = $cloudflare.telemetry_window_from
                    telemetry_window_to = $cloudflare.telemetry_window_to
                    static_worker_invocations = $cloudflare.static_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    observability_diagnostic = "PROVIDER_EVIDENCE_PENDING"
                    worker_qualification = $cloudflare.worker_qualification
                    directed_request_ledger = $cloudflare.directed_request_ledger
                    cpu_qualification_mode = $cloudflare.cpu_qualification_mode
                    cpu_evidence = $cloudflare.cpu_evidence
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                return $false
            }
            if (-not $cloudflare.cpu_evidence.passed) {
                $platformReason = Get-WorkerPlatformFailureReason `
                    -Evidence $cloudflare.cpu_evidence
                $state.candidate.validation_state = "FAILED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"; cloudflare = "FAILED"
                    compatibility = [string]$state.candidate.compatibility_state
                    reason = $platformReason
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan; routes = $cloudflare.routes
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    observability_credential_source = $cloudflare.observability_credential_source
                    observability_diagnostic = $cloudflare.observability_diagnostic
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "FAILED" }
                    worker_failures = [pscustomobject]@{
                        state = if ([int]$cloudflare.cpu_evidence.responses_5xx -gt 0 -or
                            [int]$cloudflare.cpu_evidence.responses_1102 -gt 0) {
                            "FAILED"
                        } else { "PASSED" }
                    }
                    cpu_evidence = $cloudflare.cpu_evidence
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                Write-ReleaseHistory -Event "CANDIDATE_FAILED" `
                    -Release $state.candidate -Detail @{
                        reason = $platformReason
                        validation_run = $cloudflare.validation_run
                    }
                return $false
            }
        }
        $dataParity = Test-CandidateDataParity -Stable $state.stable `
            -Candidate $Candidate -RoutePlan $routePlan
        $authInspection = Get-CandidateAuthInspection -Candidate $Candidate
        if (-not $dataParity.passed) {
            $state.candidate.validation_state = "REVIEW_REQUIRED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PASSED"
                reason = "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED"
                data_parity = $dataParity; auth_inspection = $authInspection
                route_plan = $routePlan; routes = $cloudflare.routes
                cpu_evidence = $cloudflare.cpu_evidence
                worker_qualification = $cloudflare.worker_qualification
                cpu_qualification_mode = $cloudflare.cpu_qualification_mode
                directed_request_ledger = $cloudflare.directed_request_ledger
                validation_run = [string]$cloudflare.validation_run
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
        if ((Test-CandidateAuthBoundaryChanged -RoutePlan $routePlan) -and
            [string]$authInspection.state -ne
                "UNAUTHENTICATED_BOUNDARY_CONFIRMED") {
            $state.candidate.validation_state = "REVIEW_REQUIRED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PASSED"
                reason = "ACCESS_BOUNDARY_REVIEW_REQUIRED"
                data_parity = $dataParity; auth_inspection = $authInspection
                route_plan = $routePlan; routes = $cloudflare.routes
                cpu_evidence = $cloudflare.cpu_evidence
                worker_qualification = $cloudflare.worker_qualification
                cpu_qualification_mode = $cloudflare.cpu_qualification_mode
                directed_request_ledger = $cloudflare.directed_request_ledger
                validation_run = [string]$cloudflare.validation_run
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
        $freePlanEvidence = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
            -ValidationKey ([string]$Candidate.validation_key) -Node "free_plan"
        if (-not $freePlanEvidence) {
            $state.candidate.validation_state = "REVIEW_REQUIRED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PASSED"
                reason = "FREE_PLAN_EVIDENCE_REQUIRED"
                route_plan = $routePlan; routes = $cloudflare.routes
                cpu_evidence = $cloudflare.cpu_evidence
                worker_qualification = $cloudflare.worker_qualification
                directed_request_ledger = $cloudflare.directed_request_ledger
                validation_run = [string]$cloudflare.validation_run
                data_parity = $dataParity; auth_inspection = $authInspection
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
        $evidenceQualification = Publish-CandidateQualificationEvidence `
            -Candidate $Candidate -Stable $state.stable -Compatibility $compatibility `
            -RoutePlan $routePlan -Cloudflare $cloudflare -DataParity $dataParity `
            -AuthInspection $authInspection
        $state.candidate | Add-Member -Force -NotePropertyName evidence_authority `
            -NotePropertyValue ([pscustomobject]@{
                schema_version = "release-evidence-compatibility-projection-v1"
                state = [string]$evidenceQualification.state
                validation_key = [string]$Candidate.validation_key
                node_count = @($evidenceQualification.receipts.PSObject.Properties).Count
                projected_at = [DateTimeOffset]::UtcNow.ToString("o")
            })
        $state.candidate.compatibility_state = "PASSED"
        $state.candidate.validation_state = "PASSED"
        $state.candidate.validation = [pscustomobject]@{
            key = [string]$Candidate.validation_key
            repository = "PASSED"
            windows = "PASSED"
            cloudflare = "PASSED"
            worker_changed = $workerChanged
            cloudflare_changed = $cloudflareChanged
            route_plan = $routePlan
            routes = $cloudflare.routes
            cpu_evidence = $cloudflare.cpu_evidence
            worker_qualification = $cloudflare.worker_qualification
            cpu_qualification_mode = $cloudflare.cpu_qualification_mode
            directed_request_ledger = $cloudflare.directed_request_ledger
            validation_run = [string]$cloudflare.validation_run
            data_parity = $dataParity
            auth_inspection = $authInspection
            tested_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_PASSED" -Release $state.candidate
        return $true
    } catch {
        $state = Get-ReleaseControlState
        if ($state -and (Test-ReleaseIdentity $state.candidate $Candidate)) {
            $state.candidate.validation_state = "FAILED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                error = Protect-PreflightDiagnosticText $_.Exception.Message
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            Write-ReleaseHistory -Event "CANDIDATE_FAILED" -Release $state.candidate
        }
        return $false
    }
}

function Find-NewCandidateRelease {
    $state = Get-ReleaseControlState
    if (-not $state) { return $null }
    $mainRevision = Get-OriginMainRevision
    if (-not $mainRevision) { return $null }
    $restored = Restore-ControlPlaneOnlySupersededCandidate `
        -State $state -MainRevision $mainRevision
    if ($restored) { $state = Get-ReleaseControlState }
    $observationRecovered = Restore-ControlPlaneObservationFailedCandidate `
        -State $state -MainRevision $mainRevision
    if ($observationRecovered) { $state = Get-ReleaseControlState }
    $preserved = if ($state.candidate) {
        Get-ProductionCandidateProvenanceResult -Candidate $state.candidate
    } else { $null }
    $preservationApplies = [bool](
        $preserved -and [string]$preserved.state -eq "PASSED" -and
        [string]$preserved.mode -eq "CONTROL_PLANE_ONLY_MAIN_ADVANCE" -and
        [string]$preserved.current_main_git_sha -eq $mainRevision
    )
    $versions = @(Get-CloudflareVersions | Sort-Object `
        @{ Expression = { Get-ReleaseVersionCreatedAtValue -Version $_ } }, `
        @{ Expression = { [string]$_.id } })
    if (@($versions).Count -eq 0) {
        if ($preservationApplies) {
            Set-CandidateMaterializationState -State $state -Revision $mainRevision `
                -Status "PRESERVED" `
                -WorkerVersionId ([string]$state.candidate.worker_version_id)
            Write-ReleaseControlState -State $state
            return $state.candidate
        }
        Set-CandidateMaterializationState -State $state -Revision $mainRevision `
            -Status "PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        return $null
    }
    if (-not $state.candidate_discovery.initialized_at) {
        Set-CandidateDiscoveryWatermark -State $state -Version ($versions | Select-Object -Last 1)
        if ($preservationApplies) {
            Set-CandidateMaterializationState -State $state -Revision $mainRevision `
                -Status "PRESERVED" `
                -WorkerVersionId ([string]$state.candidate.worker_version_id)
        }
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_DISCOVERY_INITIALIZED" -Release $null `
            -Detail @{
                watermark_version_id = [string]$state.candidate_discovery.watermark_version_id
                historical_versions_eligible = $false
            }
        if ($preservationApplies) { return $state.candidate }
        return $null
    }
    $newVersions = @($versions | Where-Object {
        Test-VersionAfterDiscoveryWatermark -Version $_ -Discovery $state.candidate_discovery
    })
    if ($preservationApplies) {
        foreach ($version in $newVersions) {
            Set-CandidateDiscoveryWatermark -State $state -Version $version
        }
        Set-CandidateMaterializationState -State $state -Revision $mainRevision `
            -Status "PRESERVED" `
            -WorkerVersionId ([string]$state.candidate.worker_version_id)
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        return $state.candidate
    }
    $discovered = $null
    foreach ($version in $newVersions) {
        Set-CandidateDiscoveryWatermark -State $state -Version $version
        $sha = Get-ReleaseGitShaFromVersion -Version $version
        $artifactKind = Get-ReleaseArtifactKindFromVersion -Version $version
        if (-not $sha -or $sha -ne $mainRevision -or
            $sha -eq [string]$state.stable.git_sha -or
            $artifactKind -ne $productionCandidateArtifactKind) { continue }
        $candidate = New-ReleaseIdentity -GitSha $sha `
            -WorkerVersionId ([string]$version.id) -WindowsRevision $sha `
            -Branch (Get-ReleaseBranchFromVersion -Version $version) `
            -ArtifactKind $artifactKind `
            -VersionCreatedAt (Get-ReleaseVersionCreatedAt -Version $version)
        $candidate | Add-Member -NotePropertyName browser_url `
            -NotePropertyValue (Get-ReleaseVersionPreviewUrl `
                -Version $version -Candidate $candidate)
        $discovered = $candidate
    }
    if (-not $discovered) {
        $exactVersion = @($versions | Where-Object {
            (Get-ReleaseGitShaFromVersion -Version $_) -eq $mainRevision -and
            (Get-ReleaseBranchFromVersion -Version $_) -eq "main" -and
            (Get-ReleaseArtifactKindFromVersion -Version $_) -eq
                $productionCandidateArtifactKind
        } | Select-Object -Last 1)
        if ($exactVersion.Count -gt 0 -and $state.candidate -and
            [string]$state.candidate.git_sha -eq $mainRevision -and
            [string]$state.candidate.worker_version_id -eq [string]$exactVersion[0].id) {
            Set-CandidateMaterializationState -State $state -Revision $mainRevision `
                -Status "MATERIALIZED" -WorkerVersionId ([string]$exactVersion[0].id)
            Write-ReleaseControlState -State $state
            if ([string]$state.candidate.validation_state -eq "FAILED") {
                return $null
            }
            return $state.candidate
        }
    }
    if (-not $discovered) {
        Set-CandidateMaterializationState -State $state -Revision $mainRevision `
            -Status "PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        return $null
    }
    Set-CandidateMaterializationState -State $state -Revision $mainRevision `
        -Status "MATERIALIZED" -WorkerVersionId ([string]$discovered.worker_version_id)
    if ($state.transaction) {
        $null = Write-CandidateArtifactEvidence -Candidate $discovered `
            -Root $releaseEvidenceRoot -ContractPath $releaseEvidenceContractPath
        $state.queued_candidate = $discovered
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_QUEUED" -Release $discovered
        return $null
    }
    if ($state.candidate -and (Test-ReleaseIdentity $state.candidate $discovered)) {
        Write-ReleaseControlState -State $state
        return $state.candidate
    }
    if ($state.candidate) {
        Write-ReleaseHistory -Event "CANDIDATE_SUPERSEDED" -Release $state.candidate `
            -Detail @{ replacement_key = [string]$discovered.validation_key }
    }
    $null = Write-CandidateArtifactEvidence -Candidate $discovered `
        -Root $releaseEvidenceRoot -ContractPath $releaseEvidenceContractPath
    $state.candidate = $discovered
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_DISCOVERED" -Release $discovered
    return $discovered
}

function Approve-CandidateCompatibility {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate) {
        throw "Candidate unavailable."
    }
    $candidate = $state.candidate
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        [string]$candidate.validation.reason -ne "PLATFORM_CONFIG_REVIEW_REQUIRED" -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key -or
        -not [bool]$candidate.validation.resources_verified) {
        throw "Only an exact verified non-destructive platform review can be approved."
    }
    $approvalGate = Get-CandidateCompatibilityApprovalGate -Candidate $candidate
    if ([string]$approvalGate.state -eq "RETRYABLE") {
        throw "APPROVAL_RETRYABLE: $([string]$approvalGate.reason). Retry after repository and GitHub checks are available."
    }
    if ([string]$approvalGate.state -ne "PASSED") {
        throw "APPROVAL_REJECTED: $([string]$approvalGate.reason)."
    }
    $changed = @(Get-CandidateChangedFiles `
        -StableRevision ([string]$state.stable.git_sha) `
        -CandidateRevision ([string]$candidate.git_sha))
    $requirement = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
    if ([string]$requirement.state -ne "PLATFORM_CONFIG_REVIEW_REQUIRED" -or
        -not (Test-CandidatePlatformResources -Stable $state.stable -Candidate $candidate)) {
        throw "Candidate is not eligible for non-destructive compatibility approval."
    }
    $approval = @{
        validation_key = [string]$candidate.validation_key
        approved_at = [DateTimeOffset]::UtcNow.ToString("o")
        approved_by = [Environment]::UserName
        reason = "PLATFORM_CONFIG_REVIEW_APPROVED"
        files = @($requirement.files)
        resources_verified = $true
    }
    $candidate | Add-Member -Force -NotePropertyName compatibility_approval `
        -NotePropertyValue $approval
    $candidate.compatibility_state = "APPROVED"
    $candidate.validation_state = "NEW"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_COMPATIBILITY_APPROVED" `
        -Release $candidate -Detail $approval
    return $candidate
}

function Retry-CandidateValidation {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate) {
        throw "No Candidate is available for validation retry."
    }
    $candidate = $state.candidate
    $reason = if ($candidate.validation) {
        [string]$candidate.validation.reason
    } else { "" }
    $retryableReasons = @(
        "WORKER_CPU_HEADROOM_REVIEW_REQUIRED",
        "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED"
    )
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        $reason -notin $retryableReasons -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key -or
        [string]$candidate.validation.repository -ne "PASSED" -or
        [string]$candidate.validation.windows -ne "PASSED") {
        throw "Only an exact retryable Candidate review can restart validation."
    }
    if ($reason -eq "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED") {
        return Retry-CandidateSemanticValidation
    }
    $priorTestedAt = [string]$candidate.validation.tested_at
    if ($reason -eq "WORKER_CPU_HEADROOM_REVIEW_REQUIRED") {
        if (-not $candidate.validation.validation_run -or
            @($candidate.validation.expected_requests).Count -eq 0 -or
            -not $candidate.validation.cpu_route_plan -or
            -not $candidate.validation.worker_qualification) {
            throw "CPU review cannot resume without its exact persisted qualification ledger."
        }
        $persistedPlan = Read-WorkerCpuRunArtifact `
            -ValidationRun ([string]$candidate.validation.validation_run) `
            -Name "plan.json"
        $currentPolicy = Get-WorkerCpuEvidencePolicy
        if ($persistedPlan -and [string]$persistedPlan.policy_version -ne
            [string]$currentPolicy.version) {
            $priorPolicy = [string]$persistedPlan.policy_version
            $priorQualificationKey = [string]$candidate.validation.worker_qualification.key
            $candidate.validation_state = "NEW"
            $candidate.validation = [pscustomobject]@{
                key = [string]$candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                reason = "CPU_QUALIFICATION_POLICY_MOVED"
                prior_reason = $reason
                prior_policy_version = $priorPolicy
                prior_qualification_key = $priorQualificationKey
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $state
            Write-ReleaseHistory -Event "CANDIDATE_CPU_POLICY_MOVED" `
                -Release $candidate -Detail @{
                    validation_key=[string]$candidate.validation_key
                    prior_validation_run=[string]$persistedPlan.validation_run
                    prior_policy_version=$priorPolicy
                    current_policy_version=[string]$currentPolicy.version
                    prior_qualification_key=$priorQualificationKey
                    qualification_reused=$false
                    independent_acceptance_preserved=$true
                    fresh_cpu_matrix_required=$true
                }
            return Invoke-AutomaticCandidateValidation -Candidate $candidate
        }
        $candidate.validation_state = "PLATFORM_PENDING"
        $candidate.validation.cloudflare = "PENDING"
        $candidate.validation.reason = "PROVIDER_EVIDENCE_PENDING"
        $candidate.validation | Add-Member -NotePropertyName observability_diagnostic `
            -NotePropertyValue "PROVIDER_EVIDENCE_PENDING" -Force
        $candidate.validation | Add-Member -NotePropertyName prior_reason `
            -NotePropertyValue $reason -Force
        $candidate.validation.tested_at = [DateTimeOffset]::UtcNow.ToString("o")
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_CPU_TARGETED_RETRY_REQUESTED" `
            -Release $candidate -Detail @{
                validation_key=[string]$candidate.validation_key
                validation_run=[string]$candidate.validation.validation_run
                qualification_key=[string]$candidate.validation.worker_qualification.key
                full_matrix_replay=$false
            }
        return Invoke-AutomaticCandidateValidation -Candidate $candidate
    }
    $candidate.validation_state = "NEW"
    $candidate.validation = [pscustomobject]@{
        key = [string]$candidate.validation_key
        repository = "PASSED"
        windows = "PASSED"
        cloudflare = "PENDING"
        reason = "RETRY_REQUESTED"
        prior_reason = $reason
        prior_tested_at = $priorTestedAt
        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_VALIDATION_RETRY_REQUESTED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            prior_reason = $reason
            preserved_repository = "PASSED"
            preserved_windows = "PASSED"
            preserved_migration_acceptance = [bool]($candidate.migration_acceptance -and
                [string]$candidate.migration_acceptance.validation_key -eq
                    [string]$candidate.validation_key)
        }
    return Invoke-AutomaticCandidateValidation -Candidate $candidate
}

function Retry-CandidateSemanticValidation {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate) {
        throw "No Candidate is available for semantic retry."
    }
    $candidate = $state.candidate
    $prior = $candidate.validation
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        -not $prior -or
        [string]$prior.reason -ne "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED" -or
        [string]$prior.key -cne [string]$candidate.validation_key -or
        [string]$prior.repository -ne "PASSED" -or
        [string]$prior.windows -ne "PASSED" -or
        [string]$prior.cloudflare -ne "PASSED" -or
        -not $prior.route_plan) {
        throw "SEMANTIC_RETRY_EXACT_REVIEW_EVIDENCE_REQUIRED"
    }
    if (-not (Test-PreservedCandidateEvidenceAvailable -Candidate $candidate)) {
        throw "SEMANTIC_RETRY_PRESERVED_EVIDENCE_UNAVAILABLE"
    }
    $hasCpuQualification = [bool](
        $prior.worker_qualification -or
        ($prior.cpu_evidence -and $prior.cpu_evidence -isnot [string])
    )
    if ($hasCpuQualification) {
        if (-not $prior.worker_qualification -or -not $prior.cpu_evidence -or
            -not [bool]$prior.cpu_evidence.passed) {
            throw "SEMANTIC_RETRY_CPU_QUALIFICATION_INVALID"
        }
        if (-not $prior.directed_request_ledger) {
            throw "SEMANTIC_RETRY_DIRECTED_LEDGER_INVALID"
        }
        $null = Assert-CandidateCpuQualificationReceipt `
            -Candidate $candidate -Validation $prior
    }
    if ($prior.directed_request_ledger) {
        $ledger = $prior.directed_request_ledger
        if ([string]$ledger.evidence_class -ne "CONTROLLED_EXACT" -or
            [int]$ledger.planned -le 0 -or
            [int]$ledger.completed -ne [int]$ledger.planned -or
            [int]$ledger.passed -ne [int]$ledger.planned) {
            throw "SEMANTIC_RETRY_DIRECTED_LEDGER_INVALID"
        }
    }

    $startedAt = [DateTimeOffset]::UtcNow
    Write-ReleaseHistory -Event "CANDIDATE_SEMANTIC_RETRY_REQUESTED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            prior_tested_at = [string]$prior.tested_at
            validation_run = [string]$prior.validation_run
            directed_replayed = $false
            cpu_replayed = $false
            windows_replayed = $false
            repository_replayed = $false
        }
    $dataParity = Test-CandidateDataParity -Stable $state.stable `
        -Candidate $candidate -RoutePlan $prior.route_plan
    $authInspection = Get-CandidateAuthInspection -Candidate $candidate
    $completedAt = [DateTimeOffset]::UtcNow
    $semanticRetry = [pscustomobject]@{
        started_at = $startedAt.ToString("o")
        completed_at = $completedAt.ToString("o")
        elapsed_ms = [long][Math]::Round(($completedAt - $startedAt).TotalMilliseconds)
        execution_mode = "FRESH"
        why_ran = "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED"
        prior_tested_at = [string]$prior.tested_at
        preserved_validation_run = [string]$prior.validation_run
    }
    $next = [ordered]@{
        key = [string]$candidate.validation_key
        repository = "PASSED"
        windows = "PASSED"
        cloudflare = "PASSED"
        route_plan = $prior.route_plan
        routes = @($prior.routes)
        cpu_evidence = $prior.cpu_evidence
        worker_qualification = $prior.worker_qualification
        cpu_qualification_mode = $prior.cpu_qualification_mode
        directed_request_ledger = $prior.directed_request_ledger
        validation_run = [string]$prior.validation_run
        data_parity = $dataParity
        auth_inspection = $authInspection
        semantic_retry = $semanticRetry
        tested_at = $completedAt.ToString("o")
    }
    $event = "CANDIDATE_SEMANTIC_RETRY_PASSED"
    $passed = $false
    if (-not [bool]$dataParity.passed) {
        $candidate.validation_state = "REVIEW_REQUIRED"
        $next.reason = "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED"
        $event = "CANDIDATE_SEMANTIC_RETRY_REVIEW_REQUIRED"
    } elseif ((Test-CandidateAuthBoundaryChanged -RoutePlan $prior.route_plan) -and
        [string]$authInspection.state -ne "UNAUTHENTICATED_BOUNDARY_CONFIRMED") {
        $candidate.validation_state = "REVIEW_REQUIRED"
        $next.reason = "ACCESS_BOUNDARY_REVIEW_REQUIRED"
        $event = "CANDIDATE_ACCESS_BOUNDARY_REVIEW_REQUIRED"
    } else {
        $candidate.compatibility_state = "PASSED"
        $candidate.validation_state = "PASSED"
        $next.reason = "SEMANTIC_DATA_PARITY_PASSED"
        $passed = $true
    }
    $candidate.validation = [pscustomobject]$next
    $state.updated_at = $completedAt.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event $event -Release $candidate -Detail @{
        validation_key = [string]$candidate.validation_key
        validation_run = [string]$prior.validation_run
        elapsed_ms = [long]$semanticRetry.elapsed_ms
        directed_replayed = $false
        cpu_replayed = $false
        result = [string]$candidate.validation.reason
    }
    return $passed
}

function Invoke-CandidateDiscovery {
    if (-not (Enter-ReleaseTransactionLock)) { return $false }
    try {
        $state = Get-ReleaseControlState
        if ($state) {
            $state.last_candidate_check = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $state
        }
        $null = Reconcile-ReleaseControlState
        $state = Get-ReleaseControlState
        $candidate = Find-NewCandidateRelease
        if (-not $candidate) {
            $state = Get-ReleaseControlState
            $materialization = if ($state) { $state.candidate_materialization } else { $null }
            if ($state -and -not $state.transaction -and $state.candidate -and
                $materialization -and
                [string]$materialization.state -eq "MATERIALIZED" -and
                [string]$materialization.revision -eq [string]$state.candidate.git_sha -and
                [string]$materialization.worker_version_id -eq
                    [string]$state.candidate.worker_version_id) {
                $candidate = $state.candidate
            }
        }
        if (-not $candidate) { return $false }
        if ([string]$candidate.validation_state -in @(
            "PASSED", "FAILED", "REVIEW_REQUIRED", "REBASE_REQUIRED"
        )) { return $true }
        return Invoke-AutomaticCandidateValidation -Candidate $candidate
    } finally { Exit-ReleaseTransactionLock }
}

function Start-CandidateDiscovery {
    $state = Get-ReleaseControlState
    if (-not $state) { return }
    $lastCheck = ConvertTo-ReleaseTimestampUtc -Value $state.last_candidate_check
    if (([DateTimeOffset]::UtcNow - $lastCheck) -lt $candidateDiscoveryInterval) { return }
    $arguments = @(
        "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $controlCenterEntrypointPath), "-Action", "DiscoverCandidate",
        "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
        "-RepositoryRoot", ('"{0}"' -f $repositoryRoot)
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
        -WorkingDirectory $moduleRoot -WindowStyle Hidden | Out-Null
}

function Write-RuntimeUpdateFailure {
    param(
        [string]$Revision,
        [string]$Status,
        [string]$Message,
        [string]$ErrorCode = "RUNTIME_UPDATE_FAILED",
        [string]$Phase = "UNKNOWN",
        [hashtable]$Diagnostics = @{}
    )
    Write-RuntimeUpdateState @{
        update_status = $Status
        failed_revision = $Revision
        failed_preflight_contract = $runtimePreflightContractVersion
        failed_at = [DateTimeOffset]::UtcNow.ToString("o")
        user_visible_failure = $true
        failure_message = $Message
        failure_code = $ErrorCode
        failure_phase = $Phase
        preflight_diagnostics = $Diagnostics
    }
    Write-WatchdogEvent -Event "RUNTIME_UPDATE_FAILED" -Service "all" -State $Message
}

function Write-DeferredProjectionSyncRequest {
    param(
        [Parameter(Mandatory = $true)][object]$Transaction,
        [Parameter(Mandatory = $true)][DateTimeOffset]$RequiredAfter
    )
    $obligations = @($Transaction.deferred_projection_obligations |
        Where-Object { $null -ne $_ })
    if ($obligations.Count -eq 0) { return $null }
    $routes = @($obligations | ForEach-Object { [string]$_.route })
    if (@($routes | Select-Object -Unique).Count -ne $routes.Count -or
        @($routes | Where-Object { $_ -notin $candidateOnlyProjectionRoutes }).Count) {
        throw "DEFERRED_PROJECTION_SYNC_REQUEST_INVALID"
    }
    $target = $Transaction.target
    try {
        $transactionId = ([guid]::Parse([string]$Transaction.id)).ToString()
        $workerVersionId = ([guid]::Parse(
            [string]$target.worker_version_id
        )).ToString()
    } catch {
        throw "DEFERRED_PROJECTION_SYNC_IDENTITY_INVALID"
    }
    if (-not $target -or
        [string]$target.windows_revision -notmatch '^[0-9a-f]{40}$' -or
        [string]::IsNullOrWhiteSpace([string]$target.validation_key) -or
        @($obligations | Where-Object {
            [string]$_.validation_key -ne [string]$target.validation_key -or
            [string]$_.required_producer_revision -ne
                [string]$target.windows_revision
        }).Count -gt 0) {
        throw "DEFERRED_PROJECTION_SYNC_IDENTITY_INVALID"
    }
    $request = [ordered]@{
        schema_version = "deferred-projection-sync-v1"
        request_id = [guid]::NewGuid().ToString()
        transaction_id = $transactionId
        validation_key = [string]$target.validation_key
        worker_version_id = $workerVersionId
        producer_revision = [string]$target.windows_revision
        target = "cloudflare"
        required_after = $RequiredAfter.ToUniversalTime().ToString("o")
        routes = @($routes)
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-ControlCenterJsonAtomic -Path $deferredProjectionSyncRequestPath `
        -Value $request -Depth 6
    return [pscustomobject]$request
}

function Cancel-DeferredProjectionSyncRequest {
    param([string]$FailedRevision)
    if (-not (Test-Path -LiteralPath $deferredProjectionSyncRequestPath)) { return }
    try {
        $request = Get-Content -LiteralPath $deferredProjectionSyncRequestPath `
            -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
        if ([string]$request.producer_revision -ne $FailedRevision) { return }
        $cancelled = [ordered]@{
            request = $request
            state = "CANCELLED_BY_ROLLBACK"
            cancelled_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        Write-ControlCenterJsonAtomic -Path $deferredProjectionSyncCancelledPath `
            -Value $cancelled -Depth 8
        Remove-Item -LiteralPath $deferredProjectionSyncRequestPath -Force
    } catch {
        throw "DEFERRED_PROJECTION_SYNC_CANCEL_FAILED:$($_.Exception.Message)"
    }
}

function New-PromotionFreshnessStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][DateTimeOffset]$StartedAt,
        [Parameter(Mandatory = $true)][string]$ExecutionMode,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$ReceiptDigest = ""
    )
    $completedAt = [DateTimeOffset]::UtcNow
    return [pscustomobject]@{
        name = $Name
        state = $State
        execution_mode = $ExecutionMode
        receipt_digest = $ReceiptDigest
        started_at = $StartedAt.ToString("o")
        completed_at = $completedAt.ToString("o")
        elapsed_ms = [long][Math]::Round(($completedAt - $StartedAt).TotalMilliseconds)
    }
}

function Test-CurrentBusinessRuntimeHealth {
    try {
        $response = Invoke-WebRequest -UseBasicParsing `
            -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2
        return [int]$response.StatusCode -eq 200
    } catch { return $false }
}

function Test-CurrentStableRuntimeHealth {
    return [bool]((Test-CurrentBusinessRuntimeHealth) -and
        (Test-SingleProductionOwner))
}

function Invoke-PromotionFreshnessCoordinator {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [switch]$AllowDegradedActive
    )
    $startedAt = [DateTimeOffset]::UtcNow
    $steps = @()
    $candidate = $State.candidate
    $stable = $State.stable
    $currentStepName = "identity"
    $currentStepStarted = $startedAt
    try {
        if (-not $candidate -or -not $stable -or $State.transaction -or
            [string]$candidate.validation_key -cne
                "$([string]$candidate.worker_version_id):$([string]$candidate.git_sha)") {
            throw "PROMOTION_FRESHNESS_IDENTITY_INVALID"
        }

        $stepStarted = [DateTimeOffset]::UtcNow
        $currentStepName = "migration_live_lease"
        $currentStepStarted = $stepStarted
        $changed = @(Get-CandidateChangedFiles `
            -StableRevision ([string]$stable.git_sha) `
            -CandidateRevision ([string]$candidate.git_sha))
        $compatibility = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
        if ([string]$compatibility.state -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
            if (-not $candidate.migration_acceptance -or
                [string]$candidate.migration_acceptance.validation_key -cne
                    [string]$candidate.validation_key) {
                throw "MIGRATION_ACCEPTANCE_MISSING"
            }
            $beforeDigest = if ($candidate.migration_qualification) {
                [string]$candidate.migration_qualification.receipt_digest
            } else { [string]$candidate.migration_acceptance.receipt_digest }
            $qualification = Ensure-CoordinatedMigrationQualification `
                -Candidate $candidate -Stable $stable `
                -MigrationFiles @($compatibility.files) `
                -MinimumRemaining $promotionFreshnessMinimumLifetime
            if ([string]$qualification.root_receipt_digest -cne
                [string]$candidate.migration_acceptance.receipt_digest) {
                throw "MIGRATION_RECEIPT_AUTHORITY_MISMATCH"
            }
            $afterDigest = [string]$qualification.receipt.receipt_digest
            $mode = if ($afterDigest -cne $beforeDigest) { "RENEWED" } else { "REUSED" }
            $steps += New-PromotionFreshnessStep -Name "migration_live_lease" `
                -StartedAt $stepStarted -ExecutionMode $mode `
                -State ([string]$qualification.state) -ReceiptDigest $afterDigest
            $State.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $State
        } else {
            $steps += New-PromotionFreshnessStep -Name "migration_live_lease" `
                -StartedAt $stepStarted -ExecutionMode "NOT_REQUIRED" -State "NOT_REQUIRED"
        }

        $stepStarted = [DateTimeOffset]::UtcNow
        $currentStepName = "access_provider_lease"
        $currentStepStarted = $stepStarted
        $accessRoot = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
            -ValidationKey ([string]$candidate.validation_key) -Node "human_access_root"
        if (-not $accessRoot) {
            throw "RELEASE_EVIDENCE_PRODUCER_MISSING:human_access_root"
        }
        $accessRequired = [string]$accessRoot.source_identity.qualification_state -ne
            "NOT_REQUIRED"
        $steps += New-PromotionFreshnessStep -Name "access_provider_lease" `
            -StartedAt $stepStarted `
            -ExecutionMode $(if ($accessRequired) { "FRESH" } else { "NOT_REQUIRED" }) `
            -State $(if ($accessRequired) { "PENDING_ACTION_TIME_RENEWAL" } else { "NOT_REQUIRED" }) `
            -ReceiptDigest ([string]$accessRoot.receipt_digest)

        $stepStarted = [DateTimeOffset]::UtcNow
        $currentStepName = "candidate_placement"
        $currentStepStarted = $stepStarted
        if (-not (Test-CloudflareReleasePlacement -Stable $stable -Candidate $candidate)) {
            throw "Cloudflare Stable/Candidate placement drifted."
        }
        $steps += New-PromotionFreshnessStep -Name "candidate_placement" `
            -StartedAt $stepStarted -ExecutionMode "FRESH" -State "PASSED"

        $stepStarted = [DateTimeOffset]::UtcNow
        $currentStepName = "rollback_precheck"
        $currentStepStarted = $stepStarted
        if (-not (Test-CloudflareRollbackTarget -Target $stable)) {
            throw "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        }
        $steps += New-PromotionFreshnessStep -Name "rollback_precheck" `
            -StartedAt $stepStarted -ExecutionMode "FRESH" -State "PASSED"

        $stepStarted = [DateTimeOffset]::UtcNow
        $currentStepName = "current_owner_health"
        $currentStepStarted = $stepStarted
        $runtime = Get-RuntimeCodeState
        if (-not $runtime -or
            [string]$runtime.applied_revision -cne [string]$stable.windows_revision) {
            throw "Windows Stable revision drifted."
        }
        if ($AllowDegradedActive) {
            $runtimeAuthority = Get-CurrentReleaseRuntimeReadModel `
                -PersistedState $State -ReleaseLockOwnedByCaller -ForceProviderRefresh
            $null = Assert-RecoveryRuntimeAuthority -RuntimeReadModel $runtimeAuthority `
                -RecoveryAction "APPLY_RECOVERY_HOTFIX"
        } elseif (-not (Test-CurrentStableRuntimeHealth)) {
            throw "CURRENT_STABLE_RUNTIME_UNHEALTHY"
        }
        $steps += New-PromotionFreshnessStep -Name "current_owner_health" `
            -StartedAt $stepStarted -ExecutionMode "FRESH" -State "PASSED"

        $currentStepName = "release_evidence_authority"
        $currentStepStarted = [DateTimeOffset]::UtcNow
        $evidenceQualification = Publish-PromotionFreshnessEvidence -State $State `
            -AllowDegradedActive:$AllowDegradedActive
        $steps += New-PromotionFreshnessStep -Name "release_evidence_authority" `
            -StartedAt $currentStepStarted -ExecutionMode "FRESH" `
            -State ([string]$evidenceQualification.state)

        $completedAt = [DateTimeOffset]::UtcNow
        $summary = [pscustomobject]@{
            schema_version = "promotion-freshness-coordinator-v1"
            validation_key = [string]$candidate.validation_key
            minimum_remaining_seconds =
                [int]$promotionFreshnessMinimumLifetime.TotalSeconds
            state = "PASSED"
            started_at = $startedAt.ToString("o")
            completed_at = $completedAt.ToString("o")
            elapsed_ms = [long][Math]::Round(($completedAt - $startedAt).TotalMilliseconds)
            steps = @($steps)
        }
        $candidate | Add-Member -Force -NotePropertyName promotion_freshness `
            -NotePropertyValue $summary
        $State.updated_at = $completedAt.ToString("o")
        Write-ReleaseControlState -State $State
        Write-ReleaseHistory -Event "PROMOTION_FRESHNESS_PASSED" `
            -Release $candidate -Detail @{
                schema_version = [string]$summary.schema_version
                validation_key = [string]$summary.validation_key
                minimum_remaining_seconds = [int]$summary.minimum_remaining_seconds
                state = [string]$summary.state
                started_at = [string]$summary.started_at
                completed_at = [string]$summary.completed_at
                elapsed_ms = [long]$summary.elapsed_ms
                steps = @($summary.steps)
            }
        return $summary
    } catch {
        $failureReason = Protect-PreflightDiagnosticText $_.Exception.Message
        $steps += New-PromotionFreshnessStep -Name $currentStepName `
            -StartedAt $currentStepStarted -ExecutionMode "ATTEMPTED" `
            -State "FAILED"
        $failedAt = [DateTimeOffset]::UtcNow
        $failedSummary = [pscustomobject]@{
            schema_version = "promotion-freshness-coordinator-v1"
            validation_key = [string]$candidate.validation_key
            minimum_remaining_seconds =
                [int]$promotionFreshnessMinimumLifetime.TotalSeconds
            state = "FAILED"
            reason = $failureReason
            started_at = $startedAt.ToString("o")
            completed_at = $failedAt.ToString("o")
            elapsed_ms = [long][Math]::Round(($failedAt - $startedAt).TotalMilliseconds)
            steps = @($steps)
        }
        if ($candidate -and -not $State.transaction) {
            $candidate | Add-Member -Force -NotePropertyName promotion_freshness `
                -NotePropertyValue $failedSummary
            $State.updated_at = $failedAt.ToString("o")
            Write-ReleaseControlState -State $State
        }
        Write-ReleaseHistory -Event "PROMOTION_FRESHNESS_FAILED" `
            -Release $candidate -Detail @{
                validation_key = [string]$candidate.validation_key
                started_at = [string]$failedSummary.started_at
                completed_at = [string]$failedSummary.completed_at
                elapsed_ms = [long]$failedSummary.elapsed_ms
                reason = $failureReason
                completed_steps = @($steps)
            }
        throw
    }
}

function Start-ReleasePromotion {
    param(
        [ValidateSet("NORMAL", "RECOVERY_HOTFIX")][string]$Mode = "NORMAL",
        [string]$RecoveryReason = ""
    )
    if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or $state.transaction) { throw "Release state is not ready." }
        $candidate = $state.candidate
        if (-not $candidate) { throw "Candidate is unavailable." }
        if ([string]$candidate.artifact_kind -ne $productionCandidateArtifactKind) {
            throw "Preview and unknown artifacts cannot be promoted."
        }
        Assert-ActiveControlBundle
        if (-not (Test-ProductionCandidateProvenance -Candidate $candidate)) {
            throw "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED"
        }
        if ([string]$candidate.validation_key -ne
                "$([string]$candidate.worker_version_id):$([string]$candidate.git_sha)") {
            throw "Candidate evidence key does not belong to this release."
        }
        if ([string]$candidate.git_sha -ne [string]$candidate.windows_revision) {
            throw "Worker and Windows identity is inconsistent."
        }
        if ([string]$state.deployment_status -ne "READY") {
            throw "Release control is not deployment-ready."
        }
        $recoveryEligibility = $null
        if ($Mode -eq "RECOVERY_HOTFIX") {
            $changed = @(Get-CandidateChangedFiles `
                -StableRevision ([string]$state.stable.git_sha) `
                -CandidateRevision ([string]$candidate.git_sha))
            $changePlan = Get-ReleaseEvidenceChangePlan `
                -OwnershipPath $releaseEvidenceChangeOwnershipPath -ChangedFiles $changed
            $recoveryEligibility = Get-RecoveryHotfixEligibility -ChangePlan $changePlan
            if (-not $recoveryEligibility.eligible) {
                throw "RECOVERY_HOTFIX_BLOCKED:$([string]$recoveryEligibility.reason)"
            }
            if ([string]::IsNullOrWhiteSpace($RecoveryReason)) {
                $RecoveryReason = "EXPLICIT_OPERATOR_RECOVERY_HOTFIX"
            }
        }
        $null = Invoke-PromotionFreshnessCoordinator -State $state `
            -AllowDegradedActive:($Mode -eq "RECOVERY_HOTFIX")
        $state = Get-ReleaseControlState
        $candidate = $state.candidate
        $qualification = Assert-ReleaseEvidenceQualification `
            -Root $releaseEvidenceRoot -ContractPath $releaseEvidenceContractPath `
            -ValidationKey ([string]$candidate.validation_key)
        $recoveryReceiptRefs = if ($Mode -eq "RECOVERY_HOTFIX") {
            Get-RecoveryEvidenceReceiptReferences -Qualification $qualification
        } else { $null }
        $semanticReceipt = $qualification.receipts.semantic_contract
        $deferredObligations = @($semanticReceipt.source_identity.subject.deferred_obligations |
            Where-Object { $null -ne $_ })
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
        $transactionId = [guid]::NewGuid().ToString()
        $dependencyReceipts = [ordered]@{}
        foreach ($node in $releaseEvidencePromotionDependencyNodes) {
            $dependencyReceipts[$node] = [string]$qualification.receipt_digests.$node
        }
        $targetIdentity = [pscustomobject][ordered]@{
            validation_key = [string]$candidate.validation_key
            worker_version_id = [string]$candidate.worker_version_id
            git_sha = [string]$candidate.git_sha
            windows_revision = [string]$candidate.windows_revision
            artifact_kind = [string]$candidate.artifact_kind
            release_mode = $Mode
            recovery_action = if ($Mode -eq "RECOVERY_HOTFIX") {
                "APPLY_RECOVERY_HOTFIX"
            } else { $null }
        }
        $promoteInput = [pscustomobject][ordered]@{
            transaction_id = $transactionId
            target_identity = $targetIdentity
            dependency_receipts = [pscustomobject]$dependencyReceipts
        }
        $promoteNow = [DateTimeOffset]::UtcNow
        $promoteArguments = New-ReleaseEvidenceAdapterArguments -Candidate $candidate `
            -BehaviorInputs $promoteInput -SourceIdentity ([pscustomobject]@{
                qualification_state = "PASSED"; transaction_id = $transactionId
                target_identity = $targetIdentity
                dependency_receipts = [pscustomobject]$dependencyReceipts
            }) -StartedAt $promoteNow -CompletedAt $promoteNow `
            -WhyRan "PROMOTE_TRANSACTION_DEPENDENCIES_FROZEN"
        $promoteReceipt = Publish-PromoteAttemptEvidence -Arguments $promoteArguments
        $transaction = [pscustomobject]@{
            id = $transactionId
            type = "PROMOTE"
            phase = "PRECHECK"
            mode = $Mode
            recovery_reason = if ($Mode -eq "RECOVERY_HOTFIX") {
                $RecoveryReason
            } else { $null }
            recovery_action = if ($Mode -eq "RECOVERY_HOTFIX") {
                "APPLY_RECOVERY_HOTFIX"
            } else { $null }
            eligibility_class = if ($Mode -eq "RECOVERY_HOTFIX") {
                [string]$recoveryEligibility.eligibility_class
            } else { "NORMAL_RELEASE" }
            evidence_receipt_refs = $recoveryReceiptRefs
            observe_contract = if ($Mode -eq "RECOVERY_HOTFIX") {
                $recoveryEligibility.observe_contract
            } else { $null }
            target = $candidate
            previous = $state.stable
            recovery_plan = New-RuntimeRecoveryPlan `
                -StableRevision ([string]$state.stable.windows_revision) `
                -ReleaseState $state -ServiceContracts @($services)
            deferred_projection_obligations = $deferredObligations
            evidence_authority = [pscustomobject]@{
                schema_version = "release-evidence-transaction-authority-v1"
                validation_key = [string]$candidate.validation_key
                promote_receipt_digest = [string]$promoteReceipt.receipt_digest
                dependency_receipts = [pscustomobject]$dependencyReceipts
                target_identity = $targetIdentity
            }
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
        # Candidate projection content may begin as soon as the Candidate Windows
        # revision is active. Sync remains deferred until after Worker cutover, so
        # publication ordering is enforced independently from content freshness.
        $projectionBoundary = $reloadStarted
        if ($deferredObligations.Count -gt 0) {
            $null = Write-DeferredProjectionSyncRequest `
                -Transaction $state.transaction -RequiredAfter $projectionBoundary
        }
        Complete-DeferredServiceReload -ReloadStarted $reloadStarted `
            -DeferredServiceKeys @("sync")
        if ($deferredObligations.Count -gt 0) {
            Start-RuntimeObservation -Revision ([string]$candidate.windows_revision) `
                -PreviousRevision ([string]$state.stable.windows_revision) `
                -HealthBoundary $reloadStarted `
                -DeferredProjectionObligations $deferredObligations `
                -ValidationKey ([string]$candidate.validation_key) `
                -ProjectionBoundary $projectionBoundary `
                -Mode $(if ($Mode -eq "RECOVERY_HOTFIX") { "RECOVERY_HOTFIX" } else { "PROMOTE" })
        } else {
            Start-RuntimeObservation -Revision ([string]$candidate.windows_revision) `
                -PreviousRevision ([string]$state.stable.windows_revision) `
                -HealthBoundary $reloadStarted `
                -Mode $(if ($Mode -eq "RECOVERY_HOTFIX") { "RECOVERY_HOTFIX" } else { "PROMOTE" })
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
        $authority = $state.transaction.evidence_authority
        if (-not $authority -or
            [string]$authority.validation_key -cne [string]$target.validation_key -or
            [string]$authority.target_identity.worker_version_id -cne
                [string]$target.worker_version_id -or
            [string]$authority.target_identity.git_sha -cne [string]$target.git_sha -or
            [string]$authority.target_identity.windows_revision -cne
                [string]$target.windows_revision) {
            throw "PROMOTE_EVIDENCE_AUTHORITY_MISMATCH"
        }
        $promoteReceipt = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
            -ValidationKey ([string]$target.validation_key) -Node "promote_attempt"
        if (-not $promoteReceipt -or
            [string]$promoteReceipt.receipt_digest -cne
                [string]$authority.promote_receipt_digest -or
            [string]$promoteReceipt.source_identity.subject.transaction_id -cne
                [string]$state.transaction.id) {
            throw "PROMOTE_EVIDENCE_RECEIPT_INVALID"
        }
        $runtimeObservation = Get-RuntimeUpdateState
        $observeInput = [pscustomobject][ordered]@{
            transaction_id = [string]$state.transaction.id
            target_identity = $authority.target_identity
            observe_contract = [pscustomobject][ordered]@{
                terminal_state = "PASSED"
                runtime_update_status = [string]$runtimeObservation.update_status
                deferred_projection_state = if ($deferred.Count -gt 0) {
                    [string]$runtimeObservation.observation_deferred_projection_state
                } else { "NOT_REQUIRED" }
                observation_cycles = $runtimeObservationCycles
            }
        }
        $observeNow = [DateTimeOffset]::UtcNow
        $observeArguments = New-ReleaseEvidenceAdapterArguments -Candidate $target `
            -BehaviorInputs $observeInput -SourceIdentity ([pscustomobject]@{
                qualification_state = "PASSED"
                transaction_id = [string]$state.transaction.id
                target_identity = $authority.target_identity
                terminal_state = "PASSED"
            }) -StartedAt $observeNow -CompletedAt $observeNow `
            -WhyRan "OBSERVE_TERMINAL_PASSED"
        $observeReceipt = Publish-ObserveAttemptEvidence -Arguments $observeArguments
        $state.transaction.evidence_authority | Add-Member -Force `
            -NotePropertyName observe_receipt_digest `
            -NotePropertyValue ([string]$observeReceipt.receipt_digest)
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

function Invoke-RestoreLastKnownGood {
    param([string]$RecoveryReason = "ACTIVE_COMMITTED_DRIFT")
    if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or $state.transaction -or -not $state.stable) {
            throw "RECOVERY_LKG_UNAVAILABLE"
        }
        Assert-ActiveControlBundle
        $runtimeReadModel = Get-CurrentReleaseRuntimeReadModel `
            -PersistedState $state -ReleaseLockOwnedByCaller -ForceProviderRefresh
        $authority = Assert-RecoveryRuntimeAuthority -RuntimeReadModel $runtimeReadModel `
            -RecoveryAction "RESTORE_LKG"
        $target = $state.stable
        if (-not (Test-ReleaseRuntimeIdentityEqual $target $authority.last_known_good) -or
            -not (Test-CloudflareRollbackTarget -Target $target)) {
            throw "RECOVERY_LKG_ARTIFACT_UNAVAILABLE"
        }
        if ([string]::IsNullOrWhiteSpace([string]$target.validation_key)) {
            throw "RECOVERY_LKG_EVIDENCE_KEY_UNAVAILABLE"
        }
        $qualification = Publish-PromotionFreshnessEvidence -State $state `
            -AllowDegradedActive -RecoveryAction "RESTORE_LKG" `
            -RuntimeReadModel $runtimeReadModel
        $receiptRefs = Get-RecoveryEvidenceReceiptReferences -Qualification $qualification
        $transactionId = [guid]::NewGuid().ToString()
        $targetIdentity = [pscustomobject][ordered]@{
            validation_key = [string]$target.validation_key
            worker_version_id = [string]$target.worker_version_id
            git_sha = [string]$target.git_sha
            windows_revision = [string]$target.windows_revision
            artifact_kind = [string]$target.artifact_kind
            release_mode = "RECOVERY_HOTFIX"
            recovery_action = "RESTORE_LKG"
        }
        $promoteInput = [pscustomobject][ordered]@{
            transaction_id = $transactionId
            target_identity = $targetIdentity
            dependency_receipts = $receiptRefs
        }
        $now = [DateTimeOffset]::UtcNow
        $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $target `
            -BehaviorInputs $promoteInput -SourceIdentity ([pscustomobject]@{
                qualification_state = "PASSED"; transaction_id = $transactionId
                target_identity = $targetIdentity; dependency_receipts = $receiptRefs
            }) -StartedAt $now -CompletedAt $now `
            -WhyRan "RESTORE_LKG_TRANSACTION_DEPENDENCIES_FROZEN"
        $promoteReceipt = Publish-PromoteAttemptEvidence -Arguments $arguments
        $observeContract = [pscustomobject][ordered]@{
            schema_version = "recovery-hotfix-observe-v1"
            budget_class = "SHORT_BOUNDED"
            required_consecutive_health_cycles = 2
            require_exact_post_switch_identity = $true
            require_affected_directed_routes = $true
            require_zero_resource_failures = $true
            require_single_owner = $true
            require_affected_projection_parity = $true
            require_rollback_readiness = $true
        }
        $state.transaction = [pscustomobject]@{
            id = $transactionId; type = "RECOVERY"; phase = "CUTOVER"
            mode = "RECOVERY_HOTFIX"; recovery_reason = $RecoveryReason
            recovery_action = "RESTORE_LKG"; eligibility_class = "RESTORE_COMMITTED_LKG"
            evidence_receipt_refs = $receiptRefs; observe_contract = $observeContract
            target = $target; previous = $target
            active_before = $authority.active
            recovery_plan = New-RuntimeRecoveryPlan `
                -StableRevision ([string]$target.windows_revision) `
                -ReleaseState $state -ServiceContracts @($services)
            evidence_authority = [pscustomobject]@{
                schema_version = "release-evidence-transaction-authority-v1"
                validation_key = [string]$target.validation_key
                promote_receipt_digest = [string]$promoteReceipt.receipt_digest
                dependency_receipts = $receiptRefs
                target_identity = $targetIdentity
            }
            started_at = $now.ToString("o")
        }
        $state.deployment_status = "RECOVERING_LKG"
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "RECOVERY_LKG_STARTED" -Release $target `
            -Detail @{ transaction_id = $transactionId; recovery_reason = $RecoveryReason }
        Invoke-CloudflareDeployment -StableVersionId ([string]$target.worker_version_id) `
            -Message "restore committed lkg $transactionId"
        Invoke-ReleaseWindowsRestore -Revision ([string]$target.windows_revision)
        if (-not (Test-SingleProductionOwner)) { throw "RECOVERY_SINGLE_OWNER_REQUIRED" }
        Start-RuntimeObservation -Revision ([string]$target.windows_revision) `
            -PreviousRevision ([string]$target.windows_revision) -Mode "RESTORE_LKG" `
            -ValidationKey ([string]$target.validation_key)
        $state = Get-ReleaseControlState
        $state.transaction.phase = "OBSERVING"
        $state.deployment_status = "RECOVERY_OBSERVING"
        Write-ReleaseControlState -State $state
        return $true
    } catch {
        $state = Get-ReleaseControlState
        if ($state -and $state.transaction -and
            [string]$state.transaction.type -eq "RECOVERY") {
            $null = Invoke-RuntimeRollback `
                -FailedRevision ([string]$state.transaction.target.windows_revision) `
                -PreviousRevision ([string]$state.transaction.previous.windows_revision) `
                -Reason ([string]$_.Exception.Message)
        }
        throw
    } finally { Exit-ReleaseTransactionLock }
}

function Complete-ReleaseRecovery {
    $releaseLockAcquiredHere = $false
    if (-not $script:releaseTransactionLockHeld) {
        if (-not (Enter-ReleaseTransactionLock)) { return }
        $releaseLockAcquiredHere = $true
    }
    try {
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.transaction) { return }
        if ([string]$state.transaction.type -eq "PROMOTE" -and
            (Get-ReleaseTransactionMode $state.transaction) -eq "RECOVERY_HOTFIX") {
            Complete-ReleasePromotion
            return
        }
        if ([string]$state.transaction.type -ne "RECOVERY" -or
            [string]$state.transaction.phase -ne "OBSERVING") { return }
        $target = $state.transaction.target
        $authority = $state.transaction.evidence_authority
        if (-not $authority -or
            [string]$authority.validation_key -cne [string]$target.validation_key -or
            [string]$authority.target_identity.worker_version_id -cne
                [string]$target.worker_version_id -or
            [string]$authority.target_identity.git_sha -cne [string]$target.git_sha -or
            [string]$authority.target_identity.windows_revision -cne
                [string]$target.windows_revision) {
            throw "RECOVERY_EVIDENCE_AUTHORITY_MISMATCH"
        }
        $cycle = Test-RecoveryShortObservationCycle -ReleaseState $state `
            -RuntimeState (Get-RuntimeUpdateState)
        if (-not $cycle.passed) { throw [string]$cycle.reason }
        $promoteReceipt = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
            -ValidationKey ([string]$target.validation_key) -Node "promote_attempt"
        if (-not $promoteReceipt -or
            [string]$promoteReceipt.receipt_digest -cne
                [string]$authority.promote_receipt_digest -or
            [string]$promoteReceipt.source_identity.subject.transaction_id -cne
                [string]$state.transaction.id) {
            throw "RECOVERY_PROMOTE_EVIDENCE_RECEIPT_INVALID"
        }
        $observeInput = [pscustomobject][ordered]@{
            transaction_id = [string]$state.transaction.id
            target_identity = $authority.target_identity
            observe_contract = [pscustomobject][ordered]@{
                terminal_state = "PASSED"; recovery_action = "RESTORE_LKG"
                observation_cycles = 2; committed_stable_unchanged = $true
            }
        }
        $now = [DateTimeOffset]::UtcNow
        $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $target `
            -BehaviorInputs $observeInput -SourceIdentity ([pscustomobject]@{
                qualification_state = "PASSED"; transaction_id = [string]$state.transaction.id
                target_identity = $authority.target_identity; terminal_state = "PASSED"
            }) -StartedAt $now -CompletedAt $now -WhyRan "RECOVERY_LKG_OBSERVE_PASSED"
        $null = Publish-ObserveAttemptEvidence -Arguments $arguments
        $state.transaction = $null
        $state.deployment_status = "READY"
        $state.drift = $null
        $state.updated_at = $now.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "RECOVERY_LKG_OBSERVED" -Release $target
    } finally {
        if ($releaseLockAcquiredHere) { Exit-ReleaseTransactionLock }
    }
}

function Invoke-ReleaseWindowsRestore {
    param([Parameter(Mandatory = $true)][string]$Revision)
    & git -C $moduleRoot checkout --detach --force --quiet $Revision 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot restore Windows revision." }
    $script:services = @(Resolve-ServiceLaunchContracts -Revision $Revision)
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
        # The GUI value is advisory only. Re-read exact provider, runtime,
        # bundle, ownership, transaction, and lock facts while this caller owns
        # the serialized action boundary, before creating a transaction.
        $runtimeReadModel = Get-CurrentReleaseRuntimeReadModel `
            -PersistedState $state -ReleaseLockOwnedByCaller `
            -ForceProviderRefresh
        $reversePrecheck = if ($runtimeReadModel -and $runtimeReadModel.previous) {
            $runtimeReadModel.previous.reverse_precheck
        } else { $null }
        if (-not $reversePrecheck -or -not [bool]$reversePrecheck.can_reverse) {
            $reason = if ($reversePrecheck) {
                [string]$reversePrecheck.reason
            } else { "RUNTIME_READ_MODEL_UNAVAILABLE" }
            throw "REVERSE_PRECHECK_BLOCKED:$reason"
        }
        $current = $runtimeReadModel.committed_stable
        $target = $runtimeReadModel.previous_committed
        if (-not $current -or -not $target) {
            throw "REVERSE_PRECHECK_BLOCKED:VALIDATED_IDENTITY_UNAVAILABLE"
        }
        $state.transaction = [pscustomobject]@{
            id = [guid]::NewGuid().ToString()
            type = "REVERSE"
            phase = "REVERSING"
            target = $target
            previous = $current
            recovery_plan = New-RuntimeRecoveryPlan `
                -StableRevision ([string]$current.windows_revision) `
                -ReleaseState $state -ServiceContracts @($services)
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
        $failure = $_.Exception.Message
        $state = Get-ReleaseControlState
        if ($state -and $state.transaction -and $state.transaction.recovery_plan) {
            try {
                $recoveryStarted = [DateTimeOffset]::UtcNow
                $null = Restore-RuntimeRecoveryPlan -Plan $state.transaction.recovery_plan
                $null = Wait-RuntimeRecoveryPlanHealth `
                    -Plan $state.transaction.recovery_plan `
                    -RecoveryStarted $recoveryStarted
                Invoke-CloudflareDeployment `
                    -StableVersionId ([string]$state.transaction.previous.worker_version_id) `
                    -Message "automatic reverse recovery $([string]$state.transaction.id)"
                $state.transaction = $null
                $state.deployment_status = "READY"
                $state.drift = $null
                Write-ReleaseControlState -State $state
            } catch {
                $state.deployment_status = "RECOVERY_REQUIRED"
                $state.drift = [pscustomobject]@{
                    code = "REVERSE_RECOVERY_FAILED"
                    reason = "$failure;$($_.Exception.Message)"
                    observed_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
            }
        } elseif ($state -and $state.transaction) {
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
        if ([string]$state.transaction.type -eq "RECOVERY") {
            if ([string]$state.transaction.phase -eq "CUTOVER") {
                if (-not $targetObserved) {
                    if (-not (Test-CloudflareRollbackTarget -Target $target)) {
                        $state.deployment_status = "RECOVERY_BLOCKED"
                        $state.drift = [pscustomobject]@{
                            code = "RECOVERY_LKG_ARTIFACT_UNAVAILABLE"
                            observed_at = [DateTimeOffset]::UtcNow.ToString("o")
                        }
                        Write-ReleaseControlState -State $state
                        return $state
                    }
                    if ($observedWorker -cne [string]$target.worker_version_id) {
                        Invoke-CloudflareDeployment `
                            -StableVersionId ([string]$target.worker_version_id) `
                            -Message "resume restore lkg $([string]$state.transaction.id)"
                    }
                    if ($observedWindows -cne [string]$target.windows_revision) {
                        Invoke-ReleaseWindowsRestore `
                            -Revision ([string]$target.windows_revision)
                    }
                    if (-not (Test-SingleProductionOwner)) {
                        throw "RECOVERY_SINGLE_OWNER_REQUIRED"
                    }
                    $verifiedDeployment = Get-CloudflareDeployment
                    $verifiedRuntime = Get-RuntimeCodeState
                    $verifiedWorker = [string](Get-DeploymentVersion `
                        -Deployment $verifiedDeployment -Percentage 100).version_id
                    if ($verifiedWorker -cne [string]$target.worker_version_id -or
                        -not $verifiedRuntime -or
                        [string]$verifiedRuntime.applied_revision -cne
                            [string]$target.windows_revision) {
                        throw "RECOVERY_POST_SWITCH_IDENTITY_MISMATCH"
                    }
                }
                Start-RuntimeObservation -Revision ([string]$target.windows_revision) `
                    -PreviousRevision ([string]$state.transaction.previous.windows_revision) `
                    -Mode "RESTORE_LKG" -ValidationKey ([string]$target.validation_key)
                $state.transaction.phase = "OBSERVING"
                $state.deployment_status = "RECOVERY_OBSERVING"
                Write-ReleaseControlState -State $state
                return $state
            }
            if ([string]$state.transaction.phase -eq "OBSERVING" -and $targetObserved) {
                $observation = Get-RuntimeUpdateState
                if ($observation -and [string]$observation.update_status -eq "ACTIVE" -and
                    [string]$observation.activated_revision -eq [string]$target.windows_revision) {
                    Complete-ReleaseRecovery
                    return Get-ReleaseControlState
                }
                if ($observation -and [string]$observation.update_status -eq "OBSERVING" -and
                    [string]$observation.observing_revision -eq [string]$target.windows_revision) {
                    Test-RuntimeObservation | Out-Null
                    return Get-ReleaseControlState
                }
            }
            if ([string]$state.transaction.phase -eq "OBSERVING" -and
                -not $targetObserved) {
                $null = Invoke-RuntimeRollback `
                    -FailedRevision ([string]$target.windows_revision) `
                    -PreviousRevision ([string]$state.transaction.previous.windows_revision) `
                    -Reason "RECOVERY_OBSERVE_IDENTITY_DRIFT"
                return Get-ReleaseControlState
            }
        }
        if ([string]$state.transaction.type -eq "PROMOTE" -and
            (Get-ReleaseTransactionMode $state.transaction) -eq "RECOVERY_HOTFIX" -and
            [string]$state.transaction.phase -in @("PRECHECK", "CUTOVER") -and
            -not $targetObserved) {
            $null = Invoke-RuntimeRollback `
                -FailedRevision ([string]$target.windows_revision) `
                -PreviousRevision ([string]$state.transaction.previous.windows_revision) `
                -Reason "RECOVERY_HOTFIX_SWITCH_INTERRUPTED"
            return Get-ReleaseControlState
        }
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
    $observeAttempt = [guid]::NewGuid().ToString("N")
    $arguments = @(
        (Join-Path $PSScriptRoot "check_deferred_projection_parity.py"),
        "--runtime-root", $moduleRoot,
        "--producer-root", $moduleRoot,
        "--version-id", ([string]$Target.worker_version_id),
        "--git-sha", ([string]$Target.git_sha),
        "--producer-revision", ([string]$Target.windows_revision),
        "--required-after", $RequiredAfter.ToString("o"),
        "--observe-attempt", $observeAttempt
    )
    foreach ($route in $routes) { $arguments += @("--route", $route) }
    $read = Invoke-Utf8NativeProcess -FilePath $python -Arguments $arguments `
        -WorkingDirectory $moduleRoot -Environment @{ PYTHONUTF8 = "1" }
    $output = if ($read.exit_code -eq 0) { @($read.stdout_lines) } else {
        @($read.stdout_lines) + @($read.stderr_lines)
    }
    try {
        return (($output | ForEach-Object { [string]$_ }) -join "`n") |
            ConvertFrom-ReleaseControlJson
    } catch {
        return [pscustomobject]@{
            state = "FAILED"; reason = "DEFERRED_PROJECTION_EVIDENCE_INVALID"
            diagnostic = Protect-PreflightDiagnosticText ($output -join "`n")
        }
    }
}
