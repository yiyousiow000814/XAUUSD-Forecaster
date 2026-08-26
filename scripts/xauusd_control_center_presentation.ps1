# Extracted from the latest-main Control Center.
# Owner: presentation and structured operation results.

function Get-DirectedWorkerValidationSummary {
    param([object]$Validation)
    $routes = if ($Validation -and $Validation.routes) { @($Validation.routes) } else { @() }
    $tested = if ($Validation -and $null -ne $Validation.routes_tested) {
        [int]$Validation.routes_tested
    } else { $routes.Count }
    $failed = if ($Validation -and $null -ne $Validation.routes_failed) {
        [int]$Validation.routes_failed
    } else { @($routes | Where-Object { -not $_.passed }).Count }
    $passed = if ($Validation -and $null -ne $Validation.routes_passed) {
        [int]$Validation.routes_passed
    } else { [Math]::Max(0, $tested - $failed) }
    $first = if ($Validation -and $Validation.first_failure) {
        $Validation.first_failure
    } else {
        $route = $routes | Where-Object { -not $_.passed } | Select-Object -First 1
        if ($route -and $route.first_failure) { $route.first_failure } else { $route }
    }
    $firstLine = if ($first) {
        $method = if ($first.method) { [string]$first.method } else { "REQUEST" }
        $path = if ($first.path) { [string]$first.path } else { [string]$first.route }
        $status = if ($null -ne $first.status) { "HTTP $([int]$first.status)" } else { "HTTP --" }
        $reason = if ($first.reason) { [string]$first.reason } else { "VALIDATION_FAILED" }
        $predicate = switch ($reason) {
            "CONTENT_TYPE_MISMATCH" {
                "EXPECTED content_type=$([string]$first.expected_content_type) | " +
                    "ACTUAL content_type=$([string]$first.actual_content_type)"
            }
            "INVALID_UTF8_BODY" { "EXPECTED encoding=utf-8 | ACTUAL invalid_utf8" }
            "HTML_CHARSET_MISMATCH" { "EXPECTED html_charset=utf-8 | ACTUAL charset_missing_or_invalid" }
            "MARKER_MISSING" {
                "EXPECTED marker=$([string]$first.expected_marker) | ACTUAL marker_present=false"
            }
            "EMPTY_BODY" { "EXPECTED nonempty_body=true | ACTUAL body_bytes=0" }
            "BODY_TOO_LARGE" {
                "EXPECTED body_bytes<=$candidateStaticAssetMaxBytes | " +
                    "ACTUAL body_bytes=$([int]$first.body_bytes)"
            }
            "REDIRECT_CONTRACT_MISMATCH" {
                "EXPECTED redirect_path=$([string]$first.expected_redirect_path) | " +
                    "ACTUAL redirect=$([int]$first.redirect_status) " +
                    "$([string]$first.redirect_location)"
            }
            default { "" }
        }
        "$method $path | $status | $reason$(if ($predicate) { ' | ' + $predicate })"
    } else { "" }
    return [pscustomobject]@{
        tested = $tested; passed = $passed; failed = $failed
        state = if ($Validation -and $Validation.cloudflare) {
            [string]$Validation.cloudflare
        } else { "WAITING" }
        first_failure = $first; first_failure_line = $firstLine
    }
}

function Get-ControlCenterReleasePresentation {
    param([object]$Release)

    if (-not $Release -or -not $Release.stable) {
        return [pscustomobject]@{
            candidate_state = "UNAVAILABLE"
            candidate_detail = "Release control has not been bootstrapped."
            can_promote = $false
            promote_reason = "Not bootstrapped"
            can_reverse = $false
            reverse_reason = "Not bootstrapped"
            can_verify_migration = $false
            can_approve_compatibility = $false
            compatibility_review_reason = "Not bootstrapped"
        }
    }

    $transactionActive = [bool]$Release.transaction
    $deploymentReady = [string]$Release.deployment_status -eq "READY"
    $candidateState = if ($Release.candidate) {
        [string]$Release.candidate.validation_state
    } else { "UNAVAILABLE" }
    if (-not $candidateState) { $candidateState = "UNAVAILABLE" }
    $candidateKind = if ($Release.candidate -and $Release.candidate.artifact_kind) {
        [string]$Release.candidate.artifact_kind
    } else { $unknownArtifactKind }
    $compatibilityPassed = [bool]($Release.candidate -and
        [string]$Release.candidate.compatibility_state -eq "PASSED")
    $controlBundleReady = [bool]($Release.control_bundle_hash_verified -and
        $Release.control_bundle_exact_revision -and
        [string]$Release.control_bundle_revision -match '^[0-9a-f]{40}$')
    $candidateProvenanceReady = [bool]($Release.candidate -and
        [string]$Release.candidate.branch -eq "main" -and
        [string]$Release.candidate.git_sha -eq [string]$Release.candidate.windows_revision -and
        [string]$Release.candidate.validation.key -eq [string]$Release.candidate.validation_key)
    $canApproveCompatibility = [bool]($Release.candidate -and
        $candidateState -eq "REVIEW_REQUIRED" -and
        [string]$Release.candidate.validation.reason -eq "PLATFORM_CONFIG_REVIEW_REQUIRED" -and
        [string]$Release.candidate.validation.key -eq [string]$Release.candidate.validation_key -and
        [bool]$Release.candidate.validation.resources_verified -and
        $candidateKind -eq $productionCandidateArtifactKind -and
        [string]$Release.candidate.branch -eq "main")
    $canVerifyMigration = [bool]($Release.candidate -and
        $candidateState -eq "REVIEW_REQUIRED" -and
        [string]$Release.candidate.validation.reason -in @(
            "COORDINATED_STORAGE_MIGRATION_REQUIRED",
            "COORDINATED_STORAGE_MIGRATION_EVIDENCE_INVALID"
        ) -and
        [string]$Release.candidate.validation.key -eq [string]$Release.candidate.validation_key -and
        $candidateKind -eq $productionCandidateArtifactKind -and
        [string]$Release.candidate.branch -eq "main")

    $candidateDetail = switch ($candidateState) {
        "PASSED" { "All required validation evidence is current." }
        "FAILED" {
            if ($Release.candidate.validation.error) {
                [string]$Release.candidate.validation.error
            } elseif ($Release.candidate.validation.reason) {
                [string]$Release.candidate.validation.reason
            } else { "Candidate validation failed." }
        }
        "TESTING" { "Validation is running against the exact release identity." }
        "STAGING" { "Candidate is being staged at zero percent traffic." }
        "NEW" { "Candidate is waiting for validation to begin." }
        "CHECKS_PENDING" {
            if ([string]$Release.candidate.validation.reason -in @(
                "REPOSITORY_TRANSPORT_UNAVAILABLE", "GITHUB_TEMPORARILY_UNAVAILABLE"
            )) {
                "GitHub temporarily unavailable. Retrying automatically."
            } else { "Required GitHub checks are pending for this exact SHA." }
        }
        "CHECKS_BLOCKED" { "Required GitHub checks failed and may be rerun for this exact SHA." }
        "REVIEW_REQUIRED" { [string]$Release.candidate.validation.reason }
        "REBASE_REQUIRED" { "REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED" }
        default { "No candidate release is currently available." }
    }

    $promoteReason = if ($transactionActive) {
        "A release transaction is already in progress"
    } elseif (-not $controlBundleReady) {
        "CONTROL_BUNDLE_HASH_VERIFICATION_FAILED"
    } elseif (-not $deploymentReady) {
        "Deployment status is $($Release.deployment_status)"
    } elseif (-not $Release.candidate) {
        "Candidate unavailable"
    } elseif ($candidateKind -ne $productionCandidateArtifactKind) {
        if ($candidateKind -eq $previewArtifactKind) {
            "Preview cannot be promoted"
        } elseif ($candidateKind -eq $legacyReferenceArtifactKind) {
            "REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED"
        } else { "Artifact provenance is unknown" }
    } elseif (-not $compatibilityPassed) {
        "Compatibility has not passed"
    } elseif ($candidateState -in @("TESTING", "STAGING", "NEW", "CHECKS_PENDING")) {
        "Candidate still testing"
    } elseif ($candidateState -eq "CHECKS_BLOCKED") {
        "REQUIRED_GITHUB_CHECKS_BLOCKED / RETRYABLE"
    } elseif ($candidateState -eq "FAILED") {
        if ($Release.candidate.validation.reason) {
            [string]$Release.candidate.validation.reason
        } else { "Candidate failed validation" }
    } elseif ($candidateState -ne "PASSED") {
        "Candidate has not passed validation"
    } else { "Ready to promote" }

    $reverseReason = if ($transactionActive) {
        "A release transaction is already in progress"
    } elseif (-not $controlBundleReady) {
        "CONTROL_BUNDLE_HASH_VERIFICATION_FAILED"
    } elseif (-not $deploymentReady) {
        "Deployment status is $($Release.deployment_status)"
    } elseif (-not $Release.previous_stable) {
        "Previous Stable unavailable"
    } elseif (-not [bool]$Release.previous_stable_rollback_eligible) {
        "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
    } else { "Ready to reverse" }

    [pscustomobject]@{
        candidate_state = $candidateState
        candidate_kind = $candidateKind
        candidate_detail = $candidateDetail
        can_promote = [bool]($deploymentReady -and -not $transactionActive -and
            $Release.candidate -and $candidateState -eq "PASSED" -and
            $candidateKind -eq $productionCandidateArtifactKind -and
            $compatibilityPassed -and $candidateProvenanceReady -and $controlBundleReady)
        promote_reason = $promoteReason
        can_reverse = [bool]($deploymentReady -and -not $transactionActive -and
            $Release.previous_stable -and $Release.previous_stable_rollback_eligible -and
            $controlBundleReady)
        reverse_reason = $reverseReason
        can_verify_migration = $canVerifyMigration
        can_approve_compatibility = $canApproveCompatibility
        compatibility_review_reason = if ($Release.candidate -and
            $Release.candidate.validation.reason) {
            [string]$Release.candidate.validation.reason
        } else { "No compatibility review pending" }
    }
}

function Get-ControlCenterSummaryPresentation {
    param([Parameter(Mandatory = $true)][object]$Snapshot)

    $healthyStates = @("RUNNING", "LIVE", "MARKET CLOSED", "API OK", "SYNC OK")
    $serviceRows = @($Snapshot.services)
    $healthyCount = @($serviceRows | Where-Object { [string]$_.State -in $healthyStates }).Count
    $unhealthyCount = $serviceRows.Count - $healthyCount
    $localRuntime = if ($healthyCount -eq 0) {
        "STOPPED"
    } elseif ($unhealthyCount -eq 0) {
        "RUNNING"
    } else { "PARTIAL" }

    $releaseView = Get-ControlCenterReleasePresentation -Release $Snapshot.release
    $deploymentState = if ($Snapshot.release) {
        [string]$Snapshot.release.deployment_status
    } else { "UNAVAILABLE" }
    $overall = if (
        $deploymentState -match "FAILED|DRIFT|RECOVERY" -or
        ($serviceRows.Count -gt 0 -and $unhealthyCount -eq $serviceRows.Count)
    ) {
        "FAILED"
    } elseif (
        $unhealthyCount -gt 0 -or $deploymentState -ne "READY" -or
        $releaseView.candidate_state -in @("FAILED", "CHECKS_BLOCKED")
    ) {
        "DEGRADED"
    } else { "HEALTHY" }

    $captured = "--"
    try {
        $capturedAt = [DateTimeOffset]::Parse([string]$Snapshot.captured_at)
        $captured = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
            $capturedAt, "Singapore Standard Time"
        ).ToString("HH:mm:ss")
    } catch {}

    [pscustomobject]@{
        overall = $overall
        local_runtime = $localRuntime
        candidate_state = $releaseView.candidate_state
        last_refresh = $captured
    }
}

function Import-WpfControlCenterWindow {
    $xamlPath = Join-Path $PSScriptRoot "control_center.xaml"
    if (-not (Test-Path -LiteralPath $xamlPath)) {
        throw "WPF control resource is missing: $xamlPath"
    }
    Add-Type -AssemblyName PresentationFramework
    Add-Type -AssemblyName PresentationCore
    [xml]$xaml = [IO.File]::ReadAllText(
        $xamlPath, [Text.UTF8Encoding]::new($false)
    )
    $reader = New-Object System.Xml.XmlNodeReader $xaml
    return [Windows.Markup.XamlReader]::Load($reader)
}

function Write-ControlCenterUiStarted {
    param(
        [ValidateSet("WPF", "WINFORMS_FALLBACK")][string]$Mode,
        [string]$FailureReason = ""
    )
    $bundle = Get-RuntimeControlBundleIdentity
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    [pscustomobject]@{
        time = [DateTimeOffset]::UtcNow.ToString("o")
        event = "CONTROL_CENTER_UI_STARTED"
        mode = $Mode
        control_revision = if ($bundle) { [string]$bundle.source_revision } else { $null }
        failure_reason = if ($FailureReason) {
            Protect-PreflightDiagnosticText $FailureReason
        } else { $null }
    } | ConvertTo-Json -Compress | Add-Content -LiteralPath $watchdogLog -Encoding UTF8
}

function Get-ControlCenterOperationText {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return "" }
    $content = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $content) { return "" }
    return Protect-PreflightDiagnosticText -Value (([string]$content).Trim())
}

function Get-ControlCenterOperationState {
    param([object]$ReleaseState = $null)
    if (-not $ReleaseState) { $ReleaseState = Get-ReleaseControlState }
    [pscustomobject]@{
        deployment_status = if ($ReleaseState) {
            [string]$ReleaseState.deployment_status
        } else { "UNAVAILABLE" }
        stable_validation_key = if ($ReleaseState -and $ReleaseState.stable) {
            [string]$ReleaseState.stable.validation_key
        } else { "" }
        candidate_validation_key = if ($ReleaseState -and $ReleaseState.candidate) {
            [string]$ReleaseState.candidate.validation_key
        } else { "" }
        candidate_validation_state = if ($ReleaseState -and $ReleaseState.candidate) {
            [string]$ReleaseState.candidate.validation_state
        } else { "UNAVAILABLE" }
        candidate_compatibility_state = if ($ReleaseState -and $ReleaseState.candidate) {
            [string]$ReleaseState.candidate.compatibility_state
        } else { "UNAVAILABLE" }
        transaction_type = if ($ReleaseState -and $ReleaseState.transaction) {
            [string]$ReleaseState.transaction.type
        } else { "" }
    }
}

function New-ControlCenterOperationResult {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][bool]$Success,
        [Parameter(Mandatory = $true)][bool]$Committed,
        [string]$Reason = "",
        [string]$Diagnostic = "",
        [object]$ReleaseState = $null
    )
    $bundle = Get-RuntimeControlBundleIdentity
    [pscustomobject]@{
        schema_version = "control-center-operation-v1"
        operation = $Operation
        success = $Success
        committed = $Committed
        reason = $Reason
        diagnostic = if ($Diagnostic) {
            Protect-PreflightDiagnosticText -Value $Diagnostic
        } else { "" }
        control_revision = if ($bundle) { [string]$bundle.source_revision } else { "" }
        release_validation_key = if ($ReleaseState -and $ReleaseState.candidate) {
            [string]$ReleaseState.candidate.validation_key
        } else { "" }
        authoritative_state = Get-ControlCenterOperationState -ReleaseState $ReleaseState
        completed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Write-ControlCenterOperationResult {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Result
    )
    $directory = Split-Path -Parent $Path
    if (-not $directory -or -not (Test-Path -LiteralPath $directory)) {
        throw "CONTROL_CENTER_RESULT_DIRECTORY_UNAVAILABLE"
    }
    $temporary = "$Path.$PID.tmp"
    try {
        $Result | ConvertTo-Json -Depth 12 -Compress |
            Set-Content -LiteralPath $temporary -Encoding UTF8 -ErrorAction Stop
        Move-Item -LiteralPath $temporary -Destination $Path -Force -ErrorAction Stop
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Read-ControlCenterOperationResult {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $result = Get-Content -LiteralPath $Path -Raw -ErrorAction Stop |
            ConvertFrom-Json -ErrorAction Stop
        if ([string]$result.schema_version -ne "control-center-operation-v1" -or
            -not $result.operation) { return $null }
        return $result
    } catch { return $null }
}

function Test-ControlCenterApprovalCommitted {
    param(
        [object]$ReleaseState,
        [Parameter(Mandatory = $true)][string]$ValidationKey
    )
    if (-not $ReleaseState -or -not $ReleaseState.candidate -or
        [string]$ReleaseState.candidate.validation_key -ne $ValidationKey -or
        [string]$ReleaseState.candidate.compatibility_state -ne "APPROVED" -or
        [string]$ReleaseState.candidate.compatibility_approval.validation_key -ne
            $ValidationKey) { return $false }
    if (-not (Test-Path -LiteralPath $releaseHistoryPath)) { return $false }
    foreach ($line in @(Get-Content -LiteralPath $releaseHistoryPath -Tail 1000 `
        -ErrorAction SilentlyContinue)) {
        try {
            $entry = $line | ConvertFrom-Json -ErrorAction Stop
            if ([string]$entry.event -eq "CANDIDATE_COMPATIBILITY_APPROVED" -and
                [string]$entry.release.validation_key -eq $ValidationKey -and
                [string]$entry.detail.validation_key -eq $ValidationKey) {
                return $true
            }
        } catch {}
    }
    return $false
}

function Test-ControlCenterReleaseHistoryContains {
    param(
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $true)][object]$ExpectedRelease
    )
    if (-not (Test-Path -LiteralPath $releaseHistoryPath)) { return $false }
    foreach ($line in @(Get-Content -LiteralPath $releaseHistoryPath -Tail 1000 `
        -ErrorAction SilentlyContinue)) {
        try {
            $entry = $line | ConvertFrom-Json -ErrorAction Stop
            if ([string]$entry.event -eq $Event -and
                (Test-ReleaseIdentity $entry.release $ExpectedRelease)) {
                return $true
            }
        } catch {}
    }
    return $false
}

function Test-ControlCenterReleaseOperationCommitted {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [object]$ReleaseState,
        [object]$ExpectedRelease = $null,
        [string]$ExpectedValidationKey = ""
    )
    switch ($Operation) {
        "VerifyMigrationCompatibility" {
            return [bool]($ExpectedValidationKey -and $ReleaseState -and
                $ReleaseState.candidate -and
                [string]$ReleaseState.candidate.validation_key -eq $ExpectedValidationKey -and
                [string]$ReleaseState.candidate.migration_acceptance.validation_key -eq
                    $ExpectedValidationKey -and
                [string]$ReleaseState.candidate.compatibility_state -eq
                    "COORDINATED_STORAGE_MIGRATION_PASSED" -and
                (Test-ControlCenterReleaseHistoryContains `
                    -Event "COORDINATED_STORAGE_MIGRATION_PASSED" `
                    -ExpectedRelease $ReleaseState.candidate))
        }
        "ApproveCompatibility" {
            return [bool]($ExpectedValidationKey -and
                (Test-ControlCenterApprovalCommitted -ReleaseState $ReleaseState `
                    -ValidationKey $ExpectedValidationKey))
        }
        "PromoteCandidate" {
            return [bool]($ExpectedRelease -and $ReleaseState -and (
                ((-not $ReleaseState.transaction) -and
                    [string]$ReleaseState.deployment_status -eq "READY" -and
                    (Test-ReleaseIdentity $ReleaseState.stable $ExpectedRelease) -and
                    (Test-ControlCenterReleaseHistoryContains `
                        -Event "STABLE_COMMITTED" -ExpectedRelease $ExpectedRelease)) -or
                ($ReleaseState.transaction -and
                    [string]$ReleaseState.transaction.type -eq "PROMOTE" -and
                    [string]$ReleaseState.deployment_status -in @(
                        "PROMOTING", "OBSERVING"
                    ) -and
                    (Test-ReleaseIdentity $ReleaseState.transaction.target $ExpectedRelease))))
        }
        "ReverseStable" {
            return [bool]($ExpectedRelease -and $ReleaseState -and (
                ((-not $ReleaseState.transaction) -and
                    [string]$ReleaseState.deployment_status -eq "READY" -and
                    (Test-ReleaseIdentity $ReleaseState.stable $ExpectedRelease) -and
                    (Test-ControlCenterReleaseHistoryContains `
                        -Event "STABLE_REVERSED" -ExpectedRelease $ExpectedRelease)) -or
                ($ReleaseState.transaction -and
                    [string]$ReleaseState.transaction.type -eq "REVERSE" -and
                    [string]$ReleaseState.deployment_status -in @(
                        "REVERSING", "REVERSE_OBSERVING"
                    ) -and
                    (Test-ReleaseIdentity $ReleaseState.transaction.target $ExpectedRelease))))
        }
    }
    return $false
}

function Get-ControlCenterOperationDiagnostic {
    param(
        [object]$Result = $null,
        [string]$StandardOutput = "",
        [string]$StandardError = ""
    )
    foreach ($value in @(
        $(if ($Result) { [string]$Result.diagnostic } else { "" }),
        $StandardError,
        $StandardOutput
    )) {
        if ($value) { return Protect-PreflightDiagnosticText -Value $value }
    }
    return ""
}

function Resolve-ControlCenterOperationPresentation {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Nullable[int]]$ProcessExitCode = $null,
        [object]$Result = $null,
        [object]$ReleaseState = $null,
        [object]$ExpectedRelease = $null,
        [string]$ExpectedValidationKey = "",
        [string]$ExpectedControlRevision = "",
        [string]$StandardOutput = "",
        [string]$StandardError = ""
    )
    $diagnostic = Get-ControlCenterOperationDiagnostic -Result $Result `
        -StandardOutput $StandardOutput -StandardError $StandardError
    if ($Result -and [string]$Result.operation -eq $Operation -and
        (-not $ExpectedControlRevision -or
            [string]$Result.control_revision -eq $ExpectedControlRevision)) {
        if ([bool]$Result.success) {
            return [pscustomobject]@{
                state = "SUCCESS"; committed = [bool]$Result.committed
                diagnostic = $diagnostic; reason = [string]$Result.reason
            }
        }
        return [pscustomobject]@{
            state = "FAILURE"; committed = [bool]$Result.committed
            diagnostic = $diagnostic; reason = [string]$Result.reason
        }
    }
    if (Test-ControlCenterReleaseOperationCommitted -Operation $Operation `
        -ReleaseState $ReleaseState -ExpectedRelease $ExpectedRelease `
        -ExpectedValidationKey $ExpectedValidationKey) {
        return [pscustomobject]@{
            state = "SUCCESS"; committed = $true
            diagnostic = "Operation result transport failed after authoritative commit."
            reason = "AUTHORITATIVE_COMMIT_CONFIRMED"
        }
    }
    return [pscustomobject]@{
        state = "INDETERMINATE"; committed = $false
        diagnostic = $(if ($diagnostic) { $diagnostic } else {
            "Structured operation result unavailable; authoritative state was refreshed."
        })
        reason = $(if ($null -eq $ProcessExitCode) {
            "PROCESS_EXIT_UNAVAILABLE"
        } else { "OPERATION_RESULT_UNAVAILABLE" })
    }
}

function Write-ControlCenterOperationEvent {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][object]$Presentation,
        [Nullable[int]]$ProcessExitCode = $null,
        [object]$Result = $null
    )
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    [pscustomobject]@{
        time = [DateTimeOffset]::UtcNow.ToString("o")
        event = "CONTROL_CENTER_OPERATION_COMPLETED"
        operation = $Operation
        state = [string]$Presentation.state
        committed = [bool]$Presentation.committed
        reason = [string]$Presentation.reason
        process_exit_code = if ($null -eq $ProcessExitCode) {
            $null
        } else { [int]$ProcessExitCode }
        result_available = [bool]$Result
        result_control_revision = if ($Result) {
            [string]$Result.control_revision
        } else { "" }
        release_validation_key = if ($Result) {
            [string]$Result.release_validation_key
        } else { "" }
    } | ConvertTo-Json -Compress |
        Add-Content -LiteralPath $watchdogLog -Encoding UTF8
}

function Invoke-ControlCenterOperationAction {
    param([Parameter(Mandatory = $true)][string]$Operation)
    switch ($Operation) {
        "Start" { Start-All; Start-Sleep -Seconds 2; return @(Get-ForecasterStatus) }
        "Stop" { Stop-All; Start-Sleep -Seconds 1; return @(Get-ForecasterStatus) }
        "Restart" { Restart-All; Start-Sleep -Seconds 2; return @(Get-ForecasterStatus) }
        "ServiceStart" {
            $target = $services | Where-Object Key -eq $ServiceKey
            if (-not $target) { throw "Unknown service key: $ServiceKey" }
            if ($target.Key -eq "sync") { Exit-CoordinatedMigrationSyncHold }
            Start-ForecasterService $target
            return $target
        }
        "ServiceStop" {
            $target = $services | Where-Object Key -eq $ServiceKey
            if (-not $target) { throw "Unknown service key: $ServiceKey" }
            Stop-ForecasterService $target
            return $target
        }
        "DiscoverCandidate" {
            if (-not (Invoke-CandidateDiscovery)) {
                throw "Candidate discovery did not complete."
            }
            return Get-ReleaseControlState
        }
        "ReconcileRelease" {
            if (-not (Enter-ReleaseTransactionLock)) {
                throw "Another release transaction is active."
            }
            try { return Reconcile-ReleaseControlState }
            finally { Exit-ReleaseTransactionLock }
        }
        "PromoteCandidate" {
            if (-not (Start-ReleasePromotion)) { throw "Promotion did not start." }
            return Get-ReleaseControlState
        }
        "ReverseStable" {
            if (-not (Invoke-ReverseStable)) { throw "Reverse did not start." }
            return Get-ReleaseControlState
        }
        "ApproveCompatibility" {
            if (-not (Enter-ReleaseTransactionLock)) {
                throw "Another release transaction is active."
            }
            try { return Approve-CandidateCompatibility }
            finally { Exit-ReleaseTransactionLock }
        }
        "VerifyMigrationCompatibility" {
            if (-not (Enter-ReleaseTransactionLock)) {
                throw "Another release transaction is active."
            }
            try { return Verify-CandidateCoordinatedMigration }
            finally { Exit-ReleaseTransactionLock }
        }
        default { throw "Unsupported Control Center operation: $Operation" }
    }
}

function Invoke-ControlCenterStructuredOperation {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][string]$ResultPath
    )
    $before = Get-ReleaseControlState
    $expectedRelease = if ($Operation -eq "PromoteCandidate" -and $before) {
        $before.candidate
    } elseif ($Operation -eq "ReverseStable" -and $before) {
        $before.previous_stable
    } else { $null }
    $expectedValidationKey = if ($before -and $before.candidate) {
        [string]$before.candidate.validation_key
    } else { "" }
    $operationError = $null
    try {
        $null = Invoke-ControlCenterOperationAction -Operation $Operation
    } catch {
        $operationError = $_
    }
    $after = Get-ReleaseControlState
    if ($operationError) {
        $diagnostic = Protect-PreflightDiagnosticText `
            -Value $operationError.Exception.Message
        $committed = [bool](
            ($Operation -eq "ApproveCompatibility" -and
                (Test-ControlCenterApprovalCommitted -ReleaseState $after `
                    -ValidationKey $expectedValidationKey)) -or
            ($Operation -eq "VerifyMigrationCompatibility" -and
                (Test-ControlCenterReleaseOperationCommitted -Operation $Operation `
                    -ReleaseState $after -ExpectedValidationKey $expectedValidationKey))
        )
        $result = New-ControlCenterOperationResult -Operation $Operation `
            -Success $committed -Committed $committed `
            -Reason $(if ($committed) {
                "AUTHORITATIVE_COMMIT_CONFIRMED"
            } else { "OPERATION_FAILED" }) `
            -Diagnostic $diagnostic -ReleaseState $after
        try { Write-ControlCenterOperationResult -Path $ResultPath -Result $result }
        catch {
            [Console]::Error.WriteLine(
                (Protect-PreflightDiagnosticText -Value $_.Exception.Message)
            )
            return 2
        }
        if ($committed) { return 0 }
        [Console]::Error.WriteLine($diagnostic)
        return 1
    }
    $committed = if ($Operation -in @(
        "VerifyMigrationCompatibility", "ApproveCompatibility", "PromoteCandidate", "ReverseStable"
    )) {
        Test-ControlCenterReleaseOperationCommitted -Operation $Operation `
            -ReleaseState $after -ExpectedRelease $expectedRelease `
            -ExpectedValidationKey $expectedValidationKey
    } else { $true }
    $semanticSuccess = [bool]($Operation -notin @(
        "VerifyMigrationCompatibility", "ApproveCompatibility", "PromoteCandidate", "ReverseStable"
    ) -or $committed)
    $result = New-ControlCenterOperationResult -Operation $Operation `
        -Success $semanticSuccess -Committed $committed `
        -Reason $(if ($semanticSuccess) {
            "COMPLETED"
        } else { "AUTHORITATIVE_COMMIT_MISSING" }) `
        -ReleaseState $after
    try {
        Write-ControlCenterOperationResult -Path $ResultPath -Result $result
    } catch {
        [Console]::Error.WriteLine(
            (Protect-PreflightDiagnosticText -Value $_.Exception.Message)
        )
        return 2
    }
    if ($semanticSuccess) { return 0 }
    return 1
}

function Invoke-ControlCenterUiCallback {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Callback,
        [scriptblock]$OnFailure = $null
    )
    try {
        & $Callback
        return $true
    } catch {
        $diagnostic = Protect-PreflightDiagnosticText $_.Exception.Message
        if ($OnFailure) { & $OnFailure $diagnostic }
        return $false
    }
}

function Test-WpfFallbackAllowed {
    param([bool]$ContentRendered)
    return (-not $ContentRendered)
}

function Test-WpfControlCenterLayout {
    $window = Import-WpfControlCenterWindow
    try {
        $required = @(
            "RootScrollViewer", "RootLayout", "ReleaseGrid", "StableCard",
            "CandidateCard", "PreviousCard", "CandidateChecks",
            "CandidateReason", "OpenStableButton", "OpenCandidateButton",
            "VerifyMigrationButton", "ApproveCompatibilityButton", "PromoteButton", "ReverseButton",
            "CandidateTechnicalEvidence", "FooterDisclaimer",
            "ControlPlaneIdentity", "BusinessRuntimeIdentity"
        )
        foreach ($name in $required) {
            if (-not $window.FindName($name)) { throw "Missing WPF control: $name" }
        }
        $scroll = $window.FindName("RootScrollViewer")
        if ($scroll.VerticalScrollBarVisibility -ne
            [Windows.Controls.ScrollBarVisibility]::Auto) {
            throw "RootScrollViewer must retain automatic vertical scrolling."
        }
        $results = @()
        foreach ($viewport in @(
            @(1366, 768), @(1920, 1080)
        )) {
            foreach ($scale in @(1.0, 1.25, 1.5)) {
                $width = [Math]::Floor($viewport[0] / $scale)
                $height = [Math]::Floor($viewport[1] / $scale)
                $width = [Math]::Max([double]$window.MinWidth, $width)
                $height = [Math]::Max([double]$window.MinHeight, $height)
                $content = [Windows.FrameworkElement]$window.Content
                $size = [Windows.Size]::new($width, $height)
                $content.Measure($size)
                $content.Arrange([Windows.Rect]::new(0, 0, $width, $height))
                $content.UpdateLayout()
                $reachable = $true
                foreach ($name in @(
                    "OpenStableButton", "OpenCandidateButton",
                    "VerifyMigrationButton", "ApproveCompatibilityButton", "PromoteButton", "ReverseButton"
                )) {
                    $control = [Windows.FrameworkElement]$window.FindName($name)
                    if ($control.ActualWidth -le 0 -or $control.ActualHeight -le 0) {
                        $reachable = $false
                    }
                }
                if (-not $reachable) {
                    throw "Critical WPF actions failed layout at $($viewport[0])x$($viewport[1]) scale $scale."
                }
                $results += [pscustomobject]@{
                    viewport = "$($viewport[0])x$($viewport[1])"
                    scale = $scale
                    logical_width = $width
                    logical_height = $height
                    critical_controls_reachable = $reachable
                    vertical_scroll_available = [bool]($scroll.ScrollableHeight -gt 0)
                }
            }
        }
        return $results
    } finally {
        $window.Close()
    }
}

function Show-WpfControlCenter {
    $script:wpfFailureReason = ""
    $script:wpfUiStartedRecorded = $false
    try {
        $controlIdentity = Assert-ControlCenterProcessIdentity
        $window = Import-WpfControlCenterWindow

        function Find-WpfControl([string]$Name) { return $window.FindName($Name) }
        function Format-WpfIdentity($Release) {
            if (-not $Release) { return "Git       --`nWorker    --`nWindows   --" }
            $git = if ($Release.git_sha) { ([string]$Release.git_sha).Substring(0, [Math]::Min(12, ([string]$Release.git_sha).Length)) } else { "--" }
            $worker = if ($Release.worker_version_id) { ([string]$Release.worker_version_id).Substring(0, [Math]::Min(12, ([string]$Release.worker_version_id).Length)) } else { "--" }
            $windows = if ($Release.windows_revision) { ([string]$Release.windows_revision).Substring(0, [Math]::Min(12, ([string]$Release.windows_revision).Length)) } else { "--" }
            return "Git       $git`nWorker    $worker`nWindows   $windows"
        }
        $script:wpfOperation = $null
        $script:wpfOperationName = ""
        $script:wpfOperationOutputPath = ""
        $script:wpfOperationErrorPath = ""
        $script:wpfOperationResultPath = ""
        $script:wpfOperationExpectedRelease = $null
        $script:wpfOperationValidationKey = ""
        function Test-WpfOperationActive {
            return [bool]$script:wpfOperation
        }
        function Set-WpfReleaseBusy([bool]$Busy, [string]$Operation = "") {
            foreach ($name in @("StartButton", "RestartButton", "StopButton")) {
                (Find-WpfControl $name).IsEnabled = -not $Busy
            }
            (Find-WpfControl "ServiceList").IsEnabled = -not $Busy
            if ($Busy) {
                foreach ($name in @(
                    "VerifyMigrationButton", "ApproveCompatibilityButton", "PromoteButton", "ReverseButton"
                )) {
                    (Find-WpfControl $name).IsEnabled = $false
                }
            }
            if (-not $Busy) { return }
            $state = switch ($Operation) {
                "VerifyMigrationCompatibility" { "VERIFYING MIGRATION" }
                "ApproveCompatibility" { "APPROVING" }
                "PromoteCandidate" { "PROMOTING" }
                "ReverseStable" { "REVERSING" }
                default { "WORKING" }
            }
            if ($Operation -eq "ReverseStable") {
                (Find-WpfControl "PreviousState").Text = $state
            } else {
                (Find-WpfControl "CandidateState").Text = $state
            }
            (Find-WpfControl "CandidateReason").Text =
                "$state | tracked background operation in progress"
        }
        function Invoke-WpfOperation([string]$Operation, [string]$ServiceKey = "") {
            if (Test-WpfOperationActive) { return }
            $arguments = @(
                "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-File", ('"{0}"' -f $PSCommandPath), "-Action", $Operation,
                "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
                "-RepositoryRoot", ('"{0}"' -f $repositoryRoot),
                "-ExpectedControlScriptPath", ('"{0}"' -f $PSCommandPath),
                "-ExpectedControlRevision", ([string]$controlIdentity.source_revision)
            )
            if ($ServiceKey) { $arguments += @("-ServiceKey", $ServiceKey) }
            $script:wpfOperationName = $Operation
            $script:wpfOperationOutputPath = Join-Path $env:TEMP `
                ("xauusd-wpf-operation-{0}.out" -f ([guid]::NewGuid().ToString("N")))
            $script:wpfOperationErrorPath = Join-Path $env:TEMP `
                ("xauusd-wpf-operation-{0}.err" -f ([guid]::NewGuid().ToString("N")))
            $script:wpfOperationResultPath = Join-Path $env:TEMP `
                ("xauusd-wpf-operation-{0}.json" -f ([guid]::NewGuid().ToString("N")))
            $releaseBefore = Get-ReleaseControlState
            $script:wpfOperationValidationKey = if ($releaseBefore -and
                $releaseBefore.candidate) {
                [string]$releaseBefore.candidate.validation_key
            } else { "" }
            $script:wpfOperationExpectedRelease = if (
                $Operation -eq "PromoteCandidate" -and $releaseBefore
            ) { $releaseBefore.candidate } elseif (
                $Operation -eq "ReverseStable" -and $releaseBefore
            ) { $releaseBefore.previous_stable } else { $null }
            $arguments += @(
                "-OperationResultPath", ('"{0}"' -f $script:wpfOperationResultPath)
            )
            Set-WpfReleaseBusy -Busy $true -Operation $Operation
            try {
                $script:wpfOperation = Start-Process -FilePath "powershell.exe" `
                    -ArgumentList $arguments -WorkingDirectory $moduleRoot `
                    -WindowStyle Hidden -PassThru `
                    -RedirectStandardOutput $script:wpfOperationOutputPath `
                    -RedirectStandardError $script:wpfOperationErrorPath
            } catch {
                $script:wpfOperation = $null
                Remove-Item -LiteralPath `
                    $script:wpfOperationOutputPath,$script:wpfOperationErrorPath,`
                    $script:wpfOperationResultPath `
                    -Force -ErrorAction SilentlyContinue
                Set-WpfReleaseBusy -Busy $false
                [void](Invoke-ControlCenterUiCallback -Callback { Refresh-WpfStatus })
                Show-WpfCallbackFailure `
                    (Protect-PreflightDiagnosticText $_.Exception.Message)
            }
        }
        function Refresh-WpfStatus {
            $status = @(Get-ForecasterStatus)
            $controlBundle = Get-RuntimeControlBundleIdentity
            (Find-WpfControl "ControlPlaneIdentity").Text = if ($controlBundle) {
                "Control Plane  $(([string]$controlBundle.source_revision).Substring(0, 12))  EXACT | HASH VERIFIED"
            } else { "Control Plane  --  UNVERIFIED" }
            $businessRevision = Get-CodeRevision
            (Find-WpfControl "BusinessRuntimeIdentity").Text =
                "Business Runtime  $(if ($businessRevision) { $businessRevision.Substring(0, 12) } else { '--' })"
            (Find-WpfControl "ServiceList").ItemsSource = @($status | ForEach-Object {
                [pscustomobject]@{ Key = $_.Key; Name = $_.Component; State = $_.State }
            })
            $bad = @($status | Where-Object { $_.State -match "STOPPED|ERROR|STALE|OFFLINE" }).Count
            (Find-WpfControl "OverallState").Text = if ($bad) { "DEGRADED" } else { "HEALTHY" }
            (Find-WpfControl "RefreshTime").Text = [DateTime]::Now.ToString("HH:mm:ss")
            $release = Get-ReleaseControlState
            (Find-WpfControl "StableState").Text = if ($release -and $release.stable) { "STABLE" } else { "UNAVAILABLE" }
            (Find-WpfControl "StableIdentity").Text = Format-WpfIdentity $release.stable
            (Find-WpfControl "CandidateState").Text = if ($release -and $release.candidate) { [string]$release.candidate.validation_state } else { "UNAVAILABLE" }
            (Find-WpfControl "CandidateIdentity").Text = Format-WpfIdentity $release.candidate
            $reason = if ($release -and $release.candidate -and $release.candidate.validation) {
                if ($release.candidate.validation.reason) { [string]$release.candidate.validation.reason }
                elseif ($release.candidate.validation.error) { [string]$release.candidate.validation.error }
                else { "Validation evidence is available." }
            } else { "Candidate validation is unavailable." }
            $releaseView = Get-ControlCenterReleasePresentation -Release $release
            (Find-WpfControl "CandidateReason").Text = if ($releaseView.can_promote) {
                "Ready for explicit manual promotion."
            } else { "Cannot promote: $reason" }
            $validation = if ($release -and $release.candidate) {
                $release.candidate.validation
            } else { $null }
            $directed = Get-DirectedWorkerValidationSummary -Validation $validation
            if ($directed.failed -gt 0 -and $directed.first_failure_line) {
                (Find-WpfControl "CandidateReason").Text =
                    "$($directed.first_failure_line)`nCannot promote: $reason"
            }
            $apiRouteState = if ($directed.tested -gt 0) {
                "$($directed.state) | $($directed.passed)/$($directed.tested)"
            } else { $directed.state }
            $dataParityState = if ($validation -and $validation.data_parity) {
                [string]$validation.data_parity.state
            } else { "unavailable" }
            $cpuState = if ($validation -and $validation.cpu_headroom) {
                [string]$validation.cpu_headroom.state
            } elseif ($validation -and $validation.cpu_evidence -and
                $validation.cpu_evidence -notin @("NOT_RUN", "NOT_REQUIRED")) {
                [string]$validation.cpu_evidence.gate_state
            } elseif ($validation -and $validation.cpu_evidence) {
                [string]$validation.cpu_evidence
            } else { "unavailable" }
            $failureState = if ($validation -and $validation.worker_failures) {
                [string]$validation.worker_failures.state
            } else { $cpuState }
            (Find-WpfControl "CandidateChecks").Text = @(
                "Repository: $([string]$(if ($validation) { $validation.repository } else { 'unavailable' }))"
                "Windows preflight: $([string]$(if ($validation) { $validation.windows } else { 'unavailable' }))"
                "API routes: $apiRouteState"
                "Data parity: $dataParityState"
                "CPU headroom: $cpuState"
                "5xx / 1102: $failureState"
                "Compatibility: $([string]$(if ($release -and $release.candidate) { $release.candidate.compatibility_state } else { 'unavailable' }))"
                "Access boundary: formal-host only"
            ) -join "`n"
            (Find-WpfControl "CandidateTechnicalEvidence").Text = if ($release -and $release.candidate) {
                $evidence = [ordered]@{
                    validation_key = [string]$release.candidate.validation_key
                    validation_run = if ($validation) { $validation.validation_run } else { $null }
                    reason = $reason
                    migration_acceptance = $release.candidate.migration_acceptance
                    first_failure = $directed.first_failure
                    routes = if ($validation) { $validation.routes } else { @() }
                }
                $evidence | ConvertTo-Json -Depth 10
            } else { "No exact-version evidence loaded." }
            $operationActive = Test-WpfOperationActive
            (Find-WpfControl "PromoteButton").IsEnabled = [bool]($releaseView.can_promote -and -not $operationActive)
            (Find-WpfControl "VerifyMigrationButton").IsEnabled = [bool]($releaseView.can_verify_migration -and -not $operationActive)
            (Find-WpfControl "ApproveCompatibilityButton").IsEnabled = [bool]($releaseView.can_approve_compatibility -and -not $operationActive)
            (Find-WpfControl "OpenCandidateButton").IsEnabled = [bool]($release -and $release.candidate -and $release.candidate.browser_url)
            (Find-WpfControl "PreviousState").Text = if ($release -and $release.previous_stable) { "AVAILABLE" } else { "UNAVAILABLE" }
            (Find-WpfControl "PreviousIdentity").Text = Format-WpfIdentity $release.previous_stable
            (Find-WpfControl "ReverseButton").IsEnabled = [bool]($releaseView.can_reverse -and -not $operationActive)
            if ($operationActive) {
                Set-WpfReleaseBusy -Busy $true -Operation $script:wpfOperationName
            }
        }

        function Show-WpfCallbackFailure([string]$Diagnostic) {
            try {
                (Find-WpfControl "CandidateReason").Text = "Control Center error: $Diagnostic"
                [System.Windows.MessageBox]::Show(
                    $Diagnostic, "Control Center error", "OK", "Error"
                ) | Out-Null
            } catch { Write-Warning "WPF callback failed: $Diagnostic" }
        }
        (Find-WpfControl "RefreshButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback { Refresh-WpfStatus } `
                -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        (Find-WpfControl "StartButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                Invoke-WpfOperation "Start"
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        (Find-WpfControl "RestartButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                Invoke-WpfOperation "Restart"
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        (Find-WpfControl "StopButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                Invoke-WpfOperation "Stop"
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        (Find-WpfControl "DashboardButton").Add_Click({ Start-Process $dashboardUrl })
        (Find-WpfControl "LogsButton").Add_Click({ Start-Process explorer.exe $logRoot })
        (Find-WpfControl "OpenStableButton").Add_Click({ Start-Process $dashboardUrl })
        (Find-WpfControl "OpenCandidateButton").Add_Click({
            $state = Get-ReleaseControlState
            if ($state -and $state.candidate -and $state.candidate.browser_url) {
                Start-Process ([string]$state.candidate.browser_url)
            }
        })
        (Find-WpfControl "VerifyMigrationButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                $state = Get-ReleaseControlState
                $files = @($state.candidate.validation.review_files) -join "`n"
                if ([System.Windows.MessageBox]::Show(
                    "Verify the exact Candidate migration ledger, live D1 capabilities, Stable/Reverse compatibility, and News CURRENT?`n`n$files",
                    "Verify Coordinated Migration", "YesNo", "Warning"
                ) -eq "Yes") { Invoke-WpfOperation "VerifyMigrationCompatibility" }
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        (Find-WpfControl "ApproveCompatibilityButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                if ([System.Windows.MessageBox]::Show(
                    "Approve only the displayed exact compatibility evidence?",
                    "Confirm Compatibility", "YesNo", "Warning"
                ) -eq "Yes") { Invoke-WpfOperation "ApproveCompatibility" }
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        $window.AddHandler(
            [System.Windows.Controls.Button]::ClickEvent,
            [System.Windows.RoutedEventHandler]{
                param($sender, $eventArgs)
                $button = $eventArgs.OriginalSource
                if ($button.CommandParameter -in @("ServiceStart", "ServiceStop") -and $button.Tag) {
                    [void](Invoke-ControlCenterUiCallback -Callback {
                        Invoke-WpfOperation ([string]$button.CommandParameter) `
                            ([string]$button.Tag)
                    } -OnFailure { param($message) Show-WpfCallbackFailure $message })
                }
            }
        )
        (Find-WpfControl "PromoteButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                if ([System.Windows.MessageBox]::Show(
                    "Promote only this fully validated Candidate?", "Confirm Promote",
                    "YesNo", "Warning"
                ) -eq "Yes") { Invoke-WpfOperation "PromoteCandidate" }
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        (Find-WpfControl "ReverseButton").Add_Click({
            [void](Invoke-ControlCenterUiCallback -Callback {
                if ([System.Windows.MessageBox]::Show(
                    "Reverse to the exact Previous Stable release?", "Confirm Reverse",
                    "YesNo", "Warning"
                ) -eq "Yes") { Invoke-WpfOperation "ReverseStable" }
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        $timer = New-Object Windows.Threading.DispatcherTimer
        $timer.Interval = [TimeSpan]::FromSeconds(5)
        $timer.Add_Tick({
            [void](Invoke-ControlCenterUiCallback -Callback { Refresh-WpfStatus } `
                -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        $operationTimer = New-Object Windows.Threading.DispatcherTimer
        $operationTimer.Interval = [TimeSpan]::FromMilliseconds(400)
        $operationTimer.Add_Tick({
            [void](Invoke-ControlCenterUiCallback -Callback {
                if (-not $script:wpfOperation -or -not $script:wpfOperation.HasExited) {
                    return
                }
                $script:wpfOperation.WaitForExit()
                $script:wpfOperation.Refresh()
                $exitCode = [int]$script:wpfOperation.ExitCode
                $finished = $script:wpfOperationName
                $presentation = $null
                try {
                    $output = Get-ControlCenterOperationText `
                        -Path $script:wpfOperationOutputPath
                    $errorText = Get-ControlCenterOperationText `
                        -Path $script:wpfOperationErrorPath
                    $operationResult = Read-ControlCenterOperationResult `
                        -Path $script:wpfOperationResultPath
                    $releaseAfter = Get-ReleaseControlState
                    $presentation = Resolve-ControlCenterOperationPresentation `
                        -Operation $finished -ProcessExitCode $exitCode `
                        -Result $operationResult -ReleaseState $releaseAfter `
                        -ExpectedRelease $script:wpfOperationExpectedRelease `
                        -ExpectedValidationKey $script:wpfOperationValidationKey `
                        -ExpectedControlRevision ([string]$controlIdentity.source_revision) `
                        -StandardOutput $output -StandardError $errorText
                    [void](Invoke-ControlCenterUiCallback -Callback {
                        Write-ControlCenterOperationEvent -Operation $finished `
                            -Presentation $presentation -ProcessExitCode $exitCode `
                            -Result $operationResult
                    })
                } finally {
                    Remove-Item -LiteralPath `
                        $script:wpfOperationOutputPath,$script:wpfOperationErrorPath,`
                        $script:wpfOperationResultPath `
                        -Force -ErrorAction SilentlyContinue
                    $completedProcess = $script:wpfOperation
                    $script:wpfOperation = $null
                    try { $completedProcess.Dispose() } catch {}
                    Set-WpfReleaseBusy -Busy $false
                    [void](Invoke-ControlCenterUiCallback -Callback { Refresh-WpfStatus })
                }
                if ([string]$presentation.state -eq "FAILURE") {
                    [System.Windows.MessageBox]::Show(
                        $(if ($presentation.diagnostic) {
                            [string]$presentation.diagnostic
                        } else {
                            "$finished failed."
                        }), "$finished failed", "OK", "Error"
                    ) | Out-Null
                } elseif ([string]$presentation.state -eq "INDETERMINATE") {
                    [System.Windows.MessageBox]::Show(
                        ([string]$presentation.diagnostic),
                        "$finished result unavailable", "OK", "Warning"
                    ) | Out-Null
                } elseif ($finished -in @(
                    "VerifyMigrationCompatibility", "ApproveCompatibility", "PromoteCandidate", "ReverseStable"
                )) {
                    [System.Windows.MessageBox]::Show(
                        "$finished completed and authoritative state was refreshed.",
                        "$finished completed", "OK", "Information"
                    ) | Out-Null
                }
            } -OnFailure { param($message) Show-WpfCallbackFailure $message })
        })
        $window.Add_Closing({
            param($sender, $eventArgs)
            if (Test-WpfOperationActive) {
                $eventArgs.Cancel = $true
                try {
                    [System.Windows.MessageBox]::Show(
                        "Wait for the tracked operation to finish before closing.",
                        "Operation in progress", "OK", "Warning"
                    ) | Out-Null
                } catch {}
            }
        })
        $window.Add_Closed({ $timer.Stop(); $operationTimer.Stop() })
        $window.Add_ContentRendered({
            if (-not $script:wpfUiStartedRecorded) {
                $script:wpfUiStartedRecorded = $true
                [void](Invoke-ControlCenterUiCallback -Callback {
                    Write-ControlCenterUiStarted -Mode "WPF"
                })
            }
        })
        Refresh-WpfStatus
        $timer.Start()
        $operationTimer.Start()
        [void]$window.ShowDialog()
        return $true
    } catch {
        $script:wpfFailureReason = Protect-PreflightDiagnosticText $_.Exception.Message
        if (-not (Test-WpfFallbackAllowed `
            -ContentRendered ([bool]$script:wpfUiStartedRecorded))) {
            Write-Warning "WPF runtime failure contained without fallback: $($script:wpfFailureReason)"
            return $true
        }
        Write-Warning "WPF control center unavailable; using WinForms fallback: $($_.Exception.Message)"
        return $false
    }
}

function Show-ControlCenter {
    $controlIdentity = Assert-ControlCenterProcessIdentity
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $createdNew = $false
    $activationEvent = [System.Threading.EventWaitHandle]::new(
        $false,
        [System.Threading.EventResetMode]::AutoReset,
        "Local\XAUUSD-Forecaster-Control-Center",
        [ref]$createdNew
    )
    if (-not $createdNew) {
        [void]$activationEvent.Set()
        $activationEvent.Dispose()
        return
    }

    if (Show-WpfControlCenter) {
        $activationEvent.Dispose()
        return
    }

    $canvas = [System.Drawing.Color]::FromArgb(235, 237, 232)
    $surface = [System.Drawing.Color]::FromArgb(251, 251, 248)
    $ink = [System.Drawing.Color]::FromArgb(26, 35, 36)
    $muted = [System.Drawing.Color]::FromArgb(91, 101, 100)
    $line = [System.Drawing.Color]::FromArgb(207, 211, 207)
    $accent = [System.Drawing.Color]::FromArgb(29, 48, 49)
    $active = [System.Drawing.Color]::FromArgb(30, 116, 103)
    $green = [System.Drawing.Color]::FromArgb(31, 118, 72)
    $greenWash = [System.Drawing.Color]::FromArgb(224, 241, 231)
    $amber = [System.Drawing.Color]::FromArgb(151, 99, 15)
    $amberWash = [System.Drawing.Color]::FromArgb(250, 239, 210)
    $red = [System.Drawing.Color]::FromArgb(174, 58, 48)
    $redWash = [System.Drawing.Color]::FromArgb(249, 228, 224)
    $gray = [System.Drawing.Color]::FromArgb(91, 99, 97)
    $grayWash = [System.Drawing.Color]::FromArgb(232, 235, 232)
    $headingFont = New-Object System.Drawing.Font("Segoe UI Semibold", 12.5)
    $bodyFont = New-Object System.Drawing.Font("Segoe UI Variable Text", 9.5)
    $monoFont = New-Object System.Drawing.Font("Cascadia Mono", 8.5)
    $toolTip = New-Object System.Windows.Forms.ToolTip
    $toolTip.AutoPopDelay = 12000
    $toolTip.InitialDelay = 250

    function New-UiLabel {
        param(
            [string]$Text = "",
            [System.Drawing.Font]$Font = $bodyFont,
            [System.Drawing.Color]$Color = $ink,
            [System.Windows.Forms.DockStyle]$Dock = [System.Windows.Forms.DockStyle]::None,
            [System.Drawing.ContentAlignment]$Align = [System.Drawing.ContentAlignment]::MiddleLeft
        )
        $label = New-Object System.Windows.Forms.Label
        $label.Text = $Text
        $label.Font = $Font
        $label.ForeColor = $Color
        $label.Dock = $Dock
        $label.TextAlign = $Align
        $label.AutoEllipsis = $true
        return $label
    }

    function New-UiButton {
        param([string]$Text, [string]$Kind = "Neutral", [int]$Width = 118)
        $button = New-Object System.Windows.Forms.Button
        $button.Text = $Text
        $button.Width = $Width
        $button.Height = 34
        $button.Margin = New-Object System.Windows.Forms.Padding(0, 0, 8, 0)
        $button.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $button.FlatAppearance.BorderSize = 1
        $button.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 8.7)
        if ($Kind -eq "Primary") {
            $button.BackColor = $accent
            $button.ForeColor = [System.Drawing.Color]::White
            $button.FlatAppearance.BorderColor = $accent
        } elseif ($Kind -eq "Positive") {
            $button.BackColor = $greenWash
            $button.ForeColor = $green
            $button.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(178, 214, 191)
        } elseif ($Kind -eq "Danger") {
            $button.BackColor = $surface
            $button.ForeColor = $red
            $button.FlatAppearance.BorderColor = $red
        } else {
            $button.BackColor = $surface
            $button.ForeColor = $ink
            $button.FlatAppearance.BorderColor = $line
        }
        return $button
    }

    function Add-SoftPanelBorder {
        param([System.Windows.Forms.Panel]$Panel, [System.Drawing.Color]$Color = $line)
        $borderColor = $Color
        $paintBorder = {
            param($sender, $eventArgs)
            $pen = New-Object System.Drawing.Pen($borderColor)
            try {
                $eventArgs.Graphics.DrawRectangle(
                    $pen, 0, 0,
                    [Math]::Max(0, $sender.ClientSize.Width - 1),
                    [Math]::Max(0, $sender.ClientSize.Height - 1)
                )
            } finally { $pen.Dispose() }
        }.GetNewClosure()
        $Panel.Add_Paint($paintBorder)
    }

    function Set-StatusBadge {
        param([System.Windows.Forms.Label]$Label, [string]$State)
        $normalized = if ($State) { $State.ToUpperInvariant() } else { "UNKNOWN" }
        $Label.Text = "  $normalized  "
        if ($normalized -in @("HEALTHY", "RUNNING", "LIVE", "MARKET CLOSED", "API OK", "SYNC OK", "PASSED", "READY", "CURRENT", "ENABLED")) {
            $Label.ForeColor = $green
            $Label.BackColor = $greenWash
        } elseif ($normalized -in @("DEGRADED", "NOT_CONFIGURED", "PARTIAL", "TESTING", "STAGING", "NEW", "SYNC DEGRADED", "OBSERVING", "PENDING", "BROADCAST_PENDING", "BROADCAST_BLOCKED")) {
            $Label.ForeColor = $amber
            $Label.BackColor = $amberWash
        } elseif ($normalized -in @("FAILED", "ERROR", "OFFLINE", "STOPPED", "RECOVERY REQUIRED", "DEPLOYMENT DRIFT")) {
            $Label.ForeColor = $red
            $Label.BackColor = $redWash
        } else {
            $Label.ForeColor = $gray
            $Label.BackColor = $grayWash
        }
    }

    function New-SummaryCell {
        param([string]$Caption)
        $panel = New-Object System.Windows.Forms.Panel
        $panel.Dock = "Fill"
        $panel.BackColor = $surface
        $panel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 1, 0)
        $captionLabel = New-UiLabel -Text $Caption.ToUpperInvariant() -Font (New-Object System.Drawing.Font("Segoe UI Semibold", 7.5)) -Color $muted
        $captionLabel.Location = New-Object System.Drawing.Point(14, 10)
        $captionLabel.Size = New-Object System.Drawing.Size(190, 20)
        $valueLabel = New-UiLabel -Text "CHECKING" -Font (New-Object System.Drawing.Font("Segoe UI Variable Display", 10.5, [System.Drawing.FontStyle]::Bold))
        $valueLabel.Location = New-Object System.Drawing.Point(12, 32)
        $valueLabel.AutoSize = $true
        $panel.Controls.Add($captionLabel)
        $panel.Controls.Add($valueLabel)
        return [pscustomobject]@{ Panel = $panel; Value = $valueLabel }
    }

    function New-ReleaseCard {
        param([string]$Title, [bool]$Emphasized = $false)
        $card = New-Object System.Windows.Forms.Panel
        $card.Dock = "Fill"
        $card.BackColor = $surface
        $card.BorderStyle = [System.Windows.Forms.BorderStyle]::None
        $card.Margin = New-Object System.Windows.Forms.Padding($(if ($Emphasized) { 5 } else { 0 }), 0, $(if ($Emphasized) { 5 } else { 0 }), 0)

        $titleLabel = New-UiLabel -Text $Title -Font (New-Object System.Drawing.Font("Segoe UI Semibold", 12))
        $titleLabel.Location = New-Object System.Drawing.Point(16, 12)
        $titleLabel.Size = New-Object System.Drawing.Size(180, 24)
        $card.Controls.Add($titleLabel)

        $badge = New-UiLabel -Text "  LOADING  " -Font (New-Object System.Drawing.Font("Segoe UI Semibold", 8))
        $badge.AutoSize = $true
        $badge.Location = New-Object System.Drawing.Point(16, 43)
        Set-StatusBadge -Label $badge -State "UNKNOWN"
        $card.Controls.Add($badge)

        $git = New-UiLabel -Text "Git       --" -Font $monoFont -Color $ink
        $git.Location = New-Object System.Drawing.Point(16, 76)
        $git.Size = New-Object System.Drawing.Size(165, 21)
        $git.Anchor = "Top,Left,Right"
        $worker = New-UiLabel -Text "Worker    --" -Font $monoFont -Color $muted
        $worker.Location = New-Object System.Drawing.Point(16, 98)
        $worker.Size = New-Object System.Drawing.Size(165, 21)
        $worker.Anchor = "Top,Left,Right"
        $windows = New-UiLabel -Text "Windows   --" -Font $monoFont -Color $muted
        $windows.Location = New-Object System.Drawing.Point(16, 120)
        $windows.Size = New-Object System.Drawing.Size(165, 21)
        $windows.Anchor = "Top,Left,Right"
        $detail = New-UiLabel -Text "Loading release identity..." -Color $muted
        $detail.Location = New-Object System.Drawing.Point(16, 148)
        $detail.Size = New-Object System.Drawing.Size(165, 42)
        $detail.Anchor = "Top,Left,Right"
        $card.Controls.AddRange(@($git, $worker, $windows, $detail))
        Add-SoftPanelBorder -Panel $card
        return [pscustomobject]@{
            Panel = $card; Badge = $badge; Git = $git; Worker = $worker
            Windows = $windows; Detail = $detail
        }
    }

    function Get-ShortIdentity {
        param([object]$Value, [int]$Length = 12)
        $text = [string]$Value
        if (-not $text) { return "--" }
        if ($text.Length -le $Length) { return $text }
        return $text.Substring(0, $Length)
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "XAUUSD Forecaster Control Center"
    $form.Size = New-Object System.Drawing.Size(1180, 970)
    $form.MinimumSize = New-Object System.Drawing.Size(1060, 840)
    $form.StartPosition = "CenterScreen"
    $form.ShowInTaskbar = $true
    $form.BackColor = $canvas
    $form.Font = $bodyFont
    $form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi
    $form.AutoScroll = $false
    $doubleBufferProperty = $form.GetType().GetProperty("DoubleBuffered", [System.Reflection.BindingFlags]"Instance,NonPublic")
    if ($doubleBufferProperty) { $doubleBufferProperty.SetValue($form, $true, $null) }

    $root = New-Object System.Windows.Forms.TableLayoutPanel
    $root.Dock = "Top"
    $root.AutoSize = $false
    $root.Height = 932
    $root.Padding = New-Object System.Windows.Forms.Padding(24, 20, 24, 20)
    $root.ColumnCount = 1
    $root.RowCount = 5
    [void]$root.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    foreach ($height in @(106, 256, 330, 140, 60)) {
        [void]$root.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, $height)))
    }
    $viewport = New-Object System.Windows.Forms.Panel
    $viewport.Dock = "Fill"
    $viewport.AutoScroll = $true
    $viewport.BackColor = $canvas
    $viewport.Controls.Add($root)
    $form.Controls.Add($viewport)

    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = "Fill"
    $header.BackColor = $accent
    $header.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 14)
    $title = New-UiLabel -Text "XAUUSD Forecaster" -Font (New-Object System.Drawing.Font("Segoe UI Variable Display", 20, [System.Drawing.FontStyle]::Bold)) -Color ([System.Drawing.Color]::White)
    $title.Location = New-Object System.Drawing.Point(20, 18)
    $title.Size = New-Object System.Drawing.Size(360, 34)
    $subtitle = New-UiLabel -Text "LOCAL CONTROL CENTER  /  FORWARD-ONLY OPERATIONS" -Font (New-Object System.Drawing.Font("Cascadia Mono", 8)) -Color ([System.Drawing.Color]::FromArgb(196, 213, 208))
    $subtitle.Location = New-Object System.Drawing.Point(22, 55)
    $subtitle.Size = New-Object System.Drawing.Size(420, 22)
    $controlBundle = Get-RuntimeControlBundleIdentity
    $controlIdentityLabel = New-UiLabel -Text $(if ($controlBundle) {
        "Control Plane  $(([string]$controlBundle.source_revision).Substring(0, 12))  EXACT | HASH VERIFIED"
    } else { "Control Plane  --  UNVERIFIED" }) -Font (New-Object System.Drawing.Font("Cascadia Mono", 8)) -Color ([System.Drawing.Color]::White)
    $controlIdentityLabel.Location = New-Object System.Drawing.Point(22, 74)
    $controlIdentityLabel.Size = New-Object System.Drawing.Size(440, 18)
    $businessRevision = Get-CodeRevision
    $businessIdentityLabel = New-UiLabel -Text "Business Runtime  $(if ($businessRevision) { $businessRevision.Substring(0, 12) } else { '--' })" -Font (New-Object System.Drawing.Font("Cascadia Mono", 8)) -Color ([System.Drawing.Color]::FromArgb(196, 213, 208))
    $businessIdentityLabel.Location = New-Object System.Drawing.Point(22, 89)
    $businessIdentityLabel.Size = New-Object System.Drawing.Size(440, 18)
    $header.Controls.AddRange(@($title, $subtitle, $controlIdentityLabel, $businessIdentityLabel))

    $summaryGrid = New-Object System.Windows.Forms.TableLayoutPanel
    $summaryGrid.ColumnCount = 4
    $summaryGrid.RowCount = 1
    $summaryGrid.BackColor = $line
    $summaryHost = New-Object System.Windows.Forms.Panel
    $summaryHost.Dock = "Right"
    $summaryHost.Width = 640
    $summaryHost.Padding = New-Object System.Windows.Forms.Padding(0, 15, 20, 15)
    $summaryHost.BackColor = $accent
    $summaryGrid.Dock = "Fill"
    foreach ($width in @(25, 25, 25, 25)) {
        [void]$summaryGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, $width)))
    }
    $overallSummary = New-SummaryCell -Caption "Overall"
    $stableSummary = New-SummaryCell -Caption "Stable"
    $candidateSummary = New-SummaryCell -Caption "Candidate"
    $runtimeSummary = New-SummaryCell -Caption "Local runtime"
    $summaryGrid.Controls.Add($overallSummary.Panel, 0, 0)
    $summaryGrid.Controls.Add($stableSummary.Panel, 1, 0)
    $summaryGrid.Controls.Add($candidateSummary.Panel, 2, 0)
    $summaryGrid.Controls.Add($runtimeSummary.Panel, 3, 0)
    $summaryHost.Controls.Add($summaryGrid)
    $header.Controls.Add($summaryHost)
    $root.Controls.Add($header, 0, 0)

    $servicesPanel = New-Object System.Windows.Forms.Panel
    $servicesPanel.Dock = "Fill"
    $servicesPanel.BackColor = $surface
    $servicesPanel.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $servicesPanel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 14)
    $servicesLayout = New-Object System.Windows.Forms.TableLayoutPanel
    $servicesLayout.Dock = "Fill"
    $servicesLayout.ColumnCount = 1
    $servicesLayout.RowCount = 2
    [void]$servicesLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$servicesLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 45)))
    [void]$servicesLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $servicesHeader = New-Object System.Windows.Forms.Panel
    $servicesHeader.Dock = "Fill"
    $servicesHeader.Margin = New-Object System.Windows.Forms.Padding(0)
    $servicesHeader.BackColor = $surface
    $servicesTitle = New-UiLabel -Text "Local services" -Font $headingFont
    $servicesTitle.Location = New-Object System.Drawing.Point(18, 8)
    $servicesTitle.Size = New-Object System.Drawing.Size(300, 24)
    $servicesHint = New-UiLabel -Text "Five core owners plus one isolated optional publisher" -Font (New-Object System.Drawing.Font("Segoe UI Variable Text", 8.5)) -Color $muted
    $servicesHint.Location = New-Object System.Drawing.Point(19, 30)
    $servicesHint.Size = New-Object System.Drawing.Size(360, 18)
    $servicesHeader.Controls.AddRange(@($servicesTitle, $servicesHint))

    $serviceDescriptions = @{
        quote = "Receives the cTrader XAUUSD quote stream"
        collector = "Builds the five-minute decision and training ledger"
        annotator = "Classifies eligible news evidence"
        api = "Serves the local dashboard contract"
        sync = "Publishes bounded dashboard mirrors"
        broadcast = "Publishes only compact PUBLIC_LIVE_V1 state when enabled"
    }
    $serviceGrid = New-Object System.Windows.Forms.TableLayoutPanel
    $serviceGrid.Dock = "Fill"
    $serviceGrid.Margin = New-Object System.Windows.Forms.Padding(0)
    $serviceGrid.Padding = New-Object System.Windows.Forms.Padding(18, 0, 18, 0)
    $serviceGrid.ColumnCount = 4
    $serviceGrid.RowCount = $services.Count
    [void]$serviceGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 35)))
    [void]$serviceGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 38)))
    [void]$serviceGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 145)))
    [void]$serviceGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Absolute, 145)))
    $statusLabels = @{}
    $actionButtons = New-Object System.Collections.ArrayList
    $serviceIndex = 0
    foreach ($service in $services) {
        [void]$serviceGrid.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 39)))
        $namePanel = New-Object System.Windows.Forms.Panel
        $namePanel.Dock = "Fill"
        $namePanel.Margin = New-Object System.Windows.Forms.Padding(0)
        $name = New-UiLabel -Text $service.Label -Font (New-Object System.Drawing.Font("Segoe UI Semibold", 9.3)) -Dock "Fill"
        $name.Padding = New-Object System.Windows.Forms.Padding(8, 0, 0, 0)
        $namePanel.Controls.Add($name)
        $description = New-UiLabel -Text $serviceDescriptions[$service.Key] -Color $muted -Dock "Fill"
        $description.Padding = New-Object System.Windows.Forms.Padding(6, 0, 0, 0)
        $status = New-UiLabel -Text "  CHECKING  " -Font (New-Object System.Drawing.Font("Segoe UI Semibold", 8))
        $status.AutoSize = $true
        $status.Anchor = "Left"
        Set-StatusBadge -Label $status -State "UNKNOWN"
        $statusLabels[$service.Key] = $status

        $serviceActions = New-Object System.Windows.Forms.FlowLayoutPanel
        $serviceActions.Dock = "Fill"
        $serviceActions.FlowDirection = "LeftToRight"
        $serviceActions.WrapContents = $false
        $serviceActions.Padding = New-Object System.Windows.Forms.Padding(0, 4, 0, 0)
        $startButton = New-UiButton -Text "Start" -Kind "Positive" -Width 62
        $startButton.Tag = $service.Key
        $startButton.Add_Click({ param($sender) Invoke-GuiOperation -Operation "ServiceStart" -TargetKey $sender.Tag })
        $stopButton = New-UiButton -Text "Stop" -Width 62
        $stopButton.Tag = $service.Key
        $stopButton.Add_Click({ param($sender) Invoke-GuiOperation -Operation "ServiceStop" -TargetKey $sender.Tag })
        $serviceActions.Controls.AddRange(@($startButton, $stopButton))
        [void]$actionButtons.Add($startButton)
        [void]$actionButtons.Add($stopButton)

        $serviceGrid.Controls.Add($namePanel, 0, $serviceIndex)
        $serviceGrid.Controls.Add($description, 1, $serviceIndex)
        $serviceGrid.Controls.Add($status, 2, $serviceIndex)
        $serviceGrid.Controls.Add($serviceActions, 3, $serviceIndex)
        $serviceIndex++
    }
    $servicesLayout.Controls.Add($servicesHeader, 0, 0)
    $servicesLayout.Controls.Add($serviceGrid, 0, 1)
    $servicesPanel.Controls.Add($servicesLayout)
    $root.Controls.Add($servicesPanel, 0, 1)

    $releasePanel = New-Object System.Windows.Forms.Panel
    $releasePanel.Dock = "Fill"
    $releasePanel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 14)
    $releaseLayout = New-Object System.Windows.Forms.TableLayoutPanel
    $releaseLayout.Dock = "Fill"
    $releaseLayout.ColumnCount = 1
    $releaseLayout.RowCount = 2
    [void]$releaseLayout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$releaseLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 45)))
    [void]$releaseLayout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $releaseHeader = New-Object System.Windows.Forms.Panel
    $releaseHeader.Dock = "Fill"
    $releaseHeader.Margin = New-Object System.Windows.Forms.Padding(0)
    $releaseHeader.BackColor = $canvas
    $releaseTitle = New-UiLabel -Text "Release control" -Font $headingFont
    $releaseTitle.Location = New-Object System.Drawing.Point(0, 3)
    $releaseTitle.Size = New-Object System.Drawing.Size(300, 24)
    $releaseHint = New-UiLabel -Text "One exact Git / Worker / Windows release at every boundary" -Font (New-Object System.Drawing.Font("Segoe UI Variable Text", 8.5)) -Color $muted
    $releaseHint.Location = New-Object System.Drawing.Point(1, 27)
    $releaseHint.Size = New-Object System.Drawing.Size(500, 18)
    $releaseHeader.Controls.AddRange(@($releaseTitle, $releaseHint))

    $releaseGrid = New-Object System.Windows.Forms.TableLayoutPanel
    $releaseGrid.Dock = "Fill"
    $releaseGrid.Margin = New-Object System.Windows.Forms.Padding(0)
    $releaseGrid.ColumnCount = 3
    $releaseGrid.RowCount = 1
    [void]$releaseGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 29)))
    [void]$releaseGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 42)))
    [void]$releaseGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 29)))
    $stableCard = New-ReleaseCard -Title "Stable"
    $candidateCard = New-ReleaseCard -Title "Release Candidate" -Emphasized $true
    $previousCard = New-ReleaseCard -Title "Previous Stable"
    $candidateCard.Panel.BackColor = [System.Drawing.Color]::FromArgb(246, 250, 248)
    $candidateCard.Panel.Add_Paint({
        param($sender, $eventArgs)
        $brush = New-Object System.Drawing.SolidBrush($active)
        try { $eventArgs.Graphics.FillRectangle($brush, 0, 0, $sender.ClientSize.Width, 4) }
        finally { $brush.Dispose() }
    })
    $candidateCard.Detail.Size = New-Object System.Drawing.Size(165, 20)
    $candidateCard.Detail.Location = New-Object System.Drawing.Point(16, 143)
    $releaseGrid.Controls.Add($stableCard.Panel, 0, 0)
    $releaseGrid.Controls.Add($candidateCard.Panel, 1, 0)
    $releaseGrid.Controls.Add($previousCard.Panel, 2, 0)
    $releaseLayout.Controls.Add($releaseHeader, 0, 0)
    $releaseLayout.Controls.Add($releaseGrid, 0, 1)
    $releasePanel.Controls.Add($releaseLayout)
    $root.Controls.Add($releasePanel, 0, 2)

    $candidateChecks = New-Object System.Windows.Forms.TableLayoutPanel
    $candidateChecks.Location = New-Object System.Drawing.Point(16, 166)
    $candidateChecks.Anchor = "Top,Left,Right"
    $candidateChecks.Size = New-Object System.Drawing.Size(165, 40)
    $candidateChecks.ColumnCount = 2
    $candidateChecks.RowCount = 2
    [void]$candidateChecks.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
    [void]$candidateChecks.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
    foreach ($height in @(20, 20)) {
        [void]$candidateChecks.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, $height)))
    }
    $candidateCheckLabels = @{}
    foreach ($item in @(
        @("windows", "Runtime + heartbeat"), @("contracts", "API routes"),
        @("cpu", "CPU headroom"), @("limits", "5xx + 1102")
    )) {
        $checkLabel = New-UiLabel -Text "$($item[1]): waiting" -Font (New-Object System.Drawing.Font("Segoe UI Variable Text", 8)) -Color $muted -Dock "Fill"
        $candidateCheckLabels[$item[0]] = $checkLabel
        $index = $candidateCheckLabels.Count - 1
        $candidateChecks.Controls.Add($checkLabel, ($index % 2), [Math]::Floor($index / 2))
    }
    $candidateCard.Panel.Controls.Add($candidateChecks)
    $candidateReason = New-UiLabel -Text "Waiting for release state..." -Color $muted
    $candidateReason.Location = New-Object System.Drawing.Point(16, 210)
    $candidateReason.Anchor = "Left,Right,Bottom"
    $candidateReason.Size = New-Object System.Drawing.Size(165, 18)
    $candidateReason.Font = New-Object System.Drawing.Font("Segoe UI Variable Text", 8)
    $candidateCard.Panel.Controls.Add($candidateReason)

    $openStableButton = New-UiButton -Text "Open Stable" -Width 120
    $openStableButton.Location = New-Object System.Drawing.Point(16, 232)
    $openStableButton.Anchor = "Left,Bottom"
    $openStableButton.Enabled = $false
    $openStableButton.Add_Click({ Start-Process $dashboardUrl })
    $stableCard.Panel.Controls.Add($openStableButton)
    [void]$actionButtons.Add($openStableButton)

    $bootstrapButton = New-UiButton -Text "Bootstrap Release Control" -Kind "Primary" -Width 210
    $bootstrapButton.Location = New-Object System.Drawing.Point(16, 232)
    $bootstrapButton.Anchor = "Left,Bottom"
    $bootstrapButton.Visible = $false
    $bootstrapButton.Add_Click({
        if ([System.Windows.Forms.MessageBox]::Show(
            "Initialize Release Control from the exact current production Worker and Windows runtime? Existing state will never be overwritten.",
            "Confirm Bootstrap Release Control", "YesNo", "Warning"
        ) -eq "Yes") { Invoke-GuiOperation -Operation "BootstrapRelease" }
    })
    $stableCard.Panel.Controls.Add($bootstrapButton)
    [void]$actionButtons.Add($bootstrapButton)

    $openCandidateButton = New-UiButton -Text "Open Candidate" -Width 126
    $openCandidateButton.Location = New-Object System.Drawing.Point(16, 235)
    $openCandidateButton.Anchor = "Left,Bottom"
    $openCandidateButton.Enabled = $false
    $openCandidateButton.Add_Click({
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.candidate) { return }
        try {
            $details = Get-CloudflareVersionDetails `
                -VersionId ([string]$state.candidate.worker_version_id)
            $url = Get-ReleaseVersionPreviewUrl `
                -Version $details -Candidate $state.candidate
            if (-not $url -or $url -ne [string]$state.candidate.browser_url) {
                throw "Candidate URL unavailable"
            }
            Start-Process $url
        } catch {
            [System.Windows.Forms.MessageBox]::Show(
                "Candidate URL unavailable: $($_.Exception.Message)",
                "Open Candidate", "OK", "Warning"
            ) | Out-Null
        }
    })
    $candidateCard.Panel.Controls.Add($openCandidateButton)
    [void]$actionButtons.Add($openCandidateButton)

    $verifyMigrationButton = New-UiButton `
        -Text "Verify Migration" -Width 150
    $verifyMigrationButton.Location = New-Object System.Drawing.Point(136, 235)
    $verifyMigrationButton.Anchor = "Left,Bottom"
    $verifyMigrationButton.Enabled = $false
    $verifyMigrationButton.Visible = $false
    $verifyMigrationButton.Add_Click({
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.candidate) { return }
        $files = @($state.candidate.validation.review_files) -join "`n"
        $message = "Verify the exact Candidate migration ledger, live D1 capabilities, Stable/Reverse compatibility, and News CURRENT?`n`n$files"
        if ([System.Windows.Forms.MessageBox]::Show(
            $message, "Verify Coordinated Migration", "YesNo", "Warning"
        ) -eq "Yes") { Invoke-GuiOperation -Operation "VerifyMigrationCompatibility" }
    })
    $candidateCard.Panel.Controls.Add($verifyMigrationButton)
    [void]$actionButtons.Add($verifyMigrationButton)

    $approveCompatibilityButton = New-UiButton `
        -Text "Approve Compatibility" -Width 156
    $approveCompatibilityButton.Location = New-Object System.Drawing.Point(136, 235)
    $approveCompatibilityButton.Anchor = "Left,Bottom"
    $approveCompatibilityButton.Enabled = $false
    $approveCompatibilityButton.Visible = $false
    $approveCompatibilityButton.Add_Click({
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.candidate) { return }
        $files = @($state.candidate.validation.review_files) -join "`n"
        $message = "Approve the reviewed non-destructive platform configuration for this exact Candidate only?`n`nValidation key: $($state.candidate.validation_key)`n`n$files"
        if ([System.Windows.Forms.MessageBox]::Show(
            $message, "Approve Reviewed Compatibility", "YesNo", "Warning"
        ) -eq "Yes") { Invoke-GuiOperation -Operation "ApproveCompatibility" }
    })
    $candidateCard.Panel.Controls.Add($approveCompatibilityButton)
    [void]$actionButtons.Add($approveCompatibilityButton)

    $promoteButton = New-UiButton -Text "Promote Candidate" -Kind "Primary" -Width 112
    $promoteButton.Location = New-Object System.Drawing.Point(300, 235)
    $promoteButton.Anchor = "Left,Bottom"
    $promoteButton.Enabled = $false
    $promoteButton.Add_Click({
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.candidate) { return }
        $message = "Promote this exact release to Stable?`n`nGit: $($state.candidate.git_sha)`nWorker: $($state.candidate.worker_version_id)`nWindows: $($state.candidate.windows_revision)"
        if ([System.Windows.Forms.MessageBox]::Show(
            $message, "Confirm Promote Candidate", "YesNo", "Warning"
        ) -eq "Yes") { Invoke-GuiOperation -Operation "PromoteCandidate" }
    })
    $candidateCard.Panel.Controls.Add($promoteButton)
    [void]$actionButtons.Add($promoteButton)

    $reverseButton = New-UiButton -Text "Reverse Stable" -Kind "Danger" -Width 150
    $reverseButton.Location = New-Object System.Drawing.Point(16, 232)
    $reverseButton.Anchor = "Left,Bottom"
    $reverseButton.Enabled = $false
    $reverseButton.Add_Click({
        $state = Get-ReleaseControlState
        if (-not $state -or -not $state.previous_stable) { return }
        $message = "Reverse Stable to this exact release?`n`nGit: $($state.previous_stable.git_sha)`nWorker: $($state.previous_stable.worker_version_id)`nWindows: $($state.previous_stable.windows_revision)"
        if ([System.Windows.Forms.MessageBox]::Show(
            $message, "Confirm Reverse Stable", "YesNo", "Warning"
        ) -eq "Yes") { Invoke-GuiOperation -Operation "ReverseStable" }
    })
    $previousCard.Panel.Controls.Add($reverseButton)
    [void]$actionButtons.Add($reverseButton)
    $reverseReason = New-UiLabel -Text "Waiting for release state..." -Color $muted
    $reverseReason.Location = New-Object System.Drawing.Point(16, 199)
    $reverseReason.Anchor = "Left,Right,Bottom"
    $reverseReason.Size = New-Object System.Drawing.Size(165, 28)
    $reverseReason.Font = New-Object System.Drawing.Font("Segoe UI Variable Text", 8)
    $previousCard.Panel.Controls.Add($reverseReason)

    function Update-ReleaseCardLayout {
        foreach ($card in @($stableCard, $candidateCard, $previousCard)) {
            $contentWidth = [Math]::Max(120, $card.Panel.ClientSize.Width - 32)
            $card.Git.Width = $contentWidth
            $card.Worker.Width = $contentWidth
            $card.Windows.Width = $contentWidth
            $card.Detail.Width = $contentWidth
        }
        $candidateWidth = [Math]::Max(120, $candidateCard.Panel.ClientSize.Width - 32)
        $candidateChecks.Width = $candidateWidth
        $candidateReason.Width = $candidateWidth
        $previousWidth = [Math]::Max(120, $previousCard.Panel.ClientSize.Width - 32)
        $reverseReason.Width = $previousWidth
    }
    $form.Add_SizeChanged({ Update-ReleaseCardLayout })

    $actionsPanel = New-Object System.Windows.Forms.TableLayoutPanel
    $actionsPanel.Dock = "Fill"
    $actionsPanel.ColumnCount = 3
    $actionsPanel.RowCount = 1
    $actionsPanel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 12)
    [void]$actionsPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 36)))
    [void]$actionsPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 30)))
    [void]$actionsPanel.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 34)))
    $root.Controls.Add($actionsPanel, 0, 3)

    function New-ActionGroup {
        param([string]$Title)
        $panel = New-Object System.Windows.Forms.Panel
        $panel.Dock = "Fill"
        $panel.BackColor = $surface
        $panel.BorderStyle = [System.Windows.Forms.BorderStyle]::None
        $panel.Margin = New-Object System.Windows.Forms.Padding(0, 0, 10, 0)
        Add-SoftPanelBorder -Panel $panel
        $label = New-UiLabel -Text $Title -Font (New-Object System.Drawing.Font("Segoe UI Semibold", 9)) -Color $muted
        $label.Location = New-Object System.Drawing.Point(14, 10)
        $label.Size = New-Object System.Drawing.Size(230, 22)
        $flow = New-Object System.Windows.Forms.FlowLayoutPanel
        $flow.Location = New-Object System.Drawing.Point(14, 43)
        $flow.Anchor = "Top,Left,Right"
        $flow.Size = New-Object System.Drawing.Size(170, 82)
        $flow.WrapContents = $true
        $panel.Controls.AddRange(@($label, $flow))
        return [pscustomobject]@{ Panel = $panel; Flow = $flow }
    }
    $batchGroup = New-ActionGroup -Title "Batch operations"
    $toolsGroup = New-ActionGroup -Title "Tools"
    $systemGroup = New-ActionGroup -Title "System"
    $actionsPanel.Controls.Add($batchGroup.Panel, 0, 0)
    $actionsPanel.Controls.Add($toolsGroup.Panel, 1, 0)
    $systemGroup.Panel.Margin = New-Object System.Windows.Forms.Padding(0)
    $actionsPanel.Controls.Add($systemGroup.Panel, 2, 0)

    $startAll = New-UiButton -Text "Start All" -Kind "Primary" -Width 100
    $startAll.Add_Click({ Invoke-GuiOperation -Operation "Start" })
    $restartAll = New-UiButton -Text "Restart All" -Width 105
    $restartAll.Add_Click({ Invoke-GuiOperation -Operation "Restart" })
    $stopAll = New-UiButton -Text "Stop All" -Kind "Danger" -Width 100
    $stopAll.Add_Click({
        if ([System.Windows.Forms.MessageBox]::Show(
            "Stop every local XAUUSD Forecaster service?", "Confirm Stop All", "YesNo", "Warning"
        ) -eq "Yes") { Invoke-GuiOperation -Operation "Stop" }
    })
    $batchGroup.Flow.Controls.AddRange(@($startAll, $restartAll, $stopAll))
    foreach ($button in @($startAll, $restartAll, $stopAll)) { [void]$actionButtons.Add($button) }

    $openSite = New-UiButton -Text "Open Dashboard" -Width 130
    $openSite.Add_Click({ Start-Process $dashboardUrl })
    $openLogs = New-UiButton -Text "Open Logs" -Width 100
    $openLogs.Add_Click({ Start-Process explorer.exe $logRoot })
    $refreshButton = New-UiButton -Text "Refresh" -Width 90
    $toolsGroup.Flow.Controls.AddRange(@($openSite, $openLogs, $refreshButton))

    $enableAuto = New-UiButton -Text "Enable Auto-start" -Width 135
    $enableAuto.Add_Click({ Enable-AutoStart; Request-GuiStatus })
    $disableAuto = New-UiButton -Text "Disable Auto-start" -Kind "Danger" -Width 138
    $disableAuto.Add_Click({
        if ([System.Windows.Forms.MessageBox]::Show(
            "Disable automatic startup at Windows logon?", "Confirm Disable Auto-start", "YesNo", "Warning"
        ) -eq "Yes") { Disable-AutoStart; Request-GuiStatus }
    })
    $clockButton = New-UiButton -Text "Repair Time (Admin)" -Width 142
    $clockButton.Add_Click({ Repair-WindowsTime; Request-GuiStatus })
    $systemGroup.Flow.Controls.AddRange(@($enableAuto, $disableAuto, $clockButton))

    $footer = New-Object System.Windows.Forms.Panel
    $footer.Dock = "Fill"
    $footer.BackColor = [System.Drawing.Color]::FromArgb(234, 231, 222)
    $footer.Margin = New-Object System.Windows.Forms.Padding(0)
    $operationLabel = New-UiLabel -Text "READY" -Font (New-Object System.Drawing.Font("Segoe UI Variable Text", 8.5, [System.Drawing.FontStyle]::Bold))
    $operationLabel.Location = New-Object System.Drawing.Point(14, 7)
    $operationLabel.Size = New-Object System.Drawing.Size(520, 22)
    $systemMetaLabel = New-UiLabel -Text "Windows Time: checking  /  Auto-start: checking  /  Last refresh: --" -Color $muted
    $systemMetaLabel.Dock = "Right"
    $systemMetaLabel.Width = 550
    $systemMetaLabel.Padding = New-Object System.Windows.Forms.Padding(0, 7, 14, 28)
    $systemMetaLabel.TextAlign = "MiddleRight"
    $note = New-UiLabel -Text "A powered-off PC cannot collect data. This control center never authorizes trading." -Color $muted
    $note.Location = New-Object System.Drawing.Point(14, 30)
    $note.Size = New-Object System.Drawing.Size(720, 20)
    $footer.Controls.AddRange(@($operationLabel, $systemMetaLabel, $note))
    $root.Controls.Add($footer, 0, 4)

    $script:guiOperation = $null
    $script:guiOperationName = ""
    $script:guiOperationOutputPath = ""
    $script:guiOperationErrorPath = ""
    $script:guiOperationResultPath = ""
    $script:guiOperationExpectedRelease = $null
    $script:guiOperationValidationKey = ""
    $script:lastGuiSnapshot = $null
    function Set-GuiBusy {
        param([bool]$Busy, [string]$Message)
        foreach ($button in $actionButtons) { $button.Enabled = -not $Busy }
        $refreshButton.Enabled = -not $Busy
        $operationLabel.Text = $Message.ToUpperInvariant()
        $operationLabel.ForeColor = if ($Busy) { $amber } else { $ink }
        $form.UseWaitCursor = $Busy
        if (-not $Busy -and $script:lastGuiSnapshot) { Apply-GuiStatus $script:lastGuiSnapshot }
    }
    function Invoke-GuiOperation {
        param([string]$Operation, [string]$TargetKey = "")
        if ($script:guiOperation) { return }
        $arguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $PSCommandPath), "-Action", $Operation,
            "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
            "-RepositoryRoot", ('"{0}"' -f $repositoryRoot),
            "-ExpectedControlScriptPath", ('"{0}"' -f $PSCommandPath),
            "-ExpectedControlRevision", ([string]$controlIdentity.source_revision)
        )
        if ($TargetKey) { $arguments += @("-ServiceKey", $TargetKey) }
        $script:guiOperationName = $Operation
        $script:guiOperationOutputPath = Join-Path $env:TEMP `
            ("xauusd-control-operation-{0}.out" -f ([guid]::NewGuid().ToString("N")))
        $script:guiOperationErrorPath = Join-Path $env:TEMP `
            ("xauusd-control-operation-{0}.err" -f ([guid]::NewGuid().ToString("N")))
        $script:guiOperationResultPath = Join-Path $env:TEMP `
            ("xauusd-control-operation-{0}.json" -f ([guid]::NewGuid().ToString("N")))
        $releaseBefore = Get-ReleaseControlState
        $script:guiOperationValidationKey = if ($releaseBefore -and
            $releaseBefore.candidate) {
            [string]$releaseBefore.candidate.validation_key
        } else { "" }
        $script:guiOperationExpectedRelease = if (
            $Operation -eq "PromoteCandidate" -and $releaseBefore
        ) { $releaseBefore.candidate } elseif (
            $Operation -eq "ReverseStable" -and $releaseBefore
        ) { $releaseBefore.previous_stable } else { $null }
        $arguments += @(
            "-OperationResultPath", ('"{0}"' -f $script:guiOperationResultPath)
        )
        $busyMessage = switch ($Operation) {
            "VerifyMigrationCompatibility" { "VERIFYING MIGRATION | tracked background operation in progress" }
            "ApproveCompatibility" { "APPROVING | tracked background operation in progress" }
            "PromoteCandidate" { "PROMOTING | tracked background operation in progress" }
            "ReverseStable" { "REVERSING | tracked background operation in progress" }
            default { "Working in background: $Operation" }
        }
        Set-GuiBusy -Busy $true -Message $busyMessage
        try {
            $script:guiOperation = Start-Process -FilePath "powershell.exe" `
                -ArgumentList $arguments -WorkingDirectory $moduleRoot `
                -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $script:guiOperationOutputPath `
                -RedirectStandardError $script:guiOperationErrorPath
        } catch {
            $script:guiOperation = $null
            Remove-Item -LiteralPath `
                $script:guiOperationOutputPath,$script:guiOperationErrorPath,`
                $script:guiOperationResultPath -Force -ErrorAction SilentlyContinue
            Set-GuiBusy -Busy $false -Message "Failed to start: $Operation"
            throw
        }
    }

    $statusSnapshotPath = Join-Path $env:TEMP ("xauusd-control-status-{0}.json" -f $PID)
    $script:statusRefreshProcess = $null
    $script:lastStatusRequest = [DateTime]::MinValue
    function Apply-GuiStatus {
        param([pscustomobject]$Snapshot)
        $script:lastGuiSnapshot = $Snapshot
        foreach ($row in @($Snapshot.services)) {
            $label = $statusLabels[$row.Key]
            if ($label) { Set-StatusBadge -Label $label -State ([string]$row.State) }
        }
        $summary = Get-ControlCenterSummaryPresentation -Snapshot $Snapshot
        Set-StatusBadge -Label $overallSummary.Value -State $summary.overall
        Set-StatusBadge -Label $runtimeSummary.Value -State $summary.local_runtime
        Set-StatusBadge -Label $candidateSummary.Value -State $summary.candidate_state
        $release = $Snapshot.release
        if ($release -and $release.stable) {
            $stableShort = Get-ShortIdentity $release.stable.git_sha
            $stableSummary.Value.Text = "  $stableShort  "
            $stableSummary.Value.ForeColor = [System.Drawing.Color]::White
            $stableSummary.Value.BackColor = $accent
            Set-StatusBadge -Label $stableCard.Badge -State ([string]$release.deployment_status)
            $stableCard.Git.Text = "Git       $(Get-ShortIdentity $release.stable.git_sha)"
            $stableCard.Worker.Text = "Worker    $(Get-ShortIdentity $release.stable.worker_version_id)"
            $stableCard.Windows.Text = "Windows   $(Get-ShortIdentity $release.stable.windows_revision)"
            $stableCard.Detail.Text = "Authoritative production release."
            $stableCard.Detail.Text = "$($release.stable.artifact_kind) / $($release.stable.provenance_state) / $($release.deployment_status)"
            $openStableButton.Enabled = $true
            $openStableButton.Visible = $true
            $bootstrapButton.Visible = $false

            $releaseView = Get-ControlCenterReleasePresentation -Release $release
            if ($release.candidate) {
                Set-StatusBadge -Label $candidateCard.Badge -State $releaseView.candidate_state
                $candidateCard.Git.Text = "Git       $(Get-ShortIdentity $release.candidate.git_sha)"
                $candidateCard.Worker.Text = "Worker    $(Get-ShortIdentity $release.candidate.worker_version_id)"
                $candidateCard.Windows.Text = "Windows   $(Get-ShortIdentity $release.candidate.windows_revision)"
                $candidateCard.Detail.Text = "$($releaseView.candidate_kind) / $($release.candidate.branch) / $($release.candidate.compatibility_state)"
                $validation = $release.candidate.validation
                $directed = Get-DirectedWorkerValidationSummary -Validation $validation
                $repositoryCheck = if ($validation -and $validation.repository) {
                    [string]$validation.repository
                } else { "WAITING" }
                if ($releaseView.candidate_state -eq "CHECKS_BLOCKED") {
                    $repositoryCheck = "$repositoryCheck / RETRYABLE"
                }
                $windowsCheck = if ($validation -and $validation.windows) { [string]$validation.windows } else { "WAITING" }
                $contractCheck = if ($directed.tested -gt 0) {
                    [string]$directed.state
                } elseif ($validation -and $validation.cloudflare) {
                    [string]$validation.cloudflare
                } else { "WAITING" }
                $cpuCheck = "WAITING"
                $limitCheck = "WAITING"
                if ($validation -and $validation.cpu_evidence -eq "NOT_REQUIRED") {
                    $cpuCheck = "NOT REQUIRED"
                    $limitCheck = "NOT REQUIRED"
                } elseif ($validation -and $validation.cpu_evidence) {
                    $cpuCheck = if ($validation.cpu_evidence.gate_state) {
                        [string]$validation.cpu_evidence.gate_state
                    } elseif ($validation.cpu_evidence.passed) { "PASSED" } else { "FAILED" }
                    $limitCheck = if (
                        [int]$validation.cpu_evidence.responses_5xx -eq 0 -and
                        [int]$validation.cpu_evidence.responses_1102 -eq 0 -and
                        [int]$validation.cpu_evidence.exceeded_cpu -eq 0
                    ) { "PASSED" } else { "FAILED" }
                }
                $candidateCheckLabels.windows.Text = "Repo / Windows: $repositoryCheck / $windowsCheck"
                $candidateCheckLabels.contracts.Text = if ($directed.tested -gt 0) {
                    "API routes: $contractCheck | $($directed.passed)/$($directed.tested)"
                } else { "API routes: $contractCheck" }
                $cpu = if ($validation -and $validation.cpu_evidence -and
                    $validation.cpu_evidence -ne "NOT_REQUIRED") {
                    $validation.cpu_evidence
                } else { $null }
                $candidateCheckLabels.cpu.Text = if ($cpu) {
                    "CPU p95/p99/max: $($cpu.p95_cpu_ms)/$($cpu.p99_cpu_ms)/$($cpu.max_cpu_ms) ms"
                } else { "CPU headroom: $cpuCheck" }
                $candidateCheckLabels.limits.Text = if ($cpu) {
                    "5xx/1102/exceeded: $($cpu.responses_5xx)/$($cpu.responses_1102)/$($cpu.exceeded_cpu)"
                } else { "5xx + 1102: $limitCheck" }
                $openCandidateButton.Enabled = [bool]($release.candidate.browser_url -and
                    [string]$release.candidate.artifact_kind -eq $productionCandidateArtifactKind -and
                    [string]$release.candidate.branch -eq "main")
            } else {
                Set-StatusBadge -Label $candidateCard.Badge -State "UNAVAILABLE"
                $candidateCard.Git.Text = "Git       --"
                $candidateCard.Worker.Text = "Worker    --"
                $candidateCard.Windows.Text = "Windows   --"
                $candidateCard.Detail.Text = "No immutable candidate has been discovered."
                foreach ($label in $candidateCheckLabels.Values) { $label.Text = "Not available" }
                $openCandidateButton.Enabled = $false
            }
            $candidateReason.Text = if ($release.candidate -and
                [string]$release.candidate.validation_state -eq "FAILED") {
                $failureLine = (Get-DirectedWorkerValidationSummary `
                    -Validation $release.candidate.validation).first_failure_line
                if ($failureLine) {
                    "$failureLine / $($releaseView.promote_reason)"
                } else { $releaseView.promote_reason }
            } else { $releaseView.promote_reason }
            $promoteButton.Enabled = $releaseView.can_promote
            $promoteButton.BackColor = if ($releaseView.can_promote) { $accent } else { $grayWash }
            $promoteButton.ForeColor = if ($releaseView.can_promote) { [System.Drawing.Color]::White } else { $gray }
            $promoteButton.FlatAppearance.BorderColor = if ($releaseView.can_promote) { $accent } else { $line }
            $toolTip.SetToolTip($promoteButton, $releaseView.promote_reason)
            $approveCompatibilityButton.Visible = $releaseView.can_approve_compatibility
            $approveCompatibilityButton.Enabled = $releaseView.can_approve_compatibility
            $verifyMigrationButton.Visible = $releaseView.can_verify_migration
            $verifyMigrationButton.Enabled = $releaseView.can_verify_migration
            $toolTip.SetToolTip(
                $approveCompatibilityButton, $releaseView.compatibility_review_reason
            )

            if ($release.previous_stable) {
                Set-StatusBadge -Label $previousCard.Badge -State "AVAILABLE"
                $previousCard.Git.Text = "Git       $(Get-ShortIdentity $release.previous_stable.git_sha)"
                $previousCard.Worker.Text = "Worker    $(Get-ShortIdentity $release.previous_stable.worker_version_id)"
                $previousCard.Windows.Text = "Windows   $(Get-ShortIdentity $release.previous_stable.windows_revision)"
                $previousCard.Detail.Text = "Reverse restores this exact Worker and Windows pair."
            } else {
                Set-StatusBadge -Label $previousCard.Badge -State "UNAVAILABLE"
                $previousCard.Git.Text = "Git       --"
                $previousCard.Worker.Text = "Worker    --"
                $previousCard.Windows.Text = "Windows   --"
                $previousCard.Detail.Text = "A Previous Stable appears after the first completed promotion."
            }
            $reverseButton.Enabled = $releaseView.can_reverse
            $reverseReason.Text = $releaseView.reverse_reason
            $toolTip.SetToolTip($reverseButton, $releaseView.reverse_reason)
        } else {
            $stableSummary.Value.Text = "  UNAVAILABLE  "
            Set-StatusBadge -Label $stableCard.Badge -State "UNAVAILABLE"
            Set-StatusBadge -Label $candidateCard.Badge -State "UNAVAILABLE"
            Set-StatusBadge -Label $previousCard.Badge -State "UNAVAILABLE"
            $stableCard.Detail.Text = "Release control has not been bootstrapped."
            $candidateCard.Detail.Text = "Candidate validation is unavailable."
            $previousCard.Detail.Text = "No rollback identity is available."
            $candidateReason.Text = "Not bootstrapped"
            $reverseReason.Text = "Not bootstrapped"
            $candidateCheckLabels.windows.Text = "Runtime + heartbeat: unavailable"
            $candidateCheckLabels.contracts.Text = "API routes: unavailable"
            $candidateCheckLabels.cpu.Text = "CPU headroom: unavailable"
            $candidateCheckLabels.limits.Text = "5xx + 1102: unavailable"
            $promoteButton.Enabled = $false
            $promoteButton.BackColor = $grayWash
            $promoteButton.ForeColor = $gray
            $promoteButton.FlatAppearance.BorderColor = $line
            $reverseButton.Enabled = $false
            $openStableButton.Visible = $false
            $openStableButton.Enabled = $false
            $bootstrapButton.Visible = $true
            $bootstrapButton.Enabled = $true
            $openCandidateButton.Enabled = $false
            $approveCompatibilityButton.Visible = $false
            $toolTip.SetToolTip($promoteButton, "Release control not bootstrapped")
            $toolTip.SetToolTip($reverseButton, "Release control not bootstrapped")
        }
        $autoState = if ($Snapshot.auto_start) { "ENABLED" } else { "DISABLED" }
        $clockState = if ($Snapshot.windows_time_running) { "RUNNING" } else { "ISSUE" }
        $systemMetaLabel.Text = "Windows Time: $clockState  /  Auto-start: $autoState  /  Last refresh: $($summary.last_refresh)"
    }
    function Request-GuiStatus {
        if ($script:statusRefreshProcess) { return }
        $arguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $PSCommandPath), "-Action", "StatusJson",
            "-StatusPath", ('"{0}"' -f $statusSnapshotPath),
            "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
            "-RepositoryRoot", ('"{0}"' -f $repositoryRoot),
            "-ExpectedControlScriptPath", ('"{0}"' -f $PSCommandPath),
            "-ExpectedControlRevision", ([string]$controlIdentity.source_revision)
        )
        $script:lastStatusRequest = Get-Date
        $script:statusRefreshProcess = Start-Process -FilePath "powershell.exe" `
            -ArgumentList $arguments -WorkingDirectory $moduleRoot `
            -WindowStyle Hidden -PassThru
    }
    $refreshButton.Add_Click({ Request-GuiStatus })

    $statusTimer = New-Object System.Windows.Forms.Timer
    $statusTimer.Interval = 500
    $statusTimer.Add_Tick({
        if ($script:statusRefreshProcess -and $script:statusRefreshProcess.HasExited) {
            $script:statusRefreshProcess.Dispose()
            $script:statusRefreshProcess = $null
            if (Test-Path -LiteralPath $statusSnapshotPath) {
                try { Apply-GuiStatus (Get-Content -LiteralPath $statusSnapshotPath -Raw | ConvertFrom-Json) } catch {}
            }
        }
        if (-not $script:statusRefreshProcess -and ((Get-Date) - $script:lastStatusRequest).TotalSeconds -ge 10) {
            Request-GuiStatus
        }
    })
    $statusTimer.Start()
    Request-GuiStatus

    $operationTimer = New-Object System.Windows.Forms.Timer
    $operationTimer.Interval = 400
    $operationTimer.Add_Tick({
        if (-not $script:guiOperation -or -not $script:guiOperation.HasExited) { return }
        $script:guiOperation.WaitForExit()
        $script:guiOperation.Refresh()
        $exitCode = [int]$script:guiOperation.ExitCode
        $finished = $script:guiOperationName
        $output = Get-ControlCenterOperationText -Path $script:guiOperationOutputPath
        $errorText = Get-ControlCenterOperationText -Path $script:guiOperationErrorPath
        $operationResult = Read-ControlCenterOperationResult `
            -Path $script:guiOperationResultPath
        $releaseAfter = Get-ReleaseControlState
        $presentation = Resolve-ControlCenterOperationPresentation `
            -Operation $finished -ProcessExitCode $exitCode -Result $operationResult `
            -ReleaseState $releaseAfter `
            -ExpectedRelease $script:guiOperationExpectedRelease `
            -ExpectedValidationKey $script:guiOperationValidationKey `
            -ExpectedControlRevision ([string]$controlIdentity.source_revision) `
            -StandardOutput $output -StandardError $errorText
        try {
            Write-ControlCenterOperationEvent -Operation $finished `
                -Presentation $presentation -ProcessExitCode $exitCode `
                -Result $operationResult
        } catch {}
        Remove-Item -LiteralPath `
            $script:guiOperationOutputPath,$script:guiOperationErrorPath,`
            $script:guiOperationResultPath `
            -Force -ErrorAction SilentlyContinue
        $completedProcess = $script:guiOperation
        $script:guiOperation = $null
        try { $completedProcess.Dispose() } catch {}
        Set-GuiBusy -Busy $false -Message $(switch ([string]$presentation.state) {
            "SUCCESS" { "Completed: $finished" }
            "FAILURE" { "Failed: $finished" }
            default { "Result unavailable: $finished" }
        })
        if ([string]$presentation.state -eq "FAILURE") {
            [System.Windows.Forms.MessageBox]::Show(
                $(if ($presentation.diagnostic) {
                    [string]$presentation.diagnostic
                } else { "$finished failed." }),
                "$finished failed", "OK", "Error"
            ) | Out-Null
        } elseif ([string]$presentation.state -eq "INDETERMINATE") {
            [System.Windows.Forms.MessageBox]::Show(
                ([string]$presentation.diagnostic),
                "$finished result unavailable", "OK", "Warning"
            ) | Out-Null
        } elseif ($finished -in @(
            "BootstrapRelease", "VerifyMigrationCompatibility", "ApproveCompatibility", "PromoteCandidate", "ReverseStable"
        )) {
            [System.Windows.Forms.MessageBox]::Show(
                "$finished completed and authoritative state was refreshed.",
                "$finished completed", "OK", "Information"
            ) | Out-Null
        }
        Request-GuiStatus
    })
    $operationTimer.Start()

    $activationTimer = New-Object System.Windows.Forms.Timer
    $activationTimer.Interval = 250
    $activationTimer.Add_Tick({
        if (-not $activationEvent.WaitOne(0)) { return }
        if (-not $form.Visible) { $form.Show() }
        if ($form.WindowState -eq [System.Windows.Forms.FormWindowState]::Minimized) {
            $form.WindowState = [System.Windows.Forms.FormWindowState]::Normal
        }
        $form.ShowInTaskbar = $true
        $form.Activate()
        $form.BringToFront()
        $form.TopMost = $true
        $form.TopMost = $false
    })
    $activationTimer.Start()
    $form.Add_Shown({
        Write-ControlCenterUiStarted -Mode "WINFORMS_FALLBACK" `
            -FailureReason $script:wpfFailureReason
        Update-ReleaseCardLayout
        $form.Activate()
        $form.TopMost = $true
        $form.TopMost = $false
    })
    $form.Add_FormClosed({
        $statusTimer.Stop()
        $operationTimer.Stop()
        $activationTimer.Stop()
        $activationEvent.Dispose()
        Remove-Item -LiteralPath $statusSnapshotPath -Force -ErrorAction SilentlyContinue
    })
    $form.Add_FormClosing({
        param($sender, $eventArgs)
        if ($script:guiOperation) {
            $eventArgs.Cancel = $true
            try {
                [System.Windows.Forms.MessageBox]::Show(
                    "Wait for the tracked operation to finish before closing.",
                    "Operation in progress", "OK", "Warning"
                ) | Out-Null
            } catch {}
        }
    })
    [void]$form.ShowDialog()
}
