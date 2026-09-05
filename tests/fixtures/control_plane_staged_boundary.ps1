# Test-only external-environment adapter, inserted before fixture dispatch.
# Installer, activation, mutex, bundle and heartbeat implementations stay real.
$script:fixtureRoot = '__FIXTURE_ROOT__'
$null = Get-Command Get-FileHash -ErrorAction Stop
$script:fixtureTaskPath = '\XAUUSD-Contract-__FIXTURE_ID__\'
$taskName = 'XAUUSD-Contract-__FIXTURE_ID__-Main'
$guardTaskName = 'XAUUSD-Contract-__FIXTURE_ID__-Guard'
$workerUrl = 'http://127.0.0.1:1'
$dashboardUrl = $workerUrl
$protectedDashboardUrl = $workerUrl
$script:fixtureDenyRoots = @(
    (Join-Path $env:USERPROFILE 'XAUUSD-Forecaster'),
    (Join-Path $env:USERPROFILE 'XAUUSD-Forecaster-runtime'),
    (Join-Path $env:USERPROFILE 'XAUUSD-Forecaster.local')
)
function Assert-FixturePath {
    param([string]$Path)
    $resolved = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    foreach ($denied in $script:fixtureDenyRoots) {
        if ($resolved -ieq $denied -or $resolved.StartsWith($denied + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'STAGED_PRODUCTION_TARGET_DENIED'
        }
    }
    if ($resolved -ine $script:fixtureRoot -and
        -not $resolved.StartsWith($script:fixtureRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "STAGED_PATH_OUTSIDE_OWNERSHIP: target=$resolved; root=$script:fixtureRoot"
    }
}
Assert-FixturePath $moduleRoot
Assert-FixturePath $repositoryRoot
Assert-FixturePath $PSScriptRoot
$script:fixtureBusiness = Get-Content (Join-Path $script:fixtureRoot 'business.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$script:fixtureWriteJson = ${function:Write-ControlCenterJsonAtomic}
function Write-ControlCenterJsonAtomic {
    param($Path,$Value,$Depth=12)
    Assert-FixturePath $Path
    & $script:fixtureWriteJson -Path $Path -Value $Value -Depth $Depth
}
$script:fixtureWriteEvidence = ${function:Write-ReleaseEvidenceUtf8Atomic}
function Write-ReleaseEvidenceUtf8Atomic {
    param($Path,$Content)
    Assert-FixturePath $Path
    & $script:fixtureWriteEvidence -Path $Path -Content $Content
}

# File cmdlet proxies retain real filesystem semantics, including atomic moves.
# They validate before invoking a native cmdlet, in both parent and child.
foreach ($commandName in @('Set-Content','Add-Content','New-Item','Remove-Item','Move-Item','Copy-Item')) {
    $metadata = [Management.Automation.CommandMetadata]::new(
        (Get-Command "Microsoft.PowerShell.Management\$commandName"))
    $proxy = [Management.Automation.ProxyCommand]::Create($metadata)
    $guard = @'
    foreach ($field in @('Path','LiteralPath','Destination')) {
        if ($PSBoundParameters.ContainsKey($field)) {
            foreach ($target in @($PSBoundParameters[$field])) { Assert-FixturePath $target }
        }
    }
'@
    $proxy = $proxy.Replace('begin' + [Environment]::NewLine + '{',
        'begin' + [Environment]::NewLine + '{' + [Environment]::NewLine + $guard)
    if (-not $proxy.Contains($guard)) { throw 'STAGED_FILE_GUARD_NOT_INSTALLED' }
    Set-Item -Path "function:$commandName" -Value ([scriptblock]::Create($proxy))
}

# Never call the machine scheduler. The virtual scheduler has one exact namespace
# and deliberately cannot resolve a production name. Real OS mutexes are unchanged.
function Assert-FixtureTask {
    param([string]$TaskName, [string]$TaskPath = $script:fixtureTaskPath)
    if ($TaskName -cnotin @($script:taskName, $script:guardTaskName) -or
        $TaskPath -cne $script:fixtureTaskPath) { throw 'STAGED_TASK_OUTSIDE_OWNERSHIP' }
}
function Get-ScheduledTask {
    param($TaskName, $TaskPath = $script:fixtureTaskPath)
    Assert-FixtureTask $TaskName $TaskPath
    [pscustomobject]@{TaskName=$TaskName;TaskPath=$TaskPath;Settings=[pscustomobject]@{Enabled=$true}}
}
function Stop-ScheduledTask { param($TaskName,$TaskPath=$script:fixtureTaskPath); Assert-FixtureTask $TaskName $TaskPath }
function Disable-ScheduledTask { param($TaskName,$TaskPath=$script:fixtureTaskPath); Assert-FixtureTask $TaskName $TaskPath }
function Enable-ScheduledTask { param($TaskName,$TaskPath=$script:fixtureTaskPath); Assert-FixtureTask $TaskName $TaskPath }
function Start-ScheduledTask { throw 'STAGED_UNEXPECTED_TASK_START' }
function Register-ScheduledTask { throw 'STAGED_UNEXPECTED_TASK_REGISTRATION' }
function Unregister-ScheduledTask { throw 'STAGED_UNEXPECTED_TASK_REMOVAL' }

function Start-Process {
    param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru)
    if ([IO.Path]::GetFileName($FilePath) -cne 'wscript.exe' -or
        -not ([string]$ArgumentList).Contains($script:fixtureRoot)) {
        throw 'STAGED_PROCESS_START_DENIED'
    }
    foreach ($denied in $script:fixtureDenyRoots) {
        if (([string]$ArgumentList).Contains($denied + '\')) { throw 'STAGED_PRODUCTION_TARGET_DENIED' }
    }
    Microsoft.PowerShell.Management\Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WindowStyle Hidden -PassThru:$PassThru
}
$script:fixtureRealStopOwner = ${function:Stop-VerifiedWatchdogOwner}
function Stop-VerifiedWatchdogOwner {
    param($Identity)
    $observed = Get-ControlPlaneProcessIdentity -ProcessId ([int]$Identity.process_id) -RequireCompleteInventory
    if (-not $observed -or -not $observed.command_line.Contains($script:fixtureRoot + '\')) {
        throw 'STAGED_PROCESS_STOP_DENIED'
    }
    & $script:fixtureRealStopOwner -Identity $Identity
}
function Start-All { throw 'STAGED_UNEXPECTED_BUSINESS_START' }
function Start-ForecasterService { throw 'STAGED_UNEXPECTED_BUSINESS_START' }
function Stop-ForecasterService { throw 'STAGED_UNEXPECTED_BUSINESS_STOP' }
function Start-CandidateDiscovery { throw 'STAGED_UNEXPECTED_DISCOVERY' }
function Invoke-RestMethod { throw 'STAGED_UNEXPECTED_NETWORK' }
function Invoke-WebRequest { throw 'STAGED_UNEXPECTED_NETWORK' }

# Explicit fixture business identities, not process-name guesses. Native identity
# acquisition and before/after PID + start-token comparison remain unmodified.
function Test-ForecasterServiceProcess {
    param($Process,$Service)
    return ([int]$Process.ProcessId -eq [int]$script:fixtureBusiness.($Service.Key) -and
        $Process.CommandLine.Contains($script:fixtureRoot + '\'))
}
function Test-BroadcastPublisherEnabled { return $false }
function Test-RuntimeObservation { return $true }
function Get-ForecasterStatus {
    foreach ($service in $services) {
        [pscustomobject]@{Key=$service.Key;State=if($service.Key -eq 'collector'){'STOPPED'}else{'RUNNING'}}
    }
}
