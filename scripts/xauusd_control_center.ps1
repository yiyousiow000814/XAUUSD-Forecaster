param(
    [ValidateSet("Gui", "Status", "StatusJson", "ReleaseStatusJson", "CodeRevision", "Start", "Stop", "Restart", "ServiceStart", "ServiceStop", "Watchdog", "DiscoverCandidate", "ReconcileRelease", "PromoteCandidate", "ReverseStable", "BootstrapRelease", "ApproveCompatibility", "EnableAutoStart", "DisableAutoStart", "InstallShortcut", "InstallRuntime")]
    [string]$Action = "Gui",
    [ValidateSet("", "quote", "collector", "annotator", "api", "sync")]
    [string]$ServiceKey = "",
    [string]$StatusPath = "",
    [string]$RuntimeRoot = "",
    [string]$RepositoryRoot = ""
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
$releaseLockPath = Join-Path $moduleRoot ".local\forward\release-control.lock"
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
    "xauusd_watchdog_launcher.vbs",
    "xauusd_watchdog_guard.ps1",
    "xauusd_watchdog_guard_launcher.vbs"
)
$runtimeControlManifestName = "runtime-control-bundle.json"
$collectorSecretsPath = Join-Path $repositoryRoot ".local\secrets\collector-keys.json"
$workerName = "aurum-signal-room"
$workerUrl = "https://aurum-signal-room.yiyousiow1234.workers.dev"
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
    "Analyze (actions)",
    "Analyze (csharp)",
    "Analyze (javascript-typescript)",
    "Analyze (python)"
)
$workerCpuPassP95Ms = 6.0
$workerCpuPassP99Ms = 8.0
$workerCpuPassMaxMs = 10.0
$candidateDiscoveryInterval = [TimeSpan]::FromMinutes(5)
$releaseLockOwnerGrace = [TimeSpan]::FromSeconds(30)
$bootstrapAcceptedCandidateWorker = "dd823aa4-20f0-47e1-9255-1b785a4c17b0"
$bootstrapAcceptedCandidateRevision = "14c055a35040fa963700c988f770c9bb52fa669e"

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
    }
)

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
        return $state
    } catch { $null }
}

function Write-ReleaseControlState {
    param([Parameter(Mandatory = $true)][object]$State)
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
            $ownerAlive = [bool](Get-Process -Id ([int]$owner.owner_pid) -ErrorAction SilentlyContinue)
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
        [pscustomobject]@{
            owner_pid = $PID
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
    if ($Value -is [string] -or $Value -is [DateTime] -or
        $Value -is [DateTimeOffset]) {
        if (-not [string]::IsNullOrWhiteSpace([string]$Value)) { Write-Output $Value }
        return
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        foreach ($item in $Value) { Get-ReleaseTimestampValues -Value $item }
        return
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Value)) { Write-Output $Value }
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
        $production = [Uri]$dashboardUrl
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
        $parsed = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse([string]$candidate, [ref]$parsed)) {
            $utc = $parsed.ToUniversalTime()
            if ($utc -gt $newest) { $newest = $utc }
        }
    }
    return $newest
}

function Get-ReleaseVersionCreatedAt {
    param([Parameter(Mandatory = $true)][object]$Version)
    $created = Get-ReleaseVersionCreatedAtValue -Version $Version
    if ($created -eq [DateTimeOffset]::MinValue) { return "" }
    return $created.ToString("o")
}

function Test-VersionAfterDiscoveryWatermark {
    param(
        [Parameter(Mandatory = $true)][object]$Version,
        [Parameter(Mandatory = $true)][object]$Discovery
    )
    if (-not $Discovery.watermark_created_at) { return $true }
    $createdAt = Get-ReleaseVersionCreatedAt -Version $Version
    if (-not $createdAt) { return $false }
    $created = [DateTimeOffset]::Parse($createdAt)
    $watermark = [DateTimeOffset]::Parse([string]$Discovery.watermark_created_at)
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
        deployment_status = "READY"
        drift = $null
        last_candidate_check = $null
        candidate_discovery = [pscustomobject]@{
            watermark_created_at = $null
            watermark_version_id = $null
            initialized_at = $null
        }
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

function Test-RequiredGitHubChecks {
    param([Parameter(Mandatory = $true)][string]$Revision)
    try {
        $json = @(& gh api --method GET `
            "repos/yiyousiow000814/XAUUSD-Forecaster/commits/$Revision/check-runs" 2>$null) -join "`n"
        if ($LASTEXITCODE -ne 0) { return "PENDING" }
        $runs = @(($json | ConvertFrom-Json).check_runs)
        foreach ($name in $requiredGitHubChecks) {
            $matching = @($runs | Where-Object { [string]$_.name -eq $name })
            if ($matching.Count -eq 0 -or
                @($matching | Where-Object { [string]$_.status -ne "completed" }).Count -gt 0) {
                return "PENDING"
            }
            if (@($matching | Where-Object { [string]$_.conclusion -ne "success" }).Count -gt 0) {
                return "FAILED"
            }
        }
        return "PASSED"
    } catch { return "PENDING" }
}

function Test-ProductionCandidateProvenance {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$Candidate.branch -ne "main" -or
        [string]$Candidate.git_sha -ne [string]$Candidate.windows_revision) {
        return $false
    }
    & git -C $repositoryRoot fetch origin --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    & git -C $repositoryRoot cat-file -e "$([string]$Candidate.git_sha)^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    & git -C $repositoryRoot merge-base --is-ancestor `
        ([string]$Candidate.git_sha) origin/main 2>$null
    return [bool]($LASTEXITCODE -eq 0)
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
    $token = Get-UserEnvironmentValue -Name "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN"
    if (-not $token) { return $null }
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
        if (-not $response.success) { return $null }
        return $response.result
    } catch { return $null }
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

function Get-CandidatePlatformEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [int]$ExpectedInvocations = 1,
        [string]$RoutePath = "",
        [string]$RouteMethod = "",
        [string]$RouteFamily = "GLOBAL"
    )
    $baseFilters = @(
        [pscustomobject]@{ key='$metadata.service'; operation='eq'; type='string'; value=$workerName },
        [pscustomobject]@{ key='$workers.scriptVersion.id'; operation='eq'; type='string'; value=[string]$Candidate.worker_version_id }
    )
    if ($RoutePath) {
        $baseFilters += [pscustomobject]@{
            key='$workers.event.request.path'; operation='eq'; type='string'; value=$RoutePath
        }
    }
    if ($RouteMethod) {
        $baseFilters += [pscustomobject]@{
            key='$workers.event.request.method'; operation='eq'; type='string'; value=$RouteMethod
        }
    }
    $base = Invoke-WorkersObservabilityQuery -From $From -To $To `
        -Filters $baseFilters -Calculations @(
            [pscustomobject]@{ operator='count'; alias='invocations' },
            [pscustomobject]@{ key='$workers.cpuTimeMs'; keyType='number'; operator='max'; alias='max_cpu_ms' },
            [pscustomobject]@{ key='$workers.cpuTimeMs'; keyType='number'; operator='p95'; alias='p95_cpu_ms' },
            [pscustomobject]@{ key='$workers.cpuTimeMs'; keyType='number'; operator='p99'; alias='p99_cpu_ms' },
            [pscustomobject]@{ key='$workers.wallTimeMs'; keyType='number'; operator='max'; alias='max_wall_ms' }
        )
    if (-not $base) { return $null }
    $exceeded = Invoke-WorkersObservabilityQuery -From $From -To $To `
        -Filters @($baseFilters + [pscustomobject]@{
            key='$workers.outcome'; operation='eq'; type='string'; value='exceededCpu'
        }) -Calculations @([pscustomobject]@{ operator='count'; alias='exceeded_cpu' })
    $failures = Invoke-WorkersObservabilityQuery -From $From -To $To `
        -Filters @($baseFilters + [pscustomobject]@{
            key='$workers.event.response.status'; operation='gte'; type='number'; value=500
        }) -Calculations @([pscustomobject]@{ operator='count'; alias='responses_5xx' })
    $exceededMemory = Invoke-WorkersObservabilityQuery -From $From -To $To `
        -Filters @($baseFilters + [pscustomobject]@{
            key='$workers.outcome'; operation='eq'; type='string'; value='exceededMemory'
        }) -Calculations @([pscustomobject]@{ operator='count'; alias='exceeded_memory' })
    if (-not $exceeded -or -not $failures -or -not $exceededMemory) { return $null }
    $invocations = Get-CalculationAggregate -QueryResult $base -Alias "invocations"
    $maxCpu = Get-CalculationAggregate -QueryResult $base -Alias "max_cpu_ms"
    $p95Cpu = Get-CalculationAggregate -QueryResult $base -Alias "p95_cpu_ms"
    $p99Cpu = Get-CalculationAggregate -QueryResult $base -Alias "p99_cpu_ms"
    $maxWall = Get-CalculationAggregate -QueryResult $base -Alias "max_wall_ms"
    $exceededCpu = Get-CalculationAggregate -QueryResult $exceeded -Alias "exceeded_cpu"
    $responses5xx = Get-CalculationAggregate -QueryResult $failures -Alias "responses_5xx"
    $exceededMemoryCount = Get-CalculationAggregate -QueryResult $exceededMemory -Alias "exceeded_memory"
    $responses1102 = if ($null -ne $exceededCpu -and $null -ne $exceededMemoryCount) {
        [int]$exceededCpu + [int]$exceededMemoryCount
    } else { $null }
    if (@($invocations, $maxCpu, $p95Cpu, $p99Cpu, $maxWall, $exceededCpu,
        $responses1102, $responses5xx |
        Where-Object { $null -eq $_ }).Count -gt 0) { return $null }
    $evidence = [pscustomobject]@{
        source = "CLOUDFLARE_WORKERS_OBSERVABILITY"
        route_family = $RouteFamily
        route_path = if ($RoutePath) { $RoutePath } else { $null }
        route_method = if ($RouteMethod) { $RouteMethod } else { $null }
        worker_version_id = [string]$Candidate.worker_version_id
        from = $From.ToString("o")
        to = $To.ToString("o")
        invocations = [int]$invocations
        max_cpu_ms = [double]$maxCpu
        p95_cpu_ms = [double]$p95Cpu
        p99_cpu_ms = [double]$p99Cpu
        max_wall_ms = [double]$maxWall
        exceeded_cpu = [int]$exceededCpu
        exceeded_memory = [int]$exceededMemoryCount
        responses_1102 = [int]$responses1102
        responses_5xx = [int]$responses5xx
    }
    $gateState = Get-WorkerCpuGateState -Evidence $evidence `
        -ExpectedInvocations $ExpectedInvocations
    $evidence | Add-Member -NotePropertyName gate_state -NotePropertyValue $gateState
    $evidence | Add-Member -NotePropertyName passed `
        -NotePropertyValue ([bool]($gateState -eq "PASSED"))
    return $evidence
}

function Get-CandidateCpuEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [Parameter(Mandatory = $true)][object[]]$Routes
    )
    $expected = [int](($Routes | Measure-Object -Property acceptance_samples -Sum).Sum)
    $global = Get-CandidatePlatformEvidence -Candidate $Candidate -From $From -To $To `
        -ExpectedInvocations $expected
    if (-not $global) { return $null }
    $families = @()
    $routeGroups = @($Routes | Group-Object { "{0}|{1}|{2}" -f `
        [string]$_.path, [string]$_.method, [string]$_.family })
    foreach ($group in $routeGroups) {
        $route = $group.Group[0]
        $familyExpected = [int](($group.Group |
            Measure-Object -Property acceptance_samples -Sum).Sum)
        $evidence = Get-CandidatePlatformEvidence -Candidate $Candidate `
            -From $From -To $To -ExpectedInvocations $familyExpected `
            -RoutePath ([string]$route.path) -RouteMethod ([string]$route.method) `
            -RouteFamily ([string]$route.family)
        if (-not $evidence) { return $null }
        $families += $evidence
    }
    $failed = @($families | Where-Object { [string]$_.gate_state -eq "FAILED" }).Count -gt 0
    $review = @($families | Where-Object {
        [string]$_.gate_state -eq "REVIEW_REQUIRED"
    }).Count -gt 0
    $gateState = if ($failed -or [string]$global.gate_state -eq "FAILED") {
        "FAILED"
    } elseif ($review -or [string]$global.gate_state -eq "REVIEW_REQUIRED") {
        "REVIEW_REQUIRED"
    } else { "PASSED" }
    [pscustomobject]@{
        source = "CLOUDFLARE_WORKERS_OBSERVABILITY"
        worker_version_id = [string]$Candidate.worker_version_id
        expected_invocations = $expected
        global = $global
        routes = $families
        invocations = [int]$global.invocations
        max_cpu_ms = [double]$global.max_cpu_ms
        p95_cpu_ms = [double]$global.p95_cpu_ms
        p99_cpu_ms = [double]$global.p99_cpu_ms
        max_wall_ms = [double]$global.max_wall_ms
        exceeded_cpu = [int]$global.exceeded_cpu
        exceeded_memory = [int]$global.exceeded_memory
        responses_1102 = [int]$global.responses_1102
        responses_5xx = [int]$global.responses_5xx
        gate_state = $gateState
        passed = [bool]($gateState -eq "PASSED")
    }
}

function Get-CandidateInvocationCount {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To
    )
    $result = Invoke-WorkersObservabilityQuery -From $From -To $To -Filters @(
        [pscustomobject]@{ key='$metadata.service'; operation='eq'; type='string'; value=$workerName },
        [pscustomobject]@{ key='$workers.scriptVersion.id'; operation='eq'; type='string'; value=[string]$Candidate.worker_version_id }
    ) -Calculations @([pscustomobject]@{ operator='count'; alias='invocations' })
    if (-not $result) { return $null }
    return Get-CalculationAggregate -QueryResult $result -Alias "invocations"
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
        $raw = Get-Content -LiteralPath $path -Raw
    }
    $manifest = $raw | ConvertFrom-Json
    if ([int]$manifest.schema_version -ne 2 -or @($manifest.routes).Count -eq 0 -or
        -not $manifest.fixture_builder) {
        throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
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

function Invoke-CandidateRouteSample {
    param(
        [Parameter(Mandatory = $true)][object]$Route,
        [Parameter(Mandatory = $true)][hashtable]$VersionHeaders,
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][string]$FixtureRoot,
        [string]$IngestToken = ""
    )
    $requestId = [guid]::NewGuid().ToString()
    $headers = @{} + $VersionHeaders
    $headers["X-Aurum-Validation-Run"] = $ValidationRun
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
        $passed = [bool]($response.StatusCode -eq 200)
        if ([string]$Route.strategy -eq "PRODUCTION_SHAPED_DRY_RUN") {
            try {
                $payload = $response.Content | ConvertFrom-Json
                $passed = $passed -and [string]$payload.status -eq "DRY_RUN_OK" -and
                    [bool]$payload.mutated -eq $false -and
                    [string]$payload.route_family -eq [string]$Route.family
            } catch { $passed = $false }
        }
        $reason = if ($passed) { $null } else { "VALIDATION_RESPONSE_INVALID" }
        return [pscustomobject]@{
            request_id=$requestId; status=[int]$response.StatusCode; passed=$passed
            reason=$reason
        }
    } catch {
        $status = if ($_.Exception.Response) {
            [int]$_.Exception.Response.StatusCode
        } else { 0 }
        return [pscustomobject]@{
            request_id=$requestId; status=$status; passed=$false
            reason="VALIDATION_REQUEST_FAILED"
        }
    }
}

function Invoke-CandidateWorkerValidation {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan
    )
    $header = @{
        "Cloudflare-Workers-Version-Overrides" =
            "$workerName=`"$([string]$Candidate.worker_version_id)`""
    }
    $results = @()
    $staticStartedAt = [DateTimeOffset]::UtcNow
    foreach ($route in @($RoutePlan.static_assets)) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Method Get `
                -Uri "$workerUrl$($route.path)" -Headers $header -TimeoutSec 30
            $markerPassed = -not $route.marker -or $response.Content -like "*$($route.marker)*"
            $results += [pscustomobject]@{
                route = $route.path; boundary = "STATIC_ASSET"; request_id = $null
                status = [int]$response.StatusCode
                passed = [bool]($response.StatusCode -eq 200 -and $markerPassed)
            }
        } catch {
            $status = if ($_.Exception.Response) {
                [int]$_.Exception.Response.StatusCode
            } else { 0 }
            $results += [pscustomobject]@{
                route = $route.path; boundary = "STATIC_ASSET"; request_id = $null
                status = $status
                passed = $false
            }
        }
    }
    Start-Sleep -Seconds 5
    $staticEndedAt = [DateTimeOffset]::UtcNow
    $staticInvocations = Get-CandidateInvocationCount -Candidate $Candidate `
        -From $staticStartedAt.AddSeconds(-2) -To $staticEndedAt.AddSeconds(2)
    if ($null -eq $staticInvocations -or [int]$staticInvocations -ne 0) {
        $results += [pscustomobject]@{
            route = "STATIC_ASSET_INVOCATIONS"; boundary = "STATIC_ASSET"
            request_id = $null; status = 0; passed = $false
            observed_invocations = $staticInvocations
        }
    }
    $workerRoutes = @($RoutePlan.worker_reads) + @($RoutePlan.worker_writes)
    if ($workerRoutes.Count -eq 0) {
        return [pscustomobject]@{
            passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
            validation_run = $null; expected_worker_invocations = 0
            static_worker_invocations = $staticInvocations; routes = $results
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
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken
            }
            if (@($warmups | Where-Object { -not $_.passed }).Count -gt 0) {
                $results += [pscustomobject]@{
                    route=$route.path; method=$route.method; family=$route.family
                    scenario=$route.scenario
                    boundary=$route.boundary; warmup_samples=$warmups.Count
                    acceptance_samples=0; passed=$false; reason="WARMUP_FAILED"
                }
            }
        }
        $workerStartedAt = [DateTimeOffset]::UtcNow
        foreach ($route in $workerRoutes) {
            $samples = @()
            for ($index = 0; $index -lt [int]$route.acceptance_samples; $index++) {
                $samples += Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken
            }
            $failures = @($samples | Where-Object { -not $_.passed })
            $sampleReason = if ($failures.Count) {
                [string]$failures[0].reason
            } else { $null }
            $results += [pscustomobject]@{
                route=$route.path; method=$route.method; family=$route.family
                scenario=$route.scenario
                boundary=$route.boundary; warmup_samples=[int]$route.warmup_samples
                acceptance_samples=$samples.Count
                request_ids=@($samples | ForEach-Object { $_.request_id })
                statuses=@($samples | Group-Object status | ForEach-Object {
                    [pscustomobject]@{ status=[int]$_.Name; count=$_.Count }
                })
                passed=[bool]($failures.Count -eq 0)
                reason=$sampleReason
            }
        }
        $workerEndedAt = [DateTimeOffset]::UtcNow
        Start-Sleep -Seconds 8
        $platform = $null
        for ($attempt = 0; $attempt -lt 3 -and -not $platform; $attempt++) {
            $platform = Get-CandidateCpuEvidence -Candidate $Candidate `
                -From $workerStartedAt `
                -To $workerEndedAt.AddSeconds(2) -Routes $workerRoutes
            if (-not $platform -and $attempt -lt 2) { Start-Sleep -Seconds 5 }
        }
    } finally {
        Remove-CandidateValidationFixtureWorkspace -Workspace $workspace
    }
    $expectedInvocations = [int](($workerRoutes |
        Measure-Object -Property acceptance_samples -Sum).Sum)
    [pscustomobject]@{
        passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
        validation_run = $validationRun
        expected_worker_invocations = $expectedInvocations
        static_worker_invocations = $staticInvocations
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

function Test-CandidateDataParity {
    param([Parameter(Mandatory = $true)][object]$Stable,
          [Parameter(Mandatory = $true)][object]$Candidate)
    $routes = @("/api/status", "/api/audit", "/api/learning", "/api/market-chart")
    $results = @()
    foreach ($path in $routes) {
        try {
            $stableRead = Invoke-ExactVersionJson `
                -VersionId ([string]$Stable.worker_version_id) -Path $path
            $candidateRead = Invoke-ExactVersionJson `
                -VersionId ([string]$Candidate.worker_version_id) -Path $path
            if ([string]$stableRead.observed_version_id -ne [string]$Stable.worker_version_id -or
                [string]$candidateRead.observed_version_id -ne [string]$Candidate.worker_version_id) {
                throw "EXACT_VERSION_IDENTITY_MISMATCH"
            }
            $stablePayload = $stableRead.payload
            $candidatePayload = $candidateRead.payload
            $stableProjection = ConvertTo-ReleaseSemanticProjection -Path $path `
                -Payload $stablePayload
            $candidateProjection = ConvertTo-ReleaseSemanticProjection -Path $path `
                -Payload $candidatePayload
            # Snapshot publication is asynchronous. Compare authoritative data,
            # not generated_at byte equality, and admit only one normal cadence
            # of lag for the decision surface.
            $stableProjection.generated_at = $null
            $candidateProjection.generated_at = $null
            $stableShape = @($stableProjection.Keys) -join ","
            $candidateShape = @($candidateProjection.Keys) -join ","
            $passed = [bool]($stableShape -ceq $candidateShape)
            $reason = if ($passed) { "PASSED" } else { "CANDIDATE_DATA_PARITY_FAILED" }
            if ($path -eq "/api/status") {
                if ([string]$stablePayload.forward_epoch -ne
                    [string]$candidatePayload.forward_epoch) {
                    $passed = $false; $reason = "CANDIDATE_DATA_PARITY_FAILED"
                }
                $stableCount = [int]$stablePayload.counts.decisions
                $candidateCount = [int]$candidatePayload.counts.decisions
                if ($candidateCount -lt $stableCount) {
                    $passed = $false; $reason = "CANDIDATE_DECISION_BEHIND_STABLE"
                }
                try {
                    $stableTime = [DateTimeOffset]::Parse([string]$stablePayload.generated_at)
                    $candidateTime = [DateTimeOffset]::Parse([string]$candidatePayload.generated_at)
                    if (($stableTime - $candidateTime).TotalSeconds -gt 420) {
                        $passed = $false; $reason = "CANDIDATE_STATUS_STALE"
                    }
                    $stableDecision = [DateTimeOffset]::Parse(
                        [string]$stablePayload.latest.decision.decision_time
                    )
                    $candidateDecision = [DateTimeOffset]::Parse(
                        [string]$candidatePayload.latest.decision.decision_time
                    )
                    if (($stableDecision - $candidateDecision).TotalSeconds -gt 420) {
                        $passed = $false; $reason = "CANDIDATE_DECISION_BEHIND_STABLE"
                    }
                    $candidateQuote = [DateTimeOffset]::Parse(
                        [string]$candidatePayload.latest.quote.observed_at
                    )
                    if (($candidateTime - $candidateQuote).TotalSeconds -gt 75) {
                        $passed = $false; $reason = "CANDIDATE_QUOTE_STALE"
                    }
                } catch { }
            }
            if ($path -eq "/api/audit") {
                try {
                    $stableAuditTime = [DateTimeOffset]::Parse(
                        [string]$stablePayload.generated_at
                    )
                    $candidateAuditTime = [DateTimeOffset]::Parse(
                        [string]$candidatePayload.generated_at
                    )
                    if (($stableAuditTime - $candidateAuditTime).TotalMinutes -gt 15) {
                        $passed = $false; $reason = "CANDIDATE_AUDIT_TRANSITION_STALE"
                    }
                } catch { }
            }
            if ($path -in @("/api/learning", "/api/market-chart")) {
                $stableItems = @($stablePayload.learning_curves) + @($stablePayload.decisions)
                $candidateItems = @($candidatePayload.learning_curves) + @($candidatePayload.decisions)
                if ($stableItems.Count -gt 0 -and $candidateItems.Count -eq 0) {
                    $passed = $false; $reason = "CANDIDATE_DATASET_UNEXPECTEDLY_EMPTY"
                }
            }
            $results += [pscustomobject]@{
                route = $path; state = if ($passed) { "PASSED" } else { "FAILED" }
                passed = $passed; reason = $reason
                stable_version_id = [string]$stableRead.observed_version_id
                candidate_version_id = [string]$candidateRead.observed_version_id
            }
        } catch {
            $results += [pscustomobject]@{
                route = $path; state = "FAILED"; passed = $false
                reason = if ($_.Exception.Message -eq "EXACT_VERSION_IDENTITY_MISMATCH") {
                    "EXACT_VERSION_IDENTITY_MISMATCH"
                } else { "EXACT_VERSION_READ_FAILED" }
                error = Protect-PreflightDiagnosticText $_.Exception.Message
            }
        }
    }
    return [pscustomobject]@{
        state = if (@($results | Where-Object { -not $_.passed }).Count -eq 0) {
            "PASSED"
        } else { "FAILED" }
        passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
        stable_version_id = [string]$Stable.worker_version_id
        candidate_version_id = [string]$Candidate.worker_version_id
        routes = $results
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

function Invoke-AutomaticCandidateValidation {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $state = Get-ReleaseControlState
    if (-not $state -or -not (Test-ReleaseIdentity $state.candidate $Candidate)) { return $false }
    $state.candidate.validation_state = "STAGING"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    try {
        if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind) {
            throw "Only a PRODUCTION_CANDIDATE artifact can enter validation."
        }
        if (-not (Test-ProductionCandidateProvenance -Candidate $Candidate)) {
            throw "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED"
        }
        $state.candidate.validation_state = "TESTING"
        Write-ReleaseControlState -State $state
        if (-not (Invoke-ProductionShapePreflight -Revision ([string]$Candidate.windows_revision))) {
            throw "Isolated Windows preflight failed."
        }
        $checks = Test-RequiredGitHubChecks -Revision ([string]$Candidate.git_sha)
        if ($checks -eq "FAILED") { throw "Required GitHub checks failed." }
        if ($checks -eq "PENDING") {
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PENDING"
                windows = "PASSED"
                cloudflare = "PENDING"
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
        $changed = @(Get-CandidateChangedFiles `
            -StableRevision ([string]$state.stable.git_sha) `
            -CandidateRevision ([string]$Candidate.git_sha))
        $compatibility = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
        if ([string]$compatibility.state -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
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
        $cloudflare = [pscustomobject]@{ passed = $true; routes = @(); cpu_evidence = "NOT_REQUIRED" }
        if ($cloudflareChanged) {
            $cloudflare = Invoke-CandidateWorkerValidation -Candidate $Candidate `
                -RoutePlan $routePlan
            if (-not $cloudflare.passed) { throw "Directed Worker validation failed." }
            if (-not $cloudflare.cpu_evidence) {
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = "TESTING"
                    reason = "PLATFORM_CPU_EVIDENCE_REQUIRED"
                    routes = $cloudflare.routes
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
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
                throw "Cloudflare platform CPU or 5xx validation failed."
            }
        }
        $dataParity = Test-CandidateDataParity -Stable $state.stable `
            -Candidate $Candidate
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
    $versions = @(Get-CloudflareVersions | Sort-Object `
        @{ Expression = { Get-ReleaseVersionCreatedAtValue -Version $_ } }, `
        @{ Expression = { [string]$_.id } })
    if (@($versions).Count -eq 0) { return $null }
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
        if (-not $sha -or $sha -eq [string]$state.stable.git_sha -or
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
        if (@($newVersions).Count -gt 0) {
            $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $state
        }
        return $null
    }
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
    if (-not (Test-ProductionCandidateProvenance -Candidate $candidate) -or
        (Test-RequiredGitHubChecks -Revision ([string]$candidate.git_sha)) -ne "PASSED") {
        throw "Candidate provenance and required checks must pass before approval."
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
            if ($state -and -not $state.transaction) { $candidate = $state.candidate }
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
    New-Item -ItemType Directory -Path $ControlRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    try {
        foreach ($name in $runtimeControlFileNames) {
            $source = Join-Path $SourceRoot ("scripts\{0}" -f $name)
            if (-not (Test-Path -LiteralPath $source)) {
                throw "Missing runtime control file: $source"
            }
            Copy-Item -LiteralPath $source -Destination (Join-Path $stageRoot $name) -Force
        }
        if (-not $SourceRevision) {
            $SourceRevision = (& git -C $SourceRoot rev-parse HEAD 2>$null | Select-Object -First 1)
        }
        $exactRevision = [bool]($SourceRevision -match '^[0-9a-f]{40}$')
        $hashes = @{}
        foreach ($name in $runtimeControlFileNames) {
            $hashes[$name] = Get-Sha256Hex `
                -LiteralPath (Join-Path $stageRoot $name)
        }
        [pscustomobject]@{
            schema_version = 1
            source_revision = if ($exactRevision) { $SourceRevision } else { $null }
            exact_revision = $exactRevision
            created_at = [DateTimeOffset]::UtcNow.ToString("o")
            files = $hashes
        } | ConvertTo-Json -Depth 5 | Set-Content `
            -LiteralPath (Join-Path $stageRoot $runtimeControlManifestName) -Encoding UTF8
        $payloadNames = @($runtimeControlFileNames) + @($runtimeControlManifestName)
        foreach ($name in $payloadNames) {
            $destination = Join-Path $ControlRoot $name
            if (Test-Path -LiteralPath $destination) {
                Copy-Item -LiteralPath $destination `
                    -Destination (Join-Path $backupRoot $name) -Force
            }
        }
        try {
            foreach ($name in $payloadNames) {
                Move-Item -LiteralPath (Join-Path $stageRoot $name) `
                    -Destination (Join-Path $ControlRoot $name) -Force
            }
        } catch {
            foreach ($name in $payloadNames) {
                $destination = Join-Path $ControlRoot $name
                $backup = Join-Path $backupRoot $name
                if (Test-Path -LiteralPath $backup) {
                    Move-Item -LiteralPath $backup -Destination $destination -Force
                } elseif (Test-Path -LiteralPath $destination) {
                    Remove-Item -LiteralPath $destination -Force
                }
            }
            throw
        }
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
        [ValidateSet("PROMOTE", "REVERSE")][string]$Mode = "PROMOTE"
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
            $prior = $releaseState.transaction.previous
            Invoke-CloudflareDeployment `
                -StableVersionId ([string]$prior.worker_version_id) `
                -Message "automatic release reverse $([string]$releaseState.transaction.id)"
            $releaseState.candidate.validation_state = "FAILED"
            $releaseState.candidate.validation = [pscustomobject]@{
                key = [string]$releaseState.candidate.validation_key
                error = "OBSERVATION_FAILED"
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
        $transaction = [pscustomobject]@{
            id = [guid]::NewGuid().ToString()
            type = "PROMOTE"
            phase = "PRECHECK"
            target = $candidate
            previous = $state.stable
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
        Complete-DeferredServiceReload -ReloadStarted $reloadStarted `
            -DeferredServiceKeys @("sync")
        Start-RuntimeObservation -Revision ([string]$candidate.windows_revision) `
            -PreviousRevision ([string]$state.stable.windows_revision) `
            -HealthBoundary $reloadStarted
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
        $row = $status | Where-Object Key -eq $service.Key
        if ($row.State -eq "STOPPED") {
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
    $path = Join-Path $PSScriptRoot $runtimeControlManifestName
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $identity = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if (-not [bool]$identity.exact_revision -or
            [string]$identity.source_revision -notmatch '^[0-9a-f]{40}$') {
            return $null
        }
        foreach ($name in $runtimeControlFileNames) {
            $file = Join-Path $PSScriptRoot $name
            $expected = [string]$identity.files.$name
            if (-not (Test-Path -LiteralPath $file) -or -not $expected) { return $null }
            $actual = Get-Sha256Hex -LiteralPath $file
            if ($actual -ne $expected) { return $null }
        }
        return $identity
    } catch { return $null }
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

function Write-WatchdogHeartbeat {
    $directory = Split-Path -Parent $watchdogHeartbeatPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$watchdogHeartbeatPath.tmp"
    $controlBundle = Get-RuntimeControlBundleIdentity
    [pscustomobject]@{
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
        process_id = $PID
        revision = Get-CodeRevision
        control_bundle_revision = if ($controlBundle) {
            [string]$controlBundle.source_revision
        } else { $null }
        control_bundle_exact_revision = [bool]($controlBundle -and $controlBundle.exact_revision)
        control_bundle_hash_verified = [bool]$controlBundle
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $watchdogHeartbeatPath -Force
}

function Start-WatchdogReplacement {
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
    Start-Process -FilePath $wscript -ArgumentList $arguments -WindowStyle Hidden
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
            foreach ($service in $services) {
                $row = $status | Where-Object Key -eq $service.Key
                $unhealthy = $row.State -in @(
                    "STOPPED", "DATA STALE", "API ERROR", "SYNC ERROR", "SYNC STALE",
                    "COLLECTOR STALE", "ANNOTATOR STALE", "SESSION STALE"
                )
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
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "XAUUSD Forecaster Control Center.lnk"
    $launcherPath = Join-Path $PSScriptRoot "xauusd_control_center_launcher.vbs"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:WINDIR\System32\wscript.exe"
    $shortcut.Arguments = '"{0}"' -f $launcherPath
    $shortcut.WorkingDirectory = $moduleRoot
    $shortcut.Description = "Start, stop, inspect, and configure XAUUSD Forecaster"
    $shortcut.Save()
    return $shortcutPath
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
    } elseif ($candidateState -eq "TESTING" -or $candidateState -eq "STAGING" -or $candidateState -eq "NEW") {
        "Candidate still testing"
    } elseif ($candidateState -eq "FAILED") {
        "Candidate failed validation"
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
        $releaseView.candidate_state -eq "FAILED"
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

function Show-WpfControlCenter {
    $xamlPath = Join-Path $PSScriptRoot "control_center.xaml"
    if (-not (Test-Path -LiteralPath $xamlPath)) { return $false }
    try {
        Add-Type -AssemblyName PresentationFramework
        Add-Type -AssemblyName PresentationCore
        [xml]$xaml = Get-Content -LiteralPath $xamlPath -Raw
        $reader = New-Object System.Xml.XmlNodeReader $xaml
        $window = [Windows.Markup.XamlReader]::Load($reader)

        function Find-WpfControl([string]$Name) { return $window.FindName($Name) }
        function Format-WpfIdentity($Release) {
            if (-not $Release) { return "Git       --`nWorker    --`nWindows   --" }
            $git = if ($Release.git_sha) { ([string]$Release.git_sha).Substring(0, [Math]::Min(12, ([string]$Release.git_sha).Length)) } else { "--" }
            $worker = if ($Release.worker_version_id) { ([string]$Release.worker_version_id).Substring(0, [Math]::Min(12, ([string]$Release.worker_version_id).Length)) } else { "--" }
            $windows = if ($Release.windows_revision) { ([string]$Release.windows_revision).Substring(0, [Math]::Min(12, ([string]$Release.windows_revision).Length)) } else { "--" }
            return "Git       $git`nWorker    $worker`nWindows   $windows"
        }
        function Invoke-WpfOperation([string]$Operation, [string]$ServiceKey = "") {
            $arguments = @(
                "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                "-File", ('"{0}"' -f $PSCommandPath), "-Action", $Operation,
                "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
                "-RepositoryRoot", ('"{0}"' -f $repositoryRoot)
            )
            if ($ServiceKey) { $arguments += @("-ServiceKey", $ServiceKey) }
            Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
                -WorkingDirectory $moduleRoot -WindowStyle Hidden | Out-Null
        }
        function Refresh-WpfStatus {
            $status = @(Get-ForecasterStatus)
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
            (Find-WpfControl "CandidateReason").Text = $reason
            $releaseView = Get-ControlCenterReleasePresentation -Release $release
            $validation = if ($release -and $release.candidate) {
                $release.candidate.validation
            } else { $null }
            (Find-WpfControl "CandidateChecks").Text = @(
                "Repository: $([string]$(if ($validation) { $validation.repository } else { 'unavailable' }))"
                "Windows: $([string]$(if ($validation) { $validation.windows } else { 'unavailable' }))"
                "Cloudflare: $([string]$(if ($validation) { $validation.cloudflare } else { 'unavailable' }))"
                "Data parity: $([string]$(if ($validation -and $validation.data_parity) { $validation.data_parity.state } else { 'unavailable' }))"
                "CPU / 5xx: $([string]$(if ($validation -and $validation.cpu_evidence) { $validation.cpu_evidence.gate_state } else { 'unavailable' }))"
                "Access boundary: formal-host only"
            ) -join "`n"
            (Find-WpfControl "CandidateTechnicalEvidence").Text = if ($release -and $release.candidate) {
                "validation key: $($release.candidate.validation_key)`nbrowser: $($release.candidate.browser_url)`nreason: $reason"
            } else { "No exact-version evidence loaded." }
            (Find-WpfControl "PromoteButton").IsEnabled = [bool]$releaseView.can_promote
            (Find-WpfControl "ApproveCompatibilityButton").IsEnabled = [bool]$releaseView.can_approve_compatibility
            (Find-WpfControl "OpenCandidateButton").IsEnabled = [bool]($release -and $release.candidate -and $release.candidate.browser_url)
            (Find-WpfControl "PreviousState").Text = if ($release -and $release.previous_stable) { "AVAILABLE" } else { "UNAVAILABLE" }
            (Find-WpfControl "PreviousIdentity").Text = Format-WpfIdentity $release.previous_stable
            (Find-WpfControl "ReverseButton").IsEnabled = [bool]($release -and $release.previous_stable)
        }

        (Find-WpfControl "RefreshButton").Add_Click({ Refresh-WpfStatus })
        (Find-WpfControl "StartButton").Add_Click({ Invoke-WpfOperation "Start" })
        (Find-WpfControl "RestartButton").Add_Click({ Invoke-WpfOperation "Restart" })
        (Find-WpfControl "StopButton").Add_Click({ Invoke-WpfOperation "Stop" })
        (Find-WpfControl "DashboardButton").Add_Click({ Start-Process $dashboardUrl })
        (Find-WpfControl "LogsButton").Add_Click({ Start-Process explorer.exe $logRoot })
        (Find-WpfControl "OpenStableButton").Add_Click({ Start-Process $dashboardUrl })
        (Find-WpfControl "OpenCandidateButton").Add_Click({
            $state = Get-ReleaseControlState
            if ($state -and $state.candidate -and $state.candidate.browser_url) {
                Start-Process ([string]$state.candidate.browser_url)
            }
        })
        (Find-WpfControl "ApproveCompatibilityButton").Add_Click({
            if ([System.Windows.MessageBox]::Show(
                "Approve only the displayed exact compatibility evidence?",
                "Confirm Compatibility", "YesNo", "Warning"
            ) -eq "Yes") { Invoke-WpfOperation "ApproveCompatibility" }
        })
        $window.AddHandler(
            [System.Windows.Controls.Button]::ClickEvent,
            [System.Windows.RoutedEventHandler]{
                param($sender, $eventArgs)
                $button = $eventArgs.OriginalSource
                if ($button.CommandParameter -in @("ServiceStart", "ServiceStop") -and $button.Tag) {
                    Invoke-WpfOperation ([string]$button.CommandParameter) ([string]$button.Tag)
                }
            }
        )
        (Find-WpfControl "PromoteButton").Add_Click({
            if ([System.Windows.MessageBox]::Show(
                "Promote only this fully validated Candidate?", "Confirm Promote",
                "YesNo", "Warning"
            ) -eq "Yes") { Invoke-WpfOperation "PromoteCandidate" }
        })
        (Find-WpfControl "ReverseButton").Add_Click({
            if ([System.Windows.MessageBox]::Show(
                "Reverse to the exact Previous Stable release?", "Confirm Reverse",
                "YesNo", "Warning"
            ) -eq "Yes") { Invoke-WpfOperation "ReverseStable" }
        })
        $timer = New-Object Windows.Threading.DispatcherTimer
        $timer.Interval = [TimeSpan]::FromSeconds(5)
        $timer.Add_Tick({ Refresh-WpfStatus })
        $window.Add_Closed({ $timer.Stop() })
        Refresh-WpfStatus
        $timer.Start()
        [void]$window.ShowDialog()
        return $true
    } catch {
        Write-Warning "WPF control center unavailable; using WinForms fallback: $($_.Exception.Message)"
        return $false
    }
}

function Show-ControlCenter {
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
        } elseif ($normalized -in @("DEGRADED", "PARTIAL", "TESTING", "STAGING", "NEW", "SYNC DEGRADED", "OBSERVING", "PENDING")) {
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
    $header.Controls.AddRange(@($title, $subtitle))

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
    $servicesHint = New-UiLabel -Text "Five production owners on this Windows host" -Font (New-Object System.Drawing.Font("Segoe UI Variable Text", 8.5)) -Color $muted
    $servicesHint.Location = New-Object System.Drawing.Point(19, 30)
    $servicesHint.Size = New-Object System.Drawing.Size(360, 18)
    $servicesHeader.Controls.AddRange(@($servicesTitle, $servicesHint))

    $serviceDescriptions = @{
        quote = "Receives the cTrader XAUUSD quote stream"
        collector = "Builds the five-minute decision and training ledger"
        annotator = "Classifies eligible news evidence"
        api = "Serves the local dashboard contract"
        sync = "Publishes bounded dashboard mirrors"
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
        if ($script:guiOperation -and -not $script:guiOperation.HasExited) { return }
        $arguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $PSCommandPath), "-Action", $Operation,
            "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
            "-RepositoryRoot", ('"{0}"' -f $repositoryRoot)
        )
        if ($TargetKey) { $arguments += @("-ServiceKey", $TargetKey) }
        $script:guiOperationName = $Operation
        $script:guiOperationOutputPath = Join-Path $env:TEMP `
            ("xauusd-control-operation-{0}.out" -f ([guid]::NewGuid().ToString("N")))
        $script:guiOperationErrorPath = Join-Path $env:TEMP `
            ("xauusd-control-operation-{0}.err" -f ([guid]::NewGuid().ToString("N")))
        $script:guiOperation = Start-Process -FilePath "powershell.exe" `
            -ArgumentList $arguments -WorkingDirectory $moduleRoot `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $script:guiOperationOutputPath `
            -RedirectStandardError $script:guiOperationErrorPath
        Set-GuiBusy -Busy $true -Message "Working in background: $Operation"
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
                $repositoryCheck = if ($validation -and $validation.repository) {
                    [string]$validation.repository
                } else { "WAITING" }
                $windowsCheck = if ($validation -and $validation.windows) { [string]$validation.windows } else { "WAITING" }
                $contractCheck = if ($validation -and $validation.cloudflare) { [string]$validation.cloudflare } else { "WAITING" }
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
                $candidateCheckLabels.contracts.Text = "API routes: $contractCheck"
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
            $candidateReason.Text = $releaseView.promote_reason
            $promoteButton.Enabled = $releaseView.can_promote
            $promoteButton.BackColor = if ($releaseView.can_promote) { $accent } else { $grayWash }
            $promoteButton.ForeColor = if ($releaseView.can_promote) { [System.Drawing.Color]::White } else { $gray }
            $promoteButton.FlatAppearance.BorderColor = if ($releaseView.can_promote) { $accent } else { $line }
            $toolTip.SetToolTip($promoteButton, $releaseView.promote_reason)
            $approveCompatibilityButton.Visible = $releaseView.can_approve_compatibility
            $approveCompatibilityButton.Enabled = $releaseView.can_approve_compatibility
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
        if ($script:statusRefreshProcess -and -not $script:statusRefreshProcess.HasExited) { return }
        $arguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $PSCommandPath), "-Action", "StatusJson",
            "-StatusPath", ('"{0}"' -f $statusSnapshotPath)
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
        $exitCode = $script:guiOperation.ExitCode
        $finished = $script:guiOperationName
        $output = if (Test-Path -LiteralPath $script:guiOperationOutputPath) {
            (Get-Content -LiteralPath $script:guiOperationOutputPath -Raw -ErrorAction SilentlyContinue).Trim()
        } else { "" }
        $errorText = if (Test-Path -LiteralPath $script:guiOperationErrorPath) {
            (Get-Content -LiteralPath $script:guiOperationErrorPath -Raw -ErrorAction SilentlyContinue).Trim()
        } else { "" }
        Remove-Item -LiteralPath $script:guiOperationOutputPath,$script:guiOperationErrorPath `
            -Force -ErrorAction SilentlyContinue
        $script:guiOperation.Dispose()
        $script:guiOperation = $null
        Set-GuiBusy -Busy $false -Message $(if ($exitCode -eq 0) { "Completed: $finished" } else { "Failed: $finished (exit $exitCode)" })
        if ($exitCode -ne 0) {
            [System.Windows.Forms.MessageBox]::Show(
                $(if ($errorText) { $errorText } else { "Operation failed without diagnostic output." }),
                "$finished failed", "OK", "Error"
            ) | Out-Null
        } elseif ($finished -in @("BootstrapRelease", "ApproveCompatibility")) {
            [System.Windows.Forms.MessageBox]::Show(
                $(if ($output) { $output } else { "$finished completed." }),
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
    [void]$form.ShowDialog()
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
    "Start" { Start-All; Start-Sleep -Seconds 2; Get-ForecasterStatus | Format-Table -AutoSize }
    "Stop" { Stop-All; Start-Sleep -Seconds 1; Get-ForecasterStatus | Format-Table -AutoSize }
    "Restart" { Restart-All; Start-Sleep -Seconds 2; Get-ForecasterStatus | Format-Table -AutoSize }
    "ServiceStart" {
        $target = $services | Where-Object Key -eq $ServiceKey
        if (-not $target) { throw "Unknown service key: $ServiceKey" }
        Start-ForecasterService $target
    }
    "ServiceStop" {
        $target = $services | Where-Object Key -eq $ServiceKey
        if (-not $target) { throw "Unknown service key: $ServiceKey" }
        Stop-ForecasterService $target
    }
    "Watchdog" { Start-All; exit (Invoke-ForecasterWatchdog) }
    "DiscoverCandidate" { if (Invoke-CandidateDiscovery) { exit 0 } else { exit 1 } }
    "ReconcileRelease" {
        if (-not (Enter-ReleaseTransactionLock)) { exit 1 }
        try { Reconcile-ReleaseControlState | ConvertTo-Json -Depth 12 }
        finally { Exit-ReleaseTransactionLock }
    }
    "PromoteCandidate" { if (Start-ReleasePromotion) { exit 0 } else { exit 1 } }
    "ReverseStable" { if (Invoke-ReverseStable) { exit 0 } else { exit 1 } }
    "BootstrapRelease" {
        if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
        try { Initialize-ReleaseControl | ConvertTo-Json -Depth 12 }
        finally { Exit-ReleaseTransactionLock }
    }
    "ApproveCompatibility" {
        if (-not (Enter-ReleaseTransactionLock)) { throw "Another release transaction is active." }
        try { Approve-CandidateCompatibility | ConvertTo-Json -Depth 12 }
        finally { Exit-ReleaseTransactionLock }
    }
    "EnableAutoStart" { Enable-AutoStart; Write-Output "Auto-start enabled." }
    "DisableAutoStart" { Disable-AutoStart; Write-Output "Auto-start disabled." }
    "InstallRuntime" { Install-ProductionRuntime | Format-List }
    "InstallShortcut" { Write-Output (Install-ControlShortcut) }
    default { Show-ControlCenter }
}
