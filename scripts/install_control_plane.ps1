param(
    [Parameter(Mandatory = $true)][string]$TargetRevision,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [string]$RepositoryRoot = ""
)

$ErrorActionPreference = "Stop"

function Invoke-ExactControlPlaneInstaller {
    param(
        [Parameter(Mandatory = $true)][string]$CheckoutRoot,
        [Parameter(Mandatory = $true)][string]$RuntimePath,
        [Parameter(Mandatory = $true)][string]$Revision
    )
    if ($Revision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_PLANE_EXACT_REVISION_REQUIRED"
    }
    & git -C $CheckoutRoot fetch origin main --quiet
    if ($LASTEXITCODE -ne 0) { throw "CONTROL_PLANE_ORIGIN_MAIN_FETCH_FAILED" }
    $originMain = (& git -C $CheckoutRoot rev-parse origin/main 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $originMain -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_PLANE_ORIGIN_MAIN_UNAVAILABLE"
    }
    & git -C $CheckoutRoot cat-file -e "$Revision`^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "CONTROL_PLANE_TARGET_COMMIT_UNAVAILABLE" }
    & git -C $CheckoutRoot merge-base --is-ancestor $Revision origin/main 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "CONTROL_PLANE_TARGET_NOT_REACHABLE_FROM_ORIGIN_MAIN"
    }
    if ($Revision -ne $originMain) {
        throw "CONTROL_PLANE_TARGET_MUST_EQUAL_ORIGIN_MAIN"
    }

    $temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) `
        ("xauusd-control-plane-{0}" -f ([guid]::NewGuid().ToString("N")))
    $worktreeAdded = $false
    try {
        & git -C $CheckoutRoot worktree add --detach --quiet $temporaryRoot $Revision
        if ($LASTEXITCODE -ne 0) { throw "CONTROL_PLANE_WORKTREE_STAGE_FAILED" }
        $worktreeAdded = $true
        $stagedRevision = (& git -C $temporaryRoot rev-parse HEAD 2>$null).Trim()
        if ($LASTEXITCODE -ne 0 -or $stagedRevision -ne $Revision) {
            throw "CONTROL_PLANE_WORKTREE_REVISION_MISMATCH"
        }
        $controlScript = Join-Path $temporaryRoot "scripts\xauusd_control_center.ps1"
        if (-not (Test-Path -LiteralPath $controlScript)) {
            throw "CONTROL_PLANE_INSTALL_ACTION_MISSING"
        }
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $controlScript -Action InstallControlPlane `
            -RuntimeRoot $RuntimePath -RepositoryRoot $CheckoutRoot `
            -SourceRoot $temporaryRoot -SourceRevision $Revision
        if ($LASTEXITCODE -ne 0) {
            throw "CONTROL_PLANE_INSTALL_ACTION_FAILED:$LASTEXITCODE"
        }
    } finally {
        if ($worktreeAdded) {
            & git -C $CheckoutRoot worktree remove --force $temporaryRoot 2>$null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Temporary control-plane worktree cleanup failed: $temporaryRoot"
            }
        }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $checkout = if ($RepositoryRoot) {
        [IO.Path]::GetFullPath($RepositoryRoot)
    } else {
        Split-Path -Parent $PSScriptRoot
    }
    Invoke-ExactControlPlaneInstaller -CheckoutRoot $checkout `
        -RuntimePath ([IO.Path]::GetFullPath($RuntimeRoot)) `
        -Revision $TargetRevision
}
