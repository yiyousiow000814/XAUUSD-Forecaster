param(
    [ValidateSet("Gui", "Status", "Start", "Stop", "Restart", "EnableAutoStart", "DisableAutoStart", "InstallShortcut")]
    [string]$Action = "Gui"
)

$ErrorActionPreference = "Stop"
$moduleRoot = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $moduleRoot ".local\forward\logs"
$taskName = "XAUUSD-Forecaster-Autostart"
$dashboardUrl = "https://aurum-signal-room.yiyousiow1234.chatgpt.site"

$services = @(
    [pscustomobject]@{
        Key = "collector"
        Label = "XAUUSD Collector"
        Match = "run_forward_collector.py"
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
        Script = "scripts\run_news_annotator.py"
        Arguments = @("--interval-seconds", "60", "--batch-size", "0")
    },
    [pscustomobject]@{
        Key = "api"
        Label = "Dashboard API"
        Match = "run_dashboard_api.py"
        Script = "scripts\run_dashboard_api.py"
        Arguments = @()
    },
    [pscustomobject]@{
        Key = "sync"
        Label = "Sites Sync"
        Match = "run_dashboard_sync.py"
        Script = "scripts\run_dashboard_sync.py"
        Arguments = @("--interval-seconds", "30")
    }
)

function Get-PythonProcessSnapshot {
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue)
}

function Get-ForecasterProcesses {
    param([pscustomobject]$Service)
    @(Get-PythonProcessSnapshot |
        Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Service.Match) })
}

function Get-ServiceState {
    param(
        [pscustomobject]$Service,
        [array]$Processes
    )
    if ($Processes.Count -eq 0) { return "STOPPED" }

    if ($Service.Key -eq "collector") {
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
    $snapshot = @(Get-PythonProcessSnapshot)
    foreach ($service in $services) {
        $processes = @($snapshot | Where-Object {
            $_.CommandLine -and $_.CommandLine.Contains($service.Match)
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
    $arguments = @($Service.Script) + @($Service.Arguments)
    Start-Process -FilePath "python" -ArgumentList $arguments `
        -WorkingDirectory $moduleRoot -WindowStyle Hidden `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
}

function Stop-ForecasterService {
    param([pscustomobject]$Service)
    foreach ($process in (Get-ForecasterProcesses $Service)) {
        Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
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
    $snapshot = @(Get-PythonProcessSnapshot)
    foreach ($process in $snapshot) {
        $owned = $false
        foreach ($service in $services) {
            if ($process.CommandLine -and $process.CommandLine.Contains($service.Match)) {
                $owned = $true
                break
            }
        }
        if ($owned) { Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue }
    }
}

function Restart-All {
    Stop-All
    Start-Sleep -Milliseconds 800
    Start-All
}

function Test-AutoStart {
    $null -ne (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)
}

function Enable-AutoStart {
    $quotedScript = '"{0}"' -f $PSCommandPath
    $taskAction = New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File {0} -Action Start" -f $quotedScript
    )
    $taskTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal `
        -UserId ("{0}\{1}" -f $env:USERDOMAIN, $env:USERNAME) `
        -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $taskAction `
        -Trigger $taskTrigger -Principal $principal -Force | Out-Null
}

function Disable-AutoStart {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
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
    $form.Size = New-Object System.Drawing.Size(720, 565)
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
    $startAll.Location = New-Object System.Drawing.Point(28, 310)
    $startAll.Add_Click({ Start-All })
    $form.Controls.Add($startAll)

    $restartAll = New-Object System.Windows.Forms.Button
    $restartAll.Text = "Restart All"
    $restartAll.Size = New-Object System.Drawing.Size(120, 38)
    $restartAll.Location = New-Object System.Drawing.Point(158, 310)
    $restartAll.Add_Click({ Restart-All })
    $form.Controls.Add($restartAll)

    $stopAll = New-Object System.Windows.Forms.Button
    $stopAll.Text = "Stop All"
    $stopAll.Size = New-Object System.Drawing.Size(120, 38)
    $stopAll.Location = New-Object System.Drawing.Point(288, 310)
    $stopAll.Add_Click({ Stop-All })
    $form.Controls.Add($stopAll)

    $openSite = New-Object System.Windows.Forms.Button
    $openSite.Text = "Open Dashboard"
    $openSite.Size = New-Object System.Drawing.Size(130, 38)
    $openSite.Location = New-Object System.Drawing.Point(418, 310)
    $openSite.Add_Click({ Start-Process $dashboardUrl })
    $form.Controls.Add($openSite)

    $openLogs = New-Object System.Windows.Forms.Button
    $openLogs.Text = "Open Logs"
    $openLogs.Size = New-Object System.Drawing.Size(110, 38)
    $openLogs.Location = New-Object System.Drawing.Point(558, 310)
    $openLogs.Add_Click({ Start-Process explorer.exe $logRoot })
    $form.Controls.Add($openLogs)

    $autoLabel = New-Object System.Windows.Forms.Label
    $autoLabel.AutoSize = $true
    $autoLabel.Location = New-Object System.Drawing.Point(30, 380)
    $form.Controls.Add($autoLabel)

    $enableAuto = New-Object System.Windows.Forms.Button
    $enableAuto.Text = "Enable Auto-start"
    $enableAuto.Size = New-Object System.Drawing.Size(190, 38)
    $enableAuto.Location = New-Object System.Drawing.Point(310, 369)
    $enableAuto.Add_Click({ Enable-AutoStart })
    $form.Controls.Add($enableAuto)

    $disableAuto = New-Object System.Windows.Forms.Button
    $disableAuto.Text = "Disable Auto-start"
    $disableAuto.Size = New-Object System.Drawing.Size(150, 38)
    $disableAuto.Location = New-Object System.Drawing.Point(510, 369)
    $disableAuto.Add_Click({ Disable-AutoStart })
    $form.Controls.Add($disableAuto)

    $refreshButton = New-Object System.Windows.Forms.Button
    $refreshButton.Text = "Refresh Status"
    $refreshButton.Size = New-Object System.Drawing.Size(150, 38)
    $refreshButton.Location = New-Object System.Drawing.Point(30, 425)
    $form.Controls.Add($refreshButton)

    $note = New-Object System.Windows.Forms.Label
    $note.Text = "A powered-off PC cannot collect data. This control center never authorizes trading."
    $note.AutoSize = $true
    $note.Location = New-Object System.Drawing.Point(30, 485)
    $form.Controls.Add($note)

    $refresh = {
        foreach ($row in (Get-ForecasterStatus)) {
            $label = $statusLabels[$row.Key]
            $label.Text = $row.State
            $label.ForeColor = if ($row.State -in @("RUNNING", "LIVE", "SYNC OK")) {
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
    "EnableAutoStart" { Enable-AutoStart; Write-Output "Auto-start enabled." }
    "DisableAutoStart" { Disable-AutoStart; Write-Output "Auto-start disabled." }
    "InstallShortcut" { Write-Output (Install-ControlShortcut) }
    default { Show-ControlCenter }
}
