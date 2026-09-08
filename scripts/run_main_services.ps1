param(
    [ValidateSet('Run','Start','Stop','StatusJson','Install')][string]$Action = 'Run',
    [string]$RuntimeRoot = '', [string]$RepositoryRoot = ''
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
if ($Action -eq 'StatusJson') {
    if (Test-Path -LiteralPath $script:StatusPath) { Read-RuntimeJson $script:StatusPath | ConvertTo-Json -Depth 12 }
    else { @{state='not_started'} | ConvertTo-Json }
    exit 0
}
if ($Action -eq 'Install') { Install-MainRuntimeTask; exit 0 }
New-Item -ItemType Directory -Path $script:LogRoot -Force | Out-Null
if ($Action -eq 'Stop') { Write-RuntimeJson $script:DesiredPath @{state='stopped'}; exit 0 }
if ($Action -eq 'Start') {
    Write-RuntimeJson $script:DesiredPath @{state='running'}
    $arguments = @((Join-Path $PSScriptRoot 'main_services_launcher.vbs'), (Join-Path $PSScriptRoot 'run_main_services.ps1'), $script:RuntimeRoot, $script:RepositoryRoot) | ForEach-Object { ConvertTo-RuntimeArgument $_ }
    Start-Process -FilePath 'wscript.exe' -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    exit 0
}
try { exit (Invoke-MainRuntime) }
catch {
    Write-RuntimeJson $script:StatusPath @{state='failed';updated_at=[DateTime]::UtcNow.ToString('o');error=$_.Exception.Message}
    exit 1
}
