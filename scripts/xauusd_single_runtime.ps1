param(
    [ValidateSet('Gui', 'Preflight', 'Status', 'StatusJson', 'CodeRevision', 'Start', 'SupervisorStart', 'Stop', 'Restart', 'Watchdog', 'EnableAutoStart', 'DisableAutoStart', 'InstallShortcut')]
    [string]$Action = 'Gui',
    [string]$RuntimeRoot = '',
    [string]$RepositoryRoot = ''
)
$ErrorActionPreference = 'Stop'
if (-not $RuntimeRoot) { $RuntimeRoot = Split-Path -Parent $PSScriptRoot }
if (-not $RepositoryRoot) { $RepositoryRoot = $RuntimeRoot }
$script:RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
$script:RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
$script:ForwardRoot = Join-Path $script:RuntimeRoot '.local/forward'
$script:LogRoot = Join-Path $script:ForwardRoot 'logs'
$script:StatusPath = Join-Path $script:ForwardRoot 'main-runtime-status.json'
$script:DesiredPath = Join-Path $script:ForwardRoot 'main-runtime-desired.json'
. (Join-Path $PSScriptRoot 'main_runtime.ps1')

function Set-DesiredRuntime {
    param([string]$State)
    New-Item -ItemType Directory -Path $script:ForwardRoot -Force | Out-Null
    # Share the publisher's existing byte lock. An operator command cannot
    # restart services between its stop acknowledgement and maintenance write.
    $lock = [IO.FileStream]::new((Join-Path $script:ForwardRoot 'single-publication.lock'), [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    $locked = $false
    try {
        $lock.Lock(0, 1)
        $locked = $true
        if (Test-Path -LiteralPath $script:DesiredPath) {
            $current = Read-RuntimeJson $script:DesiredPath
            if ($current.state -eq 'maintenance') { throw 'Publication owns maintenance; use its recovery entry' }
        }
        Write-RuntimeJson $script:DesiredPath @{ state = $State }
    } finally {
        if ($locked) { $lock.Unlock(0, 1) }
        $lock.Dispose()
    }
}
function Get-MainStatus {
    if (Test-Path -LiteralPath $script:StatusPath) {
        Read-RuntimeJson $script:StatusPath
    } else { [pscustomobject]@{ state = 'not_started'; updated_at = $null; error = $null } }
}
function Start-MainController {
    $launcher = Join-Path $PSScriptRoot 'xauusd_single_runtime_launcher.vbs'
    $control = Join-Path $PSScriptRoot 'xauusd_single_runtime.ps1'
    $arguments = @($launcher, $control, $script:RuntimeRoot, $script:RepositoryRoot) | ForEach-Object { ConvertTo-RuntimeArgument $_ }
    Start-Process -FilePath 'wscript.exe' -ArgumentList $arguments -WindowStyle Hidden | Out-Null
}

switch ($Action) {
    'Preflight' { Assert-RuntimeWriterIsolation; Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD') }
    'CodeRevision' { Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD') }
    'StatusJson' { Get-MainStatus | ConvertTo-Json -Depth 12 }
    'Status' { Get-MainStatus | Format-List }
    'SupervisorStart' { Assert-RuntimeWriterIsolation; Start-MainController }
    'Start' { Assert-RuntimeWriterIsolation; Set-DesiredRuntime 'running'; Start-MainController }
    'Stop' { Set-DesiredRuntime 'stopped' }
    'Restart' {
        Set-DesiredRuntime 'stopped'
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        $acknowledged = $false
        do {
            Start-Sleep -Milliseconds 500
            $status = Get-MainStatus
            if ($status.state -eq 'stopped' -and $status.updated_at -and [DateTime]::Parse($status.updated_at).ToUniversalTime() -ge $deadline.AddSeconds(-30)) { $acknowledged = $true; break }
        } while ([DateTime]::UtcNow -lt $deadline)
        if (-not $acknowledged) { throw 'Controller did not acknowledge stop; runtime remains stopped' }
        Set-DesiredRuntime 'running'
        Start-MainController
    }
    'Watchdog' {
        New-Item -ItemType Directory -Path $script:LogRoot -Force | Out-Null
        try { exit (Invoke-MainRuntime) }
        catch {
            Write-RuntimeJson $script:StatusPath @{ state = 'failed'; updated_at = [DateTime]::UtcNow.ToString('o'); error = $_.Exception.Message }
            exit 1
        }
    }
    'EnableAutoStart' {
        Assert-RuntimeWriterIsolation
        $launcher = Join-Path $PSScriptRoot 'xauusd_single_runtime_launcher.vbs'
        $control = Join-Path $PSScriptRoot 'xauusd_single_runtime.ps1'
        $arguments = (@($launcher, $control, $script:RuntimeRoot, $script:RepositoryRoot) | ForEach-Object { ConvertTo-RuntimeArgument $_ }) -join ' '
        $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        $taskAction = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument $arguments -WorkingDirectory $script:RuntimeRoot
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
        $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName 'XAUUSD-Forecaster-Autostart' -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    }
    'DisableAutoStart' {
        Unregister-ScheduledTask -TaskName 'XAUUSD-Forecaster-Autostart' -Confirm:$false -ErrorAction SilentlyContinue
    }
    'InstallShortcut' {
        Assert-RuntimeWriterIsolation

        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'XAUUSD Forecaster.lnk'))
        $shortcut.TargetPath = Join-Path $env:WINDIR 'System32/wscript.exe'
        $shortcut.Arguments = (@((Join-Path $PSScriptRoot 'xauusd_single_runtime_launcher.vbs'), (Join-Path $PSScriptRoot 'xauusd_single_runtime.ps1'), $script:RuntimeRoot, $script:RepositoryRoot, 'Gui') | ForEach-Object { ConvertTo-RuntimeArgument $_ }) -join ' '
        $shortcut.WorkingDirectory = $script:RuntimeRoot
        $shortcut.Save()
    }
    'Gui' {
        Add-Type -AssemblyName System.Windows.Forms
        $form = New-Object Windows.Forms.Form
        $form.Text = 'XAUUSD Forecaster - main'
        $form.Width = 640; $form.Height = 360
        $label = New-Object Windows.Forms.Label
        $label.Left = 20; $label.Top = 20; $label.Width = 580; $label.Height = 210
        $form.Controls.Add($label)
        $start = New-Object Windows.Forms.Button
        $start.Text = 'Start'; $start.Left = 20; $start.Top = 240; $start.Width = 100; $start.Height = 44
        $start.Add_Click({ Assert-RuntimeWriterIsolation; Set-DesiredRuntime 'running'; Start-MainController })
        $form.Controls.Add($start)
        $stop = New-Object Windows.Forms.Button
        $stop.Text = 'Stop'; $stop.Left = 140; $stop.Top = 240; $stop.Width = 100; $stop.Height = 44
        $stop.Add_Click({ Set-DesiredRuntime 'stopped' })
        $form.Controls.Add($stop)
        $refresh = {
            $status = Get-MainStatus
            $time = 'No heartbeat'
            if ($status.updated_at) {
                $utc = [DateTimeOffset]::Parse($status.updated_at)
                $time = $utc.ToOffset([TimeSpan]::FromHours(8)).ToString('yyyy-MM-dd HH:mm:ss') + ' (UTC+8)'
                if (([DateTimeOffset]::UtcNow - $utc).TotalSeconds -gt 30) { $time += ' - controller heartbeat stale' }
            }
            $start.Enabled = $status.state -ne 'maintenance'
            $stop.Enabled = $status.state -ne 'maintenance'
            $label.Text = "Runtime: $($status.state)`r`n$time`r`n$($status.error)`r`n`r`nSingle active runtime. Updates require maintenance. Assistant remains paused."
        }
        $timer = New-Object Windows.Forms.Timer
        $timer.Interval = 2000; $timer.Add_Tick($refresh)
        try { & $refresh; $timer.Start(); [void]$form.ShowDialog() }
        finally { $timer.Stop(); $timer.Dispose(); $form.Dispose() }
    }
}
