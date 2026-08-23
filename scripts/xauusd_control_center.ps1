param(
    [ValidateSet("Gui", "Status", "StatusJson", "ReleaseStatusJson", "CodeRevision", "WpfLayoutSmoke", "Start", "Stop", "Restart", "ServiceStart", "ServiceStop", "Watchdog", "DiscoverCandidate", "ReconcileRelease", "PromoteCandidate", "ReverseStable", "BootstrapRelease", "VerifyMigrationCompatibility", "ApproveCompatibility", "EnableAutoStart", "DisableAutoStart", "InstallShortcut", "InstallRuntime", "InstallControlPlane")]
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
    "xauusd_control_center_runtime.ps1",
    "xauusd_control_center_release.ps1",
    "xauusd_control_center_presentation.ps1",
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


. (Join-Path $PSScriptRoot "xauusd_control_center_runtime.ps1")
. (Join-Path $PSScriptRoot "xauusd_control_center_release.ps1")
. (Join-Path $PSScriptRoot "xauusd_control_center_presentation.ps1")


if ($ExpectedControlScriptPath -or $ExpectedControlRevision) {
    $null = Assert-ControlCenterProcessIdentity `
        -ExpectedScriptPath $ExpectedControlScriptPath `
        -ExpectedRevision $ExpectedControlRevision
}

if ($OperationResultPath) {
    $structuredActions = @(
        "Start", "Stop", "Restart", "ServiceStart", "ServiceStop",
        "DiscoverCandidate", "ReconcileRelease", "PromoteCandidate",
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
    "Watchdog" { Start-All; exit (Invoke-ForecasterWatchdog) }
    "DiscoverCandidate" {
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
