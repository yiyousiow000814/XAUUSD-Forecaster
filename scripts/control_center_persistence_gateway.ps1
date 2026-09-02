# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.

$releaseHistoryEventSchema = "release-history-event-v2"
$releaseHistoryMaximumEventBytes = 65536

function Write-ControlCenterJsonAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowNull()][object]$Value,
        [ValidateRange(2, 32)][int]$Depth = 12,
        [switch]$Immutable
    )
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    # Keep the temporary leaf short: receipt paths already contain a 64-byte
    # digest and Windows PowerShell 5.1 still encounters legacy path limits.
    $temporary = Join-Path $directory (
        ".cc-{0}.tmp" -f [guid]::NewGuid().ToString("N")
    )
    try {
        $json = $Value | ConvertTo-Json -Depth $Depth
        [System.IO.File]::WriteAllText(
            $temporary, $json, [System.Text.UTF8Encoding]::new($false)
        )
        if ($Immutable) {
            Move-Item -LiteralPath $temporary -Destination $Path -ErrorAction Stop
        } else {
            Move-Item -LiteralPath $temporary -Destination $Path -Force -ErrorAction Stop
        }
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Write-ReleaseEvidenceUtf8Atomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content,
        [switch]$CreateNew
    )
    $directory = Split-Path -Parent $Path
    $nativeDirectory = ConvertTo-ReleaseEvidenceNativePath -Path $directory
    $nativePath = ConvertTo-ReleaseEvidenceNativePath -Path $Path
    [System.IO.Directory]::CreateDirectory($nativeDirectory) | Out-Null
    $encoding = New-Object System.Text.UTF8Encoding($false)
    if ($CreateNew) {
        $stream = [System.IO.File]::Open(
            $nativePath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read)
        try {
            $writer = [System.IO.StreamWriter]::new($stream, $encoding)
            try { $writer.Write($Content) } finally { $writer.Dispose() }
        } finally { $stream.Dispose() }
        return
    }
    $temporary = "$Path.$([guid]::NewGuid().ToString('N')).tmp"
    $nativeTemporary = ConvertTo-ReleaseEvidenceNativePath -Path $temporary
    $backup = "$Path.$([guid]::NewGuid().ToString('N')).bak"
    $nativeBackup = ConvertTo-ReleaseEvidenceNativePath -Path $backup
    [System.IO.File]::WriteAllText($nativeTemporary, $Content, $encoding)
    try {
        if ([System.IO.File]::Exists($nativePath)) {
            [System.IO.File]::Replace($nativeTemporary, $nativePath, $nativeBackup)
        } else {
            [System.IO.File]::Move($nativeTemporary, $nativePath)
        }
    } finally {
        if ([System.IO.File]::Exists($nativeTemporary)) {
            [System.IO.File]::Delete($nativeTemporary)
        }
        if ([System.IO.File]::Exists($nativeBackup)) {
            [System.IO.File]::Delete($nativeBackup)
        }
    }
}

function Add-ControlCenterUtf8Line {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Line,
        [Parameter(Mandatory = $true)][int]$MaximumBytes
    )
    if ($Line.Contains("`r") -or $Line.Contains("`n")) {
        throw "PERSISTENCE_LINE_INVALID"
    }
    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes("$Line`n")
    if ($bytes.Length -gt $MaximumBytes) {
        throw "PERSISTENCE_EVENT_TOO_LARGE"
    }
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $stream = [System.IO.FileStream]::new(
        $Path, [System.IO.FileMode]::Append, [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}

function Write-ControlCenterDiagnosticEvent {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Event
    )
    $line = $Event | ConvertTo-Json -Compress -Depth 8
    Add-ControlCenterUtf8Line -Path $Path -Line $line -MaximumBytes 8192
}

function ConvertTo-BoundedReleaseHistoryValue {
    param([AllowNull()][object]$Value, [int]$Depth = 0)
    if ($null -eq $Value) { return $null }
    if ($Depth -ge 5) { return "[bounded]" }
    if ($Value -is [string]) {
        $text = [string]$Value
        if ($text.Length -gt 2048) { return $text.Substring(0, 2048) }
        return $text
    }
    if ($Value -is [ValueType]) { return $Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $answer = [ordered]@{}
        foreach ($key in @($Value.Keys | Select-Object -First 64)) {
            $answer[[string]$key] = ConvertTo-BoundedReleaseHistoryValue `
                -Value $Value[$key] -Depth ($Depth + 1)
        }
        return [pscustomobject]$answer
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        return @($Value | Select-Object -First 128 | ForEach-Object {
            ConvertTo-BoundedReleaseHistoryValue -Value $_ -Depth ($Depth + 1)
        })
    }
    $answer = [ordered]@{}
    foreach ($property in @($Value.PSObject.Properties | Select-Object -First 64)) {
        $answer[$property.Name] = ConvertTo-BoundedReleaseHistoryValue `
            -Value $property.Value -Depth ($Depth + 1)
    }
    return [pscustomobject]$answer
}

function ConvertTo-ReleaseHistoryProjection {
    param([AllowNull()][object]$Release)
    if (-not $Release) { return $null }
    $projection = [ordered]@{}
    foreach ($name in @(
        "git_sha", "worker_version_id", "windows_revision", "artifact_kind",
        "branch", "validation_key", "validation_state", "compatibility_state"
    )) {
        $projection[$name] = [string]$Release.$name
    }
    foreach ($name in @(
        "migration_acceptance", "migration_qualification", "access_qualification",
        "validation"
    )) {
        if ($Release.PSObject.Properties[$name]) {
            $projection[$name] = ConvertTo-BoundedReleaseHistoryValue `
                -Value $Release.$name
        }
    }
    return [pscustomobject]$projection
}

function Get-ReleaseHistoryReceiptReferences {
    param([AllowNull()][object]$Release, [AllowNull()][object]$Detail)
    $digests = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($source in @($Release, $Detail)) {
        if (-not $source) { continue }
        $json = $source | ConvertTo-Json -Depth 16 -Compress
        foreach ($match in [regex]::Matches($json, '[0-9a-f]{64}')) {
            $null = $digests.Add($match.Value)
        }
    }
    return @($digests | Sort-Object | Select-Object -First 64)
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
    Write-ControlCenterJsonAtomic -Path $runtimeUpdateStatePath `
        -Value $current -Depth 5
}

function Get-ReleaseLifecyclePhase {
    param([object]$ReleaseState)
    Get-ReleaseRuntimeLifecyclePhase -ReleaseState $ReleaseState
}

function Set-ReleaseLifecycleProjection {
    param([Parameter(Mandatory = $true)][object]$ReleaseState)
    $phase = Get-ReleaseLifecyclePhase -ReleaseState $ReleaseState
    if ($ReleaseState.PSObject.Properties['lifecycle_phase']) {
        $ReleaseState.lifecycle_phase = $phase
    } else {
        $ReleaseState | Add-Member -NotePropertyName lifecycle_phase `
            -NotePropertyValue $phase
    }
}

function Get-ReleaseControlState {
    if (-not (Test-Path -LiteralPath $releaseControlStatePath)) { return $null }
    try {
        $state = Get-Content -LiteralPath $releaseControlStatePath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if (-not $state.candidate_discovery) {
            $state | Add-Member -NotePropertyName candidate_discovery -NotePropertyValue (
                [pscustomobject]@{
                    watermark_created_at = $null
                    watermark_version_id = $null
                    initialized_at = $null
                }
            )
        }
        if (-not $state.PSObject.Properties['previous_stable_rollback_eligible']) {
            $state | Add-Member -NotePropertyName previous_stable_rollback_eligible `
                -NotePropertyValue $false
        }
        if (-not $state.PSObject.Properties['previous_stable_rollback_reason']) {
            $state | Add-Member -NotePropertyName previous_stable_rollback_reason `
                -NotePropertyValue "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        }
        if ([string]$state.schema_version -in @(
            "stable-candidate-release-v1", "stable-candidate-release-v2"
        )) {
            foreach ($identity in @($state.stable, $state.previous_stable)) {
                if ($identity -and -not $identity.PSObject.Properties['artifact_kind']) {
                    $identity | Add-Member -NotePropertyName artifact_kind `
                        -NotePropertyValue $productionCandidateArtifactKind
                }
            }
            foreach ($identity in @($state.candidate, $state.queued_candidate)) {
                if ($identity -and -not $identity.PSObject.Properties['artifact_kind']) {
                    $legacyAccepted = (
                        [string]$identity.worker_version_id -eq $bootstrapAcceptedCandidateWorker -and
                        [string]$identity.git_sha -eq $bootstrapAcceptedCandidateRevision
                    )
                    $identity | Add-Member -NotePropertyName artifact_kind `
                        -NotePropertyValue $(if ($legacyAccepted) {
                            $legacyReferenceArtifactKind
                        } else { $unknownArtifactKind })
                }
            }
            if ($state.stable) {
                $state.stable.artifact_kind = $legacyBootstrapStableArtifactKind
                if (-not $state.stable.PSObject.Properties['worker_git_sha']) {
                    $state.stable | Add-Member -NotePropertyName worker_git_sha `
                        -NotePropertyValue "NOT_RECORDED"
                }
            }
            if ($state.candidate -and
                [string]$state.candidate.worker_version_id -eq $bootstrapAcceptedCandidateWorker -and
                [string]$state.candidate.git_sha -eq $bootstrapAcceptedCandidateRevision) {
                $state.candidate.artifact_kind = $legacyReferenceArtifactKind
                foreach ($field in @("validation_state", "compatibility_state")) {
                    if ($state.candidate.PSObject.Properties[$field]) {
                        $state.candidate.$field = "REBASE_REQUIRED"
                    } else {
                        $state.candidate | Add-Member -NotePropertyName $field `
                            -NotePropertyValue "REBASE_REQUIRED"
                    }
                }
                if ($state.candidate.validation) {
                    $state.candidate.validation | Add-Member -NotePropertyName reason `
                        -NotePropertyValue "REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED" -Force
                }
            }
            $state.schema_version = $releaseSchemaVersion
        }
        Set-ReleaseLifecycleProjection -ReleaseState $state
        return $state
    } catch { $null }
}

function Get-ReleaseControlStatusSnapshot {
    param(
        [switch]$ForceProviderRefresh,
        [switch]$SkipProviderObservation
    )
    $state = Get-ReleaseControlState
    if (-not $state) { return $null }
    $runtimeReadModel = Get-CurrentReleaseRuntimeReadModel -PersistedState $state `
        -ForceProviderRefresh:$ForceProviderRefresh `
        -SkipProviderObservation:$SkipProviderObservation
    $waterfall = if ($state.candidate -and
        -not [string]::IsNullOrWhiteSpace([string]$state.candidate.validation_key)) {
        Get-ReleaseEvidenceWaterfall -Root $releaseEvidenceRoot `
            -ValidationKey ([string]$state.candidate.validation_key)
    } else {
        [pscustomobject]@{
            schema_version = "release-evidence-waterfall-v1"
            validation_key_digest = $null
            node_count = 0
            started_at = $null
            completed_at = $null
            elapsed_ms = 0
            nodes = @()
        }
    }
    $state | Add-Member -NotePropertyName evidence_waterfall `
        -NotePropertyValue $waterfall -Force
    $state | Add-Member -NotePropertyName release_runtime `
        -NotePropertyValue $runtimeReadModel -Force
    return $state
}

function Write-ReleaseControlState {
    param([Parameter(Mandatory = $true)][object]$State)
    Set-ReleaseLifecycleProjection -ReleaseState $State
    $controlBundle = Get-RuntimeControlBundleIdentity
    foreach ($field in @("control_bundle_revision", "control_bundle_exact_revision",
        "control_bundle_hash_verified")) {
        if (-not $State.PSObject.Properties[$field]) {
            $State | Add-Member -NotePropertyName $field -NotePropertyValue $null
        }
    }
    $State.control_bundle_revision = if ($controlBundle) {
        [string]$controlBundle.source_revision
    } else { $null }
    $State.control_bundle_exact_revision = [bool]($controlBundle -and $controlBundle.exact_revision)
    $State.control_bundle_hash_verified = [bool]$controlBundle
    Write-ControlCenterJsonAtomic -Path $releaseControlStatePath `
        -Value $State -Depth 12
}

function Write-ReleaseHistory {
    param([string]$Event, [object]$Release, [hashtable]$Detail = @{})
    $releaseProjection = ConvertTo-ReleaseHistoryProjection -Release $Release
    $boundedDetail = ConvertTo-BoundedReleaseHistoryValue -Value $Detail
    $transactionId = if ($Detail.ContainsKey("transaction_id")) {
        [string]$Detail.transaction_id
    } else { "" }
    $record = [pscustomobject]@{
        schema_version = $releaseHistoryEventSchema
        occurred_at = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        transaction_id = $transactionId
        validation_key = if ($releaseProjection) {
            [string]$releaseProjection.validation_key
        } else { "" }
        receipt_refs = @(Get-ReleaseHistoryReceiptReferences `
            -Release $releaseProjection -Detail $boundedDetail)
        release = $releaseProjection
        detail = $boundedDetail
    }
    $line = $record | ConvertTo-Json -Compress -Depth 16
    Add-ControlCenterUtf8Line -Path $releaseHistoryPath -Line $line `
        -MaximumBytes $releaseHistoryMaximumEventBytes
}

function New-ReleaseIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$GitSha,
        [Parameter(Mandatory = $true)][string]$WorkerVersionId,
        [Parameter(Mandatory = $true)][string]$WindowsRevision,
        [string]$Branch = "",
        [string]$PullRequest = "",
        [string]$ValidationState = "NEW",
        [ValidateSet("PREVIEW", "PRODUCTION_CANDIDATE", "LEGACY_REFERENCE", "LEGACY_BOOTSTRAP_STABLE", "UNKNOWN")]
        [string]$ArtifactKind = "UNKNOWN",
        [string]$VersionCreatedAt = ""
    )
    [pscustomobject]@{
        git_sha = $GitSha
        worker_version_id = $WorkerVersionId
        windows_revision = $WindowsRevision
        branch = $Branch
        pull_request = $PullRequest
        artifact_kind = $ArtifactKind
        version_created_at = $VersionCreatedAt
        compatibility_state = "PENDING"
        cutover_order = "PAUSE_SYNC_WINDOWS_WORKER_RESUME_SYNC"
        validation_state = $ValidationState
        validation_key = "$WorkerVersionId`:$GitSha"
        validation = $null
        discovered_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Test-ReleaseIdentity {
    param([object]$Left, [object]$Right)
    return [bool](
        $Left -and $Right -and
        [string]$Left.git_sha -eq [string]$Right.git_sha -and
        [string]$Left.worker_version_id -eq [string]$Right.worker_version_id -and
        [string]$Left.windows_revision -eq [string]$Right.windows_revision -and
        [string]$Left.artifact_kind -eq [string]$Right.artifact_kind
    )
}

function Enter-ReleaseTransactionLock {
    if (Test-Path -LiteralPath $runtimeStateMigrationLockPath) { return $false }
    if (Test-Path -LiteralPath $releaseLockPath) {
        $owner = $null
        try {
            $owner = Get-Content -LiteralPath (Join-Path $releaseLockPath "owner.json") -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
        } catch {}
        $ownerAlive = $false
        if ($owner -and [int]$owner.owner_pid -gt 0) {
            $ownerProcess = Get-ControlPlaneProcessIdentity `
                -ProcessId ([int]$owner.owner_pid)
            $ownerAlive = [bool]($ownerProcess -and (
                -not [string]$owner.owner_process_start_token -or
                (Test-ControlPlaneStartTokenEqual `
                    -Left $owner.owner_process_start_token `
                    -Right $ownerProcess.process_start_token)
            ))
        }
        $acquired = ConvertTo-ReleaseTimestampUtc -Value $owner.acquired_at
        $ageKnown = $owner -and $acquired -ne [DateTimeOffset]::MinValue
        $lockCreated = [DateTimeOffset](Get-Item -LiteralPath $releaseLockPath).CreationTimeUtc
        $stale = (-not $ownerAlive) -and (
            $ageKnown -or
            ([DateTimeOffset]::UtcNow - $lockCreated) -ge $releaseLockOwnerGrace
        )
        if (-not $stale) { return $false }
        # The path is fixed below the runtime state root. Only an abandoned lock
        # whose owner is gone is removed; release state/history are untouched.
        Remove-Item -LiteralPath $releaseLockPath -Recurse -Force -ErrorAction Stop
        Write-ReleaseHistory -Event "ABANDONED_LOCK_RECOVERED" -Release $null `
            -Detail @{ owner_pid = if ($owner) { [int]$owner.owner_pid } else { 0 } }
    }
    try {
        New-Item -ItemType Directory -Path $releaseLockPath -ErrorAction Stop | Out-Null
        $lockOwnerIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
        if (-not $lockOwnerIdentity) {
            throw "RELEASE_LOCK_OWNER_IDENTITY_REQUIRED"
        }
        $ownerRecord = [pscustomobject]@{
            owner_pid = $PID
            owner_process_start_token = [string]$lockOwnerIdentity.process_start_token
            acquired_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        Write-ControlCenterJsonAtomic `
            -Path (Join-Path $releaseLockPath "owner.json") `
            -Value $ownerRecord -Depth 4
        $script:releaseTransactionLockHeld = $true
        return $true
    } catch { return $false }
}

function Exit-ReleaseTransactionLock {
    if ($script:releaseTransactionLockHeld -and (Test-Path -LiteralPath $releaseLockPath)) {
        Remove-Item -LiteralPath $releaseLockPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    $script:releaseTransactionLockHeld = $false
}

function Write-CandidateCpuInFlightState {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan,
        [Parameter(Mandatory = $true)][object]$RequestPlan,
        [Parameter(Mandatory = $true)][object]$Qualification,
        [Parameter(Mandatory = $true)][DateTimeOffset]$WindowFrom
    )
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate -or
        [string]$state.candidate.validation_key -ne [string]$Candidate.validation_key -or
        [string]$state.candidate.worker_version_id -ne [string]$Candidate.worker_version_id) {
        throw "WORKER_CPU_IN_FLIGHT_CANDIDATE_MISMATCH"
    }
    $state.candidate.validation_state = "PLATFORM_PENDING"
    $state.candidate.validation = [pscustomobject]@{
        key=[string]$Candidate.validation_key; repository="PASSED"; windows="PASSED"
        cloudflare="PENDING"; reason="WORKER_CPU_DIRECTED_LEDGER_IN_PROGRESS"
        observability_diagnostic="PROVIDER_EVIDENCE_PENDING"
        validation_run=[string]$RequestPlan.validation_run
        telemetry_window_from=$WindowFrom.ToString("o")
        telemetry_window_to=$WindowFrom.ToString("o")
        expected_worker_invocations=@($RequestPlan.requests | Where-Object { $_.phase -eq "acceptance" }).Count
        expected_requests=@($RequestPlan.requests | Where-Object { $_.phase -eq "acceptance" })
        routes=@(@($RoutePlan.worker_reads) + @($RoutePlan.worker_writes))
        cpu_route_plan=$RoutePlan; worker_qualification=$Qualification
        directed_request_ledger=[pscustomobject]@{
            evidence_class="CONTROLLED_EXACT"; request_universe_digest=[string]$RequestPlan.request_universe_digest
            planned=@($RequestPlan.requests).Count; completed=0; passed=0
        }
        cpu_qualification_mode=$null
        tested_at=[DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "WORKER_CPU_DIRECTED_LEDGER_FROZEN" -Release $state.candidate `
        -Detail @{ validation_run=[string]$RequestPlan.validation_run; qualification_key=[string]$Qualification.key; planned=@($RequestPlan.requests).Count }
}
