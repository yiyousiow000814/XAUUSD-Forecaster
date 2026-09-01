$script:ReleaseRuntimeReadModelSchema = "release-runtime-read-model-v1"

function Get-ReleaseRuntimeProperty {
    param([AllowNull()][object]$InputObject, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $InputObject -or -not $InputObject.PSObject.Properties[$Name]) {
        return $null
    }
    return $InputObject.$Name
}

function ConvertTo-ReleaseRuntimeIdentity {
    param([AllowNull()][object]$Identity)
    if (-not $Identity) { return $null }
    [pscustomobject]@{
        git_sha = [string](Get-ReleaseRuntimeProperty $Identity "git_sha")
        worker_version_id = [string](
            Get-ReleaseRuntimeProperty $Identity "worker_version_id"
        )
        windows_revision = [string](
            Get-ReleaseRuntimeProperty $Identity "windows_revision"
        )
        artifact_kind = [string](
            Get-ReleaseRuntimeProperty $Identity "artifact_kind"
        )
        validation_key = [string](
            Get-ReleaseRuntimeProperty $Identity "validation_key"
        )
    }
}

function Test-ReleaseRuntimeIdentityEqual {
    param([AllowNull()][object]$Left, [AllowNull()][object]$Right)
    if (-not $Left -or -not $Right) { return $false }
    return [bool](
        [string]$Left.git_sha -ceq [string]$Right.git_sha -and
        [string]$Left.worker_version_id -ceq [string]$Right.worker_version_id -and
        [string]$Left.windows_revision -ceq [string]$Right.windows_revision
    )
}

function Get-ReleaseRuntimeLifecyclePhase {
    param([AllowNull()][object]$ReleaseState)
    if (-not $ReleaseState) { return "UNAVAILABLE" }
    if (Get-ReleaseRuntimeProperty $ReleaseState "transaction") {
        $transaction = $ReleaseState.transaction
        if ([string](Get-ReleaseRuntimeProperty $transaction "phase") -in @(
            "OBSERVING", "REVERSE_OBSERVING"
        )) { return "OBSERVE" }
        return "SWITCH"
    }
    if ([string](Get-ReleaseRuntimeProperty $ReleaseState "deployment_status") -in @(
        "RECOVERY_REQUIRED", "DEPLOYMENT_DRIFT"
    )) { return "OBSERVE" }
    if (Get-ReleaseRuntimeProperty $ReleaseState "candidate") {
        $candidate = $ReleaseState.candidate
        $validation = Get-ReleaseRuntimeProperty $candidate "validation"
        $reason = if ($validation) {
            [string](Get-ReleaseRuntimeProperty $validation "reason")
        } else { "" }
        if ([string](Get-ReleaseRuntimeProperty $candidate "validation_state") -in @(
            "NEW", "STAGING"
        ) -or $reason -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
            return "PREPARE"
        }
        return "VERIFY"
    }
    return "STABLE"
}

function New-ReleaseWorkerArtifactObservation {
    param(
        [AllowNull()][object]$Target,
        [AllowNull()][object]$VersionDetails,
        [ValidateSet("AVAILABLE", "UNAVAILABLE", "UNKNOWN")]
        [string]$ProviderStatus = "UNKNOWN",
        [bool]$ProviderScopeVerified = $false
    )
    $targetId = [string](Get-ReleaseRuntimeProperty $Target "worker_version_id")
    if (-not $Target -or [string]::IsNullOrWhiteSpace($targetId)) {
        return [pscustomobject]@{
            status = "NOT_APPLICABLE"; reason = "PREVIOUS_IDENTITY_UNAVAILABLE"
            requested_version_id = $null; observed_version_id = $null
            identity_status = "NOT_APPLICABLE"; provenance_status = "NOT_APPLICABLE"
        }
    }
    if ($ProviderStatus -ne "AVAILABLE") {
        return [pscustomobject]@{
            status = $ProviderStatus; reason = "WORKER_VERSION_PROVIDER_$ProviderStatus"
            requested_version_id = $targetId; observed_version_id = $null
            identity_status = "UNKNOWN"; provenance_status = "UNKNOWN"
        }
    }
    if (-not $ProviderScopeVerified) {
        return [pscustomobject]@{
            status = "UNKNOWN"; reason = "WORKER_PROVIDER_SCOPE_UNVERIFIED"
            requested_version_id = $targetId; observed_version_id = $null
            identity_status = "UNKNOWN"; provenance_status = "UNKNOWN"
        }
    }
    if (-not $VersionDetails -or $VersionDetails -is [System.Array] -or
        -not $VersionDetails.PSObject.Properties['id'] -or
        -not (Get-ReleaseRuntimeProperty $VersionDetails "metadata")) {
        return [pscustomobject]@{
            status = "UNKNOWN"; reason = "WORKER_VERSION_RESPONSE_MALFORMED"
            requested_version_id = $targetId; observed_version_id = $null
            identity_status = "UNKNOWN"; provenance_status = "UNKNOWN"
        }
    }
    $observedId = [string]$VersionDetails.id
    if ($observedId -cne $targetId) {
        return [pscustomobject]@{
            status = "MISMATCH"; reason = "WORKER_VERSION_IDENTITY_MISMATCH"
            requested_version_id = $targetId; observed_version_id = $observedId
            identity_status = "MISMATCH"; provenance_status = "UNKNOWN"
        }
    }
    $scriptResource = Get-ReleaseRuntimeProperty (
        Get-ReleaseRuntimeProperty $VersionDetails "resources"
    ) "script"
    if (-not $scriptResource -or 'fetch' -notin @(
        Get-ReleaseRuntimeProperty $scriptResource "handlers"
    )) {
        return [pscustomobject]@{
            status = "UNKNOWN"; reason = "WORKER_VERSION_RESPONSE_MALFORMED"
            requested_version_id = $targetId; observed_version_id = $observedId
            identity_status = "MATCH"; provenance_status = "UNKNOWN"
        }
    }
    $kind = [string](Get-ReleaseRuntimeProperty $Target "artifact_kind")
    $expectedGit = [string](Get-ReleaseRuntimeProperty $Target "worker_git_sha")
    if (-not $expectedGit) { $expectedGit = [string](Get-ReleaseRuntimeProperty $Target "git_sha") }
    $legacy = $kind -eq "LEGACY_BOOTSTRAP_STABLE" -and $expectedGit -eq "NOT_RECORDED"
    if (-not $legacy) {
        $annotations = Get-ReleaseRuntimeProperty $VersionDetails "annotations"
        $message = [string](Get-ReleaseRuntimeProperty $annotations "workers/message")
        $observedGit = if ($message -match '(?i)release:([0-9a-f]{40})') {
            $matches[1].ToLowerInvariant()
        } else { "" }
        if ($expectedGit -notmatch '^[0-9a-f]{40}$' -or $observedGit -cne $expectedGit) {
            return [pscustomobject]@{
                status = "MISMATCH"; reason = "WORKER_VERSION_PROVENANCE_MISMATCH"
                requested_version_id = $targetId; observed_version_id = $observedId
                identity_status = "MATCH"; provenance_status = "MISMATCH"
            }
        }
        if ($kind -ne "PRODUCTION_CANDIDATE" -or
            $message -notmatch '(?i)artifact[_-]kind:PRODUCTION_CANDIDATE') {
            return [pscustomobject]@{
                status = "MISMATCH"; reason = "WORKER_ARTIFACT_KIND_MISMATCH"
                requested_version_id = $targetId; observed_version_id = $observedId
                identity_status = "MATCH"; provenance_status = "MISMATCH"
            }
        }
    }
    return [pscustomobject]@{
        status = "AVAILABLE"; reason = "EXACT_WORKER_VERSION_AVAILABLE"
        requested_version_id = $targetId; observed_version_id = $observedId
        identity_status = "MATCH"; provenance_status = if ($legacy) {
            "LEGACY_NOT_RECORDED"
        } else { "MATCH" }
    }
}

function New-ReleaseWindowsArtifactObservation {
    param(
        [AllowNull()][object]$Target,
        [ValidateSet("AVAILABLE", "UNAVAILABLE", "UNKNOWN", "MISMATCH", "NOT_APPLICABLE")]
        [string]$Status,
        [string]$Reason = ""
    )
    [pscustomobject]@{
        status = if ($Target) { $Status } else { "NOT_APPLICABLE" }
        reason = if ($Target) { $Reason } else { "PREVIOUS_IDENTITY_UNAVAILABLE" }
        revision = if ($Target) { [string]$Target.windows_revision } else { $null }
    }
}

function New-ReleaseReversePrecheck {
    param(
        [AllowNull()][object]$Previous,
        [Parameter(Mandatory = $true)][object]$WorkerArtifact,
        [Parameter(Mandatory = $true)][object]$WindowsArtifact,
        [string]$ControlBundleStatus = "UNKNOWN",
        [bool]$TransactionActive = $false,
        [bool]$ReleaseLockActive = $false,
        [string]$OwnershipStatus = "UNKNOWN",
        [string]$ActiveHealthStatus = "UNKNOWN",
        [string]$RecoveryObservationStatus = "NOT_OBSERVED"
    )
    $reason = if (-not $Previous) {
        "PREVIOUS_STABLE_UNAVAILABLE"
    } elseif ($TransactionActive) {
        "RELEASE_TRANSACTION_ACTIVE"
    } elseif ($ReleaseLockActive) {
        "RELEASE_LOCK_ACTIVE"
    } elseif ([string]$WorkerArtifact.status -ne "AVAILABLE") {
        [string]$WorkerArtifact.reason
    } elseif ([string]$WindowsArtifact.status -ne "AVAILABLE") {
        [string]$WindowsArtifact.reason
    } elseif ($ControlBundleStatus -ne "AVAILABLE") {
        "CONTROL_BUNDLE_$ControlBundleStatus"
    } elseif ($OwnershipStatus -ne "SINGLE_OWNER") {
        "PRODUCTION_OWNERSHIP_$OwnershipStatus"
    } elseif ($ActiveHealthStatus -ne "HEALTHY") {
        "ACTIVE_HEALTH_$ActiveHealthStatus"
    } else { "READY" }
    [pscustomobject]@{
        status = if ($reason -eq "READY") { "READY" } else { "BLOCKED" }
        can_reverse = [bool]($reason -eq "READY")
        reason = $reason
        recovery_observation_status = $RecoveryObservationStatus
    }
}

function New-ReleaseRuntimeReadModel {
    param(
        [AllowNull()][object]$PersistedState,
        [AllowNull()][object]$ActiveWorkerObservation,
        [AllowNull()][object]$ActiveWindowsObservation,
        [AllowNull()][object]$HealthObservation,
        [AllowNull()][object]$PreviousWorkerArtifact,
        [AllowNull()][object]$PreviousWindowsArtifact,
        [AllowNull()][object]$ReversePrecheck,
        [Parameter(Mandatory = $true)][DateTimeOffset]$ObservedAt
    )
    $committed = ConvertTo-ReleaseRuntimeIdentity (
        Get-ReleaseRuntimeProperty $PersistedState "stable"
    )
    $previous = ConvertTo-ReleaseRuntimeIdentity (
        Get-ReleaseRuntimeProperty $PersistedState "previous_stable"
    )
    $target = ConvertTo-ReleaseRuntimeIdentity (
        Get-ReleaseRuntimeProperty $PersistedState "candidate"
    )
    $transaction = Get-ReleaseRuntimeProperty $PersistedState "transaction"
    if ($transaction -and (Get-ReleaseRuntimeProperty $transaction "target")) {
        $target = ConvertTo-ReleaseRuntimeIdentity $transaction.target
    }
    $activeWorkerId = [string](Get-ReleaseRuntimeProperty $ActiveWorkerObservation "version_id")
    $activeWindowsRevision = [string](
        Get-ReleaseRuntimeProperty $ActiveWindowsObservation "revision"
    )
    $activeIdentity = if ($activeWorkerId -or $activeWindowsRevision) {
        [pscustomobject]@{
            git_sha = [string](Get-ReleaseRuntimeProperty $ActiveWorkerObservation "git_sha")
            worker_version_id = $activeWorkerId
            windows_revision = $activeWindowsRevision
        }
    } else { $null }
    $activeComplete = [bool]($activeIdentity -and
        [string]$activeIdentity.worker_version_id -and
        [string]$activeIdentity.windows_revision -and
        [string]$activeIdentity.git_sha -match '^[0-9a-f]{40}$')
    $workerObservationStatus = [string](
        Get-ReleaseRuntimeProperty $ActiveWorkerObservation "status"
    )
    if (-not $workerObservationStatus -and $ActiveWorkerObservation) {
        $workerObservationStatus = "AVAILABLE"
    }
    $windowsObservationStatus = [string](
        Get-ReleaseRuntimeProperty $ActiveWindowsObservation "status"
    )
    if (-not $windowsObservationStatus -and $ActiveWindowsObservation) {
        $windowsObservationStatus = "AVAILABLE"
    }
    $activeObservationStatus = if ($activeComplete -and
        $workerObservationStatus -eq "AVAILABLE" -and
        $windowsObservationStatus -eq "AVAILABLE") {
        "AVAILABLE"
    } else { "UNKNOWN" }
    $activeMatches = [bool]($activeComplete -and
        (Test-ReleaseRuntimeIdentityEqual $activeIdentity $committed))
    $healthStatus = if ($HealthObservation) {
        [string](Get-ReleaseRuntimeProperty $HealthObservation "status")
    } else { "UNKNOWN" }
    if ($healthStatus -notin @("HEALTHY", "DEGRADED", "UNKNOWN")) {
        $healthStatus = "UNKNOWN"
    }
    if ($activeObservationStatus -ne "AVAILABLE") {
        $healthStatus = "UNKNOWN"
    }
    $driftStatus = if (-not $committed -or -not $activeComplete) {
        "UNKNOWN"
    } elseif ($activeMatches) {
        "MATCHED"
    } else { "DRIFT" }
    $phase = Get-ReleaseRuntimeLifecyclePhase $PersistedState
    $lkg = if ($committed -and [string](
        Get-ReleaseRuntimeProperty $PersistedState "schema_version"
    ) -match '^stable-candidate-release-v[123]$') { $committed } else { $null }
    $recoveryReason = if ($driftStatus -eq "DRIFT") {
        "ACTIVE_COMMITTED_IDENTITY_MISMATCH"
    } elseif ($healthStatus -eq "DEGRADED") {
        [string](Get-ReleaseRuntimeProperty $HealthObservation "reason")
    } elseif ($driftStatus -eq "UNKNOWN" -or $healthStatus -eq "UNKNOWN") {
        "ACTIVE_OBSERVATION_INCOMPLETE"
    } else { $null }
    [pscustomobject]@{
        schema_version = $script:ReleaseRuntimeReadModelSchema
        observed_at = $ObservedAt.ToUniversalTime().ToString("o")
        persisted_schema_version = [string](
            Get-ReleaseRuntimeProperty $PersistedState "schema_version"
        )
        phase = $phase
        transaction_active = [bool]$transaction
        committed_stable = $committed
        previous_committed = $previous
        target = $target
        active = [pscustomobject]@{
            worker_version_id = $activeWorkerId
            worker_git_sha = [string](
                Get-ReleaseRuntimeProperty $ActiveWorkerObservation "git_sha"
            )
            worker_traffic_percent = Get-ReleaseRuntimeProperty (
                $ActiveWorkerObservation
            ) "traffic_percent"
            windows_revision = $activeWindowsRevision
            observation_status = $activeObservationStatus
            observation_source = "CLOUDFLARE_DEPLOYMENT+WINDOWS_RUNTIME"
            health = $healthStatus
            health_reason = [string](
                Get-ReleaseRuntimeProperty $HealthObservation "reason"
            )
        }
        active_matches_committed = $activeMatches
        last_known_good = $lkg
        last_known_good_source = if ($lkg) {
            "COMMITTED_STABLE_CONTRACT"
        } else { "UNKNOWN" }
        drift_status = $driftStatus
        recovery_reason = $recoveryReason
        previous = [pscustomobject]@{
            worker_artifact = $PreviousWorkerArtifact
            windows_artifact = $PreviousWindowsArtifact
            worker_is_current_traffic_member = [bool](
                Get-ReleaseRuntimeProperty $ActiveWorkerObservation "previous_is_member"
            )
            current_traffic_percent = Get-ReleaseRuntimeProperty (
                $ActiveWorkerObservation
            ) "previous_traffic_percent"
            reverse_precheck = $ReversePrecheck
        }
        candidate_state = if ($PersistedState -and $PersistedState.candidate) {
            [string](Get-ReleaseRuntimeProperty $PersistedState.candidate "validation_state")
        } else { "UNAVAILABLE" }
    }
}
