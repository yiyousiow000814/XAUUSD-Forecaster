param(
    [Parameter(Mandatory = $true)][string]$TaskName,
    [Parameter(Mandatory = $true)][string]$HeartbeatPath,
    [int]$MaxAgeSeconds = 120
)

$ErrorActionPreference = "Stop"

function Get-WatchdogHeartbeat {
    if (-not (Test-Path -LiteralPath $HeartbeatPath)) { return $null }
    try {
        $heartbeat = Get-Content -LiteralPath $HeartbeatPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $observedAt = [DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse(
            [string]$heartbeat.observed_at, [ref]$observedAt
        )) { return $null }
        return [pscustomobject]@{
            ObservedAt = $observedAt
            ProcessId = [int]$heartbeat.process_id
        }
    } catch { return $null }
}

function Invoke-WatchdogGuard {
    $heartbeat = Get-WatchdogHeartbeat
    $now = [DateTimeOffset]::UtcNow
    $healthy = $heartbeat -and
        $heartbeat.ObservedAt -le $now.AddSeconds(30) -and
        ($now - $heartbeat.ObservedAt).TotalSeconds -le $MaxAgeSeconds
    if ($healthy) { return $false }

    if ($heartbeat -and $heartbeat.ProcessId -gt 0) {
        $process = Get-CimInstance Win32_Process `
            -Filter "ProcessId=$($heartbeat.ProcessId)" -ErrorAction SilentlyContinue
        if ($process -and $process.Name -eq "powershell.exe" -and
            $process.CommandLine -match '(?i)xauusd_control_center\.ps1' -and
            $process.CommandLine -match '(?i)-Action\s+Watchdog') {
            Stop-Process -Id $heartbeat.ProcessId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
        }
    }

    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $TaskName
    return $true
}

if ($MyInvocation.InvocationName -ne '.') {
    Invoke-WatchdogGuard | Out-Null
}
