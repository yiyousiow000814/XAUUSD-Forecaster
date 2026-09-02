$script:RecoveryHotfixSchema = "release-recovery-hotfix-v1"
$script:RecoveryHotfixModeNormal = "NORMAL"
$script:RecoveryHotfixModeRecovery = "RECOVERY_HOTFIX"
$script:RecoveryHotfixActions = @("RESTORE_LKG", "APPLY_RECOVERY_HOTFIX")
$script:RecoveryHotfixEligibleFamilies = @(
    "worker-route-serialization",
    "dashboard-api",
    "dashboard-sync",
    "windows-process-ownership",
    "control-plane-only"
)
$script:RecoveryHotfixForbiddenFamilies = @(
    "d1-schema-migration",
    "access",
    "storage-lifecycle",
    "runtime-root-authority",
    "annotator-news",
    "collector-decision",
    "unknown-cross-family"
)

function Get-RecoveryHotfixProperty {
    param([AllowNull()][object]$InputObject, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $InputObject -or -not $InputObject.PSObject.Properties[$Name]) {
        return $null
    }
    return $InputObject.$Name
}

function Get-ReleaseTransactionMode {
    param([AllowNull()][object]$Transaction)
    $mode = [string](Get-RecoveryHotfixProperty $Transaction "mode")
    if ([string]::IsNullOrWhiteSpace($mode)) { return $script:RecoveryHotfixModeNormal }
    if ($mode -notin @($script:RecoveryHotfixModeNormal, $script:RecoveryHotfixModeRecovery)) {
        return "UNKNOWN"
    }
    return $mode
}

function Get-RecoveryHotfixEligibility {
    param([Parameter(Mandatory = $true)][object]$ChangePlan)
    $family = [string](Get-RecoveryHotfixProperty $ChangePlan "family")
    $failClosed = [bool](Get-RecoveryHotfixProperty $ChangePlan "fail_closed")
    if ($failClosed -or [string]::IsNullOrWhiteSpace($family) -or
        $family -in $script:RecoveryHotfixForbiddenFamilies -or
        $family -notin $script:RecoveryHotfixEligibleFamilies) {
        return [pscustomobject][ordered]@{
            schema_version = $script:RecoveryHotfixSchema
            eligible = $false
            eligibility_class = "NORMAL_RELEASE_REQUIRED"
            family = $family
            reason = if ($family -eq "unknown-cross-family" -or $failClosed) {
                "RECOVERY_HOTFIX_CHANGE_FAMILY_UNKNOWN"
            } else { "RECOVERY_HOTFIX_CHANGE_FAMILY_FORBIDDEN" }
            observe_contract = $null
        }
    }
    return [pscustomobject][ordered]@{
        schema_version = $script:RecoveryHotfixSchema
        eligible = $true
        eligibility_class = "BOUNDED_RECOVERY_HOTFIX"
        family = $family
        reason = "RECOVERY_HOTFIX_FAMILY_ELIGIBLE"
        observe_contract = [pscustomobject][ordered]@{
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
    }
}

function Get-RecoveryEvidenceReceiptReferences {
    param([Parameter(Mandatory = $true)][object]$Qualification)
    $digests = Get-RecoveryHotfixProperty $Qualification "receipt_digests"
    if (-not $digests) { throw "RECOVERY_EVIDENCE_RECEIPTS_UNAVAILABLE" }
    $refs = [ordered]@{}
    foreach ($property in @($digests.PSObject.Properties | Sort-Object Name)) {
        $value = [string]$property.Value
        if ($value -notmatch '^[0-9a-f]{64}$') {
            throw "RECOVERY_EVIDENCE_RECEIPT_INVALID:$($property.Name)"
        }
        $refs[$property.Name] = $value
    }
    if ($refs.Count -lt 13) { throw "RECOVERY_EVIDENCE_WATERFALL_INCOMPLETE" }
    return [pscustomobject]$refs
}

function Assert-RecoveryRuntimeAuthority {
    param(
        [Parameter(Mandatory = $true)][object]$RuntimeReadModel,
        [Parameter(Mandatory = $true)][ValidateSet("RESTORE_LKG", "APPLY_RECOVERY_HOTFIX")]
        [string]$RecoveryAction
    )
    if ([bool](Get-RecoveryHotfixProperty $RuntimeReadModel "transaction_active")) {
        throw "RECOVERY_TRANSACTION_ALREADY_ACTIVE"
    }
    $active = Get-RecoveryHotfixProperty $RuntimeReadModel "active"
    if (-not $active -or
        [string](Get-RecoveryHotfixProperty $active "observation_status") -ne "AVAILABLE" -or
        [string](Get-RecoveryHotfixProperty $active "identity_status") -ne "COMPLETE") {
        throw "RECOVERY_ACTIVE_AUTHORITY_UNKNOWN"
    }
    if ([string](Get-RecoveryHotfixProperty $active "ownership_status") -ne "SINGLE_OWNER") {
        throw "RECOVERY_SINGLE_OWNER_REQUIRED"
    }
    $health = [string](Get-RecoveryHotfixProperty $active "health")
    if ($health -notin @("HEALTHY", "DEGRADED")) {
        throw "RECOVERY_ACTIVE_HEALTH_UNKNOWN"
    }
    $lkg = Get-RecoveryHotfixProperty $RuntimeReadModel "last_known_good"
    if (-not $lkg) { throw "RECOVERY_LKG_UNAVAILABLE" }
    foreach ($field in @("git_sha", "worker_version_id", "windows_revision", "artifact_kind")) {
        if ([string]::IsNullOrWhiteSpace([string](Get-RecoveryHotfixProperty $lkg $field))) {
            throw "RECOVERY_LKG_IDENTITY_INCOMPLETE"
        }
    }
    if ($RecoveryAction -eq "APPLY_RECOVERY_HOTFIX" -and
        [string](Get-RecoveryHotfixProperty $RuntimeReadModel "drift_status") -ne "MATCHED") {
        throw "RECOVERY_HOTFIX_REQUIRES_ACTIVE_COMMITTED_MATCH"
    }
    return [pscustomobject][ordered]@{
        active = $active
        last_known_good = $lkg
        active_health = $health
        active_degraded = [bool]($health -eq "DEGRADED")
    }
}

function Test-RecoveryShortObserveMode {
    param([AllowNull()][object]$Transaction)
    return [bool](
        (Get-ReleaseTransactionMode $Transaction) -eq $script:RecoveryHotfixModeRecovery -and
        [string](Get-RecoveryHotfixProperty $Transaction "recovery_action") -in
            $script:RecoveryHotfixActions -and
        [string](Get-RecoveryHotfixProperty (
            Get-RecoveryHotfixProperty $Transaction "observe_contract"
        ) "budget_class") -eq "SHORT_BOUNDED"
    )
}
