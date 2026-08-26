param(
    [ValidateSet("Gui", "Status", "StatusJson", "ReleaseStatusJson", "CodeRevision", "WpfLayoutSmoke", "Start", "Stop", "Restart", "ServiceStart", "ServiceStop", "Watchdog", "DiscoverCandidate", "RetryCandidateValidation", "ReconcileRelease", "PromoteCandidate", "ReverseStable", "BootstrapRelease", "VerifyMigrationCompatibility", "ApproveCompatibility", "EnableAutoStart", "DisableAutoStart", "InstallShortcut", "InstallRuntime", "InstallControlPlane")]
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
    [string]$OperationResultPath = ""
)

$ErrorActionPreference = "Stop"
$scriptRepositoryRoot = Split-Path -Parent $PSScriptRoot
$repositoryRoot = if ($RepositoryRoot) {
    [System.IO.Path]::GetFullPath($RepositoryRoot)
} else { $scriptRepositoryRoot }
$moduleRoot = if ($RuntimeRoot) {
    [System.IO.Path]::GetFullPath($RuntimeRoot)
} else { $scriptRepositoryRoot }
$logRoot = Join-Path $moduleRoot ".local\forward\logs"
$taskName = "XAUUSD-Forecaster-Autostart"
$guardTaskName = "XAUUSD-Forecaster-Watchdog-Guard"
$dashboardUrl = if ([Environment]::GetEnvironmentVariable("XAUUSD_DASHBOARD_URL", "User")) {
    [Environment]::GetEnvironmentVariable("XAUUSD_DASHBOARD_URL", "User")
} else {
    "https://aurum-signal-room.yiyousiow1234.chatgpt.site"
}
$watchdogLog = Join-Path $logRoot "control-watchdog.jsonl"
$watchdogHeartbeatPath = Join-Path $moduleRoot ".local\forward\control-watchdog-heartbeat.json"
$runtimeCodeStatePath = Join-Path $moduleRoot ".local\forward\runtime-code-state.json"
$runtimeUpdateStatePath = Join-Path $moduleRoot ".local\forward\runtime-update-state.json"
$releaseControlStatePath = Join-Path $moduleRoot ".local\forward\release-control-state.json"
$releaseHistoryPath = Join-Path $moduleRoot ".local\forward\release-control-history.jsonl"
$coordinatedMigrationReceiptPath = Join-Path $moduleRoot ".local\forward\coordinated-migration-receipt.json"
$releaseLockPath = Join-Path $moduleRoot ".local\forward\release-control.lock"
$controlPlaneInstallStatePath = Join-Path $moduleRoot ".local\forward\control-plane-install-state.json"
$runtimePreflightContractVersion = "isolated-critical-status-diagnostics-v4"
$preflightDiagnosticMaxCharacters = 2048
$codeReloadTimeout = [TimeSpan]::FromMinutes(5)
$serviceStartupTimeout = [TimeSpan]::FromMinutes(15)
$runtimeObservationCycles = 2
$runtimeObservationTimeout = [TimeSpan]::FromMinutes(15)
$runtimeDecisionHorizon = [TimeSpan]::FromMinutes(30)
$reloadableServiceKeys = @("collector", "annotator", "api", "sync")
$runtimeControlFileNames = @(
    "xauusd_control_center.ps1",
    "control_center.xaml",
    "xauusd_control_center_launcher.vbs",
    "xauusd_watchdog_launcher.vbs",
    "xauusd_watchdog_guard.ps1",
    "xauusd_watchdog_guard_launcher.vbs"
)
$runtimeControlManifestName = "runtime-control-bundle.json"
$collectorSecretsPath = Join-Path $repositoryRoot ".local\secrets\collector-keys.json"
$releaseSecretsRoot = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot ".local\secrets"))
$releaseSecretsPath = [System.IO.Path]::GetFullPath((Join-Path $releaseSecretsRoot "cloudflare-release.json"))
$workerName = "aurum-signal-room"
$workerUrl = "https://aurum-signal-room.yiyousiow1234.workers.dev"
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
$candidatePlacementPropagationTimeout = [TimeSpan]::FromMinutes(3)
$candidatePlacementProbeIntervalSeconds = 5
$candidateOnlyProjectionRoutes = @(
    "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions"
)
$releaseLockOwnerGrace = [TimeSpan]::FromSeconds(30)
$coordinatedMigrationReceiptMaxAge = [TimeSpan]::FromHours(2)
$bootstrapAcceptedCandidateWorker = "dd823aa4-20f0-47e1-9255-1b785a4c17b0"
$bootstrapAcceptedCandidateRevision = "14c055a35040fa963700c988f770c9bb52fa669e"

function Get-UserEnvironmentValue {
    param([Parameter(Mandatory = $true)][string]$Name)
    [Environment]::GetEnvironmentVariable($Name, "User")
}

function Get-ReleaseSecret {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ([System.IO.Path]::GetDirectoryName($releaseSecretsPath) -ne $releaseSecretsRoot) {
        return [pscustomobject]@{
            available = $false; value = ""; source = "UNAVAILABLE"
            diagnostic = "LOCAL_SECRET_PATH_INVALID"
        }
    }
    if (Test-Path -LiteralPath $releaseSecretsPath) {
        try {
            $secrets = Get-Content -LiteralPath $releaseSecretsPath -Raw -Encoding UTF8 |
                ConvertFrom-Json
        } catch {
            return [pscustomobject]@{
                available = $false; value = ""; source = "UNAVAILABLE"
                diagnostic = "LOCAL_SECRET_FILE_MALFORMED_JSON"
            }
        }
        $property = $secrets.PSObject.Properties[$Name]
        if (-not $property) {
            return [pscustomobject]@{
                available = $false; value = ""; source = "UNAVAILABLE"
                diagnostic = "LOCAL_SECRET_KEY_MISSING"
            }
        }
        $value = ([string]$property.Value).Trim()
        if (-not $value) {
            return [pscustomobject]@{
                available = $false; value = ""; source = "UNAVAILABLE"
                diagnostic = "LOCAL_SECRET_VALUE_EMPTY"
            }
        }
        return [pscustomobject]@{
            available = $true; value = $value; source = "LOCAL_SECRET_FILE"
            diagnostic = $null
        }
    }
    $value = ([string](Get-UserEnvironmentValue -Name $Name)).Trim()
    if ($value) {
        return [pscustomobject]@{
            available = $true; value = $value; source = "USER_ENVIRONMENT"
            diagnostic = $null
        }
    }
    return [pscustomobject]@{
        available = $false; value = ""; source = "UNAVAILABLE"
        diagnostic = "RELEASE_SECRET_UNAVAILABLE"
    }
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

$services = @(
    [pscustomobject]@{
        Key = "quote"
        Label = "cTrader XAUUSD Local Algo"
        Match = "run_live_quote_bridge.ps1"
        Kind = "PowerShell"
        Script = "ctrader\XauusdForwardQuoteBridge\run_live_quote_bridge.ps1"
        Arguments = @("-Symbol", "XAUUSD")
    },
    [pscustomobject]@{
        Key = "collector"
        Label = "XAUUSD Collector"
        Match = "run_forward_collector.py"
        Kind = "Python"
        Script = "scripts\run_forward_collector.py"
        Arguments = @(
            "--market-jsonl", (Join-Path $moduleRoot ".local\forward\quotes"),
            "--poll-seconds", "10",
            "--news-poll-seconds", "60",
            "--minimum-training-rows", "200",
            "--retrain-interval", "50"
        )
    },
    [pscustomobject]@{
        Key = "annotator"
        Label = "Gemini News Annotator"
        Match = "run_news_annotator.py"
        Kind = "Python"
        Script = "scripts\run_news_annotator.py"
        Arguments = @("--interval-seconds", "60", "--batch-size", "0")
    },
    [pscustomobject]@{
        Key = "api"
        Label = "Dashboard API"
        Match = "run_dashboard_api.py"
        Kind = "Python"
        Script = "scripts\run_dashboard_api.py"
        Arguments = @()
    },
    [pscustomobject]@{
        Key = "sync"
        Label = "Dashboard Mirrors"
        Match = "run_dashboard_sync.py"
        Kind = "Python"
        Script = "scripts\run_dashboard_sync.py"
        Arguments = @("--interval-seconds", "30")
    },
    [pscustomobject]@{
        Key = "broadcast"
        Label = "Live Broadcast Publisher"
        Match = "run_live_broadcast_publisher.py"
        Kind = "Python"
        Script = "scripts\run_live_broadcast_publisher.py"
        Arguments = @(
            "--interval-seconds", "30",
            "--activate-production-publisher"
        )
    }
)

function Test-BroadcastPublisherEnabled {
    [string](Get-UserEnvironmentValue -Name "AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED") -eq "1"
}

function Get-BroadcastPublisherToken {
    [string](Get-UserEnvironmentValue -Name "LIVE_BROADCAST_PUBLISH_TOKEN")
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

function Get-ReleaseLifecyclePhase {
    param([object]$ReleaseState)
    if (-not $ReleaseState) { return "UNAVAILABLE" }
    if ($ReleaseState.transaction) {
        if ([string]$ReleaseState.transaction.phase -in @(
            "OBSERVING", "REVERSE_OBSERVING"
        )) { return "OBSERVE" }
        return "SWITCH"
    }
    if ([string]$ReleaseState.deployment_status -in @(
        "RECOVERY_REQUIRED", "DEPLOYMENT_DRIFT"
    )) { return "OBSERVE" }
    if ($ReleaseState.candidate) {
        $reason = if ($ReleaseState.candidate.validation) {
            [string]$ReleaseState.candidate.validation.reason
        } else { "" }
        if ([string]$ReleaseState.candidate.validation_state -in @("NEW", "STAGING") -or
            $reason -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
            return "PREPARE"
        }
        return "VERIFY"
    }
    return "STABLE"
}

function Set-ReleaseLifecycleProjection {
    param([Parameter(Mandatory = $true)][object]$ReleaseState)
    $phase = Get-ReleaseLifecyclePhase -ReleaseState $ReleaseState
    if ($ReleaseState.PSObject.Properties['lifecycle_phase']) {
        $ReleaseState.lifecycle_phase = $phase
    } else {
        $ReleaseState | Add-Member -NotePropertyName lifecycle_phase `
            -NotePropertyValue $phase
    }
}

function Get-ReleaseControlState {
    if (-not (Test-Path -LiteralPath $releaseControlStatePath)) { return $null }
    try {
        $state = Get-Content -LiteralPath $releaseControlStatePath -Raw | ConvertFrom-Json
        if (-not $state.candidate_discovery) {
            $state | Add-Member -NotePropertyName candidate_discovery -NotePropertyValue (
                [pscustomobject]@{
                    watermark_created_at = $null
                    watermark_version_id = $null
                    initialized_at = $null
                }
            )
        }
        if (-not $state.PSObject.Properties['previous_stable_rollback_eligible']) {
            $state | Add-Member -NotePropertyName previous_stable_rollback_eligible `
                -NotePropertyValue $false
        }
        if (-not $state.PSObject.Properties['previous_stable_rollback_reason']) {
            $state | Add-Member -NotePropertyName previous_stable_rollback_reason `
                -NotePropertyValue "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        }
        if ([string]$state.schema_version -in @(
            "stable-candidate-release-v1", "stable-candidate-release-v2"
        )) {
            foreach ($identity in @($state.stable, $state.previous_stable)) {
                if ($identity -and -not $identity.PSObject.Properties['artifact_kind']) {
                    $identity | Add-Member -NotePropertyName artifact_kind `
                        -NotePropertyValue $productionCandidateArtifactKind
                }
            }
            foreach ($identity in @($state.candidate, $state.queued_candidate)) {
                if ($identity -and -not $identity.PSObject.Properties['artifact_kind']) {
                    $legacyAccepted = (
                        [string]$identity.worker_version_id -eq $bootstrapAcceptedCandidateWorker -and
                        [string]$identity.git_sha -eq $bootstrapAcceptedCandidateRevision
                    )
                    $identity | Add-Member -NotePropertyName artifact_kind `
                        -NotePropertyValue $(if ($legacyAccepted) {
                            $legacyReferenceArtifactKind
                        } else { $unknownArtifactKind })
                }
            }
            if ($state.stable) {
                $state.stable.artifact_kind = $legacyBootstrapStableArtifactKind
                if (-not $state.stable.PSObject.Properties['worker_git_sha']) {
                    $state.stable | Add-Member -NotePropertyName worker_git_sha `
                        -NotePropertyValue "NOT_RECORDED"
                }
            }
            if ($state.candidate -and
                [string]$state.candidate.worker_version_id -eq $bootstrapAcceptedCandidateWorker -and
                [string]$state.candidate.git_sha -eq $bootstrapAcceptedCandidateRevision) {
                $state.candidate.artifact_kind = $legacyReferenceArtifactKind
                foreach ($field in @("validation_state", "compatibility_state")) {
                    if ($state.candidate.PSObject.Properties[$field]) {
                        $state.candidate.$field = "REBASE_REQUIRED"
                    } else {
                        $state.candidate | Add-Member -NotePropertyName $field `
                            -NotePropertyValue "REBASE_REQUIRED"
                    }
                }
                if ($state.candidate.validation) {
                    $state.candidate.validation | Add-Member -NotePropertyName reason `
                        -NotePropertyValue "REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED" -Force
                }
            }
            $state.schema_version = $releaseSchemaVersion
        }
        Set-ReleaseLifecycleProjection -ReleaseState $state
        return $state
    } catch { $null }
}

function Write-ReleaseControlState {
    param([Parameter(Mandatory = $true)][object]$State)
    Set-ReleaseLifecycleProjection -ReleaseState $State
    $controlBundle = Get-RuntimeControlBundleIdentity
    foreach ($field in @("control_bundle_revision", "control_bundle_exact_revision",
        "control_bundle_hash_verified")) {
        if (-not $State.PSObject.Properties[$field]) {
            $State | Add-Member -NotePropertyName $field -NotePropertyValue $null
        }
    }
    $State.control_bundle_revision = if ($controlBundle) {
        [string]$controlBundle.source_revision
    } else { $null }
    $State.control_bundle_exact_revision = [bool]($controlBundle -and $controlBundle.exact_revision)
    $State.control_bundle_hash_verified = [bool]$controlBundle
    $directory = Split-Path -Parent $releaseControlStatePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$releaseControlStatePath.tmp"
    $State | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $releaseControlStatePath -Force
}

function Write-ReleaseHistory {
    param([string]$Event, [object]$Release, [hashtable]$Detail = @{})
    $directory = Split-Path -Parent $releaseHistoryPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    [pscustomobject]@{
        occurred_at = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        release = $Release
        detail = $Detail
    } | ConvertTo-Json -Compress -Depth 12 | Add-Content -LiteralPath $releaseHistoryPath -Encoding UTF8
}

function New-ReleaseIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$GitSha,
        [Parameter(Mandatory = $true)][string]$WorkerVersionId,
        [Parameter(Mandatory = $true)][string]$WindowsRevision,
        [string]$Branch = "",
        [string]$PullRequest = "",
        [string]$ValidationState = "NEW",
        [ValidateSet("PREVIEW", "PRODUCTION_CANDIDATE", "LEGACY_REFERENCE", "LEGACY_BOOTSTRAP_STABLE", "UNKNOWN")]
        [string]$ArtifactKind = "UNKNOWN",
        [string]$VersionCreatedAt = ""
    )
    [pscustomobject]@{
        git_sha = $GitSha
        worker_version_id = $WorkerVersionId
        windows_revision = $WindowsRevision
        branch = $Branch
        pull_request = $PullRequest
        artifact_kind = $ArtifactKind
        version_created_at = $VersionCreatedAt
        compatibility_state = "PENDING"
        cutover_order = "PAUSE_SYNC_WINDOWS_WORKER_RESUME_SYNC"
        validation_state = $ValidationState
        validation_key = "$WorkerVersionId`:$GitSha"
        validation = $null
        discovered_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Test-ReleaseIdentity {
    param([object]$Left, [object]$Right)
    return [bool](
        $Left -and $Right -and
        [string]$Left.git_sha -eq [string]$Right.git_sha -and
        [string]$Left.worker_version_id -eq [string]$Right.worker_version_id -and
        [string]$Left.windows_revision -eq [string]$Right.windows_revision -and
        [string]$Left.artifact_kind -eq [string]$Right.artifact_kind
    )
}

function Enter-ReleaseTransactionLock {
    if (Test-Path -LiteralPath $releaseLockPath) {
        $owner = $null
        try {
            $owner = Get-Content -LiteralPath (Join-Path $releaseLockPath "owner.json") -Raw |
                ConvertFrom-Json
        } catch {}
        $ownerAlive = $false
        if ($owner -and [int]$owner.owner_pid -gt 0) {
            $ownerProcess = Get-ControlPlaneProcessIdentity `
                -ProcessId ([int]$owner.owner_pid)
            $ownerAlive = [bool]($ownerProcess -and (
                -not [string]$owner.owner_process_start_token -or
                [string]$owner.owner_process_start_token -eq
                    [string]$ownerProcess.process_start_token
            ))
        }
        $acquired = [DateTimeOffset]::MinValue
        $ageKnown = $owner -and [DateTimeOffset]::TryParse(
            [string]$owner.acquired_at, [ref]$acquired
        )
        $lockCreated = [DateTimeOffset](Get-Item -LiteralPath $releaseLockPath).CreationTimeUtc
        $stale = (-not $ownerAlive) -and (
            $ageKnown -or
            ([DateTimeOffset]::UtcNow - $lockCreated) -ge $releaseLockOwnerGrace
        )
        if (-not $stale) { return $false }
        # The path is fixed below the runtime state root. Only an abandoned lock
        # whose owner is gone is removed; release state/history are untouched.
        Remove-Item -LiteralPath $releaseLockPath -Recurse -Force -ErrorAction Stop
        Write-ReleaseHistory -Event "ABANDONED_LOCK_RECOVERED" -Release $null `
            -Detail @{ owner_pid = if ($owner) { [int]$owner.owner_pid } else { 0 } }
    }
    try {
        New-Item -ItemType Directory -Path $releaseLockPath -ErrorAction Stop | Out-Null
        $lockOwnerIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
        if (-not $lockOwnerIdentity) {
            throw "RELEASE_LOCK_OWNER_IDENTITY_REQUIRED"
        }
        [pscustomobject]@{
            owner_pid = $PID
            owner_process_start_token = [string]$lockOwnerIdentity.process_start_token
            acquired_at = [DateTimeOffset]::UtcNow.ToString("o")
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath "owner.json") -Encoding UTF8
        $script:releaseTransactionLockHeld = $true
        return $true
    } catch { return $false }
}

function Exit-ReleaseTransactionLock {
    if ($script:releaseTransactionLockHeld -and (Test-Path -LiteralPath $releaseLockPath)) {
        Remove-Item -LiteralPath $releaseLockPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    $script:releaseTransactionLockHeld = $false
}

function Invoke-WranglerJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $webRoot = Join-Path $repositoryRoot "web"
    Push-Location $webRoot
    try {
        $output = @(& npx.cmd wrangler @Arguments --json 2>$null)
        if ($LASTEXITCODE -ne 0) { throw "Wrangler command failed." }
        ($output -join "`n") | ConvertFrom-Json
    } finally { Pop-Location }
}

function Get-CloudflareDeployment {
    Invoke-WranglerJson -Arguments @("deployments", "status", "--name", $workerName)
}

function Get-CloudflareVersions {
    $versions = Invoke-WranglerJson -Arguments @(
        "versions", "list", "--name", $workerName
    )
    # ConvertFrom-Json may return its top-level JSON array as one pipeline
    # object. Emit each version explicitly so sorting/filtering never treats
    # the complete Wrangler response as one synthetic version.
    foreach ($version in @($versions)) { Write-Output $version }
}

function Get-OriginMainRevision {
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot ".git"))) {
        return $null
    }
    $fetch = Invoke-RepositoryRead -Operation "FETCH_ORIGIN" `
        -Arguments @("-C", $repositoryRoot, "fetch", "origin", "--quiet")
    if (-not $fetch.passed) { return $null }
    $result = Invoke-RepositoryRead -Operation "READ_ORIGIN_MAIN" `
        -Arguments @("-C", $repositoryRoot, "rev-parse", "origin/main")
    if (-not $result.passed) { return $null }
    $revision = ([string](@($result.output)[0])).Trim().ToLowerInvariant()
    if ($revision -notmatch '^[0-9a-f]{40}$') { return $null }
    return $revision
}

function Set-CandidateMaterializationState {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][string]$Revision,
        [Parameter(Mandatory = $true)][ValidateSet("PENDING", "MATERIALIZED")]
        [string]$Status,
        [string]$WorkerVersionId = ""
    )
    $receipt = [pscustomobject]@{
        revision = $Revision
        state = $Status
        reason = if ($Status -eq "MATERIALIZED") {
            "EXACT_MAIN_CANDIDATE_MATERIALIZED"
        } else { "EXACT_MAIN_CANDIDATE_PENDING" }
        worker_version_id = if ($WorkerVersionId) { $WorkerVersionId } else { $null }
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    if ($State.PSObject.Properties['candidate_materialization']) {
        $State.candidate_materialization = $receipt
    } else {
        $State | Add-Member -NotePropertyName candidate_materialization `
            -NotePropertyValue $receipt
    }
}

function Get-CloudflareVersionDetails {
    param([Parameter(Mandatory = $true)][string]$VersionId)
    Invoke-WranglerJson -Arguments @(
        "versions", "view", $VersionId, "--name", $workerName
    )
}

function Invoke-CloudflareDeployment {
    param(
        [Parameter(Mandatory = $true)][string]$StableVersionId,
        [string]$CandidateVersionId = "",
        [Parameter(Mandatory = $true)][string]$Message
    )
    $specifications = @("$StableVersionId@100")
    if ($CandidateVersionId) { $specifications += "$CandidateVersionId@0" }
    $webRoot = Join-Path $repositoryRoot "web"
    Push-Location $webRoot
    try {
        $null = @(& npx.cmd wrangler versions deploy @specifications --name $workerName --yes --message $Message 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "Cloudflare deployment failed." }
    } finally { Pop-Location }
}

function Get-ReleaseGitShaFromVersion {
    param([Parameter(Mandatory = $true)][object]$Version)
    $message = [string]$Version.annotations.'workers/message'
    if ($message -match '(?i)release:([0-9a-f]{40})') { return $matches[1].ToLowerInvariant() }
    return $null
}

function Get-ReleaseBranchFromVersion {
    param([Parameter(Mandatory = $true)][object]$Version)
    $message = [string]$Version.annotations.'workers/message'
    if ($message -match '(?i)branch:([^\s]+)') { return $matches[1] }
    return [string]$Version.annotations.'workers/alias'
}

function Get-ReleaseArtifactKindFromVersion {
    param([Parameter(Mandatory = $true)][object]$Version)
    $message = [string]$Version.annotations.'workers/message'
    if ($message -match '(?i)artifact[_-]kind:(PREVIEW|PRODUCTION_CANDIDATE)') {
        return $matches[1].ToUpperInvariant()
    }
    return $unknownArtifactKind
}

function Get-ReleaseTimestampValues {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return }
    if ($Value -is [DateTimeOffset]) {
        Write-Output ($Value.ToUniversalTime().ToString(
            "o", [Globalization.CultureInfo]::InvariantCulture
        ))
        return
    }
    if ($Value -is [DateTime]) {
        Write-Output ($Value.ToUniversalTime().ToString(
            "o", [Globalization.CultureInfo]::InvariantCulture
        ))
        return
    }
    if ($Value -is [string]) {
        if (-not [string]::IsNullOrWhiteSpace($Value)) { Write-Output $Value }
        return
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        foreach ($item in $Value) { Get-ReleaseTimestampValues -Value $item }
        return
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Value)) { Write-Output $Value }
}

function ConvertTo-ReleaseTimestampUtc {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return [DateTimeOffset]::MinValue }
    if ($Value -is [DateTimeOffset]) { return $Value.ToUniversalTime() }
    if ($Value -is [DateTime]) {
        return ([DateTimeOffset]$Value.ToUniversalTime()).ToUniversalTime()
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return [DateTimeOffset]::MinValue }
    $parsed = [DateTimeOffset]::MinValue
    $styles = [Globalization.DateTimeStyles]::AllowWhiteSpaces -bor
        [Globalization.DateTimeStyles]::AssumeUniversal
    if ([DateTimeOffset]::TryParse(
        $text, [Globalization.CultureInfo]::InvariantCulture,
        $styles, [ref]$parsed
    )) { return $parsed.ToUniversalTime() }
    if ([DateTimeOffset]::TryParse($text, [ref]$parsed)) {
        return $parsed.ToUniversalTime()
    }
    return [DateTimeOffset]::MinValue
}

function Get-ReleaseVersionPreviewUrl {
    param(
        [Parameter(Mandatory = $true)][object]$Version,
        [Parameter(Mandatory = $true)][object]$Candidate
    )
    if (-not [bool]$Version.metadata.has_preview -or
        [string]$Version.id -notmatch '^[0-9a-f]{8}-[0-9a-f-]{27}$' -or
        [string]$Version.id -ne [string]$Candidate.worker_version_id -or
        (Get-ReleaseGitShaFromVersion -Version $Version) -ne [string]$Candidate.git_sha -or
        (Get-ReleaseArtifactKindFromVersion -Version $Version) -ne
            $productionCandidateArtifactKind) { return "" }
    try {
        $production = [Uri]$workerUrl
        $workerPrefix = "$workerName."
        if (-not $production.Host.StartsWith(
            $workerPrefix, [StringComparison]::OrdinalIgnoreCase
        )) { return "" }
        $suffix = $production.Host.Substring($workerPrefix.Length)
        $versionPrefix = ([string]$Version.id).Substring(0, 8)
        return "{0}://{1}-{2}.{3}" -f `
            $production.Scheme, $versionPrefix, $workerName, $suffix
    } catch { return "" }
}

function Get-ReleaseVersionCreatedAtValue {
    param([Parameter(Mandatory = $true)][object]$Version)
    $newest = [DateTimeOffset]::MinValue
    foreach ($candidate in @(Get-ReleaseTimestampValues -Value $Version.metadata.created_on)) {
        $utc = ConvertTo-ReleaseTimestampUtc -Value $candidate
        if ($utc -gt $newest) { $newest = $utc }
    }
    return $newest
}

function Get-ReleaseVersionCreatedAt {
    param([Parameter(Mandatory = $true)][object]$Version)
    $created = Get-ReleaseVersionCreatedAtValue -Version $Version
    if ($created -eq [DateTimeOffset]::MinValue) { return "" }
    return $created.ToUniversalTime().ToString(
        "o", [Globalization.CultureInfo]::InvariantCulture
    )
}

function Test-VersionAfterDiscoveryWatermark {
    param(
        [Parameter(Mandatory = $true)][object]$Version,
        [Parameter(Mandatory = $true)][object]$Discovery
    )
    if (-not $Discovery.watermark_created_at) { return $true }
    $createdAt = Get-ReleaseVersionCreatedAt -Version $Version
    if (-not $createdAt) { return $false }
    $created = ConvertTo-ReleaseTimestampUtc -Value $createdAt
    $watermark = ConvertTo-ReleaseTimestampUtc `
        -Value ([string]$Discovery.watermark_created_at)
    if ($created -eq [DateTimeOffset]::MinValue -or
        $watermark -eq [DateTimeOffset]::MinValue) { return $false }
    if ($created -gt $watermark) { return $true }
    if ($created -lt $watermark) { return $false }
    return [string]$Version.id -gt [string]$Discovery.watermark_version_id
}

function Set-CandidateDiscoveryWatermark {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][object]$Version
    )
    $State.candidate_discovery.watermark_created_at =
        Get-ReleaseVersionCreatedAt -Version $Version
    $State.candidate_discovery.watermark_version_id = [string]$Version.id
    if (-not $State.candidate_discovery.initialized_at) {
        $State.candidate_discovery.initialized_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Get-DeploymentVersion {
    param([object]$Deployment, [double]$Percentage)
    @($Deployment.versions | Where-Object { [double]$_.percentage -eq $Percentage }) |
        Select-Object -First 1
}

function New-ReleaseControlState {
    param([object]$Stable, [object]$Candidate = $null)
    [pscustomobject]@{
        schema_version = $releaseSchemaVersion
        stable = $Stable
        candidate = $Candidate
        previous_stable = $null
        previous_stable_rollback_eligible = $false
        previous_stable_rollback_reason = "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        queued_candidate = $null
        transaction = $null
        lifecycle_phase = if ($Candidate) { "PREPARE" } else { "STABLE" }
        deployment_status = "READY"
        drift = $null
        last_candidate_check = $null
        candidate_discovery = [pscustomobject]@{
            watermark_created_at = $null
            watermark_version_id = $null
            initialized_at = $null
        }
        candidate_materialization = $null
        updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Initialize-ReleaseControl {
    $existing = Get-ReleaseControlState
    if ($existing) { return $existing }
    $deployment = Get-CloudflareDeployment
    $stableVersion = Get-DeploymentVersion -Deployment $deployment -Percentage 100
    if (-not $stableVersion) { throw "Exactly one 100% Stable Worker version is required." }
    $runtime = Get-RuntimeCodeState
    $revision = if ($runtime) { [string]$runtime.applied_revision } else { Get-CodeRevision }
    if ($revision -notmatch '^[0-9a-f]{40}$') { throw "Stable Windows revision is unavailable." }
    $stable = New-ReleaseIdentity -GitSha $revision `
        -WorkerVersionId ([string]$stableVersion.version_id) `
        -WindowsRevision $revision -Branch "main" -ValidationState "PASSED" `
        -ArtifactKind $legacyBootstrapStableArtifactKind
    $stable | Add-Member -NotePropertyName worker_git_sha -NotePropertyValue "NOT_RECORDED"
    $stable | Add-Member -NotePropertyName provenance_state `
        -NotePropertyValue "LEGACY_EXACT_WORKER_WINDOWS_PAIR"
    $stable.compatibility_state = "PASSED"
    $acceptedPlacement = @($deployment.versions | Where-Object {
        [string]$_.version_id -eq $bootstrapAcceptedCandidateWorker -and
        [double]$_.percentage -eq 0
    }).Count -eq 1
    $accepted = $null
    if ($acceptedPlacement) {
        $accepted = New-ReleaseIdentity -GitSha $bootstrapAcceptedCandidateRevision `
            -WorkerVersionId $bootstrapAcceptedCandidateWorker `
            -WindowsRevision $bootstrapAcceptedCandidateRevision `
            -Branch "fix/worker-cpu-headroom" -PullRequest "268" `
            -ValidationState "REBASE_REQUIRED" -ArtifactKind $legacyReferenceArtifactKind
        $accepted.compatibility_state = "REBASE_REQUIRED"
        $accepted.validation = [pscustomobject]@{
            key = [string]$accepted.validation_key
            repository = "PASSED"
            windows = "PASSED"
            cloudflare = "PASSED"
            accepted_before_release_control = $true
            acceptance_mode = "LEGACY_ACCEPTED_MANUAL_EVIDENCE"
            source_reference = "PR_268_ACCEPTED_REVIEW_COMMENT"
            source_timestamp = $null
            source_timestamp_status = "NOT_RECORDED_IN_BOOTSTRAP_SOURCE"
            reason = "REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED"
            cpu_evidence = [pscustomobject]@{
                source = "CLOUDFLARE_WORKERS_OBSERVABILITY"
                acceptance_mode = "LEGACY_ACCEPTED_MANUAL_EVIDENCE"
                invocations = 104
                p50_cpu_ms = 2
                max_cpu_ms = 5
                p95_cpu_ms = 4
                p99_cpu_ms = 4
                exceeded_cpu = 0
                exceeded_memory = 0
                responses_1102 = 0
                responses_5xx = 0
                source_reference = "PR_268_ACCEPTED_REVIEW_COMMENT"
            }
        }
    }
    $state = New-ReleaseControlState -Stable $stable -Candidate $accepted
    $knownVersions = @(Get-CloudflareVersions | Sort-Object `
        @{ Expression = { Get-ReleaseVersionCreatedAtValue -Version $_ } }, `
        @{ Expression = { [string]$_.id } })
    $latestKnownVersion = $knownVersions | Select-Object -Last 1
    if ($latestKnownVersion) {
        $state.candidate_discovery.watermark_created_at =
            Get-ReleaseVersionCreatedAt -Version $latestKnownVersion
        $state.candidate_discovery.watermark_version_id = [string]$latestKnownVersion.id
        $state.candidate_discovery.initialized_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "BOOTSTRAPPED" -Release $stable
    if ($accepted) {
        Write-ReleaseHistory -Event "ACCEPTED_CANDIDATE_IMPORTED" -Release $accepted
    }
    return $state
}

function Test-TransientExternalRepositoryFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [string]$Diagnostic = ""
    )
    if ($ExitCode -eq 0 -or $Operation -notin @(
        "FETCH_ORIGIN", "GITHUB_CHECKS_API"
    )) { return $false }
    $text = [string]$Diagnostic
    if ($text -match '(?i)(authentication failed|could not read username|' +
        'permission denied|repository not found|invalid (ref|reference)|' +
        'bad object|ambiguous argument|unknown revision|not a valid object|' +
        'couldn''t find remote ref|remote ref .* not found|access denied|' +
        'http[^0-9]*40[14])') {
        return $false
    }
    if ($text -match '(?i)rate limit') { return $true }
    if ($text -match '(?i)http[^0-9]*403') { return $false }
    return [bool]($text -match '(?i)(timed? out|timeout|could not connect|' +
        'failed to connect|connection (refused|reset|closed)|' +
        'temporary failure in name resolution|could not resolve host|' +
        'name or service not known|socket (error|hang up)|unexpected eof|' +
        '(tls|ssl).*(handshake|connect|connection|socket|terminated)|' +
        'http[^0-9]*(429|5\d\d)|status( code)?[^0-9]*(429|5\d\d)|' +
        'rate limit)')
}

function Invoke-RepositoryRead {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $raw = @(& git @Arguments 2>&1)
    $exitCode = [int]$LASTEXITCODE
    $lines = @($raw | ForEach-Object { [string]$_ })
    $diagnostic = if ($exitCode -ne 0) {
        Protect-PreflightDiagnosticText ($lines -join "`n")
    } else { $null }
    [pscustomobject]@{
        passed = [bool]($exitCode -eq 0)
        exit_code = $exitCode
        output = if ($exitCode -eq 0) { $lines } else { @() }
        diagnostic = $diagnostic
        failure_class = if (Test-TransientExternalRepositoryFailure `
            -Operation $Operation -ExitCode $exitCode -Diagnostic $diagnostic) {
            "TRANSIENT_EXTERNAL"
        } else { "DETERMINISTIC_FAILURE" }
    }
}

function Get-CandidateChangedFiles {
    param([string]$StableRevision, [string]$CandidateRevision)
    $changed = @(& git -C $repositoryRoot diff --name-only $StableRevision $CandidateRevision 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "Candidate boundary classification failed." }
    @($changed | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
}

function Get-CandidateCompatibilityRequirement {
    param([string[]]$ChangedFiles)
    $storage = @($ChangedFiles | Where-Object {
        $_ -like "web/drizzle/*" -or
        $_ -match '(^|/)migrations?/' -or $_ -match '(?i)(^|/)schema\.(sql|sqlite)$'
    })
    if ($storage.Count -gt 0) {
        return [pscustomobject]@{
            state = "COORDINATED_STORAGE_MIGRATION_REQUIRED"; files = $storage
        }
    }
    $platform = @($ChangedFiles | Where-Object {
        $_ -in @(
            "web/wrangler.jsonc", "web/worker-configuration.d.ts",
            "web/runtime-env.d.ts"
        )
    })
    if ($platform.Count -gt 0) {
        return [pscustomobject]@{
            state = "PLATFORM_CONFIG_REVIEW_REQUIRED"; files = $platform
        }
    }
    return [pscustomobject]@{ state = "AUTOMATIC"; files = @() }
}

function Test-AutomaticStorageCompatibility {
    param([string[]]$ChangedFiles)
    return [bool]((Get-CandidateCompatibilityRequirement `
        -ChangedFiles $ChangedFiles).state -eq "AUTOMATIC")
}

function Test-CandidatePlatformResources {
    param(
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][object]$Candidate
    )
    try {
        $stableVersion = Get-CloudflareVersionDetails -VersionId $Stable.worker_version_id
        $candidateVersion = Get-CloudflareVersionDetails -VersionId $Candidate.worker_version_id
        $externalTypes = @("d1", "kv_namespace", "r2_bucket", "vectorize")
        foreach ($binding in @($candidateVersion.resources.bindings | Where-Object {
            [string]$_.type -in $externalTypes
        })) {
            $match = @($stableVersion.resources.bindings | Where-Object {
                [string]$_.type -eq [string]$binding.type -and
                [string]$_.name -eq [string]$binding.name -and
                [string]$_.id -eq [string]$binding.id -and
                [string]$_.database_id -eq [string]$binding.database_id -and
                [string]$_.namespace_id -eq [string]$binding.namespace_id -and
                [string]$_.bucket_name -eq [string]$binding.bucket_name -and
                [string]$_.index_name -eq [string]$binding.index_name
            })
            if ($match.Count -ne 1) { return $false }
        }
        return $true
    } catch { return $false }
}

function Get-MigrationD1Binding {
    param([Parameter(Mandatory = $true)][object]$Version)
    $bindings = @($Version.resources.bindings | Where-Object {
        [string]$_.type -eq "d1" -and [string]$_.name -eq "DB"
    })
    if ($bindings.Count -ne 1 -or
        [string]$bindings[0].database_id -notmatch '^[0-9a-f-]{36}$') {
        throw "MIGRATION_D1_BINDING_IDENTITY_INVALID"
    }
    return $bindings[0]
}

function Get-CoordinatedMigrationFiles {
    param(
        [Parameter(Mandatory = $true)][string[]]$ChangedFiles,
        [Parameter(Mandatory = $true)][string]$CandidateRevision
    )
    $requirement = Get-CandidateCompatibilityRequirement -ChangedFiles $ChangedFiles
    if ([string]$requirement.state -ne "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
        throw "COORDINATED_STORAGE_MIGRATION_NOT_REQUIRED"
    }
    $files = @($requirement.files | Sort-Object -Unique)
    if ($files.Count -eq 0 -or @($files | Where-Object {
        $_ -notmatch '^web/drizzle/[0-9]{4}_[A-Za-z0-9_-]+\.sql$'
    }).Count -gt 0) {
        throw "MIGRATION_FILE_SCOPE_INVALID"
    }
    foreach ($file in $files) {
        $exists = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION" `
            -Arguments @("-C", $repositoryRoot, "cat-file", "-e", "${CandidateRevision}:$file")
        if (-not $exists.passed) {
            throw "MIGRATION_FILE_MISSING:$file"
        }
    }
    return $files
}

function Assert-CoordinatedMigrationCapabilityContract {
    param(
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [Parameter(Mandatory = $true)][string]$CandidateRevision
    )
    $supported = @(
        "web/drizzle/0022_news_projection_generation.sql",
        "web/drizzle/0023_operator_retry_sync_digest.sql",
        "web/drizzle/0024_seed_bounded_audit_news_metrics.sql",
        "web/drizzle/0025_seed_legacy_news_reverse_projection.sql",
        "web/drizzle/0026_reconcile_legacy_news_current_identity.sql",
        "web/drizzle/0027_materialize_news_projection_counts.sql"
    )
    $unknown = @($MigrationFiles | Where-Object { $_ -notin $supported })
    if ($unknown.Count -gt 0) {
        throw "MIGRATION_CAPABILITY_CONTRACT_MISSING:$($unknown -join ',')"
    }
    foreach ($file in $MigrationFiles) {
        $read = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION" `
            -Arguments @("-C", $repositoryRoot, "show", "${CandidateRevision}:$file")
        if (-not $read.passed) { throw "MIGRATION_FILE_MISSING:$file" }
        $sql = @($read.output) -join "`n"
        $isBoundedAuditHandover = $file -eq "web/drizzle/0024_seed_bounded_audit_news_metrics.sql" -and
            $sql -match '(?im)ON\s+CONFLICT\s*\(`id`\)\s+DO\s+UPDATE' -and
            $sql -match '(?im)WHERE\s+`id`\s*=\s*4' -and
            $sql -match '(?im)SELECT\s+9,' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        $isLegacyNewsHandover = $file -eq "web/drizzle/0025_seed_legacy_news_reverse_projection.sql" -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_details`' -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_index`' -and
            $sql -match '(?im)FROM\s+`news_projection_details`' -and
            $sql -match '(?im)FROM\s+`news_projection_index`' -and
            $sql -match '(?im)s\.`projection_state`\s*=\s*''CURRENT''' -and
            $sql -match '(?im)s\.`receipt_digest`\s*=\s*g\.`expected_receipt_digest`' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        $isLegacyNewsReconciliation =
            $file -eq "web/drizzle/0026_reconcile_legacy_news_current_identity.sql" -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_details`' -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_index`' -and
            $sql -match '(?im)UPDATE\s+`news_index`' -and
            $sql -match '(?im)SUPERSEDED_CONTRACT' -and
            $sql -match '(?im)NOT\s+EXISTS\s*\(' -and
            $sql -match '(?im)s\.`projection_state`\s*=\s*''CURRENT''' -and
            $sql -match '(?im)s\.`receipt_digest`\s*=\s*g\.`expected_receipt_digest`' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        $isNewsFreePlanMaterialization =
            $file -eq "web/drizzle/0027_materialize_news_projection_counts.sql" -and
            $sql -match '(?im)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`news_projection_receipts_v2`' -and
            $sql -match '(?im)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`news_projection_counts`' -and
            $sql -match '(?im)CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+`news_projection_index_review_page_idx`' -and
            $sql -match '(?im)CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+`news_projection_index_review_category_page_idx`' -and
            $sql -match '(?im)`candidate_expiries`\s+text\s+NOT\s+NULL' -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_projection_counts`' -and
            $sql -match '(?im)JOIN\s+`news_projection_state`' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        if (($sql -match '(?im)\b(DROP|DELETE|UPDATE|REPLACE|TRUNCATE|VACUUM)\b') -and
            -not $isBoundedAuditHandover -and -not $isLegacyNewsHandover -and
            -not $isLegacyNewsReconciliation -and -not $isNewsFreePlanMaterialization) {
            throw "MIGRATION_REVERSE_INCOMPATIBLE:$file"
        }
    }
}

function Invoke-CoordinatedMigrationD1Query {
    param([Parameter(Mandatory = $true)][string]$Sql)
    # Windows cmd.exe cannot preserve embedded newlines in an argument passed
    # through npx.cmd.  Keep the SQL as one argument so Wrangler receives the
    # complete statement instead of an incomplete prefix.
    $command = ($Sql -replace "`r`n|`n|`r", " ").Trim()
    $blocks = @(Invoke-WranglerJson -Arguments @(
        "d1", "execute", "DB", "--remote", "--command", $command
    ))
    if ($blocks.Count -eq 0 -or @($blocks | Where-Object { -not [bool]$_.success }).Count -gt 0) {
        throw "MIGRATION_D1_QUERY_FAILED"
    }
    foreach ($block in $blocks) {
        foreach ($row in @($block.results)) { Write-Output $row }
    }
}

function Get-CoordinatedMigrationEndpointEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable
    )
    $candidateStatus = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$([string]$Candidate.browser_url)/api/status" -TimeoutSec 45
    $candidateHealth = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$([string]$Candidate.browser_url)/api/news-index?health_check=1" `
        -TimeoutSec 45
    $stableStatus = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$workerUrl/api/status" -TimeoutSec 45
    $stableNewsHealth = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$workerUrl/api/news-index?health_check=1" -TimeoutSec 45
    $candidatePayload = $candidateStatus.Content | ConvertFrom-Json
    $healthPayload = $candidateHealth.Content | ConvertFrom-Json
    $stablePayload = $stableStatus.Content | ConvertFrom-Json
    $stableNewsPayload = $stableNewsHealth.Content | ConvertFrom-Json
    $observedVersion = [string]$candidateStatus.Headers["X-Aurum-Worker-Version"]
    $observedGit = [string]$candidateStatus.Headers["X-Aurum-Git-SHA"]
    if ([int]$candidateStatus.StatusCode -ne 200 -or
        $observedVersion -ne [string]$Candidate.worker_version_id -or
        $observedGit -ne [string]$Candidate.git_sha) {
        throw "MIGRATION_CANDIDATE_READ_IDENTITY_FAILED"
    }
    if ([int]$stableStatus.StatusCode -ne 200 -or
        $null -eq $stablePayload.counts.decision_events -or
        [long]$stablePayload.counts.decision_events -le 0) {
        throw "MIGRATION_LEGACY_STABLE_READ_FAILED"
    }
    if ([int]$stableNewsHealth.StatusCode -ne 200 -or
        [string]$stableNewsPayload.status -ne "OK" -or
        [int]$stableNewsPayload.violation_count -ne 0) {
        throw "MIGRATION_LEGACY_NEWS_READ_FAILED"
    }
    if ([int]$candidateHealth.StatusCode -ne 200 -or
        [string]$healthPayload.projection_state -ne "CURRENT" -or
        -not [bool]$healthPayload.verified_complete -or
        [int]$healthPayload.index_count -ne [int]$healthPayload.detail_count -or
        [int]$healthPayload.missing_detail_count -ne 0 -or
        [int]$healthPayload.invariant_violation_count -ne 0 -or
        [string]$healthPayload.receipt_digest -ne
            [string]$healthPayload.source_receipt_digest) {
        throw "MIGRATION_NEWS_CURRENT_INVALID"
    }
    return [ordered]@{
        stable_status = 200
        stable_decision_count_positive = $true
        stable_news_status = [string]$stableNewsPayload.status
        stable_news_violation_count = [int]$stableNewsPayload.violation_count
        candidate_status = 200
        candidate_worker_version = $observedVersion
        candidate_git_sha = $observedGit
        news_generation_id = [string]$healthPayload.active_generation_id
        news_snapshot_id = [string]$healthPayload.snapshot_id
        news_source_digest = [string]$healthPayload.source_digest
        news_receipt_digest = [string]$healthPayload.receipt_digest
        news_index_count = [int]$healthPayload.index_count
        news_detail_count = [int]$healthPayload.detail_count
    }
}

function Get-CoordinatedMigrationLiveEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles
    )
    Assert-CoordinatedMigrationCapabilityContract -MigrationFiles $MigrationFiles `
        -CandidateRevision ([string]$Candidate.git_sha)
    $candidateVersion = Get-CloudflareVersionDetails `
        -VersionId ([string]$Candidate.worker_version_id)
    $stableVersion = Get-CloudflareVersionDetails `
        -VersionId ([string]$Stable.worker_version_id)
    $candidateBinding = Get-MigrationD1Binding -Version $candidateVersion
    $stableBinding = Get-MigrationD1Binding -Version $stableVersion
    if ([string]$candidateBinding.database_id -ne [string]$stableBinding.database_id) {
        throw "MIGRATION_REVERSE_DATABASE_IDENTITY_MISMATCH"
    }
    $database = Invoke-WranglerJson -Arguments @("d1", "info", "DB")
    if ([string]$database.uuid -ne [string]$candidateBinding.database_id) {
        throw "MIGRATION_DATABASE_IDENTITY_MISMATCH"
    }
    $ledger = @(Invoke-CoordinatedMigrationD1Query -Sql `
        "SELECT name,applied_at FROM d1_migrations ORDER BY id")
    $ledgerNames = @($ledger | ForEach-Object { [string]$_.name })
    $migrationTree = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION_TREE" `
        -Arguments @("-C", $repositoryRoot, "ls-tree", "-r", "--name-only",
            ([string]$Candidate.git_sha), "--", "web/drizzle")
    if (-not $migrationTree.passed) { throw "MIGRATION_FILE_SCOPE_INVALID" }
    $candidateMigrationNames = @($migrationTree.output | Where-Object {
        [string]$_ -match '^web/drizzle/[^/]+\.sql$'
    } | ForEach-Object { Split-Path ([string]$_) -Leaf } | Sort-Object -Unique)
    $pending = @($candidateMigrationNames | Where-Object { $_ -notin $ledgerNames })
    if ($pending.Count -gt 0) {
        throw "MIGRATION_LEDGER_PENDING:$($pending -join ',')"
    }
    $requiredNames = @($MigrationFiles | ForEach-Object { Split-Path $_ -Leaf })
    $missingRequired = @($requiredNames | Where-Object { $_ -notin $ledgerNames })
    if ($missingRequired.Count -gt 0) {
        throw "MIGRATION_LEDGER_REQUIRED_MISSING:$($missingRequired -join ',')"
    }
    $capabilitySql = @"
SELECT
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('news_projection_generations','news_projection_index','news_projection_details',
   'news_projection_batches','news_projection_receipts_v2','news_projection_state',
   'news_projection_counts')) AS projection_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='index' AND name IN
  ('news_projection_generations_state_idx','news_projection_index_ordinal_idx',
   'news_projection_index_page_idx','news_projection_index_category_idx',
   'news_projection_index_review_page_idx',
   'news_projection_index_review_category_page_idx')) AS projection_indexes,
 (SELECT count(*) FROM pragma_table_info('news_projection_counts') WHERE name IN
  ('generation_id','review_state','category','item_count','parsed_count','candidate_expiries')) AS projection_count_columns,
 (SELECT count(*) FROM pragma_table_info('news_projection_receipts_v2') WHERE name IN
  ('generation_id','batch_kind','batch_offset','item_count','payload_hash',
   'receipt_digest','identity_digest','updated_at')) AS projection_receipt_columns,
 (SELECT count(*) FROM pragma_table_info('operator_retry_sync_state') WHERE name IN
  ('id','payload_digest','item_count','synced_at')) AS retry_columns,
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('dashboard_snapshots','news_index','news_details','news_evidence_records')) AS legacy_tables,
  coalesce((SELECT json_array_length(json_extract(payload,'$.recent_decisions'))
    FROM dashboard_snapshots WHERE id=4 AND json_valid(payload)),0) AS legacy_decisions,
  (SELECT count(*) FROM news_projection_index pi
    WHERE pi.generation_id=s.active_generation_id
      AND EXISTS(SELECT 1 FROM news_index li WHERE li.detail_key=pi.detail_key))
    AS legacy_current_index_count,
  (SELECT count(*) FROM news_projection_index pi
    WHERE pi.generation_id=s.active_generation_id
      AND EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=pi.detail_key))
    AS legacy_current_detail_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND NOT EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=li.detail_key))
    AS legacy_missing_detail_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND NOT (
        (json_extract(li.payload,'$.annotation_status')='NOT_REQUIRED'
          AND json_extract(li.payload,'$.model_visibility')='MODEL_INELIGIBLE'
          AND json_extract(li.payload,'$.parsed_at') IS NULL)
        OR (json_extract(li.payload,'$.annotation_status')='QUEUED'
          AND json_extract(li.payload,'$.model_visibility')='NOT_YET_PARSED'
          AND json_extract(li.payload,'$.parsed_at') IS NULL)
        OR (json_extract(li.payload,'$.annotation_status')='READY'
          AND json_extract(li.payload,'$.model_visibility')<>'NOT_YET_PARSED'
          AND json_extract(li.payload,'$.parsed_at') IS NOT NULL)
        OR (json_extract(li.payload,'$.annotation_status') IN
          ('REPAIRING_DISPLAY','BACKING_OFF','DEAD_LETTER','WAITING_CONTENT','CONTENT_UNAVAILABLE')
          AND json_extract(li.payload,'$.model_visibility')=
              json_extract(li.payload,'$.annotation_status')
          AND json_extract(li.payload,'$.parsed_at') IS NULL)))
    AS legacy_review_violation_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND li.parsed <> CASE
        WHEN json_extract(li.payload,'$.parsed_at') IS NOT NULL THEN 1 ELSE 0 END)
    AS legacy_parsed_flag_mismatch_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND li.model_candidate <> CASE
        WHEN json_extract(li.payload,'$.model_visibility')='MODEL_VISIBLE' THEN 1 ELSE 0 END)
    AS legacy_candidate_flag_mismatch_count,
  (SELECT count(*) FROM (
    SELECT cluster_id FROM news_index li
     WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
     GROUP BY cluster_id HAVING count(*) > 1))
    AS legacy_duplicate_cluster_count,
  (SELECT count(*) FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      AND NOT EXISTS(
        SELECT 1 FROM news_projection_index pi
         WHERE pi.generation_id=s.active_generation_id
           AND pi.detail_key=li.detail_key))
    AS legacy_extra_current_index_count,
  coalesce((SELECT item_count FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_all_count,
  coalesce((SELECT sum(item_count) FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state<>'ALL' AND c.category=''),-1) AS summary_review_count,
  coalesce((SELECT sum(item_count) FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id AND c.category<>''),-1) AS summary_category_count,
  coalesce((SELECT parsed_count FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_parsed_count,
  coalesce((SELECT CASE WHEN candidate_expiries='' THEN 0 ELSE
      1 + length(candidate_expiries) - length(replace(candidate_expiries,char(10),'')) END
    FROM news_projection_counts c WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_candidate_count,
  (SELECT coalesce(sum(parsed),0) FROM news_projection_index i
    WHERE i.generation_id=s.active_generation_id) AS current_parsed_count,
  (SELECT count(*) FROM news_projection_index i
    WHERE i.generation_id=s.active_generation_id AND i.model_candidate=1) AS current_candidate_count,
  (SELECT count(*) FROM news_projection_index i
    WHERE i.generation_id=s.active_generation_id AND i.model_candidate=1
      AND (i.impact_expires_at IS NULL OR length(i.impact_expires_at)<>32
        OR substr(i.impact_expires_at,27)<>'+00:00')) AS invalid_candidate_expiry_count,
  s.projection_state,s.active_generation_id,s.snapshot_id,s.source_digest,s.receipt_digest,
 s.index_count,s.detail_count,s.missing_detail_count,s.invariant_violation_count,
 g.state AS generation_state,g.contract_version AS generation_contract_version,
 g.watermark AS generation_watermark,g.activated_at AS generation_activated_at,
 g.expected_receipt_digest,g.staged_index_count,g.staged_detail_count
FROM news_projection_state s JOIN news_projection_generations g
 ON g.generation_id=s.active_generation_id WHERE s.id=1
"@
    $capabilities = @(Invoke-CoordinatedMigrationD1Query -Sql $capabilitySql)
    if ($capabilities.Count -ne 1 -or
        [int]$capabilities[0].projection_tables -ne 7 -or
        [int]$capabilities[0].projection_indexes -ne 6 -or
        [int]$capabilities[0].projection_count_columns -ne 6 -or
        [int]$capabilities[0].projection_receipt_columns -ne 8 -or
        [int]$capabilities[0].retry_columns -ne 4) {
        throw "MIGRATION_SCHEMA_CAPABILITY_MISSING"
    }
    $state = $capabilities[0]
    if ([int]$state.legacy_tables -ne 4 -or [int]$state.legacy_decisions -le 0) {
        throw "MIGRATION_LEGACY_COMPATIBILITY_FAILED"
    }
    if ([string]$state.projection_state -ne "CURRENT" -or
        [string]$state.generation_state -ne "CURRENT" -or
        [int]$state.index_count -ne [int]$state.detail_count -or
        [int]$state.index_count -ne [int]$state.staged_index_count -or
        [int]$state.detail_count -ne [int]$state.staged_detail_count -or
        [int]$state.missing_detail_count -ne 0 -or
        [int]$state.invariant_violation_count -ne 0 -or
        [string]$state.receipt_digest -ne [string]$state.expected_receipt_digest) {
        throw "MIGRATION_NEWS_CURRENT_INVALID"
    }
    if ([int]$state.legacy_current_index_count -ne [int]$state.index_count -or
        [int]$state.legacy_current_detail_count -ne [int]$state.detail_count -or
        [int]$state.legacy_missing_detail_count -ne 0 -or
        [int]$state.legacy_review_violation_count -ne 0 -or
        [int]$state.legacy_parsed_flag_mismatch_count -ne 0 -or
        [int]$state.legacy_candidate_flag_mismatch_count -ne 0 -or
        [int]$state.legacy_duplicate_cluster_count -ne 0 -or
        [int]$state.legacy_extra_current_index_count -ne 0) {
        throw "MIGRATION_LEGACY_NEWS_COMPATIBILITY_FAILED"
    }
    if ([int]$state.summary_all_count -ne [int]$state.index_count -or
        [int]$state.summary_review_count -ne [int]$state.index_count -or
        [int]$state.summary_category_count -ne [int]$state.index_count -or
        [int]$state.summary_parsed_count -ne [int]$state.current_parsed_count -or
        [int]$state.summary_candidate_count -ne [int]$state.current_candidate_count -or
        [int]$state.invalid_candidate_expiry_count -ne 0) {
        throw "MIGRATION_NEWS_SUMMARY_INVALID"
    }
    $endpoints = Get-CoordinatedMigrationEndpointEvidence `
        -Candidate $Candidate -Stable $Stable
    if ([string]$endpoints.news_generation_id -ne [string]$state.active_generation_id -or
        [string]$endpoints.news_snapshot_id -ne [string]$state.snapshot_id -or
        [string]$endpoints.news_source_digest -ne [string]$state.source_digest -or
        [string]$endpoints.news_receipt_digest -ne [string]$state.receipt_digest -or
        [int]$endpoints.news_index_count -ne [int]$state.index_count -or
        [int]$endpoints.news_detail_count -ne [int]$state.detail_count) {
        throw "MIGRATION_NEWS_CURRENT_IDENTITY_MISMATCH"
    }
    $migrationHashes = @($MigrationFiles | ForEach-Object {
        $blob = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION_BLOB" `
            -Arguments @("-C", $repositoryRoot, "rev-parse",
                "$([string]$Candidate.git_sha):$_")
        $blobId = if ($blob.passed) { ([string]@($blob.output)[0]).Trim() } else { "" }
        if ($blobId -notmatch '^[0-9a-f]{40,64}$') {
            throw "MIGRATION_FILE_HASH_INVALID:$_"
        }
        [ordered]@{
            path = $_
            git_blob_oid = $blobId
        }
    })
    return [ordered]@{
        validation_key = [string]$Candidate.validation_key
        candidate_git_sha = [string]$Candidate.git_sha
        candidate_worker_version = [string]$Candidate.worker_version_id
        stable_git_sha = [string]$Stable.git_sha
        stable_worker_version = [string]$Stable.worker_version_id
        database_id = [string]$database.uuid
        database_name = [string]$database.name
        migration_files = $migrationHashes
        applied_migrations = @($ledgerNames)
        pending_migrations = @()
        projection_tables = [int]$state.projection_tables
        projection_indexes = [int]$state.projection_indexes
        projection_count_columns = [int]$state.projection_count_columns
        projection_receipt_columns = [int]$state.projection_receipt_columns
        operator_retry_columns = [int]$state.retry_columns
        legacy_tables = [int]$state.legacy_tables
        legacy_decisions = [int]$state.legacy_decisions
        legacy_news_index_count = [int]$state.legacy_current_index_count
        legacy_news_detail_count = [int]$state.legacy_current_detail_count
        legacy_news_missing_detail_count = [int]$state.legacy_missing_detail_count
        legacy_news_invariant_violation_count = [int]$state.legacy_review_violation_count
        legacy_news_parsed_flag_mismatch_count = [int]$state.legacy_parsed_flag_mismatch_count
        legacy_news_candidate_flag_mismatch_count = [int]$state.legacy_candidate_flag_mismatch_count
        legacy_news_duplicate_cluster_count = [int]$state.legacy_duplicate_cluster_count
        legacy_news_extra_current_index_count = [int]$state.legacy_extra_current_index_count
        stable_news_status = [string]$endpoints.stable_news_status
        news_generation_id = [string]$state.active_generation_id
        news_contract_version = [string]$state.generation_contract_version
        news_watermark = [string]$state.generation_watermark
        news_activated_at = [string]$state.generation_activated_at
        news_snapshot_id = [string]$state.snapshot_id
        news_source_digest = [string]$state.source_digest
        news_receipt_digest = [string]$state.receipt_digest
        news_index_count = [int]$state.index_count
        news_detail_count = [int]$state.detail_count
        stable_read = [int]$endpoints.stable_status
        candidate_read = [int]$endpoints.candidate_status
        reverse_safe = $true
    }
}

function Get-CoordinatedMigrationReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 12
    Get-Sha256BytesHex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

function New-CoordinatedMigrationReceipt {
    param([Parameter(Mandatory = $true)][object]$Evidence)
    $checkedAt = [DateTimeOffset]::UtcNow
    $core = [ordered]@{
        schema_version = "coordinated-storage-migration-receipt-v1"
        checked_at = $checkedAt.ToString("o")
        expires_at = $checkedAt.Add($coordinatedMigrationReceiptMaxAge).ToString("o")
        evidence = $Evidence
    }
    [pscustomobject]@{
        schema_version = $core.schema_version
        checked_at = $core.checked_at
        expires_at = $core.expires_at
        evidence = $core.evidence
        receipt_digest = Get-CoordinatedMigrationReceiptDigest -Core $core
    }
}

function Write-CoordinatedMigrationReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $directory = Split-Path -Parent $coordinatedMigrationReceiptPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$coordinatedMigrationReceiptPath.tmp"
    $Receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $coordinatedMigrationReceiptPath -Force
}

function Assert-CoordinatedMigrationReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles
    )
    if (-not (Test-Path -LiteralPath $coordinatedMigrationReceiptPath)) {
        throw "MIGRATION_RECEIPT_MISSING"
    }
    $receipt = Get-Content -LiteralPath $coordinatedMigrationReceiptPath -Raw |
        ConvertFrom-Json
    $core = [ordered]@{
        schema_version = [string]$receipt.schema_version
        checked_at = [string]$receipt.checked_at
        expires_at = [string]$receipt.expires_at
        evidence = $receipt.evidence
    }
    if ([string]$receipt.schema_version -ne "coordinated-storage-migration-receipt-v1" -or
        [string]$receipt.receipt_digest -ne
            (Get-CoordinatedMigrationReceiptDigest -Core $core)) {
        throw "MIGRATION_RECEIPT_TAMPERED"
    }
    $expires = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$receipt.expires_at, [ref]$expires) -or
        $expires -le [DateTimeOffset]::UtcNow) {
        throw "MIGRATION_RECEIPT_STALE"
    }
    if ([string]$receipt.evidence.validation_key -ne [string]$Candidate.validation_key -or
        [string]$receipt.evidence.candidate_git_sha -ne [string]$Candidate.git_sha -or
        [string]$receipt.evidence.candidate_worker_version -ne
            [string]$Candidate.worker_version_id) {
        throw "MIGRATION_RECEIPT_CANDIDATE_MISMATCH"
    }
    if ([string]$receipt.evidence.stable_git_sha -ne [string]$Stable.git_sha -or
        [string]$receipt.evidence.stable_worker_version -ne
            [string]$Stable.worker_version_id) {
        throw "MIGRATION_RECEIPT_STABLE_MISMATCH"
    }
    $live = Get-CoordinatedMigrationLiveEvidence -Candidate $Candidate `
        -Stable $Stable -MigrationFiles $MigrationFiles
    $immutableFields = @(
        "validation_key", "candidate_git_sha", "candidate_worker_version",
        "stable_git_sha", "stable_worker_version", "database_id", "database_name",
        "migration_files", "applied_migrations", "pending_migrations",
        "projection_tables", "projection_indexes", "projection_count_columns",
        "projection_receipt_columns", "operator_retry_columns",
        "legacy_tables", "stable_read", "candidate_read", "reverse_safe"
    )
    foreach ($field in $immutableFields) {
        $recordedValue = $receipt.evidence.$field | ConvertTo-Json -Compress -Depth 12
        $liveValue = $live.$field | ConvertTo-Json -Compress -Depth 12
        if ($recordedValue -cne $liveValue) {
            throw "MIGRATION_RECEIPT_LIVE_EVIDENCE_MISMATCH:$field"
        }
    }
    $recordedActivation = ConvertTo-RequiredReleaseTime `
        $receipt.evidence.news_activated_at
    $liveActivation = ConvertTo-RequiredReleaseTime $live.news_activated_at
    if ($liveActivation -lt $recordedActivation) {
        throw "MIGRATION_RECEIPT_GENERATION_REGRESSION"
    }
    if ([string]$live.news_generation_id -eq
            [string]$receipt.evidence.news_generation_id) {
        foreach ($field in @(
            "news_contract_version", "news_watermark", "news_activated_at",
            "news_snapshot_id", "news_source_digest", "news_receipt_digest",
            "news_index_count", "news_detail_count"
        )) {
            if ([string]$live.$field -cne [string]$receipt.evidence.$field) {
                throw "MIGRATION_RECEIPT_GENERATION_MUTATED:$field"
            }
        }
    }
    return $receipt
}

function Test-WatchdogRecoverySuppressed {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceKey,
        [Parameter(Mandatory = $true)][string]$ServiceState,
        [object]$ReleaseState
    )
    if ($ServiceKey -ne "sync" -or $ServiceState -ne "STOPPED") { return $false }
    return [bool]($ReleaseState -and $ReleaseState.transaction -and (
        ([string]$ReleaseState.transaction.type -eq "PROMOTE" -and
         [string]$ReleaseState.transaction.phase -in @("PRECHECK", "CUTOVER")) -or
        ([string]$ReleaseState.transaction.type -eq "REVERSE" -and
         [string]$ReleaseState.transaction.phase -eq "REVERSING")
    ))
}

function Verify-CandidateCoordinatedMigration {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate -or -not $state.stable) {
        throw "MIGRATION_CANDIDATE_UNAVAILABLE"
    }
    $candidate = $state.candidate
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        [string]$candidate.validation.reason -notin @(
            "COORDINATED_STORAGE_MIGRATION_REQUIRED",
            "COORDINATED_STORAGE_MIGRATION_EVIDENCE_INVALID"
        ) -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key) {
        throw "MIGRATION_EXACT_REVIEW_REQUIRED"
    }
    $approvalGate = Get-CandidateCompatibilityApprovalGate -Candidate $candidate
    if ([string]$approvalGate.state -ne "PASSED") {
        throw "MIGRATION_APPROVAL_REJECTED:$([string]$approvalGate.reason)"
    }
    $changed = @(Get-CandidateChangedFiles -StableRevision ([string]$state.stable.git_sha) `
        -CandidateRevision ([string]$candidate.git_sha))
    $files = @(Get-CoordinatedMigrationFiles -ChangedFiles $changed `
        -CandidateRevision ([string]$candidate.git_sha))
    $evidence = Get-CoordinatedMigrationLiveEvidence -Candidate $candidate `
        -Stable $state.stable -MigrationFiles $files
    $receipt = New-CoordinatedMigrationReceipt -Evidence $evidence
    Write-CoordinatedMigrationReceipt -Receipt $receipt
    $verified = Assert-CoordinatedMigrationReceipt -Candidate $candidate `
        -Stable $state.stable -MigrationFiles $files
    $candidate.compatibility_state = "COORDINATED_STORAGE_MIGRATION_PASSED"
    $candidate.validation_state = "NEW"
    $candidate.validation = [pscustomobject]@{
        key = [string]$candidate.validation_key
        repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
        reason = "COORDINATED_STORAGE_MIGRATION_PASSED"
        migration_receipt_digest = [string]$verified.receipt_digest
        migration_database_id = [string]$verified.evidence.database_id
        migration_files = @($verified.evidence.migration_files)
        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $candidate | Add-Member -NotePropertyName migration_acceptance `
        -NotePropertyValue ([pscustomobject]@{
            validation_key = [string]$candidate.validation_key
            receipt_digest = [string]$verified.receipt_digest
            database_id = [string]$verified.evidence.database_id
            checked_at = [string]$verified.checked_at
            expires_at = [string]$verified.expires_at
        }) -Force
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "COORDINATED_STORAGE_MIGRATION_PASSED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            receipt_digest = [string]$verified.receipt_digest
            database_id = [string]$verified.evidence.database_id
            migration_files = @($files)
        }
    return $candidate
}

function Get-RequiredGitHubChecksResult {
    param([Parameter(Mandatory = $true)][string]$Revision)
    try {
        $raw = @(& gh api --method GET `
            "repos/yiyousiow000814/XAUUSD-Forecaster/commits/$Revision/check-runs?filter=latest&per_page=100" `
            2>&1)
        $exitCode = [int]$LASTEXITCODE
        $json = @($raw | ForEach-Object { [string]$_ }) -join "`n"
        if ($exitCode -ne 0) {
            $diagnostic = Protect-PreflightDiagnosticText $json
            if (Test-TransientExternalRepositoryFailure -Operation "GITHUB_CHECKS_API" `
                -ExitCode $exitCode -Diagnostic $diagnostic) {
                return [pscustomobject]@{
                    state = "REPOSITORY_PENDING"
                    reason = "GITHUB_TEMPORARILY_UNAVAILABLE"
                    exit_code = $exitCode
                    diagnostic = $diagnostic
                }
            }
            return [pscustomobject]@{
                state = "FAILED"
                reason = "GITHUB_CHECKS_ACCESS_FAILED"
                exit_code = $exitCode
                diagnostic = $diagnostic
            }
        }
        $runs = @(($json | ConvertFrom-Json -ErrorAction Stop).check_runs)
        foreach ($name in $requiredGitHubChecks) {
            $matching = @($runs | Where-Object {
                [string]$_.name -eq $name -and
                [string]$_.head_sha -eq $Revision
            })
            if ($matching.Count -eq 0) {
                return [pscustomobject]@{
                    state = "PENDING"; reason = "REQUIRED_GITHUB_CHECKS_PENDING"
                }
            }
            $latest = $matching | Sort-Object `
                @{ Expression = { [string]$_.started_at }; Descending = $true }, `
                @{ Expression = { [long]$_.id }; Descending = $true } | `
                Select-Object -First 1
            if ([string]$latest.status -ne "completed") {
                return [pscustomobject]@{
                    state = "PENDING"; reason = "REQUIRED_GITHUB_CHECKS_PENDING"
                }
            }
            if ([string]$latest.conclusion -ne "success") {
                return [pscustomobject]@{
                    state = "CHECKS_BLOCKED"; reason = "REQUIRED_GITHUB_CHECKS_BLOCKED"
                }
            }
        }
        return [pscustomobject]@{ state = "PASSED"; reason = $null }
    } catch {
        return [pscustomobject]@{
            state = "FAILED"
            reason = "GITHUB_CHECKS_RESPONSE_INVALID"
            exit_code = 0
            diagnostic = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Test-RequiredGitHubChecks {
    param([Parameter(Mandatory = $true)][string]$Revision)
    $script:lastGitHubChecksResult = Get-RequiredGitHubChecksResult -Revision $Revision
    return [string]$script:lastGitHubChecksResult.state
}

function Get-ProductionCandidateProvenanceResult {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$Candidate.branch -ne "main" -or
        [string]$Candidate.git_sha -ne [string]$Candidate.windows_revision) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED"
        }
    }
    $fetch = Invoke-RepositoryRead -Operation "FETCH_ORIGIN" `
        -Arguments @("-C", $repositoryRoot, "fetch", "origin", "--quiet")
    if (-not $fetch.passed) {
        return [pscustomobject]@{
            state = if ($fetch.failure_class -eq "TRANSIENT_EXTERNAL") {
                "REPOSITORY_PENDING"
            } else { "FAILED" }
            reason = if ($fetch.failure_class -eq "TRANSIENT_EXTERNAL") {
                "REPOSITORY_TRANSPORT_UNAVAILABLE"
            } else { "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED" }
            operation = "FETCH_ORIGIN"
            exit_code = [int]$fetch.exit_code
            diagnostic = $fetch.diagnostic
        }
    }
    & git -C $repositoryRoot cat-file -e "$([string]$Candidate.git_sha)^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_COMMIT_REQUIRED"
        }
    }
    $originMain = ([string](@(& git -C $repositoryRoot rev-parse origin/main 2>$null)[0])).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $originMain -notmatch '^[0-9a-f]{40}$' -or
        $originMain -ne [string]$Candidate.git_sha) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_EXACT_MAIN_REQUIRED"
        }
    }
    return [pscustomobject]@{ state = "PASSED"; reason = $null }
}

function Test-ProductionCandidateProvenance {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $script:lastRepositoryValidationResult =
        Get-ProductionCandidateProvenanceResult -Candidate $Candidate
    return [bool]($script:lastRepositoryValidationResult.state -eq "PASSED")
}

function Get-CandidateCompatibilityApprovalGate {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $provenance = Get-ProductionCandidateProvenanceResult -Candidate $Candidate
    if ([string]$provenance.state -ne "PASSED") {
        return [pscustomobject]@{
            state = if ([string]$provenance.state -eq "REPOSITORY_PENDING") {
                "RETRYABLE"
            } else { "FAILED" }
            reason = [string]$provenance.reason
            diagnostic = [string]$provenance.diagnostic
        }
    }
    $checks = Get-RequiredGitHubChecksResult -Revision ([string]$Candidate.git_sha)
    return [pscustomobject]@{
        state = if ([string]$checks.state -eq "PASSED") {
            "PASSED"
        } elseif ([string]$checks.state -in @("REPOSITORY_PENDING", "PENDING")) {
            "RETRYABLE"
        } else { "FAILED" }
        reason = [string]$checks.reason
        diagnostic = [string]$checks.diagnostic
    }
}

function Test-SingleProductionOwner {
    foreach ($service in @($services | Where-Object { $_.Key -in $reloadableServiceKeys })) {
        if (@(Get-ForecasterProcesses $service).Count -ne 1) { return $false }
    }
    return $true
}

function Invoke-WorkersObservabilityQuery {
    param(
        [Parameter(Mandatory = $true)][object[]]$Filters,
        [Parameter(Mandatory = $true)][object[]]$Calculations,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To
    )
    $secret = Get-ReleaseSecret -Name "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN"
    $script:lastWorkersObservabilityCredentialSource = [string]$secret.source
    if (-not $secret.available) {
        $script:lastWorkersObservabilityDiagnostic = [string]$secret.diagnostic
        return $null
    }
    $token = [string]$secret.value
    $body = [pscustomobject]@{
        queryId = "aurum-release-candidate-validation"
        timeframe = [pscustomobject]@{
            from = $From.ToUnixTimeMilliseconds()
            to = $To.ToUnixTimeMilliseconds()
        }
        view = "calculations"
        chart = $false
        ignoreSeries = $true
        parameters = [pscustomobject]@{
            datasets = @()
            filterCombination = "and"
            filters = $Filters
            calculations = $Calculations
            limit = 10
        }
    }
    $uri = "https://api.cloudflare.com/client/v4/accounts/$cloudflareAccountId/workers/observability/telemetry/query"
    try {
        $response = Invoke-RestMethod -Method Post -Uri $uri `
            -Headers @{ Authorization = "Bearer $token" } `
            -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 10) `
            -TimeoutSec 30
        if (-not $response.success) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_API_REJECTED"
            return $null
        }
        $script:lastWorkersObservabilityDiagnostic = $null
        return $response.result
    } catch {
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
        $script:lastWorkersObservabilityDiagnostic = if ($statusCode -in @(401, 403)) {
            "OBSERVABILITY_CREDENTIAL_REJECTED"
        } elseif ($statusCode -eq 429) {
            "OBSERVABILITY_RATE_LIMITED"
        } elseif ($statusCode -ge 500 -and $statusCode -le 599) {
            "OBSERVABILITY_TRANSIENT_API_FAILURE"
        } else { "OBSERVABILITY_QUERY_FAILED" }
        return $null
    } finally {
        $token = $null
        $secret = $null
    }
}

function Get-CalculationAggregate {
    param([object]$QueryResult, [string]$Alias)
    $calculation = @($QueryResult.calculations | Where-Object {
        [string]$_.alias -eq $Alias
    }) | Select-Object -First 1
    if (-not $calculation -or @($calculation.aggregates).Count -eq 0) { return $null }
    return $calculation.aggregates[0].value
}

function Get-WorkerCpuGateState {
    param([Parameter(Mandatory = $true)][object]$Evidence, [int]$ExpectedInvocations)
    if ($Evidence.invocations -ne $ExpectedInvocations -or
        $Evidence.exceeded_cpu -gt 0 -or $Evidence.responses_1102 -gt 0 -or
        $Evidence.responses_5xx -gt 0 -or
        $Evidence.p99_cpu_ms -gt $workerCpuPassMaxMs -or
        $Evidence.max_cpu_ms -gt $workerCpuPassMaxMs) { return "FAILED" }
    if ($Evidence.p95_cpu_ms -le $workerCpuPassP95Ms -and
        $Evidence.p99_cpu_ms -le $workerCpuPassP99Ms -and
        $Evidence.max_cpu_ms -lt $workerCpuPassMaxMs) { return "PASSED" }
    return "REVIEW_REQUIRED"
}

function Get-WorkerPlatformFailureReason {
    param([Parameter(Mandatory = $true)][object]$Evidence)
    if ([int]$Evidence.invocations -ne [int]$Evidence.expected_invocations) {
        return "WORKER_INVOCATION_COUNT_MISMATCH"
    }
    if ([int]$Evidence.responses_5xx -gt 0) { return "WORKER_5XX_OBSERVED" }
    if ([int]$Evidence.exceeded_cpu -gt 0 -or [int]$Evidence.responses_1102 -gt 0) {
        return "WORKER_PLATFORM_LIMIT_EXCEEDED"
    }
    if ([double]$Evidence.p99_cpu_ms -gt $workerCpuPassMaxMs -or
        [double]$Evidence.max_cpu_ms -gt $workerCpuPassMaxMs) {
        return "WORKER_CPU_HEADROOM_FAILED"
    }
    return "WORKER_PLATFORM_EVIDENCE_FAILED"
}

function Get-CandidateObservabilityFilters {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [string]$RoutePath = "",
        [string]$RouteMethod = "",
        [string]$ValidationRun = ""
    )
    $filters = @(
        [pscustomobject]@{ key='$metadata.service'; operation='eq'; type='string'; value=$workerName },
        [pscustomobject]@{ key='$workers.scriptVersion.id'; operation='eq'; type='string'; value=[string]$Candidate.worker_version_id },
        [pscustomobject]@{ key='$metadata.type'; operation='eq'; type='string'; value='cf-worker-event' }
    )
    if ($ValidationRun) {
        $filters += @(
            [pscustomobject]@{
                key='$workers.event.request.headers.x-aurum-validation-run'
                operation='eq'; type='string'; value=$ValidationRun
            },
            [pscustomobject]@{
                key='$workers.event.request.headers.x-aurum-validation-phase'
                operation='eq'; type='string'; value='acceptance'
            }
        )
    }
    if ($RoutePath) {
        $filters += [pscustomobject]@{
            key='$workers.event.path'; operation='eq'; type='string'; value=$RoutePath
        }
    }
    if ($RouteMethod) {
        $filters += [pscustomobject]@{
            key='$workers.event.request.method'; operation='eq'; type='string'; value=$RouteMethod
        }
    }
    return $filters
}

function Get-CandidateInvocationCount {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [string]$ValidationRun = ""
    )
    $filters = @(Get-CandidateObservabilityFilters -Candidate $Candidate `
        -ValidationRun $ValidationRun)
    $result = Invoke-WorkersObservabilityQuery -From $From -To $To `
        -Filters $filters `
        -Calculations @([pscustomobject]@{ operator='count'; alias='invocations' })
    if (-not $result) { return $null }
    return Get-CalculationAggregate -QueryResult $result -Alias "invocations"
}

function Invoke-WorkersObservabilityEventsQuery {
    param(
        [Parameter(Mandatory = $true)][object[]]$Filters,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [string]$Offset = ""
    )
    $secret = Get-ReleaseSecret -Name "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN"
    $script:lastWorkersObservabilityCredentialSource = [string]$secret.source
    if (-not $secret.available) {
        $script:lastWorkersObservabilityDiagnostic = [string]$secret.diagnostic
        return $null
    }
    $token = [string]$secret.value
    $body = [ordered]@{
        queryId = "aurum-release-candidate-validation-events"
        timeframe = [ordered]@{
            from = $From.ToUnixTimeMilliseconds()
            to = $To.ToUnixTimeMilliseconds()
        }
        view = "events"
        limit = 2000
        parameters = [ordered]@{
            datasets = @()
            filterCombination = "and"
            filters = $Filters
            calculations = @()
        }
    }
    if ($Offset) { $body.offset = $Offset }
    $uri = "https://api.cloudflare.com/client/v4/accounts/$cloudflareAccountId/workers/observability/telemetry/query"
    try {
        $response = Invoke-RestMethod -Method Post -Uri $uri `
            -Headers @{ Authorization = "Bearer $token" } `
            -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 12) `
            -TimeoutSec 30
        if (-not $response.success -or -not $response.result.events) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_API_REJECTED"
            return $null
        }
        $script:lastWorkersObservabilityDiagnostic = $null
        return $response.result
    } catch {
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
        $script:lastWorkersObservabilityDiagnostic = if ($statusCode -in @(401, 403)) {
            "OBSERVABILITY_CREDENTIAL_REJECTED"
        } elseif ($statusCode -eq 429) {
            "OBSERVABILITY_RATE_LIMITED"
        } elseif ($statusCode -ge 500 -and $statusCode -le 599) {
            "OBSERVABILITY_TRANSIENT_API_FAILURE"
        } else { "OBSERVABILITY_QUERY_FAILED" }
        return $null
    } finally {
        $token = $null
        $secret = $null
    }
}

function Get-ReleaseTelemetryProperty {
    param([object]$Object, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $Object -or $null -eq $Object.PSObject.Properties[$Name]) {
        return $null
    }
    return $Object.PSObject.Properties[$Name].Value
}

function ConvertTo-ReleaseTelemetryRecord {
    param([Parameter(Mandatory = $true)][object]$Event)
    $metadata = Get-ReleaseTelemetryProperty -Object $Event -Name '$metadata'
    $workers = Get-ReleaseTelemetryProperty -Object $Event -Name '$workers'
    $workerEvent = Get-ReleaseTelemetryProperty -Object $workers -Name 'event'
    $request = Get-ReleaseTelemetryProperty -Object $workerEvent -Name 'request'
    $response = Get-ReleaseTelemetryProperty -Object $workerEvent -Name 'response'
    $headers = Get-ReleaseTelemetryProperty -Object $request -Name 'headers'
    $scriptVersion = Get-ReleaseTelemetryProperty -Object $workers -Name 'scriptVersion'
    $cpu = Get-ReleaseTelemetryProperty -Object $workers -Name 'cpuTimeMs'
    $wall = Get-ReleaseTelemetryProperty -Object $workers -Name 'wallTimeMs'
    $record = [pscustomobject]@{
        event_id = [string](Get-ReleaseTelemetryProperty -Object $metadata -Name 'id')
        event_type = [string](Get-ReleaseTelemetryProperty -Object $metadata -Name 'type')
        worker_version_id = [string](Get-ReleaseTelemetryProperty -Object $scriptVersion -Name 'id')
        request_id = [string](Get-ReleaseTelemetryProperty -Object $headers -Name 'x-aurum-request-id')
        validation_run = [string](Get-ReleaseTelemetryProperty -Object $headers -Name 'x-aurum-validation-run')
        validation_phase = [string](Get-ReleaseTelemetryProperty -Object $headers -Name 'x-aurum-validation-phase')
        method = [string](Get-ReleaseTelemetryProperty -Object $request -Name 'method')
        path = [string](Get-ReleaseTelemetryProperty -Object $workerEvent -Name 'path')
        status = [int](Get-ReleaseTelemetryProperty -Object $response -Name 'status')
        outcome = [string](Get-ReleaseTelemetryProperty -Object $workers -Name 'outcome')
        cpu_ms = if ($null -eq $cpu) { $null } else { [double]$cpu }
        wall_ms = if ($null -eq $wall) { $null } else { [double]$wall }
    }
    if (-not $record.event_id -or -not $record.request_id -or
        $null -eq $record.cpu_ms -or $null -eq $record.wall_ms) {
        throw "OBSERVABILITY_SCHEMA_INVALID"
    }
    return $record
}

function Get-ReleaseTelemetryDigest {
    param([Parameter(Mandatory = $true)][object[]]$Records)
    $lines = @($Records | Sort-Object event_id | ForEach-Object {
        @($_.event_id, $_.request_id, $_.worker_version_id, $_.validation_run,
            $_.validation_phase, $_.method, $_.path, $_.status, $_.outcome,
            $_.cpu_ms, $_.wall_ms) -join "|"
    })
    $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Get-ReleaseTelemetryPercentile {
    param([Parameter(Mandatory = $true)][double[]]$Values, [double]$Percentile)
    if ($Values.Count -eq 0) { return $null }
    $ordered = @($Values | Sort-Object)
    $index = [Math]::Max(0, [Math]::Ceiling($Percentile * $ordered.Count) - 1)
    return [double]$ordered[$index]
}

function Get-ReleaseTelemetryMetrics {
    param(
        [Parameter(Mandatory = $true)][object[]]$Records,
        [Parameter(Mandatory = $true)][string]$RouteFamily,
        [int]$ExpectedInvocations
    )
    $cpu = [double[]]@($Records | ForEach-Object { [double]$_.cpu_ms })
    $wall = [double[]]@($Records | ForEach-Object { [double]$_.wall_ms })
    $evidence = [pscustomobject]@{
        route_family = $RouteFamily
        invocations = $Records.Count
        max_cpu_ms = [double](($cpu | Measure-Object -Maximum).Maximum)
        p95_cpu_ms = Get-ReleaseTelemetryPercentile -Values $cpu -Percentile 0.95
        p99_cpu_ms = Get-ReleaseTelemetryPercentile -Values $cpu -Percentile 0.99
        max_wall_ms = [double](($wall | Measure-Object -Maximum).Maximum)
        exceeded_cpu = @($Records | Where-Object { $_.outcome -eq 'exceededCpu' }).Count
        exceeded_memory = @($Records | Where-Object { $_.outcome -eq 'exceededMemory' }).Count
        responses_1102 = @($Records | Where-Object {
            $_.outcome -in @('exceededCpu', 'exceededMemory')
        }).Count
        responses_5xx = @($Records | Where-Object { $_.status -ge 500 -and $_.status -le 599 }).Count
    }
    $gateState = Get-WorkerCpuGateState -Evidence $evidence -ExpectedInvocations $ExpectedInvocations
    $evidence | Add-Member gate_state $gateState
    $evidence | Add-Member passed ([bool]($gateState -eq 'PASSED'))
    return $evidence
}

function Get-CandidateFrozenPlatformEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [Parameter(Mandatory = $true)][object[]]$ExpectedRequests,
        [Parameter(Mandatory = $true)][string]$ValidationRun
    )
    $filters = @(Get-CandidateObservabilityFilters -Candidate $Candidate -ValidationRun $ValidationRun)
    $expectedIds = @($ExpectedRequests | ForEach-Object { [string]$_.request_id } | Sort-Object)
    if (@($expectedIds | Where-Object { -not $_ }).Count -gt 0 -or
        @($expectedIds | Select-Object -Unique).Count -ne $expectedIds.Count) {
        $script:lastWorkersObservabilityDiagnostic = 'EXPECTED_REQUEST_UNIVERSE_INVALID'
        return $null
    }
    $stableDigest = ''
    $stableEventIds = ''
    $stableRequestIds = ''
    $stableReads = 0
    $records = @()
    $frozenTo = $To
    $frozen = $false
    for ($attempt = 0; $attempt -lt 24; $attempt++) {
        if (-not $frozen) { $frozenTo = [DateTimeOffset]::UtcNow }
        $events = @()
        $offset = ''
        for ($pageNumber = 0; $pageNumber -lt 20; $pageNumber++) {
            $page = Invoke-WorkersObservabilityEventsQuery -Filters $filters `
                -From $From -To $frozenTo -Offset $offset
            if ($null -eq $page) { return $null }
            $pageEvents = @($page.events)
            $events += $pageEvents
            if ($pageEvents.Count -lt 2000) { break }
            $lastMetadata = Get-ReleaseTelemetryProperty -Object $pageEvents[-1] -Name '$metadata'
            $nextOffset = [string](Get-ReleaseTelemetryProperty -Object $lastMetadata -Name 'id')
            if (-not $nextOffset -or $nextOffset -eq $offset) {
                $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_EVENT_CURSOR_INVALID'
                return $null
            }
            $offset = $nextOffset
        }
        if ($events.Count -ge 40000) {
            $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_EVENT_PAGE_BOUND_EXCEEDED'
            return $null
        }
        try { $candidateRecords = @($events | ForEach-Object { ConvertTo-ReleaseTelemetryRecord $_ }) }
        catch {
            $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_SCHEMA_INVALID'
            return $null
        }
        $actualIds = @($candidateRecords | ForEach-Object { $_.request_id } | Sort-Object)
        $eventIds = @($candidateRecords | ForEach-Object { $_.event_id })
        $identityValid = @($candidateRecords | Where-Object {
            $_.worker_version_id -ne [string]$Candidate.worker_version_id -or
            $_.validation_run -ne $ValidationRun -or $_.validation_phase -ne 'acceptance' -or
            $_.event_type -ne 'cf-worker-event'
        }).Count -eq 0
        $complete = $identityValid -and $actualIds.Count -eq $expectedIds.Count -and
            @($actualIds | Select-Object -Unique).Count -eq $actualIds.Count -and
            @($eventIds | Select-Object -Unique).Count -eq $eventIds.Count -and
            (($actualIds -join "`n") -ceq ($expectedIds -join "`n"))
        if ($complete) {
            $frozen = $true
            $digest = Get-ReleaseTelemetryDigest -Records $candidateRecords
            $eventIdSet = @($eventIds | Sort-Object) -join "`n"
            $requestIdSet = $actualIds -join "`n"
            if ($digest -eq $stableDigest -and $eventIdSet -ceq $stableEventIds -and
                $requestIdSet -ceq $stableRequestIds) {
                $stableReads++
            } else {
                $stableDigest = $digest
                $stableEventIds = $eventIdSet
                $stableRequestIds = $requestIdSet
                $stableReads = 1
            }
            if ($stableReads -ge 2) { $records = $candidateRecords; break }
        } else {
            $stableDigest = ''
            $stableEventIds = ''
            $stableRequestIds = ''
            $stableReads = 0
        }
        if ($attempt -lt 23) { Start-Sleep -Seconds 10 }
    }
    if ($records.Count -ne $expectedIds.Count) {
        $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING'
        return $null
    }
    $expectedById = @{}
    foreach ($expected in $ExpectedRequests) { $expectedById[[string]$expected.request_id] = $expected }
    $families = @()
    foreach ($group in @($records | Group-Object { [string]$expectedById[$_.request_id].family })) {
        $familyExpected = @($ExpectedRequests | Where-Object { [string]$_.family -eq $group.Name }).Count
        $families += Get-ReleaseTelemetryMetrics -Records @($group.Group) `
            -RouteFamily $group.Name -ExpectedInvocations $familyExpected
    }
    $familyReconciliation = @($ExpectedRequests | Group-Object { [string]$_.family } |
        ForEach-Object {
            $name = [string]$_.Name
            $expectedCount = $_.Count
            $actualCount = @($records | Where-Object {
                [string]$expectedById[$_.request_id].family -eq $name
            }).Count
            [pscustomobject]@{
                family = $name; expected = $expectedCount; actual = $actualCount
                matched = [bool]($expectedCount -eq $actualCount)
            }
        })
    $scenarioReconciliation = @($ExpectedRequests | Group-Object {
            "{0}|{1}" -f [string]$_.family, [string]$_.scenario
        } | ForEach-Object {
            $family = [string]$_.Group[0].family
            $scenario = [string]$_.Group[0].scenario
            $expectedCount = $_.Count
            $actualCount = @($records | Where-Object {
                $row = $expectedById[$_.request_id]
                [string]$row.family -eq $family -and [string]$row.scenario -eq $scenario
            }).Count
            [pscustomobject]@{
                family = $family; scenario = $scenario
                expected = $expectedCount; actual = $actualCount
                matched = [bool]($expectedCount -eq $actualCount)
            }
        })
    $global = Get-ReleaseTelemetryMetrics -Records $records -RouteFamily 'GLOBAL' `
        -ExpectedInvocations $expectedIds.Count
    $failed = @($families | Where-Object { $_.gate_state -eq 'FAILED' }).Count -gt 0
    $review = @($families | Where-Object { $_.gate_state -eq 'REVIEW_REQUIRED' }).Count -gt 0
    $gateState = if ($failed -or $global.gate_state -eq 'FAILED') { 'FAILED' }
        elseif ($review -or $global.gate_state -eq 'REVIEW_REQUIRED') { 'REVIEW_REQUIRED' }
        else { 'PASSED' }
    return [pscustomobject]@{
        source = 'CLOUDFLARE_WORKERS_OBSERVABILITY_RAW_EVENTS'
        credential_source = [string]$script:lastWorkersObservabilityCredentialSource
        worker_version_id = [string]$Candidate.worker_version_id
        validation_run = $ValidationRun
        frozen_from = $From.ToString('o')
        frozen_to = $frozenTo.ToString('o')
        universe_digest = $stableDigest
        stable_reads = $stableReads
        expected_invocations = $expectedIds.Count
        invocations = $records.Count
        expected_requests = @($ExpectedRequests)
        request_reconciliation = [pscustomobject]@{ expected=$expectedIds.Count; actual=$records.Count; matched=$true }
        family_reconciliation = $familyReconciliation
        scenario_reconciliation = $scenarioReconciliation
        global = $global
        routes = $families
        max_cpu_ms = $global.max_cpu_ms
        p95_cpu_ms = $global.p95_cpu_ms
        p99_cpu_ms = $global.p99_cpu_ms
        max_wall_ms = $global.max_wall_ms
        exceeded_cpu = $global.exceeded_cpu
        exceeded_memory = $global.exceeded_memory
        responses_1102 = $global.responses_1102
        responses_5xx = $global.responses_5xx
        gate_state = $gateState
        passed = [bool]($gateState -eq 'PASSED')
    }
}

function Get-WorkerValidationManifest {
    param([string]$Revision = "")
    if ($Revision) {
        $object = "{0}:web/worker-validation-manifest.json" -f $Revision
        $raw = (& git -C $repositoryRoot show $object 2>$null) -join "`n"
        if ($LASTEXITCODE -ne 0 -or -not $raw) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_UNAVAILABLE"
        }
    } else {
        $path = Join-Path $repositoryRoot "web\worker-validation-manifest.json"
        if (-not (Test-Path -LiteralPath $path)) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_UNAVAILABLE"
        }
        $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    }
    $manifest = $raw | ConvertFrom-Json
    if ([int]$manifest.schema_version -ne 3 -or @($manifest.routes).Count -eq 0 -or
        -not $manifest.fixture_builder) {
        throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
    }
    $staticPaths = @($manifest.static_assets | ForEach-Object { [string]$_.path })
    if ($staticPaths.Count -eq 0 -or
        @($staticPaths | Sort-Object -Unique).Count -ne $staticPaths.Count) {
        throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
    }
    foreach ($asset in @($manifest.static_assets)) {
        $fields = @($asset.PSObject.Properties.Name)
        $missingFields = @(@(
            "path", "content_type", "body_encoding", "require_html_charset", "marker",
            "redirect_path"
        ) | Where-Object { $_ -notin $fields })
        if ($missingFields.Count -gt 0 -or
            [string]$asset.path -notmatch '^/[^?#]*$' -or
            [string]$asset.content_type -notmatch '^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$' -or
            $asset.require_html_charset -isnot [bool] -or
            ([string]$asset.body_encoding -notin @("", "utf-8")) -or
            ([bool]$asset.require_html_charset -and
                [string]$asset.body_encoding -ne "utf-8") -or
            ([string]$asset.content_type -eq "text/html" -and
                (-not [bool]$asset.require_html_charset -or
                    [string]::IsNullOrWhiteSpace([string]$asset.marker))) -or
            ([string]$asset.redirect_path -and
                ([string]$asset.redirect_path -notmatch '^/[^?#]*$' -or
                    [string]$asset.redirect_path -eq [string]$asset.path)) -or
            ($null -ne $asset.worker_expected -and
                $asset.worker_expected -isnot [bool])) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
        }
    }
    return $manifest
}

function Test-ValidationRouteOwnedByChange {
    param([object]$Route, [string[]]$ChangedFiles)
    foreach ($file in $ChangedFiles) {
        foreach ($owner in @($Route.owners)) {
            if ($file -like [string]$owner) { return $true }
        }
        foreach ($producer in @($Route.producers)) {
            if ($file -like [string]$producer) { return $true }
        }
    }
    return $false
}

function Get-CandidateRouteValidationPlan {
    param([string[]]$ChangedFiles, [string]$Revision = "")
    $manifest = Get-WorkerValidationManifest -Revision $Revision
    $manifestChanged = "web/worker-validation-manifest.json" -in $ChangedFiles
    $fixtureBuilderChanged = @($ChangedFiles | Where-Object {
        $_ -like [string]$manifest.fixture_builder -or
        $_ -eq "tests/test_release_validation_fixtures.py"
    }).Count -gt 0
    $workerCodeChanged = @($ChangedFiles | Where-Object {
        $file = $_
        @($manifest.bundle_runtime_roots | Where-Object {
            $file -like [string]$_
        }).Count -gt 0
    }).Count -gt 0
    $selectedRoutes = @($manifest.routes | Where-Object {
        [bool]$_.cpu_required -and (
            $manifestChanged -or $fixtureBuilderChanged -or
            (Test-ValidationRouteOwnedByChange -Route $_ -ChangedFiles $ChangedFiles) -or
            ($workerCodeChanged -and [bool]$_.baseline)
        )
    })
    $selected = @()
    foreach ($route in $selectedRoutes) {
        $scenarios = @($route.scenarios)
        if ($scenarios.Count -eq 0) {
            $scenarios = @([pscustomobject]@{ name = "default" })
        }
        foreach ($scenario in $scenarios) {
            $copy = $route.PSObject.Copy()
            $copy | Add-Member -NotePropertyName scenario `
                -NotePropertyValue ([string]$scenario.name)
            if ($scenario.fixture) { $copy.fixture = [string]$scenario.fixture }
            $selected += $copy
        }
    }
    $contractRoutes = @($manifest.routes | Where-Object {
        $manifestChanged -or (Test-ValidationRouteOwnedByChange -Route $_ -ChangedFiles $ChangedFiles)
    })
    $staticChanged = @($ChangedFiles | Where-Object {
        ($_ -like "web/app/*" -and $_ -notlike "web/app/*/route.ts" -and
            $_ -notlike "web/app/api/_shared/*") -or
        $_ -like "web/public/*" -or
        $_ -in @("web/vite.config.ts", "web/wrangler.jsonc", "web/worker/index.ts")
    }).Count -gt 0
    [pscustomobject]@{
        manifest_schema_version = [int]$manifest.schema_version
        static_assets = @($manifest.static_assets)
        worker_reads = @($selected | Where-Object { [string]$_.boundary -eq "WORKER_READ" })
        worker_writes = @($selected | Where-Object { [string]$_.boundary -eq "WORKER_WRITE" })
        contract_routes = $contractRoutes
        worker_cpu_required = [bool]($selected.Count -gt 0)
        requires_validation = [bool]($selected.Count -gt 0 -or $staticChanged)
    }
}

function New-CandidateValidationFixtureWorkspace {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
        ("aurum-release-validation-{0}" -f [guid]::NewGuid().ToString("N"))
    $fixtureRoot = Join-Path $stageRoot ".release-validation-fixtures"
    & git -C $repositoryRoot worktree add --detach --quiet $stageRoot `
        ([string]$Candidate.git_sha) 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Candidate fixture worktree is unavailable." }
    try {
        $python = (Get-Command python.exe -ErrorAction Stop).Source
        & $python (Join-Path $stageRoot "scripts\build_release_validation_fixtures.py") `
            --output $fixtureRoot | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $fixtureRoot)) {
            throw "Production-shaped fixture generation failed."
        }
        return [pscustomobject]@{ stage_root=$stageRoot; fixture_root=$fixtureRoot }
    } catch {
        & git -C $repositoryRoot worktree remove --force $stageRoot 2>$null
        & git -C $repositoryRoot worktree prune 2>$null
        throw
    }
}

function Remove-CandidateValidationFixtureWorkspace {
    param([object]$Workspace)
    if (-not $Workspace -or -not $Workspace.stage_root) { return }
    & git -C $repositoryRoot worktree remove --force ([string]$Workspace.stage_root) 2>$null
    & git -C $repositoryRoot worktree prune 2>$null
}

function Get-CandidateRouteResponseReason {
    param([object]$Payload, [string]$Fallback)
    if ($Payload) {
        foreach ($path in @(
            @("error_code"), @("reason"), @("error", "code"), @("error")
        )) {
            $value = $Payload
            foreach ($name in $path) {
                if ($null -eq $value -or $null -eq $value.PSObject.Properties[$name]) {
                    $value = $null
                    break
                }
                $value = $value.$name
            }
            if ($value -is [string] -and -not [string]::IsNullOrWhiteSpace($value)) {
                return Protect-PreflightDiagnosticText $value
            }
        }
    }
    return $Fallback
}

function Test-CandidateDryRunPayload {
    param([object]$Payload, [string]$ExpectedFamily)
    if (-not $Payload) { return $false }
    $fields = @($Payload.PSObject.Properties.Name)
    $missingFields = @(@("status", "mutated", "route_family") |
        Where-Object { $_ -notin $fields })
    if ($missingFields.Count -gt 0) { return $false }
    return [bool](
        $Payload.status -is [string] -and
        [string]$Payload.status -eq "DRY_RUN_OK" -and
        $Payload.mutated -is [bool] -and
        [bool]$Payload.mutated -eq $false -and
        $Payload.route_family -is [string] -and
        [string]$Payload.route_family -eq $ExpectedFamily
    )
}

function Get-CandidateStaticAssetBaseUri {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $versionId = [string]$Candidate.worker_version_id
    if ($versionId -notmatch '^[0-9a-f]{8}-[0-9a-f-]{27}$') {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    $candidateUri = $null
    if (-not [Uri]::TryCreate([string]$Candidate.browser_url,
            [UriKind]::Absolute, [ref]$candidateUri)) {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    $productionUri = [Uri]$workerUrl
    $workerPrefix = "$workerName."
    if (-not $productionUri.Host.StartsWith(
            $workerPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    $suffix = $productionUri.Host.Substring($workerPrefix.Length)
    $expectedHost = "{0}-{1}.{2}" -f $versionId.Substring(0, 8), $workerName, $suffix
    if ($candidateUri.Scheme -ne "https" -or -not $candidateUri.IsDefaultPort -or
        $candidateUri.Host -ne $expectedHost -or $candidateUri.AbsolutePath -ne "/" -or
        $candidateUri.Query -or $candidateUri.Fragment) {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    return $candidateUri
}

function Get-Sha256BytesHex {
    param([byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return -join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") })
    } finally { $sha.Dispose() }
}

function Invoke-CandidateStaticAssetRequest {
    param([Parameter(Mandatory = $true)][Uri]$RequestUri)
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    $response = $null
    try {
        $response = $client.GetAsync($RequestUri).GetAwaiter().GetResult()
        $contentType = if ($response.Content.Headers.ContentType) {
            [string]$response.Content.Headers.ContentType
        } else { "" }
        $cfCacheStatus = if ($response.Headers.Contains("CF-Cache-Status")) {
            [string]($response.Headers.GetValues("CF-Cache-Status") | Select-Object -First 1)
        } else { "" }
        $age = if ($response.Headers.Contains("Age")) {
            [string]($response.Headers.GetValues("Age") | Select-Object -First 1)
        } else { "" }
        return [pscustomobject]@{
            status = [int]$response.StatusCode
            content_type = $contentType
            body_bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            location = [string]$response.Headers.Location
            cf_cache_status = $cfCacheStatus
            etag = [string]$response.Headers.ETag
            age = $age
            worker_version = if ($response.Headers.Contains("X-Aurum-Worker-Version")) {
                [string]($response.Headers.GetValues("X-Aurum-Worker-Version") |
                    Select-Object -First 1)
            } else { "" }
            git_sha = if ($response.Headers.Contains("X-Aurum-Git-SHA")) {
                [string]($response.Headers.GetValues("X-Aurum-Git-SHA") |
                    Select-Object -First 1)
            } else { "" }
            route = if ($response.Headers.Contains("X-Aurum-Route")) {
                [string]($response.Headers.GetValues("X-Aurum-Route") |
                    Select-Object -First 1)
            } else { "" }
        }
    } finally {
        if ($response) { $response.Dispose() }
        $client.Dispose()
        $handler.Dispose()
    }
}

function Invoke-CandidateStaticAssetSample {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Route
    )
    $result = [ordered]@{
        route = [string]$Route.path; path = [string]$Route.path
        method = "GET"; boundary = "STATIC_ASSET"; request_id = $null
        requested_url = ""; requested_host = ""
        requested_worker_version = [string]$Candidate.worker_version_id
        expected_status = 200; status = 0; passed = $false; reason = $null
        expected_content_type = [string]$Route.content_type
        actual_content_type = ""; expected_encoding = [string]$Route.body_encoding
        declared_charset = ""; expected_marker = [string]$Route.marker
        marker_present = $false; body_bytes = 0; body_sha256 = ""
        expected_redirect_path = [string]$Route.redirect_path
        redirect_status = 0; redirect_location = ""; final_url = ""
        cf_cache_status = ""; etag = ""; age = ""
        observed_worker_version = ""; observed_git_sha = ""; observed_route = ""
    }
    try {
        $baseUri = Get-CandidateStaticAssetBaseUri -Candidate $Candidate
        $requestUri = [Uri]::new($baseUri, [string]$Route.path)
        $result.requested_url = $requestUri.AbsoluteUri
        $result.requested_host = $requestUri.Host
        $response = Invoke-CandidateStaticAssetRequest -RequestUri $requestUri
        if ([bool]$Route.worker_expected) {
            $result.observed_worker_version = [string]$response.worker_version
            $result.observed_git_sha = [string]$response.git_sha
            $result.observed_route = [string]$response.route
            if ($result.observed_worker_version -ne [string]$Candidate.worker_version_id -or
                $result.observed_git_sha -ne [string]$Candidate.git_sha -or
                $result.observed_route -ne [string]$Route.path) {
                $result.status = [int]$response.status
                $result.reason = "VERSION_HOST_WORKER_IDENTITY_MISMATCH"
                return [pscustomobject]$result
            }
        }
        if ($Route.redirect_path) {
            $result.redirect_status = [int]$response.status
            $result.redirect_location = [string]$response.location
            $redirectUri = $null
            try { $redirectUri = [Uri]::new($requestUri, [string]$response.location) } catch {}
            if ([int]$response.status -notin @(301, 302, 307, 308) -or
                -not $redirectUri -or $redirectUri.Scheme -ne $requestUri.Scheme -or
                $redirectUri.Host -ne $requestUri.Host -or
                $redirectUri.Port -ne $requestUri.Port -or
                $redirectUri.AbsolutePath -ne [string]$Route.redirect_path -or
                $redirectUri.Query -or $redirectUri.Fragment) {
                $result.status = [int]$response.status
                $result.reason = "REDIRECT_CONTRACT_MISMATCH"
                return [pscustomobject]$result
            }
            $result.final_url = $redirectUri.AbsoluteUri
            $response = Invoke-CandidateStaticAssetRequest -RequestUri $redirectUri
        } else { $result.final_url = $requestUri.AbsoluteUri }
        $result.status = [int]$response.status
        $result.actual_content_type = [string]$response.content_type
        $result.cf_cache_status = [string]$response.cf_cache_status
        $result.etag = [string]$response.etag
        $result.age = [string]$response.age
        $bytes = [byte[]]$response.body_bytes
        $result.body_bytes = $bytes.Length
        if ($bytes.Length -gt 0) { $result.body_sha256 = Get-Sha256BytesHex -Bytes $bytes }
        $mediaType = ([string]$response.content_type -split ';', 2)[0].Trim().ToLowerInvariant()
        $charsetMatch = [regex]::Match([string]$response.content_type,
            '(?i)(?:^|;)\s*charset\s*=\s*"?([^;"\s]+)')
        if ($charsetMatch.Success) {
            $result.declared_charset = $charsetMatch.Groups[1].Value.ToLowerInvariant()
        }
        if ($result.status -ne 200) { $result.reason = "HTTP_STATUS_MISMATCH" }
        elseif ($mediaType -ne ([string]$Route.content_type).ToLowerInvariant()) {
            $result.reason = "CONTENT_TYPE_MISMATCH"
        } elseif ($bytes.Length -eq 0) { $result.reason = "EMPTY_BODY" }
        elseif ($bytes.Length -gt $candidateStaticAssetMaxBytes) { $result.reason = "BODY_TOO_LARGE" }
        else {
            $decoded = $null
            if ([string]$Route.body_encoding -eq "utf-8") {
                try {
                    $decoded = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes)
                } catch { $result.reason = "INVALID_UTF8_BODY" }
            }
            if (-not $result.reason -and [bool]$Route.require_html_charset) {
                $httpCharsetPassed = $result.declared_charset -eq "utf-8"
                $htmlCharsetPassed = $decoded -match '(?i)<meta\b[^>]*\bcharset\s*=\s*["'']?utf-8\b'
                if (-not ($httpCharsetPassed -or $htmlCharsetPassed)) {
                    $result.reason = "HTML_CHARSET_MISMATCH"
                }
            }
            if (-not $result.reason -and $Route.marker) {
                $result.marker_present = $decoded.IndexOf(
                    [string]$Route.marker, [StringComparison]::Ordinal) -ge 0
                if (-not $result.marker_present) { $result.reason = "MARKER_MISSING" }
            } elseif (-not $Route.marker) { $result.marker_present = $true }
        }
        $result.passed = [bool](-not $result.reason)
    } catch {
        $reason = [string]$_.Exception.Message
        $result.reason = if ($reason -eq "CANDIDATE_STATIC_HOST_MISMATCH") {
            $reason
        } else { "VALIDATION_REQUEST_FAILED" }
    }
    return [pscustomobject]$result
}

function Invoke-CandidateRouteSample {
    param(
        [Parameter(Mandatory = $true)][object]$Route,
        [Parameter(Mandatory = $true)][hashtable]$VersionHeaders,
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][string]$FixtureRoot,
        [string]$IngestToken = "",
        [ValidateSet("warmup", "acceptance")][string]$ValidationPhase = "acceptance"
    )
    $requestId = [guid]::NewGuid().ToString()
    $headers = @{} + $VersionHeaders
    $headers["X-Aurum-Validation-Run"] = $ValidationRun
    $headers["X-Aurum-Validation-Phase"] = $ValidationPhase
    $headers["X-Aurum-Request-ID"] = $requestId
    $parameters = @{
        UseBasicParsing=$true; Method=[string]$Route.method
        Uri="$workerUrl$($Route.path)$([string]$Route.request_query)"; Headers=$headers; TimeoutSec=30
    }
    if ([string]$Route.strategy -eq "PRODUCTION_SHAPED_DRY_RUN") {
        if (-not $IngestToken) {
            return [pscustomobject]@{
                request_id=$requestId; status=0; passed=$false
                reason="INGEST_AUTHORITY_UNAVAILABLE"
            }
        }
        $fixture = Join-Path $FixtureRoot ([string]$Route.fixture)
        if (-not (Test-Path -LiteralPath $fixture)) {
            return [pscustomobject]@{
                request_id=$requestId; status=0; passed=$false
                reason="VALIDATION_FIXTURE_UNAVAILABLE"
            }
        }
        $headers.Authorization = "Bearer $IngestToken"
        $headers["X-Aurum-Release-Validation"] = "dry-run"
        $parameters.ContentType = "application/json"
        $parameters.Body = [System.IO.File]::ReadAllBytes($fixture)
    }
    try {
        $response = Invoke-WebRequest @parameters
        $payload = $null
        try { $payload = $response.Content | ConvertFrom-Json } catch {}
        $observedVersion = [string]$response.Headers["X-Aurum-Worker-Version"]
        $observedGit = [string]$response.Headers["X-Aurum-Git-SHA"]
        $identityPassed = [bool](
            $observedVersion -eq [string]$Route.expected_worker_version -and
            $observedGit -eq [string]$Route.expected_git_sha
        )
        $dryRunPassed = $true
        if ([string]$Route.strategy -eq "PRODUCTION_SHAPED_DRY_RUN") {
            $dryRunPassed = Test-CandidateDryRunPayload -Payload $payload `
                -ExpectedFamily ([string]$Route.family)
        }
        $passed = [bool]($response.StatusCode -eq 200 -and $identityPassed -and $dryRunPassed)
        $reason = if ([int]$response.StatusCode -ne 200) {
            Get-CandidateRouteResponseReason -Payload $payload -Fallback "HTTP_STATUS_MISMATCH"
        } elseif (-not $identityPassed) {
            "WORKER_IDENTITY_MISMATCH"
        } elseif (-not $dryRunPassed) {
            "RELEASE_DRY_RUN_CONTRACT_MISMATCH"
        } else { $null }
        return [pscustomobject]@{
            request_id=$requestId; method=[string]$Route.method
            path="$([string]$Route.path)$([string]$Route.request_query)"
            expected_status=200; status=[int]$response.StatusCode; passed=$passed
            reason=$reason
            requested_worker_version=[string]$Route.expected_worker_version
            observed_worker_version=$observedVersion; observed_git_sha=$observedGit
            route=[string]$response.Headers["X-Aurum-Route"]
            resource=[string]$response.Headers["X-Aurum-Resource"]
            d1_operations=[string]$response.Headers["X-Aurum-D1-Operations"]
            request_bytes=[string]$response.Headers["X-Aurum-Request-Bytes"]
            response_bytes=[string]$response.Headers["X-Aurum-Response-Bytes"]
            failure_stage=[string]$response.Headers["X-Aurum-Failure-Stage"]
            server_timing=[string]$response.Headers["Server-Timing"]
            validation_run=$ValidationRun
        }
    } catch {
        $errorResponse = $_.Exception.Response
        $status = if ($errorResponse) {
            [int]$errorResponse.StatusCode
        } else { 0 }
        $payload = $null
        try { $payload = $_.ErrorDetails.Message | ConvertFrom-Json } catch {}
        return [pscustomobject]@{
            request_id=$requestId; method=[string]$Route.method
            path="$([string]$Route.path)$([string]$Route.request_query)"
            expected_status=200; status=$status; passed=$false
            reason=(Get-CandidateRouteResponseReason -Payload $payload `
                -Fallback "VALIDATION_REQUEST_FAILED")
            requested_worker_version=[string]$Route.expected_worker_version
            observed_worker_version=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Worker-Version"] } else { "" }
            observed_git_sha=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Git-SHA"] } else { "" }
            route=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Route"] } else { "" }
            resource=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Resource"] } else { "" }
            d1_operations=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-D1-Operations"] } else { "" }
            request_bytes=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Request-Bytes"] } else { "" }
            response_bytes=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Response-Bytes"] } else { "" }
            failure_stage=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Failure-Stage"] } else { "request" }
            server_timing=if ($errorResponse) { [string]$errorResponse.Headers["Server-Timing"] } else { "" }
            validation_run=$ValidationRun
        }
    }
}

function Invoke-CandidateWorkerValidation {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan
    )
    $script:lastWorkersObservabilityDiagnostic = $null
    $script:lastWorkersObservabilityCredentialSource = "UNAVAILABLE"
    $header = @{
        "Cloudflare-Workers-Version-Overrides" =
            "$workerName=`"$([string]$Candidate.worker_version_id)`""
    }
    foreach ($route in @($RoutePlan.worker_reads) + @($RoutePlan.worker_writes)) {
        $route | Add-Member -NotePropertyName expected_worker_version `
            -NotePropertyValue ([string]$Candidate.worker_version_id) -Force
        $route | Add-Member -NotePropertyName expected_git_sha `
            -NotePropertyValue ([string]$Candidate.git_sha) -Force
    }
    $results = @()
    foreach ($route in @($RoutePlan.static_assets)) {
        $results += Invoke-CandidateStaticAssetSample -Candidate $Candidate -Route $route
    }
    $expectedVersionRouteInvocations = @($RoutePlan.static_assets | Where-Object {
        [bool]$_.worker_expected
    }).Count
    $workerExpectedPaths = @($RoutePlan.static_assets | Where-Object {
        [bool]$_.worker_expected
    } | ForEach-Object { [string]$_.path })
    $staticInvocations = @($results | Where-Object {
        [string]$_.route -in $workerExpectedPaths -and [bool]$_.passed -and
        [string]$_.observed_worker_version -eq [string]$Candidate.worker_version_id -and
        [string]$_.observed_git_sha -eq [string]$Candidate.git_sha -and
        [string]$_.observed_route -eq [string]$_.route
    }).Count
    $staticObservabilityState = if ([int]$staticInvocations -eq $expectedVersionRouteInvocations) {
        "PASSED"
    } else { "FAILED" }
    if ([int]$staticInvocations -ne $expectedVersionRouteInvocations) {
        $results += [pscustomobject]@{
            route = "VERSION_HOST_ROUTE_INVOCATIONS"; boundary = "VERSION_HOST_ROUTE"
            method = "GET"; request_id = $null; status = 0; passed = $false
            reason = "VERSION_HOST_ROUTE_WORKER_INVOCATION_MISMATCH"
            expected_invocations = $expectedVersionRouteInvocations
            observed_invocations = $staticInvocations
        }
    }
    $workerRoutes = @($RoutePlan.worker_reads) + @($RoutePlan.worker_writes)
    if ($workerRoutes.Count -eq 0) {
        return [pscustomobject]@{
            channel = "VERSION_HOST_RESULT"
            passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
            validation_run = $null; expected_worker_invocations = $expectedVersionRouteInvocations
            static_worker_invocations = $staticInvocations; routes = $results
            static_observability_state = $staticObservabilityState
            cpu_evidence = "NOT_REQUIRED"
        }
    }
    $workspace = $null
    $validationRun = [guid]::NewGuid().ToString()
    $ingestToken = [Environment]::GetEnvironmentVariable("CLOUDFLARE_INGEST_TOKEN", "User")
    try {
        if (@($RoutePlan.worker_writes).Count -gt 0) {
            $workspace = New-CandidateValidationFixtureWorkspace -Candidate $Candidate
        }
        $fixtureRoot = if ($workspace) { [string]$workspace.fixture_root } else { "" }
        foreach ($route in $workerRoutes) {
            $warmups = @()
            for ($index = 0; $index -lt [int]$route.warmup_samples; $index++) {
                $warmups += Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken `
                    -ValidationPhase "warmup"
            }
            if (@($warmups | Where-Object { -not $_.passed }).Count -gt 0) {
                $firstWarmupFailure = @($warmups | Where-Object { -not $_.passed })[0]
                $results += [pscustomobject]@{
                    route=$route.path; method=$route.method; family=$route.family
                    scenario=$route.scenario
                    boundary=$route.boundary; warmup_samples=$warmups.Count
                    acceptance_samples=0; passed=$false; reason="WARMUP_FAILED"
                    first_failure=$firstWarmupFailure
                }
            }
        }
        $workerStartedAt = [DateTimeOffset]::UtcNow
        foreach ($route in $workerRoutes) {
            $samples = @()
            for ($index = 0; $index -lt [int]$route.acceptance_samples; $index++) {
                $samples += Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken `
                    -ValidationPhase "acceptance"
            }
            $failures = @($samples | Where-Object { -not $_.passed })
            $sampleReason = if ($failures.Count) {
                [string]$failures[0].reason
            } else { $null }
            $results += [pscustomobject]@{
                route=$route.path; path="$([string]$route.path)$([string]$route.request_query)"
                method=$route.method; family=$route.family
                scenario=$route.scenario
                boundary=$route.boundary; warmup_samples=[int]$route.warmup_samples
                acceptance_samples=$samples.Count
                request_ids=@($samples | ForEach-Object { $_.request_id })
                statuses=@($samples | Group-Object status | ForEach-Object {
                    [pscustomobject]@{ status=[int]$_.Name; count=$_.Count }
                })
                passed=[bool]($failures.Count -eq 0)
                reason=$sampleReason
                first_failure=if ($failures.Count) { $failures[0] } else { $null }
            }
        }
        $workerEndedAt = [DateTimeOffset]::UtcNow
        $platform = $null
        if (@($results | Where-Object { -not $_.passed }).Count -eq 0) {
            $expectedInvocations = [int](($workerRoutes |
                Measure-Object -Property acceptance_samples -Sum).Sum)
            $expectedRequests = @($results | Where-Object {
                $_.boundary -in @('WORKER_READ', 'WORKER_WRITE') -and $_.request_ids
            } | ForEach-Object {
                $result = $_
                @($result.request_ids | ForEach-Object {
                    [pscustomobject]@{
                        request_id = [string]$_
                        family = [string]$result.family
                        scenario = [string]$result.scenario
                        method = [string]$result.method
                        path = [string]$result.route
                    }
                })
            })
            Start-Sleep -Seconds 8
            $platform = Get-CandidateFrozenPlatformEvidence -Candidate $Candidate `
                -From $workerStartedAt -To ([DateTimeOffset]::UtcNow) `
                -ExpectedRequests $expectedRequests -ValidationRun $validationRun
        } else {
            $platform = "NOT_RUN"
        }
    } finally {
        Remove-CandidateValidationFixtureWorkspace -Workspace $workspace
    }
    $expectedInvocations = [int](($workerRoutes |
        Measure-Object -Property acceptance_samples -Sum).Sum)
    [pscustomobject]@{
        channel = "VERSION_HOST_RESULT"
        passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
        validation_run = $validationRun
        expected_worker_invocations = $expectedInvocations
        observed_worker_invocations = if ($platform -and $platform -ne "NOT_RUN") {
            $platform.invocations
        } else { $null }
        static_worker_invocations = $staticInvocations
        static_observability_state = $staticObservabilityState
        observability_credential_source = [string]$script:lastWorkersObservabilityCredentialSource
        observability_diagnostic = [string]$script:lastWorkersObservabilityDiagnostic
        routes = $results
        cpu_evidence = $platform
    }
}

function Set-CloudflareCandidatePointer {
    param([object]$Stable, [object]$Candidate)
    Invoke-CloudflareDeployment `
        -StableVersionId ([string]$Stable.worker_version_id) `
        -CandidateVersionId ([string]$Candidate.worker_version_id) `
        -Message "stage release candidate $([string]$Candidate.validation_key)"
}

function Invoke-ExactVersionJson {
    param(
        [Parameter(Mandatory = $true)][string]$VersionId,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $headers = @{
        "Cloudflare-Workers-Version-Overrides" = "$workerName=`"$VersionId`""
    }
    $response = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$workerUrl$Path" -Headers $headers -TimeoutSec 30
    if ([int]$response.StatusCode -ne 200) {
        throw "Exact-version read $Path returned $([int]$response.StatusCode)."
    }
    return [pscustomobject]@{
        payload = $response.Content | ConvertFrom-Json
        requested_version_id = $VersionId
        observed_version_id = [string]$response.Headers["X-Aurum-Worker-Version"]
        observed_git_sha = [string]$response.Headers["X-Aurum-Git-SHA"]
        server_timing = [string]$response.Headers["Server-Timing"]
    }
}

function Get-ReleaseResponseHeaderValue {
    param([object]$Response, [Parameter(Mandatory = $true)][string]$Name)
    if (-not $Response) { return "" }
    try {
        $value = if ($Response.Headers.PSObject.Methods['GetValues'] -and
            $Response.Headers.Contains($Name)) {
            @($Response.Headers.GetValues($Name)) -join ","
        } else { $Response.Headers[$Name] }
        if ($value) { return [string]$value }
    } catch {}
    try {
        $value = if ($Name -eq "Content-Type" -and
            $Response.Content.Headers.ContentType) {
            $Response.Content.Headers.ContentType.ToString()
        } elseif ($Response.Content.Headers.PSObject.Methods['GetValues'] -and
            $Response.Content.Headers.Contains($Name)) {
            @($Response.Content.Headers.GetValues($Name)) -join ","
        } else { $Response.Content.Headers[$Name] }
        if ($value) { return [string]$value }
    } catch {}
    return ""
}

function Get-BoundedReleaseErrorBody {
    param([object]$Response, [int]$MaxBytes = 65536)
    if (-not $Response) { return $null }
    try {
        $body = $null
        if ($Response.PSObject.Properties['Content'] -and
            $Response.Content -is [string]) {
            $body = [string]$Response.Content
        } elseif ($Response.PSObject.Properties['Content'] -and
            $Response.Content -and
            $Response.Content.PSObject.Methods['ReadAsStringAsync']) {
            $readTask = $Response.Content.ReadAsStringAsync()
            $body = [string]$readTask.GetAwaiter().GetResult()
        } elseif ($Response.PSObject.Methods['GetResponseStream']) {
            $stream = $Response.GetResponseStream()
            if ($stream) {
                $reader = [System.IO.StreamReader]::new(
                    $stream, [System.Text.Encoding]::UTF8, $true, 1024, $true
                )
                try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
            }
        }
        if ([string]::IsNullOrWhiteSpace([string]$body)) { return $null }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$body)
        if ($bytes.Length -gt $MaxBytes) { return $null }
        return [string]$body
    } catch { return $null }
}

function Get-ReleaseFailureFingerprint {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][int]$StatusCode,
        [string]$ObservedRoute,
        [string]$Resource,
        [string]$FailureStage,
        [string]$ContentType,
        [AllowNull()][string]$Body
    )
    $basePath = ($ExpectedPath -split '\?', 2)[0]
    $unsafeStages = @("exception", "framework_fallback", "ssr")
    if ($StatusCode -lt 400 -or $ObservedRoute -cne $basePath -or
        [string]::IsNullOrWhiteSpace($Resource) -or
        [string]::IsNullOrWhiteSpace($FailureStage) -or
        $FailureStage -in $unsafeStages -or
        $ContentType -notmatch '^application/(?:[a-z0-9.+-]*\+)?json(?:\s*;|$)' -or
        [string]::IsNullOrWhiteSpace($Body)) {
        return [pscustomobject]@{
            available = $false; digest = $null; machine_reason = $null
            hard_safety_failure = $false
        }
    }
    try {
        $payload = $Body | ConvertFrom-Json -ErrorAction Stop
        if (-not $payload -or -not $payload.PSObject.Properties) { throw "NOT_OBJECT" }
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
        if ($bodyBytes.Length -gt 65536) { throw "BODY_TOO_LARGE" }
        $bodyDigest = Get-Sha256BytesHex -Bytes $bodyBytes
        $machineReason = ""
        foreach ($field in @("error_code", "code", "reason")) {
            if ($payload.PSObject.Properties[$field] -and
                $payload.$field -is [string] -and
                [string]$payload.$field -match '^[A-Z][A-Z0-9_]{2,127}$') {
                $machineReason = [string]$payload.$field
                break
            }
        }
        if ([string]::IsNullOrWhiteSpace($machineReason)) {
            throw "MACHINE_REASON_REQUIRED"
        }
        $material = @(
            "release-debt-fingerprint-v1", $basePath, [string]$StatusCode,
            $Resource, $FailureStage, $bodyDigest
        ) -join "`n"
        $digest = Get-Sha256BytesHex -Bytes `
            ([System.Text.Encoding]::UTF8.GetBytes($material))
        $hardReason = $machineReason -match `
            '(AUTH|UNAUTHORIZED|FORBIDDEN|IDENTITY|INTEGRITY|CORRUPT|INVARIANT|SCHEMA|CAPABILITY|MIGRATION|RECEIPT)'
        $hardStage = $FailureStage -in @(
            "authorization", "release_validation_identity", "json_validation"
        )
        return [pscustomobject]@{
            available = $true; digest = $digest
            machine_reason = $machineReason
            hard_safety_failure = [bool]($hardReason -or $hardStage)
        }
    } catch {
        return [pscustomobject]@{
            available = $false; digest = $null; machine_reason = $null
            hard_safety_failure = $false
        }
    }
}

function Get-ExactVersionJsonObservation {
    param(
        [Parameter(Mandatory = $true)][string]$VersionId,
        [Parameter(Mandatory = $true)][string]$GitSha,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowLegacyIdentity
    )
    try {
        $read = Invoke-ExactVersionJson -VersionId $VersionId -Path $Path
        $identityPassed = [bool](
            ([string]$read.observed_version_id -eq $VersionId -or
             ($AllowLegacyIdentity -and
              [string]::IsNullOrWhiteSpace([string]$read.observed_version_id))) -and
            ([string]$read.observed_git_sha -eq $GitSha -or
             ($AllowLegacyIdentity -and
              [string]::IsNullOrWhiteSpace([string]$read.observed_git_sha)))
        )
        return [pscustomobject]@{
            passed = $true
            identity_passed = $identityPassed
            failure_class = $null
            failure_fingerprint = $null
            failure_fingerprint_available = $false
            hard_safety_failure = $false
            payload = $read.payload
            observed_version_id = [string]$read.observed_version_id
            observed_git_sha = [string]$read.observed_git_sha
        }
    } catch {
        $statusCode = 0
        $observedVersion = ""
        $observedGit = ""
        $observedRoute = ""
        $resource = ""
        $failureStage = ""
        $contentType = ""
        $body = $null
        try {
            $response = $_.Exception.Response
            if ($response) {
                $statusCode = [int]$response.StatusCode
                $observedVersion = Get-ReleaseResponseHeaderValue $response `
                    "X-Aurum-Worker-Version"
                $observedGit = Get-ReleaseResponseHeaderValue $response "X-Aurum-Git-SHA"
                $observedRoute = Get-ReleaseResponseHeaderValue $response "X-Aurum-Route"
                $resource = Get-ReleaseResponseHeaderValue $response "X-Aurum-Resource"
                $failureStage = Get-ReleaseResponseHeaderValue $response `
                    "X-Aurum-Failure-Stage"
                $contentType = Get-ReleaseResponseHeaderValue $response "Content-Type"
                $body = Get-BoundedReleaseErrorBody $response
            }
        } catch {}
        $fingerprint = Get-ReleaseFailureFingerprint -ExpectedPath $Path `
            -StatusCode $statusCode -ObservedRoute $observedRoute -Resource $resource `
            -FailureStage $failureStage -ContentType $contentType -Body $body
        $identityPassed = [bool](
            ([string]$observedVersion -eq $VersionId -or
             ($AllowLegacyIdentity -and [string]::IsNullOrWhiteSpace($observedVersion))) -and
            ([string]$observedGit -eq $GitSha -or
             ($AllowLegacyIdentity -and [string]::IsNullOrWhiteSpace($observedGit)))
        )
        return [pscustomobject]@{
            passed = $false
            identity_passed = $identityPassed
            failure_class = if ($statusCode -gt 0) { "HTTP_$statusCode" } `
                else { "EXACT_VERSION_READ_FAILED" }
            failure_fingerprint = [string]$fingerprint.digest
            failure_fingerprint_available = [bool]$fingerprint.available
            failure_reason_code = [string]$fingerprint.machine_reason
            failure_stage = $failureStage
            hard_safety_failure = [bool]$fingerprint.hard_safety_failure
            payload = $null
            observed_version_id = $observedVersion
            observed_git_sha = $observedGit
            diagnostic = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Get-CandidateParityClass {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$RoutePlan
    )
    $basePath = ([string]$Path -split '\?', 2)[0]
    if ($basePath -eq "/api/status") { return "A" }
    if (@($RoutePlan.contract_routes | Where-Object {
        [string]$_.path -eq $basePath
    }).Count -gt 0) { return "B" }
    return "C"
}

function Test-CandidateAuthBoundaryChanged {
    param([Parameter(Mandatory = $true)][object]$RoutePlan)
    return [bool](@($RoutePlan.contract_routes | Where-Object {
        [bool]$_.auth_required
    }).Count -gt 0)
}

function Wait-CandidatePlacementPropagation {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $deadline = [DateTimeOffset]::UtcNow + $candidatePlacementPropagationTimeout
    do {
        try {
            $read = Invoke-ExactVersionJson `
                -VersionId ([string]$Candidate.worker_version_id) -Path "/api/ingest"
            if ([string]$read.observed_version_id -eq
                    [string]$Candidate.worker_version_id -and
                [string]$read.observed_git_sha -eq [string]$Candidate.git_sha) {
                return [pscustomobject]@{
                    passed = $true; state = "PASSED"; reason = "PASSED"
                    observed_version_id = [string]$read.observed_version_id
                    observed_git_sha = [string]$read.observed_git_sha
                }
            }
        } catch {
            $lastError = Protect-PreflightDiagnosticText $_.Exception.Message
        }
        if ([DateTimeOffset]::UtcNow -lt $deadline) {
            Start-Sleep -Seconds $candidatePlacementProbeIntervalSeconds
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    return [pscustomobject]@{
        passed = $false; state = "RETRYABLE"
        reason = "CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
        diagnostic = $lastError
    }
}

function ConvertTo-ReleaseSemanticProjection {
    param([Parameter(Mandatory = $true)][string]$Path, [object]$Payload)
    switch ($Path) {
        "/api/status" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                forward_epoch = $Payload.forward_epoch
                counts = $Payload.counts
                latest = $Payload.latest
                training = $Payload.training
            }
        }
        "/api/audit" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                news_metrics = $Payload.news_metrics
                daily_news_brief_summary = $Payload.daily_news_brief_summary
                storyline_summary = $Payload.storyline_summary
            }
        }
        "/api/learning" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                training = $Payload.training
                learning_curves = $Payload.learning_curves
            }
        }
        "/api/market-chart" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                decisions = $Payload.decisions
                training_markers = $Payload.training_markers
            }
        }
        default { return $Payload }
    }
}

function Test-ReleaseJsonProperty {
    param([object]$Object, [Parameter(Mandatory = $true)][string]$Name)
    return [bool]($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name])
}

function ConvertTo-RequiredReleaseTime {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    )) {
        throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
    }
    return $parsed
}

function Get-ReleaseDatasetCount {
    param([Parameter(Mandatory = $true)][string]$Path, [object]$Payload)
    $properties = switch -Wildcard ($Path) {
        "/api/audit-briefs*" { @("daily_news_briefs") }
        "/api/audit-stories*" { @("storylines", "market_narrative_candidates", "story_event_candidates") }
        "/api/audit-decisions*" { @("recent_decisions", "predictions") }
        "/api/learning*" { @("learning_curves", "models") }
        "/api/market-chart*" { @("decisions", "points") }
        "/api/market-history*" { @("items", "points", "decisions") }
        "/api/news-index*" { @("items", "articles") }
        "/api/news-evidence*" { @("items", "news_evidence") }
        default { @() }
    }
    $count = 0
    foreach ($name in $properties) {
        if (Test-ReleaseJsonProperty -Object $Payload -Name $name) {
            $count += @($Payload.$name).Count
        }
    }
    return $count
}

function Test-CandidateStatusPayload {
    param([object]$StablePayload, [object]$CandidatePayload)
    try {
        foreach ($payload in @($StablePayload, $CandidatePayload)) {
            foreach ($name in @("generated_at", "forward_epoch", "counts", "latest", "system")) {
                if (-not (Test-ReleaseJsonProperty -Object $payload -Name $name)) {
                    throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
                }
            }
            if (-not (Test-ReleaseJsonProperty -Object $payload.counts -Name "decision_events") -or
                -not (Test-ReleaseJsonProperty -Object $payload.latest -Name "decision_time") -or
                -not (Test-ReleaseJsonProperty -Object $payload.system -Name "market_session")) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
            if ($null -eq $payload.counts.decision_events -or
                -not ([string]$payload.system.market_session -in
                    @("OPEN", "CLOSED", "WEEKLY_CLOSED", "DATA_UNAVAILABLE"))) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
        }
        if ([string]::IsNullOrWhiteSpace([string]$StablePayload.forward_epoch) -or
            [string]::IsNullOrWhiteSpace([string]$CandidatePayload.forward_epoch)) {
            throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
        }
        if ([string]$StablePayload.forward_epoch -ne [string]$CandidatePayload.forward_epoch) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_STATUS_SCHEMA_MISMATCH" }
        }
        $stableGenerated = ConvertTo-RequiredReleaseTime $StablePayload.generated_at
        $candidateGenerated = ConvertTo-RequiredReleaseTime $CandidatePayload.generated_at
        $stableDecision = ConvertTo-RequiredReleaseTime $StablePayload.latest.decision_time
        $candidateDecision = ConvertTo-RequiredReleaseTime $CandidatePayload.latest.decision_time
        $stableCount = [long]$StablePayload.counts.decision_events
        $candidateCount = [long]$CandidatePayload.counts.decision_events
        if ($candidateCount -lt $stableCount) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_COUNT_REGRESSION" }
        }
        if (($stableGenerated - $candidateGenerated).TotalSeconds -gt 420) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_STATUS_STALE" }
        }
        if (($stableDecision - $candidateDecision).TotalSeconds -gt 420) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_DECISION_BEHIND_STABLE" }
        }
        $stableSession = [string]$StablePayload.system.market_session
        $candidateSession = [string]$CandidatePayload.system.market_session
        if ($stableSession -eq "OPEN" -and $candidateSession -ne "OPEN") {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_QUOTE_STALE" }
        }
        if ($candidateSession -eq "OPEN") {
            if (-not (Test-ReleaseJsonProperty -Object $CandidatePayload.system -Name "quote_age_seconds") -or
                $null -eq $CandidatePayload.system.quote_age_seconds) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
            $quoteAge = 0.0
            if (-not [double]::TryParse(
                [string]$CandidatePayload.system.quote_age_seconds,
                [Globalization.NumberStyles]::Float,
                [Globalization.CultureInfo]::InvariantCulture,
                [ref]$quoteAge
            ) -or $quoteAge -lt 0) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
            if ($quoteAge -gt 75) {
                return [pscustomobject]@{ passed=$false; reason="CANDIDATE_QUOTE_STALE" }
            }
        }
        return [pscustomobject]@{ passed=$true; reason="PASSED" }
    } catch {
        return [pscustomobject]@{
            passed=$false
            reason=if ($_.Exception.Message -eq "CANDIDATE_STATUS_SCHEMA_MISMATCH") {
                "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            } else { "CANDIDATE_STATUS_SCHEMA_MISMATCH" }
        }
    }
}

function Test-CandidateDataParity {
    param([Parameter(Mandatory = $true)][object]$Stable,
          [Parameter(Mandatory = $true)][object]$Candidate,
          [object]$RoutePlan = ([pscustomobject]@{ contract_routes = @() }))
    $routes = @(
        "/api/status", "/api/audit", "/api/audit-briefs",
        "/api/audit-stories", "/api/audit-decisions", "/api/learning",
        "/api/market-chart", "/api/market-history?limit=20",
        "/api/news-index?page=1&limit=20",
        "/api/news-evidence?mode=all&page=1&limit=20"
    )
    $legacyMode = [string]$Stable.artifact_kind -eq $legacyBootstrapStableArtifactKind
    $identityMode = if ($legacyMode) {
        "LEGACY_BOOTSTRAP_STABLE_COMPAT"
    } else { "EXACT_VERSION" }
    if ($legacyMode) {
        try { $deployment = Get-CloudflareDeployment } catch { $deployment = $null }
        $stablePlacement = @($deployment.versions | Where-Object {
            [string]$_.version_id -eq [string]$Stable.worker_version_id -and
            [double]$_.percentage -eq 100
        })
        $candidatePlacement = @($deployment.versions | Where-Object {
            [string]$_.version_id -eq [string]$Candidate.worker_version_id -and
            [double]$_.percentage -eq 0
        })
        $runtime = Get-RuntimeCodeState
        $legacyEvidencePassed = [bool](
            $stablePlacement.Count -eq 1 -and
            $candidatePlacement.Count -eq 1 -and
            [string]$Stable.git_sha -match '^[0-9a-f]{40}$' -and
            [string]$Stable.windows_revision -eq [string]$Stable.git_sha -and
            $runtime -and
            [string]$runtime.applied_revision -eq [string]$Stable.windows_revision
        )
        if (-not $legacyEvidencePassed) {
            return [pscustomobject]@{
                state = "FAILED"; passed = $false; identity_mode = $identityMode
                reason = "LEGACY_STABLE_DEPLOYMENT_EVIDENCE_UNPROVEN"
                stable_version_id = [string]$Stable.worker_version_id
                candidate_version_id = [string]$Candidate.worker_version_id
                routes = @()
            }
        }
    }
    $results = @()
    $legacyAuditTime = $null
    foreach ($path in $routes) {
        $acceptanceClass = Get-CandidateParityClass -Path $path `
            -RoutePlan $RoutePlan
        if ($legacyMode -and $path -in @(
            "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions"
        )) {
            try {
                $candidateRead = Invoke-ExactVersionJson `
                    -VersionId ([string]$Candidate.worker_version_id) -Path $path
                if ([string]$candidateRead.observed_version_id -ne
                        [string]$Candidate.worker_version_id -or
                    [string]::IsNullOrWhiteSpace([string]$candidateRead.observed_git_sha) -or
                    [string]$candidateRead.observed_git_sha -ne [string]$Candidate.git_sha) {
                    throw "EXACT_VERSION_IDENTITY_MISMATCH"
                }
                $payload = $candidateRead.payload
                $generated = ConvertTo-RequiredReleaseTime $payload.generated_at
                $knownFields = switch ($path) {
                    "/api/audit-briefs" { @("daily_news_briefs") }
                    "/api/audit-stories" { @("storylines", "market_narrative_candidates", "story_event_candidates") }
                    default { @("recent_decisions", "predictions") }
                }
                if (@($knownFields | Where-Object {
                    Test-ReleaseJsonProperty -Object $payload -Name $_
                }).Count -eq 0) {
                    throw "LEGACY_AUDIT_SPLIT_SCHEMA_MISMATCH"
                }
                if ($null -eq $legacyAuditTime) {
                    throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
                }
                # The legacy Windows producer cannot own these resources even
                # when a retained D1 snapshot happens to be recent.
                $deferred = $true
                $results += [pscustomobject]@{
                    route = $path
                    acceptance_class = $acceptanceClass
                    state = if ($deferred) {
                        "DEFERRED_TO_POST_CUTOVER_OBSERVATION"
                    } else { "PASSED" }
                    passed = -not $deferred
                    blocking = $false
                    reason = if ($deferred) {
                        "CANDIDATE_PROJECTION_PRODUCER_NOT_ACTIVE"
                    } else { "PASSED" }
                    required_producer_revision = [string]$Candidate.windows_revision
                    validation_key = [string]$Candidate.validation_key
                    observed_generated_at = $generated.ToString("o")
                    authority_generated_at = $legacyAuditTime.ToString("o")
                    stable_version_id = [string]$Stable.worker_version_id
                    candidate_version_id = [string]$candidateRead.observed_version_id
                }
            } catch {
                $reason = if ($_.Exception.Message -in @(
                    "EXACT_VERSION_IDENTITY_MISMATCH", "CANDIDATE_AUDIT_TRANSITION_STALE",
                    "LEGACY_AUDIT_SPLIT_SCHEMA_MISMATCH"
                )) { $_.Exception.Message } else { "EXACT_VERSION_READ_FAILED" }
                $results += [pscustomobject]@{
                    route = $path; acceptance_class = $acceptanceClass
                    state = "FAILED"; passed = $false; reason = $reason
                    error = Protect-PreflightDiagnosticText $_.Exception.Message
                    stable_version_id = [string]$Stable.worker_version_id
                    candidate_version_id = [string]$Candidate.worker_version_id
                }
            }
            continue
        }
        $stableRead = Get-ExactVersionJsonObservation `
            -VersionId ([string]$Stable.worker_version_id) `
            -GitSha ([string]$Stable.git_sha) -Path $path `
            -AllowLegacyIdentity:$legacyMode
        $candidateRead = Get-ExactVersionJsonObservation `
            -VersionId ([string]$Candidate.worker_version_id) `
            -GitSha ([string]$Candidate.git_sha) -Path $path
        if (-not $candidateRead.identity_passed -or
            (-not $legacyMode -and -not $stableRead.identity_passed)) {
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = "FAILED"; passed = $false; blocking = $true
                reason = "EXACT_VERSION_IDENTITY_MISMATCH"
                stable_failure = [string]$stableRead.failure_class
                candidate_failure = [string]$candidateRead.failure_class
            }
            continue
        }
        if (-not $stableRead.passed -or -not $candidateRead.passed) {
            $equivalentDebt = [bool](
                $acceptanceClass -eq "C" -and -not $stableRead.passed -and
                -not $candidateRead.passed -and
                -not [bool]$candidateRead.hard_safety_failure -and
                [bool]$stableRead.failure_fingerprint_available -and
                [bool]$candidateRead.failure_fingerprint_available -and
                [string]$stableRead.failure_fingerprint -ceq
                    [string]$candidateRead.failure_fingerprint
            )
            $matchingDebt = [bool](
                $acceptanceClass -eq "C" -and -not $stableRead.passed -and (
                    $candidateRead.passed -or
                    $equivalentDebt
                )
            )
            $failureReason = if ($matchingDebt) {
                if ($candidateRead.passed) { "CANDIDATE_IMPROVES_STABLE_DEBT" }
                else { "UNCHANGED_EXISTING_STABLE_DEBT" }
            } elseif ([bool]$candidateRead.hard_safety_failure) {
                "CANDIDATE_HARD_SAFETY_FAILURE"
            } elseif ($acceptanceClass -eq "B") {
                "CHANGED_BOUNDARY_FAILURE"
            } elseif ($acceptanceClass -eq "C" -and $stableRead.passed) {
                "CANDIDATE_REGRESSION"
            } elseif ($acceptanceClass -eq "C" -and -not $stableRead.passed -and
                -not $candidateRead.passed) {
                "CANDIDATE_DEBT_EQUIVALENCE_UNPROVEN"
            } else { "EXACT_VERSION_READ_FAILED" }
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = if ($matchingDebt) {
                    if ($candidateRead.passed) { "STABLE_DEBT_IMPROVED" }
                    else { "EXISTING_STABLE_DEBT" }
                } else { "FAILED" }
                passed = $matchingDebt
                blocking = -not $matchingDebt
                reason = $failureReason
                stable_failure = [string]$stableRead.failure_class
                candidate_failure = [string]$candidateRead.failure_class
                stable_failure_fingerprint = [string]$stableRead.failure_fingerprint
                candidate_failure_fingerprint = [string]$candidateRead.failure_fingerprint
                stable_failure_reason_code = [string]$stableRead.failure_reason_code
                candidate_failure_reason_code = [string]$candidateRead.failure_reason_code
                stable_diagnostic = [string]$stableRead.diagnostic
                candidate_diagnostic = [string]$candidateRead.diagnostic
            }
            continue
        }
        try {
            $stablePayload = $stableRead.payload
            $candidatePayload = $candidateRead.payload
            $stableProjection = ConvertTo-ReleaseSemanticProjection -Path $path -Payload $stablePayload
            $candidateProjection = ConvertTo-ReleaseSemanticProjection -Path $path -Payload $candidatePayload
            $passed = [bool]((@($stableProjection.Keys) -join ",") -ceq
                (@($candidateProjection.Keys) -join ","))
            $reason = if ($passed) { "PASSED" } else { "CANDIDATE_DATA_PARITY_FAILED" }
            if ($path -eq "/api/status") {
                $statusResult = Test-CandidateStatusPayload -StablePayload $stablePayload `
                    -CandidatePayload $candidatePayload
                $passed = [bool]$statusResult.passed; $reason = [string]$statusResult.reason
            }
            if ($path -eq "/api/audit") {
                try {
                    $stableAuditTime = ConvertTo-RequiredReleaseTime $stablePayload.generated_at
                    $candidateAuditTime = ConvertTo-RequiredReleaseTime $candidatePayload.generated_at
                    if (($stableAuditTime - $candidateAuditTime).TotalMinutes -gt 15) {
                        $passed = $false; $reason = "CANDIDATE_AUDIT_TRANSITION_STALE"
                    }
                    if ($legacyMode) { $legacyAuditTime = $stableAuditTime }
                } catch {
                    $passed = $false; $reason = "CANDIDATE_STATUS_SCHEMA_MISMATCH"
                }
            }
            if ($path -notin @("/api/status", "/api/audit")) {
                $stableCount = Get-ReleaseDatasetCount -Path $path -Payload $stablePayload
                $candidateCount = Get-ReleaseDatasetCount -Path $path -Payload $candidatePayload
                if ($stableCount -gt 0 -and $candidateCount -eq 0) {
                    $passed = $false; $reason = "CANDIDATE_DATASET_UNEXPECTEDLY_EMPTY"
                }
            }
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = if ($passed) { "PASSED" } else { "FAILED" }
                passed = $passed; blocking = -not $passed; reason = $reason
                stable_version_id = if ($legacyMode) { [string]$Stable.worker_version_id } else { [string]$stableRead.observed_version_id }
                candidate_version_id = [string]$candidateRead.observed_version_id
            }
        } catch {
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = "FAILED"; passed = $false; blocking = $true
                reason = if ($_.Exception.Message -eq "EXACT_VERSION_IDENTITY_MISMATCH") {
                    "EXACT_VERSION_IDENTITY_MISMATCH"
                } else { "EXACT_VERSION_READ_FAILED" }
                error = Protect-PreflightDiagnosticText $_.Exception.Message
            }
        }
    }
    $deferred = @($results | Where-Object {
        [string]$_.state -eq "DEFERRED_TO_POST_CUTOVER_OBSERVATION"
    })
    $blocking = @($results | Where-Object {
        [bool]$_.blocking -or (-not $_.passed -and [string]$_.state -ne
            "DEFERRED_TO_POST_CUTOVER_OBSERVATION")
    })
    $stableDebt = @($results | Where-Object {
        [string]$_.state -in @("EXISTING_STABLE_DEBT", "STABLE_DEBT_IMPROVED")
    })
    return [pscustomobject]@{
        state = if ($blocking.Count -gt 0) { "FAILED" } elseif ($deferred.Count -gt 0) {
            "PASSED_WITH_DEFERRED_OBLIGATIONS"
        } else { "PASSED" }
        passed = [bool]($blocking.Count -eq 0)
        identity_mode = $identityMode
        stable_version_id = [string]$Stable.worker_version_id
        candidate_version_id = [string]$Candidate.worker_version_id
        routes = $results
        stable_debt = $stableDebt
        deferred_obligations = @($deferred | ForEach-Object {
            [pscustomobject]@{
                route = [string]$_.route
                state = [string]$_.state
                validation_key = [string]$_.validation_key
                required_producer_revision = [string]$_.required_producer_revision
                authority_generated_at = [string]$_.authority_generated_at
            }
        })
    }
}

function Get-CandidateAuthInspection {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    # workers.dev version URLs are not the Access-protected production host.
    # They may prove application behavior, never a successful human login.
    $result = [ordered]@{
        state = "AUTH_BOUNDARY_NOT_TESTABLE"
        version_id = [string]$Candidate.worker_version_id
        versioned_workers_dev = "UNPROTECTED_TEST_SURFACE"
        production_host_probe = "NOT_OBSERVED"
    }
    try {
        $headers = @{
            "Cloudflare-Workers-Version-Overrides" =
                "$workerName=`"$([string]$Candidate.worker_version_id)`""
        }
        $response = Invoke-WebRequest -UseBasicParsing -Method Get `
            -Uri "$dashboardUrl/admin/api/session" -Headers $headers `
            -MaximumRedirection 0 -TimeoutSec 30
        $result.production_host_probe = "HTTP_$([int]$response.StatusCode)"
        if ([int]$response.StatusCode -in @(401, 403)) {
            $result.state = "UNAUTHENTICATED_BOUNDARY_CONFIRMED"
        }
    } catch {
        $status = if ($_.Exception.Response) {
            [int]$_.Exception.Response.StatusCode
        } else { 0 }
        $result.production_host_probe = if ($status) { "HTTP_$status" } `
            else { "PROBE_UNAVAILABLE" }
        if ($status -in @(401, 403)) {
            $result.state = "UNAUTHENTICATED_BOUNDARY_CONFIRMED"
        }
    }
    return [pscustomobject]$result
}

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
        $publishedAt = [DateTimeOffset]::MinValue
        $publishedValid = [DateTimeOffset]::TryParse(
            [string]$health.latest_published_at, [ref]$publishedAt
        )
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
                    $receipt = Assert-CoordinatedMigrationReceipt `
                        -Candidate $Candidate -Stable $state.stable `
                        -MigrationFiles @($compatibility.files)
                    if ([string]$receipt.receipt_digest -ne
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
        $workerChanged = [bool]$routePlan.worker_cpu_required
        $cloudflareChanged = [bool]$routePlan.requires_validation
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
            $cloudflare = Invoke-CandidateWorkerValidation -Candidate $Candidate `
                -RoutePlan $routePlan
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
                $telemetryPending = [bool](
                    [string]$cloudflare.observability_diagnostic -eq
                        "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"
                )
                if ($telemetryPending) {
                    $state.candidate.validation_state = "PLATFORM_PENDING"
                }
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = if ($telemetryPending) { "PENDING" } else { "TESTING" }
                    reason = if ($telemetryPending) {
                        "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"
                    } else { "PLATFORM_CPU_EVIDENCE_REQUIRED" }
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan
                    routes = $cloudflare.routes
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    observability_credential_source = $cloudflare.observability_credential_source
                    observability_diagnostic = $cloudflare.observability_diagnostic
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "DIAGNOSTIC_UNAVAILABLE" }
                    worker_failures = [pscustomobject]@{ state = "DIAGNOSTIC_UNAVAILABLE" }
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                if ($telemetryPending) {
                    Write-ReleaseHistory -Event "CANDIDATE_PLATFORM_PENDING" `
                        -Release $state.candidate -Detail @{
                            reason = "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"
                            retryable = $true
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
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
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
    $versions = @(Get-CloudflareVersions | Sort-Object `
        @{ Expression = { Get-ReleaseVersionCreatedAtValue -Version $_ } }, `
        @{ Expression = { [string]$_.id } })
    if (@($versions).Count -eq 0) {
        Set-CandidateMaterializationState -State $state -Revision $mainRevision `
            -Status "PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        return $null
    }
    if (-not $state.candidate_discovery.initialized_at) {
        Set-CandidateDiscoveryWatermark -State $state -Version ($versions | Select-Object -Last 1)
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_DISCOVERY_INITIALIZED" -Release $null `
            -Detail @{
                watermark_version_id = [string]$state.candidate_discovery.watermark_version_id
                historical_versions_eligible = $false
            }
        return $null
    }
    $newVersions = @($versions | Where-Object {
        Test-VersionAfterDiscoveryWatermark -Version $_ -Discovery $state.candidate_discovery
    })
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
    $priorTestedAt = [string]$candidate.validation.tested_at
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
    $lastCheck = [DateTimeOffset]::MinValue
    if ($state.last_candidate_check) {
        [DateTimeOffset]::TryParse([string]$state.last_candidate_check, [ref]$lastCheck) | Out-Null
    }
    if (([DateTimeOffset]::UtcNow - $lastCheck) -lt $candidateDiscoveryInterval) { return }
    $arguments = @(
        "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath), "-Action", "DiscoverCandidate",
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

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString(
            $algorithm.ComputeHash($stream)
        ) -replace "-", "").ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Get-RuntimeControlBundleIdentityAtRoot {
    param([Parameter(Mandatory = $true)][string]$ControlRoot)
    $path = Join-Path $ControlRoot $runtimeControlManifestName
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $identity = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if (-not [bool]$identity.exact_revision -or
            [string]$identity.source_revision -notmatch '^[0-9a-f]{40}$') {
            return $null
        }
        foreach ($name in $runtimeControlFileNames) {
            $file = Join-Path $ControlRoot $name
            $expected = [string]$identity.files.$name
            if (-not (Test-Path -LiteralPath $file) -or
                $expected -notmatch '^[0-9a-f]{64}$') { return $null }
            $actual = Get-Sha256Hex -LiteralPath $file
            if ($actual -ne $expected) { return $null }
        }
        return $identity
    } catch { return $null }
}

function New-VerifiedRuntimeControlBundleStage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$SourceRevision,
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [switch]$RequireImmutableSource
    )
    if ($SourceRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
    $revisionOutput = @(& git -C $SourceRoot rev-parse HEAD 2>$null)
    $revisionExitCode = $LASTEXITCODE
    $observedRevision = if ($revisionOutput.Count -gt 0) {
        ([string]$revisionOutput[0]).Trim()
    } else { "" }
    if ($revisionExitCode -ne 0 -or $observedRevision -ne $SourceRevision) {
        throw "CONTROL_BUNDLE_SOURCE_REVISION_MISMATCH"
    }
    if ($RequireImmutableSource) {
        $dirty = @(& git -C $SourceRoot status --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
            throw "CONTROL_BUNDLE_IMMUTABLE_SOURCE_REQUIRED"
        }
        & git -C $SourceRoot symbolic-ref -q HEAD 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            throw "CONTROL_BUNDLE_DETACHED_SOURCE_REQUIRED"
        }
    }
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
    foreach ($name in $runtimeControlFileNames) {
        $source = Join-Path $SourceRoot ("scripts\{0}" -f $name)
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Missing runtime control file: $source"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $StageRoot $name) -Force
    }
    $hashes = @{}
    foreach ($name in $runtimeControlFileNames) {
        $hashes[$name] = Get-Sha256Hex -LiteralPath (Join-Path $StageRoot $name)
    }
    [pscustomobject]@{
        schema_version = 1
        source_revision = $SourceRevision
        exact_revision = $true
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
        files = $hashes
    } | ConvertTo-Json -Depth 5 | Set-Content `
        -LiteralPath (Join-Path $StageRoot $runtimeControlManifestName) -Encoding UTF8
    $identity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $StageRoot
    if (-not $identity -or [string]$identity.source_revision -ne $SourceRevision) {
        throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED"
    }
    return $identity
}

function Install-VerifiedRuntimeControlBundleStage {
    param(
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [Parameter(Mandatory = $true)][string]$ControlRoot,
        [Parameter(Mandatory = $true)][string]$BackupRoot
    )
    $stagedIdentity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $StageRoot
    if (-not $stagedIdentity) { throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED" }
    New-Item -ItemType Directory -Path $ControlRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $payloadNames = @($runtimeControlFileNames) + @($runtimeControlManifestName)
    foreach ($name in $payloadNames) {
        $destination = Join-Path $ControlRoot $name
        if (Test-Path -LiteralPath $destination) {
            Copy-Item -LiteralPath $destination -Destination (Join-Path $BackupRoot $name) -Force
        }
    }
    try {
        foreach ($name in $payloadNames) {
            Copy-Item -LiteralPath (Join-Path $StageRoot $name) `
                -Destination (Join-Path $ControlRoot $name) -Force
        }
        $installed = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $ControlRoot
        if (-not $installed -or
            [string]$installed.source_revision -ne [string]$stagedIdentity.source_revision) {
            throw "CONTROL_BUNDLE_INSTALLED_HASH_VERIFICATION_FAILED"
        }
        return $installed
    } catch {
        Restore-RuntimeControlBundleBackup -BackupRoot $BackupRoot -ControlRoot $ControlRoot
        throw
    }
}

function Restore-RuntimeControlBundleBackup {
    param(
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)][string]$ControlRoot
    )
    $backupIdentity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $BackupRoot
    if (-not $backupIdentity) { throw "CONTROL_BUNDLE_BACKUP_VERIFICATION_FAILED" }
    foreach ($name in @($runtimeControlFileNames) + @($runtimeControlManifestName)) {
        Copy-Item -LiteralPath (Join-Path $BackupRoot $name) `
            -Destination (Join-Path $ControlRoot $name) -Force
    }
    $restored = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $ControlRoot
    if (-not $restored -or
        [string]$restored.source_revision -ne [string]$backupIdentity.source_revision) {
        throw "CONTROL_BUNDLE_ROLLBACK_VERIFICATION_FAILED"
    }
    return $restored
}

function Sync-StableRuntimeControlFiles {
    param(
        [string]$SourceRoot = $moduleRoot,
        [string]$ControlRoot = (Join-Path $repositoryRoot ".local\runtime-control"),
        [string]$SourceRevision = ""
    )
    # Keep transactional paths beside the bundle and short enough for Windows
    # PowerShell/.NET installations that still enforce legacy MAX_PATH limits.
    $controlParent = Split-Path -Parent $ControlRoot
    $transactionId = [guid]::NewGuid().ToString("N")
    $stageRoot = Join-Path $controlParent (".rcs-{0}" -f $transactionId)
    $backupRoot = Join-Path $controlParent (".rcb-{0}" -f $transactionId)
    try {
        if (-not $SourceRevision) {
            $SourceRevision = (& git -C $SourceRoot rev-parse HEAD 2>$null | Select-Object -First 1)
        }
        $null = New-VerifiedRuntimeControlBundleStage -SourceRoot $SourceRoot `
            -SourceRevision $SourceRevision -StageRoot $stageRoot
        $null = Install-VerifiedRuntimeControlBundleStage -StageRoot $stageRoot `
            -ControlRoot $ControlRoot -BackupRoot $backupRoot
    } finally {
        if (Test-Path -LiteralPath $stageRoot) {
            Remove-Item -LiteralPath $stageRoot -Recurse -Force
        }
        if (Test-Path -LiteralPath $backupRoot) {
            Remove-Item -LiteralPath $backupRoot -Recurse -Force
        }
    }
}

function Get-AvailableLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback, 0
    )
    try {
        $listener.Start()
        return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    } finally {
        $listener.Stop()
    }
}

function Copy-CandidatePreflightDatabase {
    param(
        [string]$Python,
        [string]$SourceDatabase,
        [string]$TargetDatabase
    )
    $copy = @'
import sqlite3
import sys
from pathlib import Path

source_path = Path(sys.argv[1]).resolve()
target_path = Path(sys.argv[2]).resolve()
target_path.parent.mkdir(parents=True, exist_ok=True)
if target_path.exists():
    target_path.unlink()
source = sqlite3.connect(source_path.as_uri() + '?mode=ro', uri=True)
destination = sqlite3.connect(target_path)
try:
    source.backup(destination)
finally:
    destination.close()
    source.close()
'@
    $result = & $Python -c $copy $SourceDatabase $TargetDatabase 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "candidate evidence copy failed: $result"
    }
}

function Migrate-CandidatePreflightDatabase {
    param(
        [string]$Python,
        [string]$StageRoot,
        [string]$TargetDatabase
    )
    $migration = @'
import sys
from pathlib import Path

stage_root = Path(sys.argv[1]).resolve()
target_path = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(stage_root))
from xauusd_forecaster.forward_ledger import ForwardLedger
ledger = ForwardLedger(target_path)
ledger.close()
'@
    $result = & $Python -c $migration $StageRoot $TargetDatabase 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "candidate evidence migration failed: $result"
    }
}

function New-CandidatePreflightDatabase {
    param(
        [string]$Python,
        [string]$StageRoot,
        [string]$SourceDatabase,
        [string]$TargetDatabase
    )
    Copy-CandidatePreflightDatabase -Python $Python `
        -SourceDatabase $SourceDatabase -TargetDatabase $TargetDatabase
    Migrate-CandidatePreflightDatabase -Python $Python -StageRoot $StageRoot `
        -TargetDatabase $TargetDatabase
}

function Copy-CandidatePreflightState {
    param(
        [string]$SourceDatabase,
        [string]$TargetDatabase
    )
    $sourceRoot = Split-Path -Parent $SourceDatabase
    $targetRoot = Split-Path -Parent $TargetDatabase
    foreach ($name in @(
        "dashboard-sync-status.json", "news-annotator-status.json"
    )) {
        $source = Join-Path $sourceRoot $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination (Join-Path $targetRoot $name) -Force
        }
    }
    foreach ($pattern in @(
        "dashboard-news-sync-state*.json",
        "dashboard-learning-sync-state*.json",
        "dashboard-learning-history-sync-state*.json",
        "dashboard-market-history-sync-state*.json"
    )) {
        Get-ChildItem -LiteralPath $sourceRoot -Filter $pattern -File `
            -ErrorAction SilentlyContinue | ForEach-Object {
                Copy-Item -LiteralPath $_.FullName `
                    -Destination (Join-Path $targetRoot $_.Name) -Force
            }
    }
    $marketSession = Join-Path $sourceRoot "quotes\market-session.json"
    if (Test-Path -LiteralPath $marketSession) {
        $targetQuotes = Join-Path $targetRoot "quotes"
        New-Item -ItemType Directory -Path $targetQuotes -Force | Out-Null
        Copy-Item -LiteralPath $marketSession `
            -Destination (Join-Path $targetQuotes "market-session.json") -Force
    }
}

function Protect-PreflightDiagnosticText {
    param([object]$Value, [int]$Limit = $preflightDiagnosticMaxCharacters)
    $text = [string]$Value
    if (-not $text) { return $null }
    $text = [regex]::Replace(
        $text, '(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+', 'Bearer [REDACTED]'
    )
    $text = [regex]::Replace(
        $text,
        '(?i)(["'']?\b(?:api[_-]?key|token|secret|password|authorization)\b["'']?\s*[:=]\s*)["'']?[^\s,;"'']+',
        '$1[REDACTED]'
    )
    $text = [regex]::Replace(
        $text, '(?i)(https://)[^/@\s]+@', '$1[REDACTED]@'
    )
    if ($text.Length -gt $Limit) {
        return "[TRUNCATED]" + $text.Substring($text.Length - $Limit)
    }
    return $text
}

function Get-PreflightLogTail {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $tail = (Get-Content -LiteralPath $Path -Tail 40 -ErrorAction Stop) -join "`n"
        return Protect-PreflightDiagnosticText $tail
    } catch { return $null }
}

function Invoke-CandidateStatusProbe {
    param([string]$Url, [int]$TimeoutSeconds = 20)
    $started = [Diagnostics.Stopwatch]::StartNew()
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url `
            -TimeoutSec $TimeoutSeconds
        $started.Stop()
        return [pscustomobject]@{
            ready = [int]$response.StatusCode -eq 200
            error_code = if ([int]$response.StatusCode -eq 200) {
                $null
            } else { "CRITICAL_STATUS_HTTP_ERROR" }
            http_status = [int]$response.StatusCode
            response_body = if ([int]$response.StatusCode -eq 200) {
                $null
            } else { Protect-PreflightDiagnosticText $response.Content }
            transport_error = $null
            elapsed_ms = [math]::Round($started.Elapsed.TotalMilliseconds, 1)
        }
    } catch {
        $started.Stop()
        $statusCode = $null
        try {
            if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
        } catch {}
        $message = Protect-PreflightDiagnosticText $_.Exception.Message
        $body = Protect-PreflightDiagnosticText $_.ErrorDetails.Message
        $timedOut = (
            [string]$_.Exception.Status -eq "Timeout" -or
            [string]$_.Exception.Message -match '(?i)timed?\s*out|timeout'
        )
        return [pscustomobject]@{
            ready = $false
            error_code = if ($null -ne $statusCode) {
                "CRITICAL_STATUS_HTTP_ERROR"
            } elseif ($timedOut) {
                "CRITICAL_STATUS_TIMEOUT"
            } else { "CRITICAL_STATUS_TRANSPORT_ERROR" }
            http_status = $statusCode
            response_body = $body
            transport_error = $message
            elapsed_ms = [math]::Round($started.Elapsed.TotalMilliseconds, 1)
        }
    }
}

function Wait-CandidateCriticalStatus {
    param(
        [object]$Process,
        [string]$Url,
        [DateTimeOffset]$Deadline
    )
    $started = [Diagnostics.Stopwatch]::StartNew()
    $lastProbe = $null
    do {
        Start-Sleep -Milliseconds 500
        try { $null = $Process.Refresh() } catch {}
        if ($Process.HasExited) {
            $started.Stop()
            return [pscustomobject]@{
                ready = $false
                error_code = "CANDIDATE_API_EXITED"
                process_exited = $true
                exit_code = $Process.ExitCode
                last_probe = $lastProbe
                elapsed_seconds = [math]::Round($started.Elapsed.TotalSeconds, 3)
            }
        }
        $lastProbe = Invoke-CandidateStatusProbe -Url $Url
        if ($lastProbe.ready) {
            $started.Stop()
            return [pscustomobject]@{
                ready = $true
                error_code = $null
                process_exited = $false
                exit_code = $null
                last_probe = $lastProbe
                elapsed_seconds = [math]::Round($started.Elapsed.TotalSeconds, 3)
            }
        }
    } while ([DateTimeOffset]::UtcNow -lt $Deadline)
    $started.Stop()
    return [pscustomobject]@{
        ready = $false
        error_code = if ($lastProbe -and $lastProbe.error_code) {
            [string]$lastProbe.error_code
        } else { "CRITICAL_STATUS_NOT_READY" }
        process_exited = $false
        exit_code = $null
        last_probe = $lastProbe
        elapsed_seconds = [math]::Round($started.Elapsed.TotalSeconds, 3)
    }
}

function Invoke-ProductionShapePreflight {
    param([string]$Revision)
    $preflightRoot = Join-Path $repositoryRoot ".local\runtime-preflight"
    $stageRoot = Join-Path $preflightRoot $Revision
    $database = Join-Path $moduleRoot ".local\forward\forward-evidence.sqlite3"
    $preflightPort = Get-AvailableLoopbackPort
    $process = $null
    $phase = "STAGE_WORKTREE"
    $failureCode = $null
    $readiness = $null
    $productionShapeOutput = $null
    $stdout = $null
    $stderr = $null
    $preflightStarted = [Diagnostics.Stopwatch]::StartNew()
    if (-not (Test-Path -LiteralPath $database)) {
        Write-RuntimeUpdateFailure -Revision $Revision -Status "PREFLIGHT_FAILED" `
            -Message "Candidate preflight failed in COPY_DATABASE (EVIDENCE_DATABASE_MISSING); current runtime retained." `
            -ErrorCode "EVIDENCE_DATABASE_MISSING" -Phase "COPY_DATABASE" `
            -Diagnostics @{ elapsed_seconds = 0; source_database_exists = $false }
        return $false
    }
    New-Item -ItemType Directory -Path $preflightRoot -Force | Out-Null
    try {
        if (Test-Path -LiteralPath $stageRoot) {
            & git -C $repositoryRoot worktree remove --force $stageRoot 2>$null
            if (Test-Path -LiteralPath $stageRoot) {
                throw "stale candidate worktree cannot be cleared"
            }
        }
        & git -C $repositoryRoot worktree add --detach --quiet $stageRoot $Revision 2>$null
        if ($LASTEXITCODE -ne 0) { throw "cannot stage candidate worktree" }
        $python = (Get-Command python.exe -ErrorAction Stop).Source
        $candidateDatabase = Join-Path $stageRoot ".local\preflight\forward-evidence.sqlite3"
        $phase = "COPY_DATABASE"
        Copy-CandidatePreflightDatabase -Python $python `
            -SourceDatabase $database -TargetDatabase $candidateDatabase
        $phase = "MIGRATE_DATABASE"
        Migrate-CandidatePreflightDatabase -Python $python -StageRoot $stageRoot `
            -TargetDatabase $candidateDatabase
        $phase = "COPY_STATE"
        Copy-CandidatePreflightState -SourceDatabase $database `
            -TargetDatabase $candidateDatabase
        $stdout = Join-Path $logRoot "runtime-preflight.stdout.log"
        $stderr = Join-Path $logRoot "runtime-preflight.stderr.log"
        New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
        Set-Content -LiteralPath $stdout -Value "" -Encoding UTF8
        Set-Content -LiteralPath $stderr -Value "" -Encoding UTF8
        $phase = "START_API"
        $process = Start-Process -FilePath $python -ArgumentList @(
            (Join-Path $stageRoot "scripts\run_dashboard_api.py"),
            "--database", $candidateDatabase, "--host", "127.0.0.1",
            "--port", [string]$preflightPort
        ) -WorkingDirectory $stageRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $statusUrl = "http://127.0.0.1:$preflightPort/api/critical-status"
        $phase = "WAIT_CRITICAL_STATUS"
        $readiness = Wait-CandidateCriticalStatus -Process $process `
            -Url $statusUrl -Deadline ([DateTimeOffset]::UtcNow.AddSeconds(60))
        if (-not $readiness.ready) {
            $failureCode = [string]$readiness.error_code
            throw "candidate critical status did not become ready"
        }
        $phase = "PRODUCTION_SHAPE"
        $arguments = @(
            (Join-Path $stageRoot "scripts\check_production_shape.py"),
            "--status-url", $statusUrl,
            "--allow-pending-generation-decision"
        )
        $result = & $python @arguments 2>&1
        if ($LASTEXITCODE -ne 0) {
            $failureCode = "PRODUCTION_SHAPE_REJECTED"
            $productionShapeOutput = Protect-PreflightDiagnosticText ($result -join "`n")
            throw "candidate production shape rejected"
        }
        $preflightStarted.Stop()
        Write-RuntimeUpdateState @{
            update_status = "PREFLIGHT_PASSED"
            preflight_revision = $Revision
            preflight_at = [DateTimeOffset]::UtcNow.ToString("o")
            user_visible_failure = $false
            failure_message = $null
            failed_revision = $null
            failed_at = $null
            failed_preflight_contract = $null
            failure_code = $null
            failure_phase = $null
            preflight_diagnostics = $null
        }
        Write-WatchdogEvent -Event "RUNTIME_PREFLIGHT_PASSED" `
            -Service "all" -State $Revision
        return $true
    } catch {
        $preflightStarted.Stop()
        $failureDetail = Protect-PreflightDiagnosticText $_.Exception.Message
        $processExited = $false
        $exitCode = $null
        if ($process) {
            try { $null = $process.Refresh() } catch {}
            $processExited = [bool]$process.HasExited
            if ($processExited) {
                try { $exitCode = [int]$process.ExitCode } catch {}
            } else {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                try { $process.WaitForExit(5000) | Out-Null } catch {}
            }
        }
        if (-not $failureCode) { $failureCode = "${phase}_FAILED" }
        $lastProbe = if ($readiness) { $readiness.last_probe } else { $null }
        $diagnostics = @{
            elapsed_seconds = [math]::Round($preflightStarted.Elapsed.TotalSeconds, 3)
            candidate_process_exited = $processExited
            candidate_exit_code = $exitCode
            last_http_status = if ($lastProbe) { $lastProbe.http_status } else { $null }
            last_http_body = if ($lastProbe) { $lastProbe.response_body } else { $null }
            last_transport_error = if ($lastProbe) { $lastProbe.transport_error } else { $null }
            last_probe_elapsed_ms = if ($lastProbe) { $lastProbe.elapsed_ms } else { $null }
            stdout_tail = Get-PreflightLogTail $stdout
            stderr_tail = Get-PreflightLogTail $stderr
            production_shape_output = $productionShapeOutput
            failure_detail = $failureDetail
        }
        Write-RuntimeUpdateFailure -Revision $Revision -Status "PREFLIGHT_FAILED" `
            -Message "Candidate preflight failed in $phase ($failureCode); current runtime retained." `
            -ErrorCode $failureCode -Phase $phase -Diagnostics $diagnostics
        return $false
    } finally {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
        if (Test-Path -LiteralPath $stageRoot) {
            & git -C $repositoryRoot worktree remove --force $stageRoot 2>$null
        }
        & git -C $repositoryRoot worktree prune 2>$null
    }
}

function Update-RuntimeCheckout {
    param([string]$Revision)
    if (-not $RuntimeRoot) { return $false }
    $previousRevision = Get-CodeRevision
    $releaseState = Get-ReleaseControlState
    if (-not $releaseState -or -not $releaseState.candidate -or
        [string]$releaseState.candidate.validation_state -ne "PASSED" -or
        [string]$releaseState.candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$releaseState.candidate.windows_revision -ne $Revision -or
        [string]$releaseState.candidate.validation.key -ne
            [string]$releaseState.candidate.validation_key) { return $false }
    if (-not (Invoke-ProductionShapePreflight -Revision $Revision)) { return $false }
    Write-RuntimeUpdateState @{
        update_status = "SWITCHING"
        previous_revision = $previousRevision
        staged_revision = $Revision
        staged_at = [DateTimeOffset]::UtcNow.ToString("o")
        user_visible_failure = $false
        failure_message = $null
    }
    $checkoutChanged = $false
    try {
        & git -C $moduleRoot checkout --detach --force --quiet $Revision 2>$null
        if ($LASTEXITCODE -ne 0) { throw "verified revision checkout failed" }
        $checkoutChanged = $true
        Write-RuntimeUpdateState @{
            previous_revision = $previousRevision
            staged_revision = $Revision
            staged_at = [DateTimeOffset]::UtcNow.ToString("o")
            update_status = "STAGED"
        }
        return $true
    } catch {
        $reason = $_.Exception.Message
        if ($checkoutChanged) {
            & git -C $moduleRoot checkout --detach --force --quiet $previousRevision 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-RuntimeUpdateFailure -Revision $Revision -Status "ROLLBACK_FAILED" `
                    -Message "Candidate switch preparation failed and the previous checkout could not be restored: $reason"
                return $false
            }
        }
        Write-RuntimeUpdateFailure -Revision $Revision -Status "SWITCH_FAILED" `
            -Message "Candidate switch failed before service reload; the current version is still running: $reason"
        return $false
    }
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
    param(
        [ValidateSet("ACTIVE", "QUIESCED")][string]$SupervisionMode = "ACTIVE",
        [string]$InstallTransactionId = ""
    )
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
        supervision_mode = $SupervisionMode
        install_transaction_id = if ($InstallTransactionId) {
            $InstallTransactionId
        } else { $null }
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

function Test-ControlPlaneServiceOwnerRequired {
    param(
        [Parameter(Mandatory = $true)][object]$Service,
        [object]$ReleaseState
    )
    if ([string]$Service.Key -eq "broadcast") {
        return [bool](Test-BroadcastPublisherEnabled)
    }
    return $true
}

function Assert-ControlPlaneIsolationBaseline {
    param(
        [Parameter(Mandatory = $true)][object]$Snapshot,
        [object]$ReleaseState
    )
    foreach ($service in $services) {
        $owners = @($Snapshot.services.($service.Key))
        $required = Test-ControlPlaneServiceOwnerRequired -Service $service `
            -ReleaseState $ReleaseState
        if ($required -and $owners.Count -ne 1) {
            throw "CONTROL_PLANE_SERVICE_OWNER_REQUIRED:$($service.Key)"
        }
        if ((-not $required) -and $owners.Count -ne 0) {
            throw "CONTROL_PLANE_UNEXPECTED_SERVICE_OWNER:$($service.Key)"
        }
    }
}

function Repair-AbandonedControlPlaneBundleForWatchdog {
    $current = Get-RuntimeControlBundleIdentity
    $state = Get-ControlPlaneInstallState
    if (-not $state -or [string]$state.phase -in @(
        "COMMITTED", "ROLLED_BACK", "FAILED"
    )) {
        if ($current) { return $current }
        throw "CONTROL_BUNDLE_HASH_VERIFICATION_FAILED"
    }
    $installer = $state.install_owner_identity
    $installerAlive = $false
    if ($installer) {
        $observed = Get-ControlPlaneProcessIdentity `
            -ProcessId ([int]$installer.process_id)
        $installerAlive = [bool]($observed -and
            [string]$observed.process_start_token -eq
                [string]$installer.process_start_token)
    }
    if ($current) {
        if ([string]$current.source_revision -eq [string]$state.target_revision) {
            return $current
        }
        if ([string]$current.source_revision -eq [string]$state.previous_revision) {
            if ($installerAlive) { return $current }
            Write-ControlPlaneInstallState @{
                phase = "ROLLED_BACK"
                completed_at = [DateTimeOffset]::UtcNow.ToString("o")
                rollback_result = "ROLLED_BACK_BEFORE_BUNDLE_SWAP"
                recovery = "INSTALL_OWNER_EXITED_BEFORE_BUNDLE_SWAP"
            }
            Restore-ControlPlaneSupervision -State $state.supervision_state
            return $current
        }
        throw "CONTROL_PLANE_ABANDONED_BUNDLE_IDENTITY_MISMATCH"
    }
    if ($installerAlive) { throw "CONTROL_PLANE_INSTALL_OWNER_STILL_ACTIVE" }
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $stage = Get-RuntimeControlBundleIdentityAtRoot `
        -ControlRoot ([string]$state.stage_root)
    if ($stage -and [string]$stage.source_revision -eq
            [string]$state.target_revision) {
        foreach ($name in @($runtimeControlFileNames) + @($runtimeControlManifestName)) {
            Copy-Item -LiteralPath (Join-Path ([string]$state.stage_root) $name) `
                -Destination (Join-Path $controlRoot $name) -Force
        }
        $repaired = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $controlRoot
        if ($repaired) {
            Write-ControlPlaneInstallState @{
                phase = "START_NEW_WATCHDOG"
                recovery = "FORWARD_REPAIRED_INTERRUPTED_BUNDLE_COPY"
            }
            return $repaired
        }
    }
    $backup = Get-RuntimeControlBundleIdentityAtRoot `
        -ControlRoot ([string]$state.backup_root)
    if ($backup) {
        $restored = Restore-RuntimeControlBundleBackup `
            -BackupRoot ([string]$state.backup_root) -ControlRoot $controlRoot
        Write-ControlPlaneInstallState @{
            phase = "ROLLED_BACK"
            completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            rollback_result = "ROLLED_BACK_AFTER_INTERRUPTED_BUNDLE_COPY"
        }
        return $restored
    }
    throw "CONTROL_PLANE_ABANDONED_BUNDLE_RECOVERY_FAILED"
}

function Get-ControlPlaneInstallOwnerAlive {
    param([Parameter(Mandatory = $true)][object]$State)
    $installer = $State.install_owner_identity
    if (-not $installer -or [int]$installer.process_id -le 0 -or
        -not [string]$installer.process_start_token) {
        return $false
    }
    $observed = Get-ControlPlaneProcessIdentity `
        -ProcessId ([int]$installer.process_id)
    return [bool]($observed -and
        [string]$observed.process_start_token -eq
            [string]$installer.process_start_token)
}

function Assert-AbandonedControlPlaneInstallActivation {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][string]$TransactionId
    )
    if ([string]$State.transaction_id -ne $TransactionId) {
        throw "CONTROL_PLANE_INSTALL_FENCE_LOST"
    }
    if (Get-ControlPlaneInstallOwnerAlive -State $State) {
        throw "CONTROL_PLANE_INSTALL_OWNER_STILL_ACTIVE"
    }
    if (-not [bool]$State.bundle_hash_verified) {
        throw "CONTROL_PLANE_ABANDONED_BUNDLE_NOT_VERIFIED"
    }
    $bundle = Get-RuntimeControlBundleIdentity
    if (-not $bundle -or
        [string]$bundle.source_revision -ne [string]$State.target_revision -or
        -not [bool]$bundle.exact_revision) {
        throw "CONTROL_PLANE_ABANDONED_BUNDLE_IDENTITY_MISMATCH"
    }
    $oldOwner = $State.old_watchdog_identity
    if (-not $oldOwner -or -not [string]$oldOwner.process_start_token) {
        throw "CONTROL_PLANE_OLD_WATCHDOG_IDENTITY_MISSING"
    }
    $oldObserved = Get-ControlPlaneProcessIdentity `
        -ProcessId ([int]$oldOwner.process_id)
    if ($oldObserved -and [string]$oldObserved.process_start_token -eq
            [string]$oldOwner.process_start_token) {
        throw "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"
    }
    $owners = @(Get-VerifiedWatchdogOwners)
    $currentIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
    if ($owners.Count -ne 1 -or -not $currentIdentity -or
        [int]$owners[0].process_id -ne $PID -or
        [string]$owners[0].process_start_token -ne
            [string]$currentIdentity.process_start_token) {
        throw "CONTROL_PLANE_RECOVERY_EXACTLY_ONE_REPLACEMENT_REQUIRED"
    }
    if (-not (Test-Path -LiteralPath $watchdogHeartbeatPath)) {
        throw "CONTROL_PLANE_RECOVERY_QUIESCED_ACK_MISSING"
    }
    try {
        $heartbeat = Get-Content -LiteralPath $watchdogHeartbeatPath -Raw |
            ConvertFrom-Json
    } catch {
        throw "CONTROL_PLANE_RECOVERY_QUIESCED_ACK_INVALID"
    }
    if ([string]$heartbeat.install_transaction_id -ne $TransactionId -or
        [string]$heartbeat.supervision_mode -ne "QUIESCED" -or
        [string]$heartbeat.control_bundle_revision -ne
            [string]$State.target_revision -or
        -not [bool]$heartbeat.control_bundle_exact_revision -or
        -not [bool]$heartbeat.control_bundle_hash_verified -or
        [int]$heartbeat.process_id -ne $PID -or
        [string]$heartbeat.process_start_token -ne
            [string]$currentIdentity.process_start_token) {
        throw "CONTROL_PLANE_RECOVERY_QUIESCED_ACK_MISMATCH"
    }
    $release = Get-ReleaseControlState
    if ($release -and $release.transaction) {
        throw "CONTROL_PLANE_RECOVERY_RELEASE_TRANSACTION_APPEARED"
    }
    if (Test-Path -LiteralPath $releaseLockPath) {
        $lockOwner = $null
        try {
            $lockOwner = Get-Content -LiteralPath `
                (Join-Path $releaseLockPath "owner.json") -Raw |
                ConvertFrom-Json
        } catch {}
        if (-not $lockOwner -or
            [int]$lockOwner.owner_pid -ne
                [int]$State.install_owner_identity.process_id -or
            [string]$lockOwner.owner_process_start_token -ne
                [string]$State.install_owner_identity.process_start_token) {
            throw "CONTROL_PLANE_RECOVERY_CONCURRENT_RELEASE_LOCK"
        }
    }
    $baseline = $State.isolation_before
    if (-not $baseline) {
        throw "CONTROL_PLANE_RECOVERY_BASELINE_MISSING"
    }
    Assert-ControlPlaneIsolationBaseline -Snapshot $baseline `
        -ReleaseState $release
    $currentIsolation = Get-ControlPlaneIsolationSnapshot
    Assert-ControlPlaneIsolationSnapshot -Before $baseline `
        -After $currentIsolation
    return [pscustomobject]@{
        owner = $owners[0]
        isolation = $currentIsolation
    }
}

function Restore-AbandonedControlPlaneInstallForWatchdog {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][string]$Failure
    )
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $backup = Get-RuntimeControlBundleIdentityAtRoot `
        -ControlRoot ([string]$State.backup_root)
    if (-not $backup -or [string]$backup.source_revision -ne
            [string]$State.previous_revision) {
        throw "CONTROL_PLANE_ABANDONED_SAFE_BUNDLE_UNAVAILABLE: $Failure"
    }
    $restored = Restore-RuntimeControlBundleBackup `
        -BackupRoot ([string]$State.backup_root) -ControlRoot $controlRoot
    if (-not $restored -or [string]$restored.source_revision -ne
            [string]$State.previous_revision) {
        throw "CONTROL_PLANE_ABANDONED_SAFE_BUNDLE_RESTORE_FAILED: $Failure"
    }
    Write-ControlPlaneInstallState @{
        phase = "ROLLED_BACK"
        completed_at = [DateTimeOffset]::UtcNow.ToString("o")
        rollback_result = "ROLLED_BACK_BY_RECOVERY_WATCHDOG"
        recovery = "ABANDONED_INSTALL_FAILED_INDEPENDENT_SAFETY_CHECKS"
        failure = $Failure
    }
    Restore-ControlPlaneSupervision -State $State.supervision_state
    return $restored
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
        if ($beforeProcesses.Count -ne $afterProcesses.Count) {
            throw "CONTROL_PLANE_INSTALL_CHANGED_SERVICE_$($service.Key.ToUpperInvariant())"
        }
        for ($index = 0; $index -lt $beforeProcesses.Count; $index++) {
            if ([int]$beforeProcesses[$index].process_id -ne
                    [int]$afterProcesses[$index].process_id -or
                [string]$beforeProcesses[$index].process_start_token -ne
                    [string]$afterProcesses[$index].process_start_token) {
                throw "CONTROL_PLANE_INSTALL_CHANGED_SERVICE_$($service.Key.ToUpperInvariant())"
            }
        }
    }
}

function Get-ControlPlaneInstallState {
    if (-not (Test-Path -LiteralPath $controlPlaneInstallStatePath)) { return $null }
    try {
        Get-Content -LiteralPath $controlPlaneInstallStatePath -Raw | ConvertFrom-Json
    } catch { return $null }
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
        # Keep the main task enabled so a machine restart can launch the exact
        # installed bundle and resume the durable handoff. Only the guard is
        # disabled because it would otherwise race the intentional owner gap.
        Disable-ScheduledTask -TaskName $guardTaskName | Out-Null
        Stop-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue
    } catch {
        Restore-ControlPlaneSupervision -State $state
        throw
    }
    return $state
}

function Restore-ControlPlaneSupervision {
    param([object]$State)
    if (-not $State) { return }
    foreach ($name in @($taskName, $guardTaskName)) {
        $enabled = if ($State -is [System.Collections.IDictionary]) {
            [bool]$State[$name]
        } elseif ($State.PSObject.Properties[$name]) {
            [bool]$State.$name
        } else { $false }
        if ($enabled) { Enable-ScheduledTask -TaskName $name | Out-Null }
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
    param(
        [switch]$PassThru,
        [string]$InstallTransactionId = ""
    )
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $controlScript = Join-Path $controlRoot "xauusd_control_center.ps1"
    $launcher = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
    if (-not (Test-Path -LiteralPath $controlScript) -or
        -not (Test-Path -LiteralPath $launcher)) {
        throw "Updated watchdog control files are unavailable."
    }
    $wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
    $arguments = if ($InstallTransactionId) {
        '"{0}" "{1}" "{2}" "{3}" "{4}"' -f `
            $launcher, $controlScript, $moduleRoot, $repositoryRoot, $InstallTransactionId
    } else {
        '"{0}" "{1}" "{2}" "{3}"' -f `
            $launcher, $controlScript, $moduleRoot, $repositoryRoot
    }
    $process = Start-Process -FilePath $wscript -ArgumentList $arguments `
        -WindowStyle Hidden -PassThru
    if ($PassThru) { return $process }
}

function Wait-VerifiedWatchdogHandoff {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][object]$PreviousIdentity,
        [ValidateSet("ACTIVE", "QUIESCED")][string]$ExpectedMode = "ACTIVE",
        [string]$ExpectedInstallTransactionId = "",
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
            [string]$heartbeat.supervision_mode -ne $ExpectedMode -or
            [string]$heartbeat.install_transaction_id -ne
                $ExpectedInstallTransactionId -or
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

function Wait-ControlPlaneInstallActivation {
    param([Parameter(Mandatory = $true)][string]$TransactionId)
    while ($true) {
        $state = Get-ControlPlaneInstallState
        if (-not $state -or [string]$state.transaction_id -ne $TransactionId) {
            throw "CONTROL_PLANE_INSTALL_FENCE_LOST"
        }
        if ([string]$state.phase -in @("FAILED", "ROLLED_BACK", "COMMITTED")) {
            throw "CONTROL_PLANE_INSTALL_ACTIVATION_WITHDRAWN"
        }
        $installerAlive = Get-ControlPlaneInstallOwnerAlive -State $state
        Write-WatchdogHeartbeat -SupervisionMode "QUIESCED" `
            -InstallTransactionId $TransactionId
        if ([string]$state.phase -eq "ACTIVATE_NEW_WATCHDOG") {
            if ($installerAlive) { return "INSTALLER_GRANTED" }
        }
        if (-not $installerAlive -and [string]$state.phase -in @(
            "INSTALL_BUNDLE", "START_NEW_WATCHDOG", "VERIFY_QUIESCED_HANDOFF"
        ) -or (-not $installerAlive -and
            [string]$state.phase -eq "ACTIVATE_NEW_WATCHDOG")) {
            try {
                $verified = Assert-AbandonedControlPlaneInstallActivation `
                    -State $state -TransactionId $TransactionId
                Write-ControlPlaneInstallState @{
                    phase = "ACTIVATE_NEW_WATCHDOG"
                    recovery = "INSTALL_OWNER_EXITED_AFTER_INDEPENDENT_VERIFICATION"
                    new_watchdog_identity = $verified.owner
                    isolation_after = $verified.isolation
                }
                return "RECOVERED"
            } catch {
                $failure = $_.Exception.Message
                $null = Restore-AbandonedControlPlaneInstallForWatchdog `
                    -State $state -Failure $failure
                throw "CONTROL_PLANE_ABANDONED_INSTALL_ROLLED_BACK: $failure"
            }
        }
        Start-Sleep -Milliseconds 250
    }
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
    $isolationBefore = $null

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
    $installOwnerIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
    if (-not $installOwnerIdentity) {
        throw "CONTROL_PLANE_INSTALL_OWNER_IDENTITY_REQUIRED"
    }
    Write-ControlPlaneInstallState @{
        transaction_id = $transactionId
        target_revision = $TargetRevision
        previous_revision = [string]$currentBundle.source_revision
        started_at = $startedAt
        completed_at = $null
        phase = "PRECHECK"
        old_watchdog_identity = $oldOwner
        install_owner_identity = $installOwnerIdentity
        stage_root = $stageRoot
        backup_root = $backupRoot
        old_watchdog_heartbeat = $oldHeartbeat
        new_watchdog_identity = $null
        bundle_hash_verified = $false
        rollback_result = $null
        failure = $null
        isolation_before = $null
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
        Write-ControlPlaneInstallState @{ supervision_state = $supervisionState }
        Wait-ControlPlaneGuardQuiesced
        # Revalidate the complete stage before the first destructive process action.
        if (-not (Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $stageRoot)) {
            throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED"
        }
        Write-ControlPlaneInstallState @{ phase = "STOP_OLD_WATCHDOG" }
        Stop-VerifiedWatchdogOwner -Identity $oldOwner
        $oldStopped = $true
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (@(Get-VerifiedWatchdogOwners).Count -ne 0) {
            throw "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"
        }
        # The watchdog can recover a service while the immutable bundle stage is
        # being verified. Establish the service baseline only after supervision
        # is quiesced and the old watchdog has stopped, so no owner can mutate it
        # between the snapshot and the handoff.
        # Release state is mutable before the transaction lock is acquired and
        # the old supervisor is fenced. Classify the service baseline only from
        # the fresh state at this quiesced boundary.
        $release = Get-ReleaseControlState
        if ($release -and $release.transaction) {
            throw "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"
        }
        $isolationBefore = Get-ControlPlaneIsolationSnapshot
        Assert-ControlPlaneIsolationBaseline -Snapshot $isolationBefore `
            -ReleaseState $release
        Write-ControlPlaneInstallState @{ isolation_before = $isolationBefore }
        Write-ControlPlaneInstallState @{ phase = "INSTALL_BUNDLE" }
        $installed = Install-VerifiedRuntimeControlBundleStage `
            -StageRoot $stageRoot -ControlRoot $controlRoot -BackupRoot $backupRoot
        $bundleInstalled = $true
        if ([string]$installed.source_revision -ne $TargetRevision) {
            throw "CONTROL_BUNDLE_INSTALLED_REVISION_MISMATCH"
        }
        Write-ControlPlaneInstallState @{
            phase = "START_NEW_WATCHDOG"
            handoff_mode = "QUIESCED"
        }
        $null = Start-WatchdogReplacement -PassThru `
            -InstallTransactionId $transactionId
        Write-ControlPlaneInstallState @{ phase = "VERIFY_QUIESCED_HANDOFF" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner -ExpectedMode "QUIESCED" `
            -ExpectedInstallTransactionId $transactionId
        $isolationAfter = Get-ControlPlaneIsolationSnapshot
        Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
            -After $isolationAfter
        Write-ControlPlaneInstallState @{ phase = "ACTIVATE_NEW_WATCHDOG" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner -ExpectedMode "ACTIVE"
        Restore-ControlPlaneSupervision -State $supervisionState
        $supervisionState = $null
        Write-ControlPlaneInstallState @{
            phase = "COMMITTED"
            completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            new_watchdog_identity = $newOwner
            rollback_result = "NOT_REQUIRED"
            failure = $null
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
                Write-ControlPlaneInstallState @{ phase = "ROLLING_BACK" }
                foreach ($owner in @(Get-VerifiedWatchdogOwners)) {
                    Stop-VerifiedWatchdogOwner -Identity $owner
                }
                if ($bundleInstalled) {
                    $null = Restore-RuntimeControlBundleBackup `
                        -BackupRoot $backupRoot -ControlRoot $controlRoot
                }
                if ($isolationBefore) {
                    $isolationAfter = Get-ControlPlaneIsolationSnapshot
                    Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
                        -After $isolationAfter
                }
                # Recovery proves restoration against the captured baseline.
                # It deliberately does not re-run the contextual normal-state
                # owner rule that may have caused the forward handoff failure.
                $null = Start-WatchdogReplacement -PassThru
                $restoredOwner = Wait-VerifiedWatchdogHandoff `
                    -ExpectedRevision ([string]$currentBundle.source_revision) `
                    -PreviousIdentity $oldOwner
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

function Invoke-ForecasterWatchdog {
    param([string]$InstallTransactionId = "")
    $null = Repair-AbandonedControlPlaneBundleForWatchdog
    $null = Assert-ActiveControlBundle
    if (-not $InstallTransactionId) {
        $pendingInstall = Get-ControlPlaneInstallState
        $bundle = Get-RuntimeControlBundleIdentity
        if ($pendingInstall -and $bundle -and
            [string]$pendingInstall.target_revision -eq
                [string]$bundle.source_revision -and
            [string]$pendingInstall.phase -in @(
                "INSTALL_BUNDLE", "START_NEW_WATCHDOG",
                "VERIFY_QUIESCED_HANDOFF", "ACTIVATE_NEW_WATCHDOG"
            )) {
            $InstallTransactionId = [string]$pendingInstall.transaction_id
        }
    }
    if (-not $InstallTransactionId) { Start-All }
    if ($InstallTransactionId) {
        $activation = Wait-ControlPlaneInstallActivation `
            -TransactionId $InstallTransactionId
        Write-WatchdogHeartbeat -SupervisionMode "ACTIVE"
        if ($activation -eq "RECOVERED") {
            $recoveredOwner = Get-ControlPlaneProcessIdentity -ProcessId $PID
            Write-ControlPlaneInstallState @{
                phase = "COMMITTED"
                completed_at = [DateTimeOffset]::UtcNow.ToString("o")
                new_watchdog_identity = $recoveredOwner
                rollback_result = "NOT_REQUIRED"
                recovery = "FORWARD_COMPLETED_AFTER_INSTALL_OWNER_EXIT"
                failure = $null
            }
        }
    }
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
        lifecycle_phase = if ($ReleaseState) {
            Get-ReleaseLifecyclePhase -ReleaseState $ReleaseState
        } else { "UNAVAILABLE" }
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
        "RetryCandidateValidation" {
            if (-not (Enter-ReleaseTransactionLock)) {
                throw "Another release transaction is active."
            }
            try { return Retry-CandidateValidation }
            finally { Exit-ReleaseTransactionLock }
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
            Set-StatusBadge -Label $stableCard.Badge -State ([string]$release.lifecycle_phase)
            $stableCard.Git.Text = "Git       $(Get-ShortIdentity $release.stable.git_sha)"
            $stableCard.Worker.Text = "Worker    $(Get-ShortIdentity $release.stable.worker_version_id)"
            $stableCard.Windows.Text = "Windows   $(Get-ShortIdentity $release.stable.windows_revision)"
            $stableCard.Detail.Text = "Authoritative production release."
            $stableCard.Detail.Text = "$($release.stable.artifact_kind) / $($release.stable.provenance_state) / $($release.lifecycle_phase)"
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

if ($ExpectedControlScriptPath -or $ExpectedControlRevision) {
    $null = Assert-ControlCenterProcessIdentity `
        -ExpectedScriptPath $ExpectedControlScriptPath `
        -ExpectedRevision $ExpectedControlRevision
}

if ($OperationResultPath) {
    $structuredActions = @(
        "Start", "Stop", "Restart", "ServiceStart", "ServiceStop",
        "DiscoverCandidate", "RetryCandidateValidation", "ReconcileRelease", "PromoteCandidate",
        "ReverseStable", "VerifyMigrationCompatibility", "ApproveCompatibility"
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
            release = Get-ReleaseControlState
        } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
    }
    "ReleaseStatusJson" {
        if (-not $StatusPath) { throw "StatusPath is required for ReleaseStatusJson." }
        Get-ReleaseControlState | ConvertTo-Json -Depth 12 |
            Set-Content -LiteralPath $StatusPath -Encoding UTF8
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
    "DiscoverCandidate" {
        try { $null = Invoke-ControlCenterOperationAction -Operation $Action; exit 0 }
        catch { Write-Error $_.Exception.Message; exit 1 }
    }
    "RetryCandidateValidation" {
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
    "EnableAutoStart" { Enable-AutoStart; Write-Output "Auto-start enabled." }
    "DisableAutoStart" { Disable-AutoStart; Write-Output "Auto-start disabled." }
    "InstallRuntime" { Install-ProductionRuntime | Format-List }
    "InstallControlPlane" {
        if (-not $SourceRoot -or -not $SourceRevision) {
            throw "SourceRoot and SourceRevision are required for InstallControlPlane."
        }
        Invoke-ControlPlaneInstall -VerifiedSourceRoot `
            ([System.IO.Path]::GetFullPath($SourceRoot)) `
            -TargetRevision $SourceRevision | Format-List
    }
    "InstallShortcut" { Write-Output (Install-ControlShortcut) }
    default { Show-ControlCenter }
}
