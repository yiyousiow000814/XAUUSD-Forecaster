# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
function New-ServiceLaunchContract {
    param(
        [string]$Revision, [string]$Key, [string]$Label,
        [ValidateSet("PowerShell", "Python")][string]$Kind,
        [string]$Script, [string[]]$Arguments, [string]$CodeRoot = $moduleRoot
    )
    $scriptPath = [System.IO.Path]::GetFullPath((Join-Path $CodeRoot $Script))
    if (-not $scriptPath.StartsWith(
        [System.IO.Path]::GetFullPath($CodeRoot) + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) { throw "SERVICE_LAUNCH_SCRIPT_OUTSIDE_REVISION:$Key" }
    [pscustomobject]@{
        Revision = $Revision; CodeRoot = [System.IO.Path]::GetFullPath($CodeRoot)
        Key = $Key; Label = $Label; Match = [System.IO.Path]::GetFileName($Script)
        Kind = $Kind; Script = $Script; ScriptPath = $scriptPath
        Arguments = @($Arguments)
    }
}

function Resolve-ServiceContractArgument {
    param([string]$Value)
    $resolved = $Value.Replace("{runtime_forward_root}", $runtimeForwardRoot)
    $resolved = $resolved.Replace(
        "{repository_config_root}", (Join-Path $repositoryLocalRoot "config")
    )
    if ($resolved -match '\{[^}]+\}') { throw "SERVICE_LAUNCH_TOKEN_UNKNOWN:$Value" }
    return $resolved
}

function Get-LegacyQuoteAuthorityPath {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentName,
        [Parameter(Mandatory = $true)][string]$ConfigFileName
    )
    $value = ([string](Get-UserEnvironmentValue -Name $EnvironmentName)).Trim()
    if (-not $value) {
        $path = Join-Path (Join-Path $repositoryLocalRoot "config") $ConfigFileName
        if (Test-Path -LiteralPath $path) {
            $value = (Get-Content -LiteralPath $path -Raw -Encoding UTF8).Trim()
        }
    }
    if (-not $value) { return "" }
    return [System.IO.Path]::GetFullPath($value)
}

function Get-LegacyStableServiceLaunchContracts {
    param([string]$Revision, [string]$CodeRoot = $moduleRoot)
    if ($Revision -ne "783d25314b090dd7fbbf124777c3b8de517d2b85") {
        throw "LEGACY_SERVICE_LAUNCH_REVISION_UNKNOWN:$Revision"
    }
    $legacyQuoteArguments = @(
        "-Symbol","XAUUSD","-OutputDirectory",(Join-Path $runtimeForwardRoot "quotes")
    )
    $legacyCliPath = Get-LegacyQuoteAuthorityPath `
        -EnvironmentName "CTRADER_CLI_PATH" -ConfigFileName "windows_cli_path.txt"
    $legacySecretRoot = Get-LegacyQuoteAuthorityPath `
        -EnvironmentName "CTRADER_SECRET_ROOT" -ConfigFileName "windows_secret_path.txt"
    if ($legacyCliPath) { $legacyQuoteArguments += @("-CliPath", $legacyCliPath) }
    if ($legacySecretRoot) { $legacyQuoteArguments += @("-SecretRoot", $legacySecretRoot) }
    @(
        New-ServiceLaunchContract -Revision $Revision -CodeRoot $CodeRoot `
            -Key "quote" -Label "cTrader XAUUSD Local Algo" -Kind "PowerShell" `
            -Script "ctrader\XauusdForwardQuoteBridge\run_live_quote_bridge.ps1" `
            -Arguments $legacyQuoteArguments
        New-ServiceLaunchContract -Revision $Revision -CodeRoot $CodeRoot `
            -Key "collector" -Label "XAUUSD Collector" -Kind "Python" `
            -Script "scripts\run_forward_collector.py" -Arguments @(
                "--local-root",$runtimeForwardRoot,"--market-jsonl",(Join-Path $runtimeForwardRoot "quotes"),
                "--status-file",(Join-Path $runtimeForwardRoot "collector-status.json"),
                "--poll-seconds","10","--news-poll-seconds","60",
                "--minimum-training-rows","200","--retrain-interval","50")
        New-ServiceLaunchContract -Revision $Revision -CodeRoot $CodeRoot `
            -Key "annotator" -Label "Gemini News Annotator" -Kind "Python" `
            -Script "scripts\run_news_annotator.py" -Arguments @(
                "--database",(Join-Path $runtimeForwardRoot "forward-evidence.sqlite3"),
                "--status-file",(Join-Path $runtimeForwardRoot "news-annotator-status.json"),
                "--interval-seconds","60","--batch-size","0")
        New-ServiceLaunchContract -Revision $Revision -CodeRoot $CodeRoot `
            -Key "api" -Label "Dashboard API" -Kind "Python" `
            -Script "scripts\run_dashboard_api.py" -Arguments @(
                "--database",(Join-Path $runtimeForwardRoot "forward-evidence.sqlite3"))
        New-ServiceLaunchContract -Revision $Revision -CodeRoot $CodeRoot `
            -Key "sync" -Label "Dashboard Mirrors" -Kind "Python" `
            -Script "scripts\run_dashboard_sync.py" -Arguments @(
                "--config",(Join-Path $runtimeForwardRoot "dashboard-sync.json"),
                "--status-file",(Join-Path $runtimeForwardRoot "dashboard-sync-status.json"),
                "--interval-seconds","30")
    )
}

function Resolve-ServiceLaunchContracts {
    param([Parameter(Mandatory = $true)][string]$Revision, [string]$CodeRoot = $moduleRoot)
    $observed = Get-BusinessRuntimeRevision -CodeRoot $CodeRoot
    if ($observed -ne $Revision) {
        throw ("SERVICE_LAUNCH_REVISION_MISMATCH:{0}:{1}" -f $observed, $Revision)
    }
    $manifestPath = Join-Path $CodeRoot "scripts\windows-service-launch-contract.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        return @(Get-LegacyStableServiceLaunchContracts -Revision $Revision -CodeRoot $CodeRoot)
    }
    $manifestSpec = "${Revision}:scripts/windows-service-launch-contract.json"
    $expectedBlob = (& git.exe -C $CodeRoot rev-parse $manifestSpec 2>$null).Trim()
    $observedBlob = (& git.exe -C $CodeRoot hash-object --path `
        "scripts/windows-service-launch-contract.json" $manifestPath 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $expectedBlob -notmatch '^[0-9a-f]{40,64}$' -or
        $observedBlob -ne $expectedBlob) {
        throw "SERVICE_LAUNCH_MANIFEST_REVISION_MISMATCH"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    if ([string]$manifest.schema_version -ne "windows-service-launch-contract-v1") {
        throw "SERVICE_LAUNCH_MANIFEST_SCHEMA_INVALID"
    }
    $contracts = @()
    foreach ($entry in @($manifest.services)) {
        $arguments = @($entry.arguments | ForEach-Object {
            Resolve-ServiceContractArgument -Value ([string]$_)
        })
        $contracts += New-ServiceLaunchContract -Revision $Revision -CodeRoot $CodeRoot `
            -Key ([string]$entry.key) -Label ([string]$entry.label) `
            -Kind ([string]$entry.kind) -Script ([string]$entry.script) `
            -Arguments $arguments
    }
    $keys = @($contracts.Key)
    if ($keys.Count -ne 6 -or @($keys | Sort-Object -Unique).Count -ne 6 -or
        @($keys | Where-Object { $_ -notin @("quote","collector","annotator","api","sync","broadcast") }).Count) {
        throw "SERVICE_LAUNCH_MANIFEST_SERVICE_SET_INVALID"
    }
    return $contracts
}

function Test-BroadcastPublisherEnabled {
    [string](Get-UserEnvironmentValue -Name "AURUM_LIVE_BROADCAST_PUBLISHER_ENABLED") -eq "1"
}

function Get-BroadcastPublisherToken {
    [string](Get-UserEnvironmentValue -Name "LIVE_BROADCAST_PUBLISH_TOKEN")
}

function Get-ForecasterProcessSnapshot {
    param([switch]$RequireCompleteInventory)
    $enumerationErrorAction = if ($RequireCompleteInventory) { 'Stop' } else { 'SilentlyContinue' }
    $processes = @(Get-CimInstance Win32_Process -ErrorAction $enumerationErrorAction |
        Where-Object { $_.Name -in @("python.exe", "powershell.exe") })
    if ($RequireCompleteInventory -and @($processes | Where-Object { -not $_.CommandLine }).Count -gt 0) {
        throw 'RUNTIME_RECOVERY_PROCESS_INVENTORY_UNKNOWN'
    }
    return $processes
}

function Test-ForecasterServiceProcess {
    param([object]$Process, [pscustomobject]$Service)
    if (-not $Process.CommandLine) { return $false }
    if ($Process.CommandLine.IndexOf(
        [string]$Service.ScriptPath,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -lt 0) { return $false }
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
    param([pscustomobject]$Service, [switch]$RequireCompleteInventory)
    @(Get-ForecasterProcessSnapshot -RequireCompleteInventory:$RequireCompleteInventory |
        Where-Object { Test-ForecasterServiceProcess -Process $_ -Service $Service })
}

function Get-CodeRevision {
    try {
        $read = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $moduleRoot, "rev-parse", "HEAD")
        $revision = ([string]$read.stdout).Trim()
        if ($read.exit_code -eq 0 -and $revision -match '^[0-9a-f]{40}$') {
            return $revision
        }
    } catch {}
    return $null
}

function Get-RuntimeUpdateState {
    if (-not (Test-Path -LiteralPath $runtimeUpdateStatePath)) { return $null }
    try {
        Get-Content -LiteralPath $runtimeUpdateStatePath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
    } catch { $null }
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
    $read = Invoke-Utf8NativeProcess -FilePath $Python `
        -Arguments @("-c", $copy, $SourceDatabase, $TargetDatabase) `
        -Environment @{ PYTHONUTF8 = "1" }
    if ($read.exit_code -ne 0) {
        throw "candidate evidence copy failed: $((@($read.stdout_lines) + @($read.stderr_lines)) -join "`n")"
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
    $read = Invoke-Utf8NativeProcess -FilePath $Python `
        -Arguments @("-c", $migration, $StageRoot, $TargetDatabase) `
        -Environment @{ PYTHONUTF8 = "1" }
    if ($read.exit_code -ne 0) {
        throw "candidate evidence migration failed: $((@($read.stdout_lines) + @($read.stderr_lines)) -join "`n")"
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
        $tail = (Get-Content -LiteralPath $Path -Tail 40 -Encoding UTF8 `
            -ErrorAction Stop) -join "`n"
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
    $database = Join-Path $runtimeForwardRoot "forward-evidence.sqlite3"
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
        $candidateStateRoot = Join-Path $runtimeLocalRoot "preflight"
        $candidateDatabase = Join-Path $candidateStateRoot "forward-evidence.sqlite3"
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
        $priorPythonUtf8 = $env:PYTHONUTF8
        try {
            $env:PYTHONUTF8 = "1"
            $process = Start-Process -FilePath $python -ArgumentList @(
                (Join-Path $stageRoot "scripts\run_dashboard_api.py"),
                "--state-root", $candidateStateRoot,
                "--runtime-role", "preflight",
                "--database", $candidateDatabase, "--host", "127.0.0.1",
                "--port", [string]$preflightPort
            ) -WorkingDirectory $stageRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        } finally {
            $env:PYTHONUTF8 = $priorPythonUtf8
        }
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
        $shape = Invoke-Utf8NativeProcess -FilePath $python -Arguments $arguments `
            -WorkingDirectory $stageRoot -Environment @{ PYTHONUTF8 = "1" }
        if ($shape.exit_code -ne 0) {
            $failureCode = "PRODUCTION_SHAPE_REJECTED"
            $productionShapeOutput = Protect-PreflightDiagnosticText `
                ((@($shape.stdout_lines) + @($shape.stderr_lines)) -join "`n")
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
        if ($candidateStateRoot -and (Test-Path -LiteralPath $candidateStateRoot)) {
            Remove-Item -LiteralPath $candidateStateRoot -Recurse -Force
        }
        & git -C $repositoryRoot worktree prune 2>$null
    }
}

function Update-RuntimeCheckout {
    param([string]$Revision)
    if (-not $RuntimeRoot) { return $false }
    $previousRevision = Get-CodeRevision
    $releaseState = Get-ReleaseControlState
    $transaction = if ($releaseState) { $releaseState.transaction } else { $null }
    $authority = if ($transaction) { $transaction.evidence_authority } else { $null }
    if (-not $releaseState -or -not $transaction -or
        [string]$transaction.type -ne "PROMOTE" -or -not $authority -or
        [string]$transaction.target.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$authority.target_identity.windows_revision -ne $Revision -or
        [string]$authority.target_identity.validation_key -ne
            [string]$transaction.target.validation_key) { return $false }
    $promoteReceipt = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
        -ValidationKey ([string]$authority.validation_key) -Node "promote_attempt"
    if (-not $promoteReceipt -or [string]$promoteReceipt.receipt_digest -cne
        [string]$authority.promote_receipt_digest) { return $false }
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
        $script:services = @(Resolve-ServiceLaunchContracts -Revision $Revision)
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
            $script:services = @(Resolve-ServiceLaunchContracts -Revision $previousRevision)
        }
        Write-RuntimeUpdateFailure -Revision $Revision -Status "SWITCH_FAILED" `
            -Message "Candidate switch failed before service reload; the current version is still running: $reason"
        return $false
    }
}

function Get-RuntimeCodeState {
    if (-not (Test-Path -LiteralPath $runtimeCodeStatePath)) { return $null }
    try {
        return Get-Content -LiteralPath $runtimeCodeStatePath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
    } catch {
        return $null
    }
}

function Write-RuntimeCodeState {
    param([string]$Revision)
    $servicePids = @{}
    foreach ($service in @($services | Where-Object { $_.Key -in $reloadableServiceKeys })) {
        $servicePids[$service.Key] = @(
            Get-ForecasterProcesses $service | ForEach-Object { $_.ProcessId }
        )
    }
    $state = [pscustomobject]@{
        applied_revision = $Revision
        applied_at = [DateTimeOffset]::UtcNow.ToString("o")
        service_pids = $servicePids
    }
    Write-ControlCenterJsonAtomic -Path $runtimeCodeStatePath `
        -Value $state -Depth 4
}

function Get-RuntimeHeartbeat {
    param(
        [string]$Path,
        [string]$ServiceName,
        [string[]]$AllowedStates = @("RUNNING")
    )
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $heartbeat = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $lastSuccess = ConvertTo-ReleaseTimestampUtc -Value $heartbeat.last_success
        if ([string]$heartbeat.service -ne $ServiceName -or
            [string]$heartbeat.state -notin $AllowedStates -or
            $lastSuccess -eq [DateTimeOffset]::MinValue) { return $null }
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
        if ([string]$heartbeatSpec[0] -notin $RequiredServiceKeys) { continue }
        # Collector reconciliation can temporarily keep the annotator waiting
        # on SQLite during a coordinated reload.  A fresh STARTING heartbeat
        # proves either candidate process launched; the subsequent observation
        # boundary still requires real decision cycles and rolls back a stuck
        # startup.
        $heartbeat = Get-RuntimeHeartbeat `
            -Path (Join-Path $runtimeForwardRoot $heartbeatSpec[1]) `
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
    $statusFile = Join-Path $runtimeForwardRoot "dashboard-sync-status.json"
    if (-not (Test-Path -LiteralPath $statusFile)) { return $false }
    try {
        $syncStatus = Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $lastAttempt = ConvertTo-ReleaseTimestampUtc -Value $syncStatus.last_attempt
        if ($lastAttempt -eq [DateTimeOffset]::MinValue) { return $false }
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
        $read = Invoke-Utf8NativeProcess -FilePath $python -Arguments $arguments `
            -WorkingDirectory $moduleRoot -Environment @{ PYTHONUTF8 = "1" }
        $exitCode = [int]$read.exit_code
        $resultText = ((@($read.stdout_lines) + @($read.stderr_lines)) -join "`n")
        if ($exitCode -eq 75) {
            try {
                $payload = $resultText | ConvertFrom-ReleaseControlJson
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
        [ValidateSet("PROMOTE", "REVERSE", "RECOVERY_HOTFIX", "RESTORE_LKG")]
        [string]$Mode = "PROMOTE",
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
        observation_deferred_projection_evidence = $null
        observation_deferred_projection_passed_at = $null
        observation_projection_boundary_at = $ProjectionBoundary.ToString("o")
        observation_deferred_code = $null
        observation_deferred_at = $null
        observation_original_failure_reason = $null
        observation_original_failure_evidence = $null
        observation_original_failed_at = $null
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
        $rollbackState = Get-ReleaseControlState
        $recoveryPlan = if ($rollbackState -and $rollbackState.transaction) {
            $rollbackState.transaction.recovery_plan
        } else { $null }
        if (-not $recoveryPlan -or
            [string]$recoveryPlan.body.stable_revision -ne $PreviousRevision) {
            throw "RUNTIME_ROLLBACK_CAPTURED_AUTHORITY_REQUIRED"
        }
        try {
            Cancel-DeferredProjectionSyncRequest -FailedRevision $FailedRevision
        } catch {
            Write-WatchdogEvent -Event "DEFERRED_PROJECTION_SYNC_CANCEL_DEGRADED" `
                -Service "sync" -State (
                    Protect-PreflightDiagnosticText $_.Exception.Message
                )
        }
        $recoveryStarted = [DateTimeOffset]::UtcNow
        $null = Restore-RuntimeRecoveryPlan -Plan $recoveryPlan
        try {
            $recoveredBaseline = Wait-RuntimeRecoveryPlanHealth -Plan $recoveryPlan `
                -RecoveryStarted $recoveryStarted
        } catch {
            throw "RUNTIME_ROLLBACK_HEALTH_FAILED:$($_.Exception.Message)"
        }
        Write-RuntimeCodeState -Revision $PreviousRevision
        $degradedRollback = [bool]$recoveryPlan.body.collector_clock_recovery
        Write-RuntimeUpdateFailure -Revision $FailedRevision -Status $(if ($degradedRollback) {
            'ROLLED_BACK_DEGRADED_BASELINE'
        } else { 'ROLLED_BACK' }) `
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
            $authority = $releaseState.transaction.evidence_authority
            if ($authority) {
                $observeInput = [pscustomobject][ordered]@{
                    transaction_id = [string]$releaseState.transaction.id
                    target_identity = $authority.target_identity
                    observe_contract = [pscustomobject][ordered]@{
                        terminal_state = "FAILED"
                        reason = Protect-PreflightDiagnosticText $Reason
                        rollback_restored_lkg = -not $degradedRollback
                        rollback_baseline_health = if ($degradedRollback) { 'DEGRADED_RECOVERY_BASELINE' } else { 'HEALTHY' }
                    }
                }
                $observeNow = [DateTimeOffset]::UtcNow
                $observeArguments = New-ReleaseEvidenceAdapterArguments `
                    -Candidate $releaseState.candidate -BehaviorInputs $observeInput `
                    -SourceIdentity ([pscustomobject]@{
                        qualification_state = "FAILED"
                        transaction_id = [string]$releaseState.transaction.id
                        target_identity = $authority.target_identity
                        terminal_state = "FAILED"
                    }) -StartedAt $observeNow -CompletedAt $observeNow `
                    -WhyRan $(if ($degradedRollback) { 'OBSERVE_TERMINAL_FAILED_DEGRADED_BASELINE_RESTORED' } else { 'OBSERVE_TERMINAL_FAILED_LKG_RESTORED' })
                $observeArguments.State = "FAILED"
                $null = Publish-ObserveAttemptEvidence -Arguments $observeArguments
            }
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
            $releaseState.deployment_status = if ($degradedRollback) { 'RECOVERY_REQUIRED' } else { 'READY' }
            $releaseState.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $releaseState
            Write-ReleaseHistory -Event "PROMOTION_REVERSED" -Release $prior `
                -Detail @{ reason = $Reason; baseline_health = $recoveredBaseline.baseline_health }
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
        } elseif ($releaseState -and $releaseState.transaction -and
            [string]$releaseState.transaction.type -eq "RECOVERY") {
            $lkg = $releaseState.transaction.previous
            Invoke-CloudflareDeployment `
                -StableVersionId ([string]$lkg.worker_version_id) `
                -Message "failed recovery restore lkg $([string]$releaseState.transaction.id)"
            $releaseState.transaction = $null
            $releaseState.deployment_status = "RECOVERY_REQUIRED"
            $releaseState.drift = [pscustomobject]@{
                code = "RECOVERY_LKG_OBSERVATION_FAILED"
                reason = $Reason
                observed_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            $releaseState.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $releaseState
            Write-ReleaseHistory -Event "RECOVERY_LKG_OBSERVATION_FAILED" `
                -Release $lkg -Detail @{ reason = $Reason }
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
        $statusPath = Join-Path $runtimeForwardRoot "live-broadcast-publisher-status.json"
        if (-not (Test-Path -LiteralPath $statusPath)) { return "DEGRADED" }
        try {
            $publisher = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            $lastSuccess = ConvertTo-ReleaseTimestampUtc -Value $publisher.last_success
            $fresh = $lastSuccess -ne [DateTimeOffset]::MinValue -and
                ([DateTimeOffset]::UtcNow - $lastSuccess) -le $broadcastFreshnessThreshold
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
            -Path (Join-Path $runtimeForwardRoot $statusName) `
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
        $quoteRoot = Join-Path $runtimeForwardRoot "quotes"
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
        $statusFile = Join-Path $runtimeForwardRoot "dashboard-sync-status.json"
        if (-not (Test-Path -LiteralPath $statusFile)) { return "STARTING" }
        try {
            $syncStatus = Get-Content -LiteralPath $statusFile -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            $lastSuccess = if ($syncStatus.last_success) {
                ConvertTo-ReleaseTimestampUtc -Value $syncStatus.last_success
            } else { $null }
            $lastAttempt = if ($syncStatus.last_attempt) {
                ConvertTo-ReleaseTimestampUtc -Value $syncStatus.last_attempt
            } else { $null }
            if ($lastSuccess -eq [DateTimeOffset]::MinValue) { $lastSuccess = $null }
            if ($lastAttempt -eq [DateTimeOffset]::MinValue) { $lastAttempt = $null }
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
    if ($Service.Key -eq 'collector' -and (Test-CollectorClockRecoveryHold)) {
        throw 'COLLECTOR_CLOCK_RECOVERY_REQUIRED'
    }
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
        $scriptPath = [string]$Service.ScriptPath
        $rawArguments = @(
            "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
            "-File", $scriptPath
        ) + @($Service.Arguments)
        $arguments = @($rawArguments | ForEach-Object {
            ConvertTo-NativeProcessArgument -Argument ([string]$_)
        })
        Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
            -WorkingDirectory ([string]$Service.CodeRoot) -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    } else {
        $rawArguments = @([string]$Service.ScriptPath) + @($Service.Arguments)
        $arguments = @($rawArguments | ForEach-Object {
            ConvertTo-NativeProcessArgument -Argument ([string]$_)
        })
        Start-Process -FilePath "python" -ArgumentList $arguments `
            -WorkingDirectory ([string]$Service.CodeRoot) -WindowStyle Hidden `
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
        if ($service.Key -eq 'collector' -and (Test-CollectorClockRecoveryHold)) { continue }
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
    $eventRecord = [pscustomobject]@{
        time = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        service = $Service
        state = $State
    }
    Write-ControlCenterDiagnosticEvent -Path $watchdogLog -Event $eventRecord
}

function Get-RuntimeControlBundleIdentity {
    Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $PSScriptRoot
}

function Assert-ActiveControlBundle {
    $identity = Get-RuntimeControlBundleIdentityAtRoot `
        -ControlRoot $PSScriptRoot -RequireDependencyClosure
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
    $currentScript = [System.IO.Path]::GetFullPath($controlCenterEntrypointPath)
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
    $ownership = $script:watchdogOwnershipContext
    if (-not $ownership -or -not $ownership.acquired) {
        throw "WATCHDOG_SINGLETON_NOT_OWNED"
    }
    $receiptMode = if ($SupervisionMode -eq "QUIESCED") {
        "QUIESCED_INSTALL"
    } else { "ACTIVE" }
    if ([string]$ownership.receipt.mode -ne $receiptMode -or
        [string]$ownership.receipt.install_transaction_id -ne $InstallTransactionId) {
        Update-WatchdogSingletonMode -Mode $receiptMode `
            -InstallTransactionId $InstallTransactionId
        $ownership = $script:watchdogOwnershipContext
    }
    $controlBundle = Get-RuntimeControlBundleIdentity
    $processIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
    $heartbeat = [pscustomobject]@{
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
        instance_id = [string]$ownership.receipt.instance_id
        owner_receipt_digest = [string]$ownership.receipt_digest
        mutex_identity_hash = [string]$ownership.descriptor.mutex_identity_hash
        collector_clock_recovery = $(
            $incidentContext = Get-CollectorClockRecoveryContext
            if ($incidentContext) {
                [pscustomobject]@{
                    incident = [string]$incidentContext.incident
                    baseline = 'DEGRADED_RECOVERY_BASELINE'
                    broken_revision = [string]$incidentContext.broken_revision
                    target_revision = [string]$incidentContext.target_revision
                }
            } else { $null }
        )
    }
    Write-ControlCenterJsonAtomic -Path $watchdogHeartbeatPath `
        -Value $heartbeat -Depth 6
}

function Get-ControlPlaneProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [switch]$RequireCompleteInventory
    )
    $enumerationErrorAction = if ($RequireCompleteInventory) { 'Stop' } else { 'SilentlyContinue' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" `
        -ErrorAction $enumerationErrorAction
    if (-not $process) { return $null }
    $created = [DateTimeOffset]$process.CreationDate
    $ownerSid = $null
    try {
        $owner = Invoke-CimMethod -InputObject $process -MethodName GetOwnerSid `
            -ErrorAction Stop
        if ([int]$owner.ReturnValue -eq 0) { $ownerSid = [string]$owner.Sid }
    } catch {
        if ($RequireCompleteInventory) { throw 'CONTROL_PLANE_PROCESS_IDENTITY_UNAVAILABLE' }
        $ownerSid = $null
    }
    if ($RequireCompleteInventory -and (-not $ownerSid -or -not $process.CommandLine)) {
        throw 'CONTROL_PLANE_PROCESS_IDENTITY_UNAVAILABLE'
    }
    [pscustomobject]@{
        process_id = [int]$process.ProcessId
        parent_process_id = [int]$process.ParentProcessId
        process_start_token = $created.ToUniversalTime().ToString("o")
        name = [string]$process.Name
        command_line = [string]$process.CommandLine
        owner_sid = $ownerSid
    }
}

function Get-WatchdogOwnershipInventory {
    param([switch]$RequireCompleteInventory)
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $controlScript = Join-Path $controlRoot "xauusd_control_center.ps1"
    $enumerationErrorAction = if ($RequireCompleteInventory) { 'Stop' } else { 'SilentlyContinue' }
    $candidates = @(
        Get-CimInstance Win32_Process -ErrorAction $enumerationErrorAction |
            Where-Object {
                $_.Name -eq "powershell.exe" -and $_.CommandLine -and
                $_.CommandLine.Contains($controlScript) -and
                $_.CommandLine -match '(?i)-Action\s+Watchdog' -and
                $_.CommandLine.Contains($moduleRoot) -and
                $_.CommandLine.Contains($repositoryRoot)
            } | ForEach-Object {
                $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId) `
                    -RequireCompleteInventory:$RequireCompleteInventory
                if ($RequireCompleteInventory -and -not $identity) {
                    throw 'CONTROL_PLANE_PROCESS_INVENTORY_CHANGED'
                }
                $identity
            }
    )
    $shaped = @()
    $legacyOrphaned = @()
    $authoritative = @()
    $duplicates = @()
    $unknown = @()
    $expectedLauncher = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
    $expectedOwnerSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    foreach ($identity in $candidates) {
        $launcher = Get-ControlPlaneProcessIdentity `
            -ProcessId ([int]$identity.parent_process_id) `
            -RequireCompleteInventory:$RequireCompleteInventory
        if (-not $launcher) {
            if ([string]$identity.owner_sid -eq [string]$expectedOwnerSid) {
                $legacyOrphaned += $identity
            } else {
                $unknown += $identity
            }
            continue
        }
        if ($launcher.name -ne "wscript.exe" -or
            -not $launcher.command_line.Contains($expectedLauncher) -or
            -not $launcher.command_line.Contains($moduleRoot) -or
            -not $launcher.command_line.Contains($repositoryRoot)) {
            $unknown += $identity
            continue
        }
        $identity | Add-Member -NotePropertyName launcher_identity `
            -NotePropertyValue $launcher
        $shaped += $identity
    }
    $receipt = $null
    $receiptReadFailed = $false
    try { $receipt = Read-WatchdogOwnerReceipt }
    catch {
        $receiptReadFailed = $true
        $unknown = @($unknown) + @($shaped)
        $shaped = @()
    }
    if (($receipt -or $receiptReadFailed) -and $legacyOrphaned.Count -gt 0) {
        $unknown = @($unknown) + @($legacyOrphaned)
        $legacyOrphaned = @()
    }
    if ($receipt) {
        $descriptor = Get-WatchdogSingletonDescriptor
        if (-not (Test-WatchdogOwnerReceiptShape -Receipt $receipt -Descriptor $descriptor)) {
            $unknown = @($unknown) + @($shaped)
        } else {
            foreach ($identity in $shaped) {
                $isOwner = [int]$identity.process_id -eq [int]$receipt.process_id -and
                    (Test-ControlPlaneStartTokenEqual -Left $identity.process_start_token `
                        -Right $receipt.process_start_token) -and
                    [int]$identity.launcher_identity.process_id -eq [int]$receipt.launcher_pid -and
                    (Test-ControlPlaneStartTokenEqual `
                        -Left $identity.launcher_identity.process_start_token `
                        -Right $receipt.launcher_start_token)
                if ($isOwner) {
                    $identity | Add-Member -NotePropertyName watchdog_owner_receipt `
                        -NotePropertyValue $receipt -Force
                    $identity | Add-Member -NotePropertyName watchdog_owner_state `
                        -NotePropertyValue ([string]$receipt.mode) -Force
                    $authoritative += $identity
                } else { $duplicates += $identity }
            }
        }
    } elseif ($shaped.Count -gt 0) { $duplicates = @($shaped) }
    [pscustomobject]@{
        authoritative = @($authoritative)
        duplicate_shaped = @($duplicates)
        legacy_orphaned = @($legacyOrphaned)
        unknown = @($unknown)
        receipt = $receipt
    }
}

function Get-VerifiedWatchdogOwners {
    param([switch]$AllowLegacySingleOwner, [switch]$RequireCompleteInventory)
    $inventory = Get-WatchdogOwnershipInventory -RequireCompleteInventory:$RequireCompleteInventory
    if ($inventory.authoritative.Count -eq 1 -and
        $inventory.duplicate_shaped.Count -eq 0 -and
        $inventory.legacy_orphaned.Count -eq 0 -and
        $inventory.unknown.Count -eq 0) {
        return @($inventory.authoritative)
    }
    if ($AllowLegacySingleOwner -and -not $inventory.receipt -and
        $inventory.duplicate_shaped.Count -eq 1 -and
        $inventory.legacy_orphaned.Count -eq 0 -and
        $inventory.unknown.Count -eq 0) {
        $legacy = $inventory.duplicate_shaped[0]
        $legacy | Add-Member -NotePropertyName watchdog_owner_state `
            -NotePropertyValue 'LEGACY_SINGLE_OWNER' -Force
        return @($legacy)
    }
    return @()
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
        $heartbeat = Get-Content -LiteralPath $watchdogHeartbeatPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $observedAt = ConvertTo-ReleaseTimestampUtc -Value $heartbeat.observed_at
        if ($observedAt -eq [DateTimeOffset]::MinValue) {
            throw "invalid watchdog timestamp"
        }
    } catch {
        throw "CONTROL_PLANE_CURRENT_WATCHDOG_HEARTBEAT_INVALID"
    }
    if (($observedAt -gt [DateTimeOffset]::UtcNow.AddSeconds(30)) -or
        ([DateTimeOffset]::UtcNow - $observedAt).TotalSeconds -gt 120 -or
        [int]$heartbeat.process_id -ne [int]$Owner.process_id -or
        ([string]$heartbeat.process_start_token -and
         -not (Test-ControlPlaneStartTokenEqual `
            -Left $heartbeat.process_start_token `
            -Right $Owner.process_start_token)) -or
        [string]$heartbeat.control_bundle_revision -ne $ExpectedRevision -or
        -not [bool]$heartbeat.control_bundle_exact_revision -or
        -not [bool]$heartbeat.control_bundle_hash_verified) {
        throw "CONTROL_PLANE_CURRENT_WATCHDOG_HEARTBEAT_MISMATCH"
    }
    if ([string]$Owner.watchdog_owner_state -ne 'LEGACY_SINGLE_OWNER') {
        $receipt = $Owner.watchdog_owner_receipt
        if (-not $receipt -or
            [string]$receipt.installed_control_revision -ne $ExpectedRevision -or
            [string]$receipt.mode -ne 'ACTIVE' -or
            [string]$heartbeat.instance_id -ne [string]$receipt.instance_id -or
            [string]$heartbeat.owner_receipt_digest -ne
                (Get-WatchdogOwnerReceiptDigest -Receipt $receipt) -or
            [string]$heartbeat.mutex_identity_hash -ne
                [string]$receipt.mutex_identity_hash) {
            throw "CONTROL_PLANE_CURRENT_WATCHDOG_HEARTBEAT_MISMATCH"
        }
    }
    return $heartbeat
}

function Get-ControlPlaneIsolationSnapshot {
    param([switch]$RequireCompleteInventory)
    $completeSnapshot = if ($RequireCompleteInventory) {
        @(Get-ForecasterProcessSnapshot -RequireCompleteInventory)
    } else { @() }
    $serviceIdentities = [ordered]@{}
    foreach ($service in $services) {
        $processes = if ($RequireCompleteInventory) {
            @($completeSnapshot | Where-Object {
                Test-ForecasterServiceProcess -Process $_ -Service $service
            })
        } else { @(Get-ForecasterProcesses -Service $service) }
        $serviceIdentities[$service.Key] = @(
            $processes | ForEach-Object {
                $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId) `
                    -RequireCompleteInventory:$RequireCompleteInventory
                if ($RequireCompleteInventory -and -not $identity) {
                    throw 'CONTROL_PLANE_PROCESS_INVENTORY_CHANGED'
                }
                $identity
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
        [object]$ReleaseState,
        [object]$CollectorClockRecoveryBaseline = $null
    )
    if ($CollectorClockRecoveryBaseline) {
        Assert-CollectorClockRecoveryContext -Context $CollectorClockRecoveryBaseline
        if ([string]$Snapshot.business_runtime_revision -cne
            [string]$CollectorClockRecoveryBaseline.broken_revision -or
            [string]$ReleaseState.stable.windows_revision -cne
            [string]$CollectorClockRecoveryBaseline.broken_revision) {
            throw 'COLLECTOR_RECOVERY_BASELINE_REVISION_CHANGED'
        }
    }
    foreach ($service in $services) {
        $owners = @($Snapshot.services.($service.Key))
        $required = Test-ControlPlaneServiceOwnerRequired -Service $service `
            -ReleaseState $ReleaseState
        if ($CollectorClockRecoveryBaseline -and $service.Key -eq 'collector') { $required = $false }
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
            (Test-ControlPlaneStartTokenEqual `
                -Left $observed.process_start_token `
                -Right $installer.process_start_token))
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
        (Test-ControlPlaneStartTokenEqual `
            -Left $observed.process_start_token `
            -Right $installer.process_start_token))
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
        -ProcessId ([int]$oldOwner.process_id) `
        -RequireCompleteInventory:([bool]$State.collector_clock_recovery)
    if ($oldObserved -and (Test-ControlPlaneStartTokenEqual `
            -Left $oldObserved.process_start_token `
            -Right $oldOwner.process_start_token)) {
        throw "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"
    }
    $owners = @(Get-VerifiedWatchdogOwners -RequireCompleteInventory:([bool]$State.collector_clock_recovery))
    $currentIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
    if ($owners.Count -ne 1 -or -not $currentIdentity -or
        [int]$owners[0].process_id -ne $PID -or
        -not (Test-ControlPlaneStartTokenEqual `
            -Left $owners[0].process_start_token `
            -Right $currentIdentity.process_start_token)) {
        throw "CONTROL_PLANE_RECOVERY_EXACTLY_ONE_REPLACEMENT_REQUIRED"
    }
    if (-not (Test-Path -LiteralPath $watchdogHeartbeatPath)) {
        throw "CONTROL_PLANE_RECOVERY_QUIESCED_ACK_MISSING"
    }
    try {
        $heartbeat = Get-Content -LiteralPath $watchdogHeartbeatPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
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
        -not (Test-ControlPlaneStartTokenEqual `
            -Left $heartbeat.process_start_token `
            -Right $currentIdentity.process_start_token)) {
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
                (Join-Path $releaseLockPath "owner.json") -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
        } catch {}
        if (-not $lockOwner -or
            [int]$lockOwner.owner_pid -ne
                [int]$State.install_owner_identity.process_id -or
            -not (Test-ControlPlaneStartTokenEqual `
                -Left $lockOwner.owner_process_start_token `
                -Right $State.install_owner_identity.process_start_token)) {
            throw "CONTROL_PLANE_RECOVERY_CONCURRENT_RELEASE_LOCK"
        }
    }
    $baseline = $State.isolation_before
    if (-not $baseline) {
        throw "CONTROL_PLANE_RECOVERY_BASELINE_MISSING"
    }
    Assert-ControlPlaneIsolationBaseline -Snapshot $baseline `
        -ReleaseState $release -CollectorClockRecoveryBaseline $State.collector_clock_recovery
    $currentIsolation = Get-ControlPlaneIsolationSnapshot `
        -RequireCompleteInventory:([bool]$State.collector_clock_recovery)
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
                -not (Test-ControlPlaneStartTokenEqual `
                    -Left $beforeProcesses[$index].process_start_token `
                    -Right $afterProcesses[$index].process_start_token)) {
                throw "CONTROL_PLANE_INSTALL_CHANGED_SERVICE_$($service.Key.ToUpperInvariant())"
            }
        }
    }
}

function Invoke-ForecasterWatchdogOwned {
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
            $incident = Get-CollectorClockRecoveryContext
            if (-not $incident -or [string](Get-ReleaseControlState).stable.windows_revision -ceq
                [string]$incident.target_revision) { Start-CandidateDiscovery }
            $observationHealthy = Test-RuntimeObservation
            $currentRevision = Get-CodeRevision
            if ($currentRevision -ne $watchdogRevisionAtStart -and
                (-not $observationHealthy -or -not (Get-ReleaseControlState).transaction)) {
                # Only an explicit Promote/Reverse may change the checkout. Once
                # that durable transaction finishes, hand supervision to its
                # matching control bundle.
                return 76
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

function Invoke-ForecasterWatchdog {
    param([string]$InstallTransactionId = "")
    $ownership = Enter-WatchdogSingletonOwnership `
        -InstallTransactionId $InstallTransactionId
    if (-not $ownership.acquired) { return 0 }
    $result = 0
    try {
        $result = Invoke-ForecasterWatchdogOwned `
            -InstallTransactionId $InstallTransactionId
    } finally {
        Exit-WatchdogSingletonOwnership -Context $ownership
    }
    if ([int]$result -eq 76) {
        Start-WatchdogReplacement
        return 0
    }
    return [int]$result
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
    $guardArguments = '"{0}" "{1}" "{2}" "{3}" "{4}" "{5}" "{6}" "{7}"' -f `
        $launcherPath, $guardPath, $taskName, $watchdogHeartbeatPath,
        $watchdogOwnerReceiptPath, $ControlScript, $moduleRoot, $repositoryRoot
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
        Register-WatchdogGuardTask -ControlScript $controlCenterEntrypointPath -Principal $principal
        Write-WatchdogEvent -Event "WATCHDOG_GUARD_REGISTERED" `
            -Service "watchdog" -State "MONITORING"
    } catch {
        Write-WatchdogEvent -Event "WATCHDOG_GUARD_REGISTRATION_ERROR" `
            -Service "watchdog" -State $_.Exception.Message
    }
}

function Invoke-WatchdogOwnershipRepair {
    if (-not (Enter-ReleaseTransactionLock)) {
        throw 'WATCHDOG_REPAIR_RELEASE_LOCK_UNAVAILABLE'
    }
    $mainTaskWasEnabled = $false
    $guardTaskWasEnabled = $false
    try {
        $release = Get-ReleaseControlState
        if (-not $release -or $release.transaction) {
            throw 'WATCHDOG_REPAIR_RELEASE_TRANSACTION_ACTIVE'
        }
        foreach ($service in $services | Where-Object { $_.Key -ne 'broadcast' }) {
            if (@(Get-ForecasterProcesses -Service $service).Count -ne 1) {
                throw "WATCHDOG_REPAIR_BUSINESS_OWNER_INVALID:$($service.Key)"
            }
        }
        $provider = Get-ReleaseProviderRuntimeFacts -PersistedState $release `
            -ForceProviderRefresh
        $traffic = if ($provider) { $provider.active_worker_observation } else { $null }
        if (-not $traffic -or [string]$traffic.status -ne 'AVAILABLE' -or
            [double]$traffic.traffic_percent -ne 100.0 -or
            [string]$traffic.version_id -ne
                [string]$release.stable.worker_version_id) {
            throw 'WATCHDOG_REPAIR_STABLE_TRAFFIC_UNPROVED'
        }
        $inventory = Get-WatchdogOwnershipInventory
        if ($inventory.unknown.Count -gt 0) {
            throw 'UNKNOWN_WATCHDOG_IDENTITY'
        }
        if ($inventory.authoritative.Count -eq 1 -and
            $inventory.duplicate_shaped.Count -eq 0 -and
            $inventory.legacy_orphaned.Count -eq 0) {
            $installedControlRoot = Join-Path $repositoryRoot '.local\runtime-control'
            $bundle = Get-RuntimeControlBundleIdentityAtRoot `
                -ControlRoot $installedControlRoot -RequireDependencyClosure
            if (-not $bundle) { throw 'WATCHDOG_REPAIR_INSTALLED_BUNDLE_INVALID' }
            $null = Assert-CurrentWatchdogHeartbeat `
                -Owner $inventory.authoritative[0] `
                -ExpectedRevision ([string]$bundle.source_revision)
            Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop
            Enable-ScheduledTask -TaskName $guardTaskName -ErrorAction Stop
            return [pscustomobject]@{
                status = 'NOT_REQUIRED'
                owner = $inventory.authoritative[0]
                guard_enabled = $true
            }
        }
        if (-not $inventory.receipt -and
            $inventory.duplicate_shaped.Count -eq 1 -and
            $inventory.legacy_orphaned.Count -eq 0) {
            # This is the bounded bridge from the last pre-v2 bundle. Do not
            # re-enable the legacy Guard: final installation replaces this one
            # owner with a receipt-backed owner before Guard is restored.
            Disable-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue
            return [pscustomobject]@{
                status = 'LEGACY_SINGLE_OWNER_REQUIRES_CONTROL_PLANE_INSTALL'
                owner = $inventory.duplicate_shaped[0]
                guard_enabled = $false
            }
        }

        $mainTask = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        $guardTask = Get-ScheduledTask -TaskName $guardTaskName -ErrorAction Stop
        $mainTaskWasEnabled = [bool]$mainTask.Settings.Enabled
        $guardTaskWasEnabled = [bool]$guardTask.Settings.Enabled
        Disable-ScheduledTask -TaskName $guardTaskName -ErrorAction Stop
        Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Stop-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        foreach ($identity in @($inventory.authoritative) + @($inventory.duplicate_shaped)) {
            Stop-VerifiedWatchdogOwner -Identity $identity
        }
        foreach ($identity in @($inventory.legacy_orphaned)) {
            $null = Stop-WatchdogControllerOwner -RootIdentity $identity `
                -AllowLegacyReceiptless
        }
        $afterStop = Get-WatchdogOwnershipInventory
        if ($afterStop.authoritative.Count -ne 0 -or
            $afterStop.duplicate_shaped.Count -ne 0 -or
            $afterStop.legacy_orphaned.Count -ne 0 -or
            $afterStop.unknown.Count -ne 0) {
            throw 'WATCHDOG_REPAIR_TERMINATION_UNRESOLVED'
        }
        Remove-Item -LiteralPath $watchdogOwnerReceiptPath -Force -ErrorAction SilentlyContinue
        $installedControlScript = Join-Path $repositoryRoot `
            '.local\runtime-control\xauusd_control_center.ps1'
        Register-AutoStartTask -ControlScript $installedControlScript `
            -RuntimePath $moduleRoot -SourceRepository $repositoryRoot
        Disable-ScheduledTask -TaskName $guardTaskName -ErrorAction Stop
        $deadline = [DateTimeOffset]::UtcNow.AddSeconds(90)
        $owner = $null
        do {
            Start-Sleep -Milliseconds 250
            $owners = @(Get-VerifiedWatchdogOwners -AllowLegacySingleOwner)
            if ($owners.Count -eq 1) { $owner = $owners[0]; break }
        } while ([DateTimeOffset]::UtcNow -lt $deadline)
        if (-not $owner) { throw 'WATCHDOG_REPAIR_OWNER_START_TIMEOUT' }
        $installedControlRoot = Join-Path $repositoryRoot '.local\runtime-control'
        $bundle = Get-RuntimeControlBundleIdentityAtRoot `
            -ControlRoot $installedControlRoot -RequireDependencyClosure
        if (-not $bundle) { throw 'WATCHDOG_REPAIR_INSTALLED_BUNDLE_INVALID' }
        $first = Assert-CurrentWatchdogHeartbeat -Owner $owner `
            -ExpectedRevision ([string]$bundle.source_revision)
        Start-Sleep -Seconds 31
        $second = Assert-CurrentWatchdogHeartbeat -Owner $owner `
            -ExpectedRevision ([string]$bundle.source_revision)
        if ([string]$first.observed_at -eq [string]$second.observed_at) {
            throw 'WATCHDOG_REPAIR_HEARTBEAT_NOT_ADVANCING'
        }
        $legacyOwner = [string]$owner.watchdog_owner_state -eq 'LEGACY_SINGLE_OWNER'
        if (-not $legacyOwner) {
            Enable-ScheduledTask -TaskName $guardTaskName -ErrorAction Stop
        }
        return [pscustomobject]@{
            status = if ($legacyOwner) {
                'LEGACY_REPAIRED_REQUIRES_CONTROL_PLANE_INSTALL'
            } else { 'REPAIRED' }
            owner = $owner
            first_heartbeat = $first.observed_at
            second_heartbeat = $second.observed_at
            guard_enabled = [bool](-not $legacyOwner)
        }
    } finally { Exit-ReleaseTransactionLock }
}

function Enable-AutoStart {
    Register-AutoStartTask -ControlScript $controlCenterEntrypointPath `
        -RuntimePath $moduleRoot -SourceRepository $repositoryRoot
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
