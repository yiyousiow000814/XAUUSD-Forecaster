param(
    [ValidateSet("Gui", "Status", "StatusJson", "ReleaseStatusJson", "ReleaseProviderFactsJson", "TerminateProviderObservation", "TerminateWatchdogOwner", "CodeRevision", "WpfLayoutSmoke", "Start", "Stop", "Restart", "ServiceStart", "ServiceStop", "Watchdog", "RepairWatchdogOwnership", "RecoverCollectorClock", "DiscoverCandidate", "RetryCandidateValidation", "RetrySemantic", "ReconcileRelease", "PromoteCandidate", "PromoteRecoveryHotfix", "RestoreLastKnownGood", "ReverseStable", "BootstrapRelease", "VerifyMigrationCompatibility", "ApproveCompatibility", "ApproveAccessBoundary", "RegisterAccessProviderInspection", "RegisterFreePlanEvidence", "ReuseAccessQualification", "EnableAutoStart", "DisableAutoStart", "InstallShortcut", "InstallRuntime", "InstallControlPlane", "ControlBundlePreflight", "PreflightRuntimeStateRoot", "MigrateRuntimeStateRoot")]
    [string]$Action = "Gui",
    [ValidateSet("", "quote", "collector", "annotator", "api", "sync", "broadcast")]
    [string]$ServiceKey = "",
    [string]$StatusPath = "",
    [string]$RuntimeRoot = "",
    [string]$RepositoryRoot = "",
    [string]$SourceRoot = "",
    [string]$SourceRevision = "",
    [string]$ExpectedControlScriptPath = "",
    [string]$ExpectedControlRevision = "",
    [string]$InstallTransactionId = "",
    [string]$OperationResultPath = "",
    [string]$AccessChecklistConfirmation = "",
    [string]$AccessProviderInspectionPath = "",
    [string]$ReleaseFreePlanProofPath = "",
    [string]$NativeProcessReceiptPath = "",
    [int]$TargetProcessId = 0,
    [string]$TargetProcessStartToken = "",
    [switch]$SkipProviderObservation,
    [switch]$CollectorClockRecovery
)

$ErrorActionPreference = "Stop"
$controlCenterEntrypointPath = [System.IO.Path]::GetFullPath($PSCommandPath)
$scriptRepositoryRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = if ($RepositoryRoot) {
    [System.IO.Path]::GetFullPath($RepositoryRoot)
} else { $scriptRepositoryRoot }
$moduleRoot = if ($RuntimeRoot) {
    [System.IO.Path]::GetFullPath($RuntimeRoot)
} else { $scriptRepositoryRoot }
$script:nativeProcessOwnershipReceiptPath = $NativeProcessReceiptPath
$runtimeLocalRoot = Join-Path $moduleRoot ".local"
$runtimeForwardRoot = Join-Path $runtimeLocalRoot "forward"
$repositoryLocalRoot = Join-Path $repositoryRoot ".local"
$logRoot = Join-Path $runtimeForwardRoot "logs"
$taskName = "XAUUSD-Forecaster-Autostart"
$guardTaskName = "XAUUSD-Forecaster-Watchdog-Guard"
$workerName = "aurum-signal-room"
$workerUrl = "https://aurum-signal-room.yiyousiow1234.workers.dev"
$dashboardUrl = if ([Environment]::GetEnvironmentVariable("XAUUSD_DASHBOARD_URL", "User")) {
    [Environment]::GetEnvironmentVariable("XAUUSD_DASHBOARD_URL", "User")
} else {
    $workerUrl
}
$protectedDashboardUrl = $workerUrl
$watchdogLog = Join-Path $logRoot "control-watchdog.jsonl"
$watchdogHeartbeatPath = Join-Path $runtimeForwardRoot "control-watchdog-heartbeat.json"
$watchdogOwnerReceiptPath = Join-Path $runtimeForwardRoot "watchdog-owner-v2.json"
$watchdogSingletonContractVersion = "watchdog-machine-singleton-v2"
$runtimeCodeStatePath = Join-Path $runtimeForwardRoot "runtime-code-state.json"
$runtimeUpdateStatePath = Join-Path $runtimeForwardRoot "runtime-update-state.json"
$deferredProjectionSyncRequestPath = Join-Path $runtimeForwardRoot `
    "deferred-projection-sync-request.json"
$deferredProjectionSyncCancelledPath = Join-Path $runtimeForwardRoot `
    "deferred-projection-sync-cancelled.json"
$releaseControlStatePath = Join-Path $runtimeForwardRoot "release-control-state.json"
$releaseHistoryPath = Join-Path $runtimeForwardRoot "release-control-history.jsonl"
$releaseEvidenceRoot = Join-Path $runtimeForwardRoot "release-evidence"
$coordinatedMigrationReceiptPath = Join-Path $runtimeForwardRoot "coordinated-migration-receipt.json"
$coordinatedMigrationRootReceiptRoot = Join-Path $runtimeForwardRoot `
    "coordinated-migration-root-receipts"
$coordinatedMigrationRenewalReceiptRoot = Join-Path $runtimeForwardRoot `
    "coordinated-migration-renewal-receipts"
$accessBoundaryReceiptRoot = Join-Path $runtimeForwardRoot "access-boundary-receipts"
$accessQualificationContractPath = Join-Path $PSScriptRoot "access-qualification-contract.json"
$accessProviderInspectionRoot = Join-Path $runtimeForwardRoot "access-provider-inspections"
$accessQualificationReuseReceiptRoot = Join-Path $runtimeForwardRoot "access-qualification-reuse-receipts"
$accessQualificationRenewalReceiptRoot = Join-Path $runtimeForwardRoot "access-qualification-renewal-receipts"
$releaseLockPath = Join-Path $runtimeForwardRoot "release-control.lock"
$runtimeStateMigrationLockPath = Join-Path $moduleRoot ".runtime-state-migration.lock"
$controlPlaneInstallStatePath = Join-Path $runtimeForwardRoot "control-plane-install-state.json"
$runtimePreflightContractVersion = "isolated-critical-status-diagnostics-v4"
$preflightDiagnosticMaxCharacters = 2048
$codeReloadTimeout = [TimeSpan]::FromMinutes(5)
$serviceStartupTimeout = [TimeSpan]::FromMinutes(15)
$runtimeObservationCycles = 2
$runtimeObservationTimeout = [TimeSpan]::FromMinutes(15)
$promotionFreshnessMinimumLifetime = $serviceStartupTimeout + $runtimeObservationTimeout
$runtimeDecisionHorizon = [TimeSpan]::FromMinutes(30)
$reloadableServiceKeys = @("collector", "annotator", "api", "sync")
$runtimeControlSourceManifestName = "runtime-control-files.json"
$runtimeControlSourceManifestPath = Join-Path $PSScriptRoot `
    $runtimeControlSourceManifestName
if (-not (Test-Path -LiteralPath $runtimeControlSourceManifestPath)) {
    throw "CONTROL_BUNDLE_SOURCE_MANIFEST_MISSING"
}
$runtimeControlSourceManifest = Get-Content -LiteralPath `
    $runtimeControlSourceManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ([int]$runtimeControlSourceManifest.schema_version -ne 1) {
    throw "CONTROL_BUNDLE_SOURCE_MANIFEST_INVALID"
}
$runtimeControlFileNames = @($runtimeControlSourceManifest.files | ForEach-Object {
    [string]$_
})
$runtimeControlManifestName = "runtime-control-bundle.json"
$runtimeControlBundleSchemaVersion = 3
$runtimeControlBundleDigestAlgorithm = "xauusd.control-bundle.sha256.v1"
$collectorSecretsPath = Join-Path $repositoryRoot ".local\secrets\collector-keys.json"
$releaseSecretsRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot ".local\secrets"))
$releaseSecretsPath = [System.IO.Path]::GetFullPath((Join-Path $releaseSecretsRoot "cloudflare-release.json"))
$broadcastHealthUrl = "https://aurum-live-broadcast.yiyousiow1234.workers.dev/health"
$broadcastPublishDryRunUrl = "https://aurum-live-broadcast.yiyousiow1234.workers.dev/publish?dry_run=true"
$broadcastFreshnessThreshold = [TimeSpan]::FromSeconds(90)
$cloudflareAccountId = "48ce531f39e2310b4c858c8916a01d51"
$releaseSchemaVersion = "stable-candidate-release-v3"
$previewArtifactKind = "PREVIEW"
$productionCandidateArtifactKind = "PRODUCTION_CANDIDATE"
$legacyReferenceArtifactKind = "LEGACY_REFERENCE"
$legacyBootstrapStableArtifactKind = "LEGACY_BOOTSTRAP_STABLE"
$unknownArtifactKind = "UNKNOWN"
$requiredGitHubChecks = @(
    "Python regression suite",
    "Web build and tests",
    "Windows runtime contracts",
    "Repository policy",
    "Release Control TLC",
    "Analyze (actions)",
    "Analyze (csharp)",
    "Analyze (javascript-typescript)",
    "Analyze (python)"
)
$workerCpuPassP95Ms = 6.0
$workerCpuPassP99Ms = 8.0
$workerCpuPassMaxMs = 10.0
$candidateStaticAssetMaxBytes = 1048576
$candidateDiscoveryInterval = [TimeSpan]::FromMinutes(5)
$releaseProviderObservationInterval = [TimeSpan]::FromSeconds(30)
$releaseProviderObservationMaximumStaleAge = [TimeSpan]::FromSeconds(60)
$releaseProviderCommandTimeoutMilliseconds = 20000
$releaseProviderBackgroundDeadline = [TimeSpan]::FromSeconds(25)
$script:releaseProviderRefreshInFlight = $false
$script:releaseDeploymentObservationCache = $null
$script:releaseExactVersionObservationCache = @{}
$candidatePlacementPropagationTimeout = [TimeSpan]::FromMinutes(3)
$candidatePlacementProbeIntervalSeconds = 5
$candidateSupersessionHistoryLimit = 128
$candidateSupersessionHistoryByteLimit = 32MB
# A chain consumes at least one immutable history edge per hop. Keep traversal
# materially below the bounded history window so missing ancestry fails closed.
$candidateSupersessionMaxDepth = 16
$candidateOnlyProjectionRoutes = @(
    "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions"
)
$releaseLockOwnerGrace = [TimeSpan]::FromSeconds(30)
$coordinatedMigrationReceiptMaxAge = [TimeSpan]::FromHours(2)
$coordinatedMigrationRenewalMaximumDepth = 32
$coordinatedMigrationRenewalStoreMaximumReceipts = 128
$accessBoundaryReceiptMaxAge = [TimeSpan]::FromHours(2)
$accessMachineReceiptMaxAge = [TimeSpan]::FromHours(2)
# Cloudflare documents 18-month audit-log retention. Keep automatic renewal's
# accepted lookback far inside that provider boundary so coverage fails closed.
$accessProviderAuditMaximumLookback = [TimeSpan]::FromDays(30)
$accessProviderAuditMaximumPages = 10
$accessChecklistConfirmationValue = "ALL_REQUIRED_ACCESS_CHECKS_PASSED"
$bootstrapAcceptedCandidateWorker = "dd823aa4-20f0-47e1-9255-1b785a4c17b0"
$bootstrapAcceptedCandidateRevision = "14c055a35040fa963700c988f770c9bb52fa669e"
$convertFromJsonSupportsDateKind =
    (Get-Command ConvertFrom-Json).Parameters.ContainsKey("DateKind")

. (Join-Path $PSScriptRoot "worker_cpu_evidence.ps1")
. (Join-Path $PSScriptRoot "control_center_common.ps1")
. (Join-Path $PSScriptRoot "control_center_persistence_gateway.ps1")
. (Join-Path $PSScriptRoot "release_evidence_nodes.ps1")
. (Join-Path $PSScriptRoot "release_evidence_authority.ps1")
. (Join-Path $PSScriptRoot "recovery_hotfix.ps1")
. (Join-Path $PSScriptRoot "release_runtime_read_model.ps1")
. (Join-Path $PSScriptRoot "control_center_provider_adapters.ps1")
. (Join-Path $PSScriptRoot "control_center_watchdog_singleton.ps1")
. (Join-Path $PSScriptRoot "control_center_runtime_supervision.ps1")
. (Join-Path $PSScriptRoot "control_center_evidence_authority.ps1")
. (Join-Path $PSScriptRoot "control_center_transaction_engine.ps1")
. (Join-Path $PSScriptRoot "control_center_recovery_engine.ps1")
. (Join-Path $PSScriptRoot "control_center_install.ps1")
. (Join-Path $PSScriptRoot "control_center_presentation.ps1")
$releaseEvidenceContractPath = Join-Path $PSScriptRoot "release-evidence-contract.json"
$releaseEvidenceChangeOwnershipPath = Join-Path $PSScriptRoot `
    "release-evidence-change-ownership.json"
$releaseFreePlanContractPath = Join-Path $PSScriptRoot "release-free-plan-contract.json"





















$serviceContractCodeRoot = $moduleRoot
try {
    $serviceContractRevision = Get-BusinessRuntimeRevision -CodeRoot $serviceContractCodeRoot
} catch {
    # Install/test harnesses can load the controller before RuntimeRoot is a
    # Git checkout. They use the controller's own exact revision contract;
    # production launch/recovery still requires an exact business checkout.
    $serviceContractCodeRoot = $scriptRepositoryRoot
    $serviceContractRevision = Get-BusinessRuntimeRevision -CodeRoot $serviceContractCodeRoot
}
$services = @(Resolve-ServiceLaunchContracts -Revision $serviceContractRevision `
    -CodeRoot $serviceContractCodeRoot)

































































































































































































































































































































































































































































































































































































































































































































































































































if ($ExpectedControlScriptPath -or $ExpectedControlRevision) {
    $null = Assert-ControlCenterProcessIdentity `
        -ExpectedScriptPath $ExpectedControlScriptPath `
        -ExpectedRevision $ExpectedControlRevision
}

if ($OperationResultPath -and $Action -ne "ControlBundlePreflight") {
    $structuredActions = @(
        "Start", "Stop", "Restart", "ServiceStart", "ServiceStop",
        "DiscoverCandidate", "RetryCandidateValidation", "RetrySemantic", "ReconcileRelease", "PromoteCandidate",
        "PromoteRecoveryHotfix", "RestoreLastKnownGood", "ReverseStable",
        "VerifyMigrationCompatibility", "ApproveCompatibility",
        "ApproveAccessBoundary"
    )
    if ($Action -notin $structuredActions) {
        [Console]::Error.WriteLine("Unsupported structured operation: $Action")
        exit 2
    }
    $operationExitCode = Invoke-ControlCenterStructuredOperation `
        -Operation $Action -ResultPath $OperationResultPath
    exit ([int]$operationExitCode)
}

switch ($Action) {
    "Status" { Get-ForecasterStatus | Format-Table -AutoSize }
    "StatusJson" {
        if (-not $StatusPath) { throw "StatusPath is required for StatusJson." }
        $timeService = Get-Service W32Time -ErrorAction SilentlyContinue
        $currentRevision = Get-CodeRevision
        $runtimeState = Get-RuntimeCodeState
        $appliedRevision = if ($runtimeState) {
            [string]$runtimeState.applied_revision
        } else { $null }
        [pscustomobject]@{
            captured_at = [DateTimeOffset]::UtcNow.ToString("o")
            services = @(Get-ForecasterStatus)
            auto_start = [bool](Test-AutoStart)
            windows_time_running = [bool]($timeService -and $timeService.Status -eq "Running")
            runtime_code = [pscustomobject]@{
                current_revision = $currentRevision
                applied_revision = $appliedRevision
                status = if ($currentRevision -and $currentRevision -eq $appliedRevision) {
                    "CURRENT"
                } else { "RELOAD_REQUIRED" }
                applied_at = if ($runtimeState) { $runtimeState.applied_at } else { $null }
            }
            release = Get-ReleaseControlStatusSnapshot `
                -SkipProviderObservation:$SkipProviderObservation
        } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
    }
    "ReleaseStatusJson" {
        if (-not $StatusPath) { throw "StatusPath is required for ReleaseStatusJson." }
        Get-ReleaseControlStatusSnapshot | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath $StatusPath -Encoding UTF8
    }
    "ReleaseProviderFactsJson" {
        if (-not $StatusPath) {
            throw "StatusPath is required for ReleaseProviderFactsJson."
        }
        $providerState = Get-ReleaseControlState
        if (-not $providerState) { throw "RELEASE_CONTROL_STATE_UNAVAILABLE" }
        $authority = Get-ControlCenterProviderAuthorityFingerprint $providerState
        if (-not $authority.valid) { throw "PROVIDER_AUTHORITY_FINGERPRINT_INVALID" }
        try {
            $providerFacts = Get-ReleaseProviderRuntimeFacts `
                -PersistedState $providerState -ForceProviderRefresh
        } catch {
            if ($_.Exception.Message -eq "NATIVE_PROCESS_TERMINATION_UNRESOLVED" -and
                $script:unresolvedNativeProcess) {
                try {
                    Wait-NativeProcessContainment -Process $script:unresolvedNativeProcess
                } finally { $script:unresolvedNativeProcess = $null }
            }
            throw
        }
        $providerFacts | Add-Member -NotePropertyName authority_fingerprint `
            -NotePropertyValue ([string]$authority.digest) -Force
        $providerFacts | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath $StatusPath -Encoding UTF8
    }
    "TerminateProviderObservation" {
        if (-not $StatusPath -or $TargetProcessId -le 0 -or
            [string]::IsNullOrWhiteSpace($TargetProcessStartToken)) {
            throw "PROVIDER_TERMINATION_IDENTITY_REQUIRED"
        }
        Invoke-ControlCenterProviderTermination -ProcessId $TargetProcessId `
            -ProcessStartToken $TargetProcessStartToken `
            -NativeReceiptPath $NativeProcessReceiptPath |
            ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
    }
    "TerminateWatchdogOwner" {
        if ($TargetProcessId -le 0 -or
            [string]::IsNullOrWhiteSpace($TargetProcessStartToken)) {
            throw 'WATCHDOG_TERMINATION_IDENTITY_REQUIRED'
        }
        if (-not (Enter-ReleaseTransactionLock)) {
            throw 'WATCHDOG_TERMINATION_CONTROL_TRANSIENT_ACTIVE'
        }
        try {
            $release = Get-ReleaseControlState
            if (-not $release -or $release.transaction) {
                throw 'WATCHDOG_TERMINATION_CONTROL_TRANSIENT_ACTIVE'
            }
            $inventory = Get-WatchdogOwnershipInventory
            $owner = @($inventory.authoritative + $inventory.duplicate_shaped |
                Where-Object {
                    [int]$_.process_id -eq $TargetProcessId -and
                    (Test-ControlPlaneStartTokenEqual -Left $_.process_start_token `
                        -Right $TargetProcessStartToken)
                })
            if ($owner.Count -ne 1) { throw 'WATCHDOG_OWNER_IDENTITY_UNRESOLVED' }
            Stop-VerifiedWatchdogOwner -Identity $owner[0] | Out-Null
        } finally { Exit-ReleaseTransactionLock }
    }
    "CodeRevision" { Write-Output (Get-CodeRevision) }
    "WpfLayoutSmoke" {
        Test-WpfControlCenterLayout | ConvertTo-Json -Depth 4
    }
    "Start" { Invoke-ControlCenterOperationAction -Operation $Action | Format-Table -AutoSize }
    "Stop" { Invoke-ControlCenterOperationAction -Operation $Action | Format-Table -AutoSize }
    "Restart" { Invoke-ControlCenterOperationAction -Operation $Action | Format-Table -AutoSize }
    "ServiceStart" { $null = Invoke-ControlCenterOperationAction -Operation $Action }
    "ServiceStop" { $null = Invoke-ControlCenterOperationAction -Operation $Action }
    "Watchdog" {
        exit (Invoke-ForecasterWatchdog -InstallTransactionId $InstallTransactionId)
    }
    "RepairWatchdogOwnership" {
        Invoke-WatchdogOwnershipRepair | ConvertTo-Json -Depth 12
    }
    "DiscoverCandidate" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "RetryCandidateValidation" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "RetrySemantic" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "ReconcileRelease" {
        Invoke-ControlCenterOperationAction -Operation $Action | ConvertTo-Json -Depth 12
    }
    "PromoteCandidate" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "PromoteRecoveryHotfix" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "RestoreLastKnownGood" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "ReverseStable" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "BootstrapRelease" {
        if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
        try { Initialize-ReleaseControl | ConvertTo-Json -Depth 12 }
        finally { Exit-ReleaseTransactionLock }
    }
    "VerifyMigrationCompatibility" {
        Invoke-ControlCenterOperationAction -Operation $Action | ConvertTo-Json -Depth 12
    }
    "ApproveCompatibility" {
        Invoke-ControlCenterOperationAction -Operation $Action | ConvertTo-Json -Depth 12
    }
    "ApproveAccessBoundary" {
        Invoke-ControlCenterOperationAction -Operation $Action | ConvertTo-Json -Depth 12
    }
    "RegisterAccessProviderInspection" {
        if (-not $AccessProviderInspectionPath -or
            -not (Test-Path -LiteralPath $AccessProviderInspectionPath -PathType Leaf)) {
            throw "ACCESS_PROVIDER_INSPECTION_FILE_REQUIRED"
        }
        $file = Get-Item -LiteralPath $AccessProviderInspectionPath
        if ($file.Length -gt 32768) { throw "ACCESS_PROVIDER_INSPECTION_FILE_TOO_LARGE" }
        $inspection = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if (-not (Enter-ReleaseTransactionLock)) {
            throw "Another release transaction is active."
        }
        try {
            $receipt = Register-AccessProviderInspection -Inspection $inspection
            $null = Finalize-CandidateQualificationEvidence `
                -WhyRan "ACCESS_PROVIDER_INSPECTION_COMPLETED"
        } finally { Exit-ReleaseTransactionLock }
        $receipt | ConvertTo-Json -Depth 12
    }
    "RegisterFreePlanEvidence" {
        if (-not $ReleaseFreePlanProofPath) {
            throw "FREE_PLAN_PROOF_FILE_REQUIRED"
        }
        if (-not (Enter-ReleaseTransactionLock)) {
            throw "Another release transaction is active."
        }
        try {
            $state = Get-ReleaseControlState
            if (-not $state -or -not $state.candidate -or $state.transaction) {
                throw "FREE_PLAN_CANDIDATE_UNAVAILABLE"
            }
            $receipt = Register-CandidateFreePlanEvidence -Candidate $state.candidate `
                -ProofPath $ReleaseFreePlanProofPath
            $null = Finalize-CandidateQualificationEvidence `
                -WhyRan "FREE_PLAN_EVIDENCE_COMPLETED"
        } finally { Exit-ReleaseTransactionLock }
        $receipt | ConvertTo-Json -Depth 12
    }
    "ReuseAccessQualification" {
        Invoke-CandidateAccessQualificationReuse | ConvertTo-Json -Depth 12
    }
    "EnableAutoStart" { Enable-AutoStart; Write-Output "Auto-start enabled." }
    "DisableAutoStart" { Disable-AutoStart; Write-Output "Auto-start disabled." }
    "InstallRuntime" { Install-ProductionRuntime | Format-List }
    "InstallControlPlane" {
        if (-not $SourceRoot -or -not $SourceRevision) {
            throw "SourceRoot and SourceRevision are required for InstallControlPlane."
        }
        Invoke-ControlPlaneInstall -VerifiedSourceRoot `
            ([System.IO.Path]::GetFullPath($SourceRoot)) `
            -TargetRevision $SourceRevision -CollectorClockRecovery:$CollectorClockRecovery | Format-List
    }
    "RecoverCollectorClock" {
        if (-not (Enter-ReleaseTransactionLock)) { throw 'COLLECTOR_RECOVERY_RELEASE_LOCK_REQUIRED' }
        try {
            Invoke-CollectorClockRecoveryOperation -Apply | ConvertTo-Json -Depth 8
        } finally { Exit-ReleaseTransactionLock }
    }
    "ControlBundlePreflight" {
        if (-not $OperationResultPath) {
            throw "OperationResultPath is required for ControlBundlePreflight."
        }
        $bundle = Get-RuntimeControlBundleIdentityAtRoot `
            -ControlRoot $PSScriptRoot -RequireDependencyClosure
        if (-not $bundle) { throw "CONTROL_BUNDLE_STARTUP_PREFLIGHT_FAILED" }
        [pscustomobject]@{
            schema = "xauusd.control-bundle-startup-preflight.v1"
            observed_at = [DateTimeOffset]::UtcNow.ToString("o")
            process_id = $PID
            supervision_mode = "QUIESCED"
            control_bundle_revision = [string]$bundle.source_revision
            control_bundle_hash_verified = $true
            dependency_closed = [bool]$bundle.dependency_closed_verified
            bundle_digest = [string]$bundle.bundle_digest
        } | ConvertTo-Json -Depth 5 | Set-Content `
            -LiteralPath $OperationResultPath -Encoding UTF8
    }
    "PreflightRuntimeStateRoot" {
        Invoke-RuntimeStateRootMigration -PreflightOnly | Format-List
    }
    "MigrateRuntimeStateRoot" {
        Invoke-RuntimeStateRootMigration | Format-List
    }
    "InstallShortcut" { Write-Output (Install-ControlShortcut) }
    default { Show-ControlCenter }
}
