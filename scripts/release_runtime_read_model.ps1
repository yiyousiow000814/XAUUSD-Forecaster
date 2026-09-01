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
        branch = [string](Get-ReleaseRuntimeProperty $Identity "branch")
        worker_git_sha = [string](
            Get-ReleaseRuntimeProperty $Identity "worker_git_sha"
        )
        validation_key = [string](
            Get-ReleaseRuntimeProperty $Identity "validation_key"
        )
    }
}

function Test-ReleaseRuntimeIdentityComplete {
    param(
        [AllowNull()][object]$Identity,
        [string]$PersistedSchemaVersion = "stable-candidate-release-v3"
    )
    if (-not $Identity -or $PersistedSchemaVersion -notmatch
        '^stable-candidate-release-v[123]$') { return $false }
    $git = [string](Get-ReleaseRuntimeProperty $Identity "git_sha")
    $workerGit = [string](Get-ReleaseRuntimeProperty $Identity "worker_git_sha")
    $worker = [string](Get-ReleaseRuntimeProperty $Identity "worker_version_id")
    $windows = [string](Get-ReleaseRuntimeProperty $Identity "windows_revision")
    $kind = [string](Get-ReleaseRuntimeProperty $Identity "artifact_kind")
    $branch = [string](Get-ReleaseRuntimeProperty $Identity "branch")
    if ($git -notmatch '^[0-9a-f]{40}$' -or
        $windows -notmatch '^[0-9a-f]{40}$' -or
        $worker -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        $git -cne $windows -or $branch -cne "main") { return $false }
    if ($kind -eq "PRODUCTION_CANDIDATE") {
        return [bool](-not $workerGit -or
            ($workerGit -match '^[0-9a-f]{40}$' -and $workerGit -ceq $git))
    }
    if ($kind -eq "LEGACY_BOOTSTRAP_STABLE") {
        return [bool]($workerGit -ceq "NOT_RECORDED" -and
            $git -ceq "783d25314b090dd7fbbf124777c3b8de517d2b85" -and
            $worker -ceq "76d314fc-e484-4f50-8ace-3689e0896709" -and
            [string](Get-ReleaseRuntimeProperty $Identity "provenance_state") -ceq
                "LEGACY_EXACT_WORKER_WINDOWS_PAIR")
    }
    return $false
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
        if ($message -notmatch '(?i)(?:^|\s)branch:([^\s]+)' -or
            [string]$matches[1] -cne "main") {
            return [pscustomobject]@{
                status = "MISMATCH"; reason = "WORKER_BRANCH_PROVENANCE_MISMATCH"
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
        [string]$ActiveObservationStatus = "UNKNOWN",
        [string]$ActiveIdentityStatus = "INCOMPLETE",
        [bool]$ActiveMatchesCommitted = $false,
        [string]$RecoveryObservationStatus = "NOT_OBSERVED"
    )
    $reason = if (-not $Previous) {
        "PREVIOUS_STABLE_UNAVAILABLE"
    } elseif ($TransactionActive) {
        "RELEASE_TRANSACTION_ACTIVE"
    } elseif ($ReleaseLockActive) {
        "RELEASE_LOCK_ACTIVE"
    } elseif ($ActiveObservationStatus -ne "AVAILABLE") {
        "ACTIVE_OBSERVATION_UNAVAILABLE"
    } elseif ($ActiveIdentityStatus -ne "COMPLETE") {
        "ACTIVE_IDENTITY_INCOMPLETE"
    } elseif (-not $ActiveMatchesCommitted) {
        "ACTIVE_COMMITTED_MISMATCH_REQUIRES_RECOVERY_MODE"
    } elseif ([string]$WorkerArtifact.status -ne "AVAILABLE") {
        [string]$WorkerArtifact.reason
    } elseif ([string]$WindowsArtifact.status -ne "AVAILABLE") {
        [string]$WindowsArtifact.reason
    } elseif ($ControlBundleStatus -ne "AVAILABLE") {
        "CONTROL_BUNDLE_$ControlBundleStatus"
    } elseif ($OwnershipStatus -ne "SINGLE_OWNER") {
        "PRODUCTION_OWNERSHIP_$OwnershipStatus"
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
    $schema = [string](Get-ReleaseRuntimeProperty $PersistedState "schema_version")
    $committedRaw = Get-ReleaseRuntimeProperty $PersistedState "stable"
    $previousRaw = Get-ReleaseRuntimeProperty $PersistedState "previous_stable"
    $targetRaw = Get-ReleaseRuntimeProperty $PersistedState "candidate"
    $committedValid = Test-ReleaseRuntimeIdentityComplete $committedRaw $schema
    $previousValid = Test-ReleaseRuntimeIdentityComplete $previousRaw $schema
    $targetValid = Test-ReleaseRuntimeIdentityComplete $targetRaw $schema
    $committed = if ($committedValid) { ConvertTo-ReleaseRuntimeIdentity $committedRaw } else { $null }
    $previous = if ($previousValid) { ConvertTo-ReleaseRuntimeIdentity $previousRaw } else { $null }
    $target = if ($targetValid) { ConvertTo-ReleaseRuntimeIdentity $targetRaw } else { $null }
    $transaction = Get-ReleaseRuntimeProperty $PersistedState "transaction"
    if ($transaction -and (Get-ReleaseRuntimeProperty $transaction "target")) {
        $target = if (Test-ReleaseRuntimeIdentityComplete $transaction.target $schema) {
            ConvertTo-ReleaseRuntimeIdentity $transaction.target
        } else { $null }
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
        [string]$activeIdentity.worker_version_id -match
            '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -and
        [string]$activeIdentity.windows_revision -match '^[0-9a-f]{40}$' -and
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
    $activeObservationStatus = if ($workerObservationStatus -eq "AVAILABLE" -and
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
    $driftStatus = if (-not $committed -or $activeObservationStatus -ne "AVAILABLE" -or
        -not $activeComplete) {
        "UNKNOWN"
    } elseif ($activeMatches) {
        "MATCHED"
    } else { "DRIFT" }
    $phase = Get-ReleaseRuntimeLifecyclePhase $PersistedState
    $lkg = if ($committedValid) { $committed } else { $null }
    $recoveryReason = if ($driftStatus -eq "DRIFT") {
        "ACTIVE_COMMITTED_MISMATCH_REQUIRES_RECOVERY_MODE"
    } elseif ($healthStatus -eq "DEGRADED") {
        [string](Get-ReleaseRuntimeProperty $HealthObservation "reason")
    } elseif ($activeObservationStatus -ne "AVAILABLE") {
        "ACTIVE_OBSERVATION_UNAVAILABLE"
    } elseif (-not $activeComplete) {
        "ACTIVE_IDENTITY_INCOMPLETE"
    } elseif ($healthStatus -eq "UNKNOWN") {
        "ACTIVE_HEALTH_UNKNOWN"
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
        committed_identity_status = if ($committedValid) { "COMPLETE" } else { "INCOMPLETE" }
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
            identity_status = if ($activeComplete) { "COMPLETE" } else { "INCOMPLETE" }
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
            worker_traffic_membership_status = if (
                Get-ReleaseRuntimeProperty $ActiveWorkerObservation "previous_membership_status"
            ) { [string]$ActiveWorkerObservation.previous_membership_status } elseif (-not $previousRaw) {
                "NOT_APPLICABLE"
            } else { "UNKNOWN" }
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
