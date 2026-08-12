param(
    [ValidateSet("Gui", "Status", "StatusJson", "CodeRevision", "Start", "Stop", "Restart", "ServiceStart", "ServiceStop", "Watchdog", "EnableAutoStart", "DisableAutoStart", "InstallShortcut", "InstallRuntime")]
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
$dashboardSyncConfigPath = Join-Path $moduleRoot ".local\forward\dashboard-sync.json"
$runtimeUpdateCheckInterval = [TimeSpan]::FromMinutes(5)
$reloadableServiceKeys = @("collector", "annotator", "api", "sync")
$collectorSecretsPath = Join-Path $repositoryRoot ".local\secrets\collector-keys.json"

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

function Get-DeploymentStatusUrl {
    $environmentUrl = [Environment]::GetEnvironmentVariable(
        "CLOUDFLARE_INGEST_URL", "User"
    )
    if ($environmentUrl -and $environmentUrl.StartsWith("https://")) {
        return $environmentUrl.Trim()
    }
    if (-not (Test-Path -LiteralPath $dashboardSyncConfigPath)) { return $null }
    try {
        $config = Get-Content -LiteralPath $dashboardSyncConfigPath -Raw | ConvertFrom-Json
        $targets = @($config.targets | Where-Object {
            $_.enabled -ne $false -and
            ([string]$_.remote_ingest_url).StartsWith("https://")
        })
        $cloudflare = $targets | Where-Object {
            ([string]$_.name) -eq "cloudflare" -or
            ([Uri]$_.remote_ingest_url).Host.EndsWith("workers.dev")
        } | Select-Object -First 1
        if ($cloudflare) { return ([string]$cloudflare.remote_ingest_url).Trim() }
        if (([string]$config.remote_ingest_url).StartsWith("https://")) {
            return ([string]$config.remote_ingest_url).Trim()
        }
    } catch {}
    return $null
}

function Get-DeployedMainRevision {
    $url = Get-DeploymentStatusUrl
    if (-not $url) { return $null }
    try {
        $response = Invoke-RestMethod -Method Get -Uri $url -TimeoutSec 5
        $revision = [string]$response.main_revision
        if ($response.status -eq "OK" -and $revision -match '^[0-9a-f]{40}$') {
            return $revision
        }
    } catch {}
    return $null
}

function Get-VerifiedOriginMain {
    try {
        & git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 `
            -C $repositoryRoot fetch origin main --quiet 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $revision = (& git -C $repositoryRoot rev-parse origin/main 2>$null).Trim()
        if ($LASTEXITCODE -eq 0 -and $revision -match '^[0-9a-f]{40}$') {
            Write-RuntimeUpdateState @{
                last_remote_check = [DateTimeOffset]::UtcNow.ToString("o")
                last_remote_revision = $revision
            }
            return $revision
        }
    } catch {}
    return $null
}

function Test-RevisionDescendsFrom {
    param([string]$Ancestor, [string]$Candidate)
    if (-not $Ancestor -or -not $Candidate) { return $false }
    & git -C $repositoryRoot merge-base --is-ancestor $Ancestor $Candidate 2>$null
    return $LASTEXITCODE -eq 0
}

function Test-MainCandidate {
    param([string]$CurrentRevision, [string]$CandidateRevision)
    if (-not $CandidateRevision -or $CandidateRevision -eq $CurrentRevision) {
        return $false
    }
    $state = Get-RuntimeUpdateState
    $acceptedMain = if ($state) { [string]$state.accepted_main_revision } else { "" }
    if ($acceptedMain) {
        # A PR runtime may be bootstrapped before a squash merge.  In that case
        # the new main commit is not a descendant of the PR commit, but it must
        # still advance the last independently verified main checkpoint.
        return (
            $CandidateRevision -ne $acceptedMain -and
            (Test-RevisionDescendsFrom $acceptedMain $CandidateRevision)
        )
    }
    return Test-RevisionDescendsFrom $CurrentRevision $CandidateRevision
}

function Get-DesiredMainRevision {
    param([string]$CurrentRevision)
    $state = Get-RuntimeUpdateState
    $lastCheck = [DateTimeOffset]::MinValue
    if ($state -and $state.last_remote_check) {
        [DateTimeOffset]::TryParse([string]$state.last_remote_check, [ref]$lastCheck) | Out-Null
    }
    if (([DateTimeOffset]::UtcNow - $lastCheck) -lt $runtimeUpdateCheckInterval) {
        return $null
    }
    Write-RuntimeUpdateState @{
        last_remote_check = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $deployed = Get-DeployedMainRevision
    if (-not $deployed -or $deployed -eq $CurrentRevision) { return $null }
    $verified = Get-VerifiedOriginMain
    if ($verified -eq $deployed -and (Test-MainCandidate $CurrentRevision $verified)) {
        Write-RuntimeUpdateState @{ last_deployed_revision = $deployed }
        return $verified
    }
    return $null
}

function Update-RuntimeCheckout {
    param([string]$Revision)
    if (-not $RuntimeRoot) { return $false }
    if (-not (Test-MainCandidate (Get-CodeRevision) $Revision)) { return $false }
    & git -C $moduleRoot checkout --detach --force --quiet $Revision 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    $stableScript = Join-Path $repositoryRoot ".local\runtime-control\xauusd_control_center.ps1"
    Copy-Item -LiteralPath (Join-Path $moduleRoot "scripts\xauusd_control_center.ps1") `
        -Destination $stableScript -Force
    Copy-Item -LiteralPath (Join-Path $moduleRoot "scripts\xauusd_watchdog_launcher.vbs") `
        -Destination (Join-Path (Split-Path -Parent $stableScript) "xauusd_watchdog_launcher.vbs") `
        -Force
    Copy-Item -LiteralPath (Join-Path $moduleRoot "scripts\xauusd_watchdog_guard.ps1") `
        -Destination (Join-Path (Split-Path -Parent $stableScript) "xauusd_watchdog_guard.ps1") `
        -Force
    Copy-Item -LiteralPath (Join-Path $moduleRoot "scripts\xauusd_watchdog_guard_launcher.vbs") `
        -Destination (Join-Path (Split-Path -Parent $stableScript) "xauusd_watchdog_guard_launcher.vbs") `
        -Force
    Write-RuntimeUpdateState @{
        accepted_main_revision = $Revision
        staged_revision = $Revision
        staged_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    return $true
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
    param([string]$Path, [string]$ServiceName)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $heartbeat = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        $lastSuccess = [DateTimeOffset]::MinValue
        if ([string]$heartbeat.service -ne $ServiceName -or
            [string]$heartbeat.state -ne "RUNNING" -or
            -not [DateTimeOffset]::TryParse(
                [string]$heartbeat.last_success, [ref]$lastSuccess
            )) { return $null }
        return [pscustomobject]@{ LastSuccess = $lastSuccess }
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
    param([DateTimeOffset]$ReloadStarted)
    foreach ($service in @($services | Where-Object { $_.Key -in $reloadableServiceKeys })) {
        if (@(Get-ForecasterProcesses $service).Count -eq 0) { return $false }
    }
    foreach ($heartbeatSpec in @(
        @("collector", "collector-status.json"),
        @("annotator", "news-annotator-status.json")
    )) {
        $heartbeat = Get-RuntimeHeartbeat `
            -Path (Join-Path $moduleRoot ".local\forward\$($heartbeatSpec[1])") `
            -ServiceName $heartbeatSpec[0]
        if (-not $heartbeat -or $heartbeat.LastSuccess -lt $ReloadStarted) {
            return $false
        }
    }
    try {
        $response = Invoke-WebRequest -UseBasicParsing `
            -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 2
        if ($response.StatusCode -ne 200) { return $false }
    } catch { return $false }
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
    param([string]$Revision)
    $targets = @($services | Where-Object { $_.Key -in $reloadableServiceKeys })
    $reloadStarted = [DateTimeOffset]::UtcNow
    Write-WatchdogEvent -Event "CODE_REVISION_RELOAD_STARTED" `
        -Service "collector,annotator,api,sync" -State $Revision
    foreach ($service in $targets) { Stop-ForecasterService $service }
    Start-Sleep -Milliseconds 800
    foreach ($service in $targets) {
        Start-ForecasterService $service -SkipExistingCheck
    }
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
    do {
        Start-Sleep -Milliseconds 500
        Write-WatchdogHeartbeat
        $healthy = Test-CodeReloadHealth -ReloadStarted $reloadStarted
    } while (-not $healthy -and [DateTimeOffset]::UtcNow -lt $deadline)
    if (-not $healthy) { throw "Code revision reload failed functional health checks." }
    Write-RuntimeCodeState -Revision $Revision
    Write-WatchdogEvent -Event "CODE_REVISION_RELOAD_APPLIED" `
        -Service "collector,annotator,api,sync" -State $Revision
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

function Test-BrokerMarketClosure {
    $path = Join-Path $moduleRoot ".local\forward\quotes\market-session.json"
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    try {
        $session = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        $observedAt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse(
            [string]$session.observed_at, [ref]$observedAt
        )) { return $false }
        $now = [DateTimeOffset]::UtcNow
        return (
            $observedAt -le $now.AddSeconds(5) -and
            ($now - $observedAt).TotalSeconds -le 20 -and
            $session.is_open -eq $false
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
            -ServiceName $Service.Key
        $startedAt = Get-ServiceProcessStartedAt -Processes $Processes
        if ($heartbeat -and
            $heartbeat.LastSuccess -ge $startedAt -and
            ([DateTimeOffset]::UtcNow - $heartbeat.LastSuccess).TotalSeconds -le 300) {
            return "RUNNING"
        }
        if ($startedAt -ne [DateTimeOffset]::MinValue -and
            ([DateTimeOffset]::UtcNow - $startedAt).TotalSeconds -le 180) {
            return "STARTING"
        }
        return "$($Service.Key.ToUpper()) STALE"
    }

    if ($Service.Key -eq "quote") {
        if (Test-BrokerMarketClosure) { return "MARKET CLOSED" }
        $quoteRoot = Join-Path $moduleRoot ".local\forward\quotes"
        $latestQuote = Get-ChildItem -LiteralPath $quoteRoot -Filter "*.jsonl" `
            -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -eq $latestQuote -or ((Get-Date) - $latestQuote.LastWriteTime).TotalSeconds -gt 60) {
            if (Test-ExpectedWeeklyMarketClosure) { return "MARKET CLOSED" }
            return "DATA STALE"
        }
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

function Write-WatchdogHeartbeat {
    $directory = Split-Path -Parent $watchdogHeartbeatPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$watchdogHeartbeatPath.tmp"
    [pscustomobject]@{
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
        process_id = $PID
        revision = Get-CodeRevision
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
    $lastCodeReload = [DateTimeOffset]::MinValue
    $watchdogRevisionAtStart = Get-CodeRevision
    Ensure-WatchdogGuardTask
    Write-WatchdogEvent -Event "WATCHDOG_STARTED" -Service "all" -State "MONITORING"
    while ($true) {
        Write-WatchdogHeartbeat
        try {
            $currentRevision = Get-CodeRevision
            $desiredRevision = Get-DesiredMainRevision -CurrentRevision $currentRevision
            if ($desiredRevision -and (Update-RuntimeCheckout -Revision $desiredRevision)) {
                Write-WatchdogEvent -Event "MAIN_RUNTIME_UPDATED" `
                    -Service "all" -State $desiredRevision
                $currentRevision = $desiredRevision
            }
            $runtimeState = Get-RuntimeCodeState
            $appliedRevision = if ($runtimeState) {
                [string]$runtimeState.applied_revision
            } else { "" }
            if ($currentRevision -and $currentRevision -ne $appliedRevision -and (
                [DateTimeOffset]::UtcNow - $lastCodeReload
            ).TotalSeconds -ge 120) {
                $lastCodeReload = [DateTimeOffset]::UtcNow
                Restart-CodeReloadableServices -Revision $currentRevision
                foreach ($service in $services) {
                    $lastRestart[$service.Key] = [DateTimeOffset]::UtcNow
                    $failureCounts[$service.Key] = 0
                }
                if ($currentRevision -ne $watchdogRevisionAtStart) {
                    # The launcher that started this process may predate its own
                    # supervisor loop, so start the newly copied launcher before
                    # this process exits. This makes the first upgrade self-hosting.
                    Start-WatchdogReplacement
                    return 0
                }
            }
            $status = @(Get-ForecasterStatus)
            foreach ($service in $services) {
                $row = $status | Where-Object Key -eq $service.Key
                $unhealthy = $row.State -in @(
                    "STOPPED", "DATA STALE", "API ERROR", "SYNC ERROR", "SYNC STALE",
                    "COLLECTOR STALE", "ANNOTATOR STALE"
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
    $launcherSource = Join-Path $moduleRoot "scripts\xauusd_watchdog_launcher.vbs"
    if (-not (Test-Path -LiteralPath $launcherSource)) {
        throw "Missing windowless watchdog launcher: $launcherSource"
    }
    $controlRoot = Split-Path -Parent $ControlScript
    $launcherPath = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
    if (-not $launcherSource.Equals($launcherPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $launcherSource -Destination $launcherPath -Force
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
    $guardSource = Join-Path $moduleRoot "scripts\xauusd_watchdog_guard.ps1"
    $guardPath = Join-Path $controlRoot "xauusd_watchdog_guard.ps1"
    $launcherSource = Join-Path $moduleRoot "scripts\xauusd_watchdog_guard_launcher.vbs"
    $launcherPath = Join-Path $controlRoot "xauusd_watchdog_guard_launcher.vbs"
    if (-not $guardSource.Equals($guardPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $guardSource -Destination $guardPath -Force
    }
    if (-not $launcherSource.Equals($launcherPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $launcherSource -Destination $launcherPath -Force
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
    & git -C $source fetch origin main --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Cannot verify the initial origin/main checkpoint." }
    $baseMainRevision = (& git -C $source rev-parse origin/main 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $baseMainRevision -notmatch '^[0-9a-f]{40}$') {
        throw "Cannot resolve the initial origin/main checkpoint."
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
        accepted_main_revision = $baseMainRevision
        bootstrap_revision = $revision
        installed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $controlRoot = Join-Path $sourceLocal "runtime-control"
    New-Item -ItemType Directory -Path $controlRoot -Force | Out-Null
    $stableScript = Join-Path $controlRoot "xauusd_control_center.ps1"
    Copy-Item -LiteralPath $PSCommandPath -Destination $stableScript -Force

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

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "XAUUSD Forecaster Control Center"
    $form.Size = New-Object System.Drawing.Size(720, 680)
    $form.StartPosition = "CenterScreen"
    $form.ShowInTaskbar = $true
    $form.MinimumSize = New-Object System.Drawing.Size(720, 680)
    $form.BackColor = [System.Drawing.Color]::FromArgb(235, 230, 215)
    $form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = "XAUUSD FORECASTER / LOCAL CONTROL CENTER"
    $title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 16, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(24, 20)
    $form.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = "Local process control. Dashboard mirrors are view-only and never place orders."
    $subtitle.AutoSize = $true
    $subtitle.Location = New-Object System.Drawing.Point(26, 58)
    $form.Controls.Add($subtitle)

    $statusLabels = @{}
    $actionButtons = New-Object System.Collections.ArrayList
    $rowY = 105
    foreach ($service in $services) {
        $name = New-Object System.Windows.Forms.Label
        $name.Text = $service.Label
        $name.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 11, [System.Drawing.FontStyle]::Bold)
        $name.AutoSize = $true
        $name.Location = New-Object System.Drawing.Point(30, $rowY)
        $form.Controls.Add($name)

        $status = New-Object System.Windows.Forms.Label
        $status.Text = "CHECKING"
        $status.Width = 130
        $status.Location = New-Object System.Drawing.Point(315, ($rowY + 2))
        $form.Controls.Add($status)
        $statusLabels[$service.Key] = $status

        $startButton = New-Object System.Windows.Forms.Button
        $startButton.Text = "Start"
        $startButton.Tag = $service.Key
        $startButton.Size = New-Object System.Drawing.Size(80, 30)
        $startButton.Location = New-Object System.Drawing.Point(455, ($rowY - 6))
        $startButton.Add_Click({
            param($sender)
            Invoke-GuiOperation -Operation "ServiceStart" -TargetKey $sender.Tag
        })
        $form.Controls.Add($startButton)
        [void]$actionButtons.Add($startButton)

        $stopButton = New-Object System.Windows.Forms.Button
        $stopButton.Text = "Stop"
        $stopButton.Tag = $service.Key
        $stopButton.Size = New-Object System.Drawing.Size(80, 30)
        $stopButton.Location = New-Object System.Drawing.Point(545, ($rowY - 6))
        $stopButton.Add_Click({
            param($sender)
            Invoke-GuiOperation -Operation "ServiceStop" -TargetKey $sender.Tag
        })
        $form.Controls.Add($stopButton)
        [void]$actionButtons.Add($stopButton)
        $rowY += 48
    }

    $startAll = New-Object System.Windows.Forms.Button
    $startAll.Text = "Start All"
    $startAll.Size = New-Object System.Drawing.Size(120, 38)
    $startAll.Location = New-Object System.Drawing.Point(28, 355)
    $startAll.Add_Click({ Invoke-GuiOperation -Operation "Start" })
    $form.Controls.Add($startAll)
    [void]$actionButtons.Add($startAll)

    $restartAll = New-Object System.Windows.Forms.Button
    $restartAll.Text = "Restart All"
    $restartAll.Size = New-Object System.Drawing.Size(120, 38)
    $restartAll.Location = New-Object System.Drawing.Point(158, 355)
    $restartAll.Add_Click({ Invoke-GuiOperation -Operation "Restart" })
    $form.Controls.Add($restartAll)
    [void]$actionButtons.Add($restartAll)

    $stopAll = New-Object System.Windows.Forms.Button
    $stopAll.Text = "Stop All"
    $stopAll.Size = New-Object System.Drawing.Size(120, 38)
    $stopAll.Location = New-Object System.Drawing.Point(288, 355)
    $stopAll.Add_Click({ Invoke-GuiOperation -Operation "Stop" })
    $form.Controls.Add($stopAll)
    [void]$actionButtons.Add($stopAll)

    $openSite = New-Object System.Windows.Forms.Button
    $openSite.Text = "Open Dashboard"
    $openSite.Size = New-Object System.Drawing.Size(130, 38)
    $openSite.Location = New-Object System.Drawing.Point(418, 355)
    $openSite.Add_Click({ Start-Process $dashboardUrl })
    $form.Controls.Add($openSite)

    $openLogs = New-Object System.Windows.Forms.Button
    $openLogs.Text = "Open Logs"
    $openLogs.Size = New-Object System.Drawing.Size(110, 38)
    $openLogs.Location = New-Object System.Drawing.Point(558, 355)
    $openLogs.Add_Click({ Start-Process explorer.exe $logRoot })
    $form.Controls.Add($openLogs)

    $autoLabel = New-Object System.Windows.Forms.Label
    $autoLabel.AutoSize = $true
    $autoLabel.Location = New-Object System.Drawing.Point(30, 425)
    $form.Controls.Add($autoLabel)

    $enableAuto = New-Object System.Windows.Forms.Button
    $enableAuto.Text = "Enable Auto-start"
    $enableAuto.Size = New-Object System.Drawing.Size(190, 38)
    $enableAuto.Location = New-Object System.Drawing.Point(310, 414)
    $enableAuto.Add_Click({ Enable-AutoStart })
    $form.Controls.Add($enableAuto)

    $disableAuto = New-Object System.Windows.Forms.Button
    $disableAuto.Text = "Disable Auto-start"
    $disableAuto.Size = New-Object System.Drawing.Size(150, 38)
    $disableAuto.Location = New-Object System.Drawing.Point(510, 414)
    $disableAuto.Add_Click({ Disable-AutoStart })
    $form.Controls.Add($disableAuto)

    $refreshButton = New-Object System.Windows.Forms.Button
    $refreshButton.Text = "Refresh Status"
    $refreshButton.Size = New-Object System.Drawing.Size(150, 38)
    $refreshButton.Location = New-Object System.Drawing.Point(30, 470)
    $form.Controls.Add($refreshButton)

    $clockButton = New-Object System.Windows.Forms.Button
    $clockButton.Text = "Repair Windows Time (Admin)"
    $clockButton.Size = New-Object System.Drawing.Size(220, 38)
    $clockButton.Location = New-Object System.Drawing.Point(195, 470)
    $clockButton.Add_Click({ Repair-WindowsTime })
    $form.Controls.Add($clockButton)

    $clockLabel = New-Object System.Windows.Forms.Label
    $clockLabel.AutoSize = $true
    $clockLabel.Location = New-Object System.Drawing.Point(430, 481)
    $form.Controls.Add($clockLabel)

    $operationLabel = New-Object System.Windows.Forms.Label
    $operationLabel.Text = "Ready"
    $operationLabel.AutoSize = $true
    $operationLabel.Location = New-Object System.Drawing.Point(30, 545)
    $operationLabel.ForeColor = [System.Drawing.Color]::FromArgb(82, 78, 68)
    $form.Controls.Add($operationLabel)

    $note = New-Object System.Windows.Forms.Label
    $note.Text = "A powered-off PC cannot collect data. This control center never authorizes trading."
    $note.AutoSize = $true
    $note.Location = New-Object System.Drawing.Point(30, 590)
    $form.Controls.Add($note)

    $script:guiOperation = $null
    $script:guiOperationName = ""
    function Set-GuiBusy {
        param([bool]$Busy, [string]$Message)
        foreach ($button in $actionButtons) { $button.Enabled = -not $Busy }
        $refreshButton.Enabled = -not $Busy
        $operationLabel.Text = $Message
        $form.UseWaitCursor = $Busy
    }
    function Invoke-GuiOperation {
        param([string]$Operation, [string]$TargetKey = "")
        if ($script:guiOperation -and -not $script:guiOperation.HasExited) { return }
        $arguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", ('"{0}"' -f $PSCommandPath), "-Action", $Operation
        )
        if ($TargetKey) { $arguments += @("-ServiceKey", $TargetKey) }
        $script:guiOperationName = $Operation
        $script:guiOperation = Start-Process -FilePath "powershell.exe" `
            -ArgumentList $arguments -WorkingDirectory $moduleRoot `
            -WindowStyle Hidden -PassThru
        Set-GuiBusy -Busy $true -Message "Working in background: $Operation"
    }

    $statusSnapshotPath = Join-Path $env:TEMP ("xauusd-control-status-{0}.json" -f $PID)
    $script:statusRefreshProcess = $null
    $script:lastStatusRequest = [DateTime]::MinValue
    function Apply-GuiStatus {
        param([pscustomobject]$Snapshot)
        foreach ($row in @($Snapshot.services)) {
            $label = $statusLabels[$row.Key]
            $label.Text = $row.State
            $label.ForeColor = if ($row.State -in @("RUNNING", "LIVE", "MARKET CLOSED", "API OK", "SYNC OK")) {
                [System.Drawing.Color]::FromArgb(52, 105, 38)
            } elseif ($row.State -eq "SYNC DEGRADED") {
                [System.Drawing.Color]::FromArgb(170, 105, 0)
            } else {
                [System.Drawing.Color]::FromArgb(190, 45, 36)
            }
        }
        $autoLabel.Text = if ($Snapshot.auto_start) {
            "Auto-start: enabled at Windows logon"
        } else {
            "Auto-start: disabled"
        }
        $clockLabel.Text = if ($Snapshot.windows_time_running) {
            "Windows Time: RUNNING"
        } else {
            "Windows Time: STOPPED"
        }
        $clockLabel.ForeColor = if ($Snapshot.windows_time_running) {
            [System.Drawing.Color]::FromArgb(52, 105, 38)
        } else {
            [System.Drawing.Color]::FromArgb(190, 45, 36)
        }
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
        $script:guiOperation.Dispose()
        $script:guiOperation = $null
        Set-GuiBusy -Busy $false -Message $(if ($exitCode -eq 0) { "Completed: $finished" } else { "Failed: $finished (exit $exitCode)" })
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
        } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatusPath -Encoding UTF8
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
    "EnableAutoStart" { Enable-AutoStart; Write-Output "Auto-start enabled." }
    "DisableAutoStart" { Disable-AutoStart; Write-Output "Auto-start disabled." }
    "InstallRuntime" { Install-ProductionRuntime | Format-List }
    "InstallShortcut" { Write-Output (Install-ControlShortcut) }
    default { Show-ControlCenter }
}
