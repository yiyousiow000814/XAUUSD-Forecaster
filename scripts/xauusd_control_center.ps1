param(
    [ValidateSet("Gui", "Status", "Start", "Stop", "Restart", "Watchdog", "EnableAutoStart", "DisableAutoStart", "InstallShortcut")]
    [string]$Action = "Gui"
)

$ErrorActionPreference = "Stop"
$moduleRoot = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $moduleRoot ".local\forward\logs"
$taskName = "XAUUSD-Forecaster-Autostart"
$dashboardUrl = "https://aurum-signal-room.yiyousiow1234.chatgpt.site"
$watchdogLog = Join-Path $logRoot "control-watchdog.jsonl"

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
        Label = "Sites Sync"
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

function Get-ServiceState {
    param(
        [pscustomobject]$Service,
        [array]$Processes
    )
    if ($Processes.Count -eq 0) { return "STOPPED" }

    if ($Service.Key -eq "quote") {
        $quoteRoot = Join-Path $moduleRoot ".local\forward\quotes"
        $latestQuote = Get-ChildItem -LiteralPath $quoteRoot -Filter "*.jsonl" `
            -File -Recurse -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($null -eq $latestQuote -or ((Get-Date) - $latestQuote.LastWriteTime).TotalSeconds -gt 60) {
            return "DATA STALE"
        }
        return "LIVE"
    }

    if ($Service.Key -eq "api") {
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8765/api/status" -TimeoutSec 10
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
    if ($Service.Key -eq "sync") {
        $env:SITES_BYPASS_TOKEN = [Environment]::GetEnvironmentVariable(
            "SITES_BYPASS_TOKEN", "User"
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

function Invoke-ForecasterWatchdog {
    $failureCounts = @{}
    $lastRestart = @{}
    foreach ($service in $services) {
        $failureCounts[$service.Key] = 0
        $lastRestart[$service.Key] = [DateTimeOffset]::MinValue
    }
    Write-WatchdogEvent -Event "WATCHDOG_STARTED" -Service "all" -State "MONITORING"
    while ($true) {
        try {
            $status = @(Get-ForecasterStatus)
            foreach ($service in $services) {
                $row = $status | Where-Object Key -eq $service.Key
                $unhealthy = $row.State -in @("STOPPED", "DATA STALE", "API ERROR")
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
        Start-Sleep -Seconds 30
    }
}

function Test-AutoStart {
    $null -ne (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
}

function Enable-AutoStart {
    $quotedScript = '"{0}"' -f $PSCommandPath
    $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File {0} -Action Watchdog" -f $quotedScript
    )
    $taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal `
        -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
        -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit (New-TimeSpan -Days 3650)
    Register-ScheduledTask -TaskName $taskName -Action $taskAction `
        -Trigger $taskTrigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
}

function Disable-AutoStart {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}

function Repair-WindowsTime {
    $command = "Set-Service W32Time -StartupType Automatic; Start-Service W32Time; w32tm /resync /force"
    try {
        $process = Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -Wait -PassThru -ArgumentList @(
            "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command
        )
        if ($process.ExitCode -ne 0) {
            throw "管理员命令退出码 $($process.ExitCode)"
        }
        [System.Windows.Forms.MessageBox]::Show(
            "Windows Time 已启动并请求重新同步。报价时钟偏差通常会在随后几次报价中下降。",
            "时钟修复已执行"
        ) | Out-Null
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            "无法完成时钟修复：$($_.Exception.Message)",
            "时钟修复失败"
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

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "XAUUSD Forecaster Control Center"
    $form.Size = New-Object System.Drawing.Size(720, 680)
    $form.StartPosition = "CenterScreen"
    $form.ShowInTaskbar = $true
    $form.BackColor = [System.Drawing.Color]::FromArgb(235, 230, 215)
    $form.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 10)

    $title = New-Object System.Windows.Forms.Label
    $title.Text = "XAUUSD FORECASTER / LOCAL CONTROL CENTER"
    $title.Font = New-Object System.Drawing.Font("Microsoft YaHei UI", 16, [System.Drawing.FontStyle]::Bold)
    $title.AutoSize = $true
    $title.Location = New-Object System.Drawing.Point(24, 20)
    $form.Controls.Add($title)

    $subtitle = New-Object System.Windows.Forms.Label
    $subtitle.Text = "Single local control surface for processes, logs, and auto-start. Sites is view-only."
    $subtitle.AutoSize = $true
    $subtitle.Location = New-Object System.Drawing.Point(26, 58)
    $form.Controls.Add($subtitle)

    $statusLabels = @{}
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
            $target = $services | Where-Object Key -eq $sender.Tag
            Start-ForecasterService $target
        })
        $form.Controls.Add($startButton)

        $stopButton = New-Object System.Windows.Forms.Button
        $stopButton.Text = "Stop"
        $stopButton.Tag = $service.Key
        $stopButton.Size = New-Object System.Drawing.Size(80, 30)
        $stopButton.Location = New-Object System.Drawing.Point(545, ($rowY - 6))
        $stopButton.Add_Click({
            param($sender)
            $target = $services | Where-Object Key -eq $sender.Tag
            Stop-ForecasterService $target
        })
        $form.Controls.Add($stopButton)
        $rowY += 48
    }

    $startAll = New-Object System.Windows.Forms.Button
    $startAll.Text = "Start All"
    $startAll.Size = New-Object System.Drawing.Size(120, 38)
    $startAll.Location = New-Object System.Drawing.Point(28, 355)
    $startAll.Add_Click({ Start-All })
    $form.Controls.Add($startAll)

    $restartAll = New-Object System.Windows.Forms.Button
    $restartAll.Text = "Restart All"
    $restartAll.Size = New-Object System.Drawing.Size(120, 38)
    $restartAll.Location = New-Object System.Drawing.Point(158, 355)
    $restartAll.Add_Click({ Restart-All })
    $form.Controls.Add($restartAll)

    $stopAll = New-Object System.Windows.Forms.Button
    $stopAll.Text = "Stop All"
    $stopAll.Size = New-Object System.Drawing.Size(120, 38)
    $stopAll.Location = New-Object System.Drawing.Point(288, 355)
    $stopAll.Add_Click({ Stop-All })
    $form.Controls.Add($stopAll)

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
    $clockButton.Text = "修复本机时钟（管理员）"
    $clockButton.Size = New-Object System.Drawing.Size(220, 38)
    $clockButton.Location = New-Object System.Drawing.Point(195, 470)
    $clockButton.Add_Click({ Repair-WindowsTime })
    $form.Controls.Add($clockButton)

    $clockLabel = New-Object System.Windows.Forms.Label
    $clockLabel.AutoSize = $true
    $clockLabel.Location = New-Object System.Drawing.Point(430, 481)
    $form.Controls.Add($clockLabel)

    $note = New-Object System.Windows.Forms.Label
    $note.Text = "A powered-off PC cannot collect data. This control center never authorizes trading."
    $note.AutoSize = $true
    $note.Location = New-Object System.Drawing.Point(30, 590)
    $form.Controls.Add($note)

    $refresh = {
        foreach ($row in (Get-ForecasterStatus)) {
            $label = $statusLabels[$row.Key]
            $label.Text = $row.State
            $label.ForeColor = if ($row.State -in @("RUNNING", "LIVE", "API OK", "SYNC OK")) {
                [System.Drawing.Color]::FromArgb(52, 105, 38)
            } else {
                [System.Drawing.Color]::FromArgb(190, 45, 36)
            }
        }
        $autoLabel.Text = if (Test-AutoStart) {
            "Auto-start: enabled at Windows logon"
        } else {
            "Auto-start: disabled"
        }
        $timeService = Get-Service W32Time -ErrorAction SilentlyContinue
        $clockLabel.Text = if ($timeService -and $timeService.Status -eq "Running") {
            "Windows Time: RUNNING"
        } else {
            "Windows Time: STOPPED"
        }
        $clockLabel.ForeColor = if ($timeService -and $timeService.Status -eq "Running") {
            [System.Drawing.Color]::FromArgb(52, 105, 38)
        } else {
            [System.Drawing.Color]::FromArgb(190, 45, 36)
        }
    }
    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 10000
    $timer.Add_Tick($refresh)
    $refreshButton.Add_Click($refresh)
    $timer.Start()
    & $refresh
    $form.Add_Shown({
        $form.Activate()
        $form.TopMost = $true
        $form.TopMost = $false
    })
    [void]$form.ShowDialog()
}

switch ($Action) {
    "Status" { Get-ForecasterStatus | Format-Table -AutoSize }
    "Start" { Start-All; Start-Sleep -Seconds 2; Get-ForecasterStatus | Format-Table -AutoSize }
    "Stop" { Stop-All; Start-Sleep -Seconds 1; Get-ForecasterStatus | Format-Table -AutoSize }
    "Restart" { Restart-All; Start-Sleep -Seconds 2; Get-ForecasterStatus | Format-Table -AutoSize }
    "Watchdog" { Start-All; Invoke-ForecasterWatchdog }
    "EnableAutoStart" { Enable-AutoStart; Write-Output "Auto-start enabled." }
    "DisableAutoStart" { Disable-AutoStart; Write-Output "Auto-start disabled." }
    "InstallShortcut" { Write-Output (Install-ControlShortcut) }
    default { Show-ControlCenter }
}
