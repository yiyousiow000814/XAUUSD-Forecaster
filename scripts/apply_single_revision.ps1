# Internal native boundary called by publish_single_version.py.
param(
    [Parameter(Mandatory=$true)][string]$RuntimeRoot,
    [Parameter(Mandatory=$true)][string]$RepositoryRoot,
    [ValidateSet('ApplyRevision', 'StartServices')][string]$Action = 'ApplyRevision',
    [string]$Revision
)
$ErrorActionPreference = 'Stop'
$script:RuntimeRoot = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\')
$script:RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd('\')
$script:ForwardRoot = Join-Path $script:RuntimeRoot '.local/forward'
$script:LogRoot = Join-Path $script:ForwardRoot 'logs'
. (Join-Path $PSScriptRoot 'main_runtime.ps1')
Assert-RuntimeWriterIsolation
$desired = Read-RuntimeJson (Join-Path $script:ForwardRoot 'main-runtime-desired.json')
if ($desired.state -ne 'maintenance') { throw 'Code changes require active maintenance ownership' }
if ($Action -eq 'ApplyRevision') {
    if ($Revision -notmatch '^[0-9a-f]{40}$') { throw 'A fixed source revision is required' }
    $changed = Set-RuntimeRevision -Services @(Get-RuntimeServices) -Revision $Revision
    @{changed=$changed; revision=(Invoke-RuntimeGit -Arguments @('rev-parse', 'HEAD'))} | ConvertTo-Json
} else {
    foreach ($service in @(Get-RuntimeServices)) { Start-RuntimeService $service }
}
