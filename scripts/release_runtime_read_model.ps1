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
        provenance_state = [string](
            Get-ReleaseRuntimeProperty $Identity "provenance_state"
        )
    }
}

function Resolve-ReleaseRuntimeIdentity {
    param(
        [AllowNull()][object]$Identity,
        [string]$PersistedSchemaVersion = "stable-candidate-release-v3"
    )
    if ($PersistedSchemaVersion -notmatch '^stable-candidate-release-v[123]$') {
        return [pscustomobject]@{
            status = "UNKNOWN"; identity = $null
            reason = "PERSISTED_IDENTITY_SCHEMA_UNKNOWN"
            identity_kind = "NOT_APPLICABLE"
        }
    }
    if (-not $Identity) {
        return [pscustomobject]@{
            status = "INCOMPLETE"; identity = $null
            reason = "PERSISTED_IDENTITY_UNAVAILABLE"
            identity_kind = "NOT_APPLICABLE"
        }
    }
    $git = [string](Get-ReleaseRuntimeProperty $Identity "git_sha")
    $workerGit = [string](Get-ReleaseRuntimeProperty $Identity "worker_git_sha")
    $worker = [string](Get-ReleaseRuntimeProperty $Identity "worker_version_id")
    $windows = [string](Get-ReleaseRuntimeProperty $Identity "windows_revision")
    $kind = [string](Get-ReleaseRuntimeProperty $Identity "artifact_kind")
    $branch = [string](Get-ReleaseRuntimeProperty $Identity "branch")
    if ([string]::IsNullOrWhiteSpace($git) -or
        [string]::IsNullOrWhiteSpace($worker) -or
        [string]::IsNullOrWhiteSpace($windows) -or
        [string]::IsNullOrWhiteSpace($kind) -or
        [string]::IsNullOrWhiteSpace($branch)) {
        return [pscustomobject]@{
            status = "INCOMPLETE"; identity = $null
            reason = "PERSISTED_IDENTITY_FIELDS_INCOMPLETE"
            identity_kind = "NOT_APPLICABLE"
        }
    }
    if ($git -notmatch '^[0-9a-f]{40}$' -or
        $windows -notmatch '^[0-9a-f]{40}$' -or
        $worker -notmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -or
        $git -cne $windows -or $branch -cne "main") {
        return [pscustomobject]@{
            status = "MISMATCH"; identity = $null
            reason = "PERSISTED_IDENTITY_CONTRACT_MISMATCH"
            identity_kind = "NOT_APPLICABLE"
        }
    }
    if ($kind -eq "PRODUCTION_CANDIDATE") {
        if ($workerGit -and ($workerGit -notmatch '^[0-9a-f]{40}$' -or
            $workerGit -cne $git)) {
            return [pscustomobject]@{
                status = "MISMATCH"; identity = $null
                reason = "CURRENT_WORKER_PROVENANCE_MISMATCH"
                identity_kind = "NOT_APPLICABLE"
            }
        }
        return [pscustomobject]@{
            status = "COMPLETE"; identity = ConvertTo-ReleaseRuntimeIdentity $Identity
            reason = "CURRENT_IDENTITY_COMPLETE"; identity_kind = "CURRENT"
        }
    }
    if ($kind -eq "LEGACY_BOOTSTRAP_STABLE") {
        $exactPair = [bool]($workerGit -ceq "NOT_RECORDED" -and
            $git -ceq "783d25314b090dd7fbbf124777c3b8de517d2b85" -and
            $worker -ceq "76d314fc-e484-4f50-8ace-3689e0896709" -and
            [string](Get-ReleaseRuntimeProperty $Identity "provenance_state") -ceq
                "LEGACY_EXACT_WORKER_WINDOWS_PAIR")
        return [pscustomobject]@{
            status = if ($exactPair) { "COMPLETE" } else { "MISMATCH" }
            identity = if ($exactPair) { ConvertTo-ReleaseRuntimeIdentity $Identity } else { $null }
            reason = if ($exactPair) {
                "NARROW_LEGACY_IDENTITY_COMPLETE"
            } else { "NARROW_LEGACY_IDENTITY_MISMATCH" }
            identity_kind = if ($exactPair) { "NARROW_LEGACY" } else { "NOT_APPLICABLE" }
        }
    }
    return [pscustomobject]@{
        status = "MISMATCH"; identity = $null
        reason = "PERSISTED_IDENTITY_KIND_MISMATCH"
        identity_kind = "NOT_APPLICABLE"
    }
}

function Test-ReleaseRuntimeIdentityComplete {
    param(
        [AllowNull()][object]$Identity,
        [string]$PersistedSchemaVersion = "stable-candidate-release-v3"
    )
    return [bool]([string](
        Resolve-ReleaseRuntimeIdentity $Identity $PersistedSchemaVersion
    ).status -eq "COMPLETE")
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
        [Parameter(Mandatory = $true)][object]$IdentityResolution,
        [AllowNull()][object]$VersionDetails,
        [ValidateSet("AVAILABLE", "UNAVAILABLE", "UNKNOWN")]
        [string]$ProviderStatus = "UNKNOWN",
        [bool]$ProviderScopeVerified = $false
    )
    $resolutionStatus = [string](
        Get-ReleaseRuntimeProperty $IdentityResolution "status"
    )
    $Target = Get-ReleaseRuntimeProperty $IdentityResolution "identity"
    $targetId = [string](Get-ReleaseRuntimeProperty $Target "worker_version_id")
    if ($resolutionStatus -ne "COMPLETE" -or -not $Target -or
        [string]::IsNullOrWhiteSpace($targetId)) {
        return [pscustomobject]@{
            status = if ($resolutionStatus -eq "MISMATCH") { "MISMATCH" } else {
                "NOT_APPLICABLE"
            }
            reason = if ($resolutionStatus -eq "MISMATCH") {
                "PREVIOUS_IDENTITY_MISMATCH"
            } else { "PREVIOUS_IDENTITY_UNAVAILABLE" }
            requested_version_id = $null; observed_version_id = $null
            identity_status = $resolutionStatus; provenance_status = "NOT_APPLICABLE"
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
    $identityKind = [string](
        Get-ReleaseRuntimeProperty $IdentityResolution "identity_kind"
    )
    $kind = [string](Get-ReleaseRuntimeProperty $Target "artifact_kind")
    $expectedGit = [string](Get-ReleaseRuntimeProperty $Target "worker_git_sha")
    if (-not $expectedGit) { $expectedGit = [string](Get-ReleaseRuntimeProperty $Target "git_sha") }
    $legacy = $identityKind -eq "NARROW_LEGACY"
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
        [Parameter(Mandatory = $true)][object]$IdentityResolution,
        [ValidateSet("AVAILABLE", "UNAVAILABLE", "UNKNOWN", "MISMATCH", "NOT_APPLICABLE")]
        [string]$Status,
        [string]$Reason = ""
    )
    $Target = Get-ReleaseRuntimeProperty $IdentityResolution "identity"
    $resolutionStatus = [string](
        Get-ReleaseRuntimeProperty $IdentityResolution "status"
    )
    [pscustomobject]@{
        status = if ($resolutionStatus -eq "COMPLETE" -and $Target) {
            $Status
        } elseif ($resolutionStatus -eq "MISMATCH") { "MISMATCH" } else {
            "NOT_APPLICABLE"
        }
        reason = if ($resolutionStatus -eq "COMPLETE" -and $Target) {
            $Reason
        } elseif ($resolutionStatus -eq "MISMATCH") {
            "PREVIOUS_IDENTITY_MISMATCH"
        } else { "PREVIOUS_IDENTITY_UNAVAILABLE" }
        revision = if ($resolutionStatus -eq "COMPLETE" -and $Target) {
            [string]$Target.windows_revision
        } else { $null }
    }
}

function New-ReleaseReversePrecheck {
    param(
        [AllowNull()][object]$PreviousIdentity,
        [string]$CommittedIdentityStatus = "UNKNOWN",
        [string]$PreviousIdentityStatus = "UNKNOWN",
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
    $reason = if ($CommittedIdentityStatus -ne "COMPLETE") {
        "COMMITTED_IDENTITY_INVALID"
    } elseif ($PreviousIdentityStatus -eq "MISMATCH") {
        "PREVIOUS_IDENTITY_MISMATCH"
    } elseif ($PreviousIdentityStatus -ne "COMPLETE" -or -not $PreviousIdentity) {
        "PREVIOUS_IDENTITY_INVALID"
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
        [AllowNull()][object]$CommittedIdentityResolution,
        [AllowNull()][object]$PreviousIdentityResolution,
        [AllowNull()][object]$TargetIdentityResolution,
        [Parameter(Mandatory = $true)][DateTimeOffset]$ObservedAt
    )
    $schema = [string](Get-ReleaseRuntimeProperty $PersistedState "schema_version")
    $committedRaw = Get-ReleaseRuntimeProperty $PersistedState "stable"
    $previousRaw = Get-ReleaseRuntimeProperty $PersistedState "previous_stable"
    $transaction = Get-ReleaseRuntimeProperty $PersistedState "transaction"
    $targetRaw = if ($transaction -and
        (Get-ReleaseRuntimeProperty $transaction "target")) {
        $transaction.target
    } else { Get-ReleaseRuntimeProperty $PersistedState "candidate" }
    $committedResolution = if ($CommittedIdentityResolution) {
        $CommittedIdentityResolution
    } else { Resolve-ReleaseRuntimeIdentity $committedRaw $schema }
    $previousResolution = if ($PreviousIdentityResolution) {
        $PreviousIdentityResolution
    } else { Resolve-ReleaseRuntimeIdentity $previousRaw $schema }
    $targetResolution = if ($TargetIdentityResolution) {
        $TargetIdentityResolution
    } else { Resolve-ReleaseRuntimeIdentity $targetRaw $schema }
    $committedValid = [string]$committedResolution.status -eq "COMPLETE"
    $previousValid = [string]$previousResolution.status -eq "COMPLETE"
    $targetValid = [string]$targetResolution.status -eq "COMPLETE"
    $committed = if ($committedValid) { $committedResolution.identity } else { $null }
    $previous = if ($previousValid) { $previousResolution.identity } else { $null }
    $target = if ($targetValid) { $targetResolution.identity } else { $null }
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
    if ($workerObservationStatus -notin @(
        "AVAILABLE", "UNKNOWN", "MISMATCH", "UNAVAILABLE"
    )) { $workerObservationStatus = "UNKNOWN" }
    $windowsObservationStatus = [string](
        Get-ReleaseRuntimeProperty $ActiveWindowsObservation "status"
    )
    if ($windowsObservationStatus -notin @(
        "AVAILABLE", "UNKNOWN", "MISMATCH", "UNAVAILABLE"
    )) { $windowsObservationStatus = "UNKNOWN" }
    $activeObservationStatus = if ($workerObservationStatus -eq "AVAILABLE" -and
        $windowsObservationStatus -eq "AVAILABLE") {
        "AVAILABLE"
    } else { "UNKNOWN" }
    $activeMatches = [bool]($activeComplete -and
        (Test-ReleaseRuntimeIdentityEqual $activeIdentity $committed))
    $businessHealthStatus = if ($HealthObservation -and
        (Get-ReleaseRuntimeProperty $HealthObservation "business_health_status")) {
        [string]$HealthObservation.business_health_status
    } elseif ($HealthObservation) {
        [string](Get-ReleaseRuntimeProperty $HealthObservation "status")
    } else { "UNKNOWN" }
    if ($businessHealthStatus -notin @("HEALTHY", "DEGRADED", "UNKNOWN")) {
        $businessHealthStatus = "UNKNOWN"
    }
    $ownershipStatus = [string](
        Get-ReleaseRuntimeProperty $HealthObservation "ownership_status"
    )
    if ($ownershipStatus -notin @("SINGLE_OWNER", "INVALID", "UNKNOWN")) {
        $ownershipStatus = "UNKNOWN"
    }
    $healthStatus = if ($businessHealthStatus -eq "HEALTHY" -and
        $ownershipStatus -eq "SINGLE_OWNER") {
        "HEALTHY"
    } elseif ($ownershipStatus -eq "UNKNOWN" -or
        $businessHealthStatus -eq "UNKNOWN") { "UNKNOWN" } else { "DEGRADED" }
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
    } elseif ($ownershipStatus -ne "SINGLE_OWNER") {
        "PRODUCTION_OWNERSHIP_$ownershipStatus"
    } elseif ($businessHealthStatus -eq "DEGRADED") {
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
        committed_identity_status = [string]$committedResolution.status
        committed_identity_reason = [string]$committedResolution.reason
        previous_committed = $previous
        previous_identity_status = [string]$previousResolution.status
        previous_identity_reason = [string]$previousResolution.reason
        previous_identity_kind = [string]$previousResolution.identity_kind
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
            business_health_status = $businessHealthStatus
            business_health_reason = [string](
                Get-ReleaseRuntimeProperty $HealthObservation "business_health_reason"
            )
            ownership_status = $ownershipStatus
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
