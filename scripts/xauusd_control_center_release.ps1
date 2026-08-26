# Extracted from the latest-main Control Center.
# Owner: release transactions and validation.

function Get-ReleaseControlState {
    if (-not (Test-Path -LiteralPath $releaseControlStatePath)) { return $null }
    try {
        $state = Get-Content -LiteralPath $releaseControlStatePath -Raw | ConvertFrom-Json
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
        return $state
    } catch { $null }
}

function Write-ReleaseControlState {
    param([Parameter(Mandatory = $true)][object]$State)
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
    $directory = Split-Path -Parent $releaseControlStatePath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$releaseControlStatePath.tmp"
    $State | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $releaseControlStatePath -Force
}

function Write-ReleaseHistory {
    param([string]$Event, [object]$Release, [hashtable]$Detail = @{})
    $directory = Split-Path -Parent $releaseHistoryPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    [pscustomobject]@{
        occurred_at = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        release = $Release
        detail = $Detail
    } | ConvertTo-Json -Compress -Depth 12 | Add-Content -LiteralPath $releaseHistoryPath -Encoding UTF8
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
    if (Test-Path -LiteralPath $releaseLockPath) {
        $owner = $null
        try {
            $owner = Get-Content -LiteralPath (Join-Path $releaseLockPath "owner.json") -Raw |
                ConvertFrom-Json
        } catch {}
        $ownerAlive = $false
        if ($owner -and [int]$owner.owner_pid -gt 0) {
            $ownerAlive = [bool](Get-Process -Id ([int]$owner.owner_pid) -ErrorAction SilentlyContinue)
        }
        $acquired = [DateTimeOffset]::MinValue
        $ageKnown = $owner -and [DateTimeOffset]::TryParse(
            [string]$owner.acquired_at, [ref]$acquired
        )
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
        [pscustomobject]@{
            owner_pid = $PID
            acquired_at = [DateTimeOffset]::UtcNow.ToString("o")
        } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $releaseLockPath "owner.json") -Encoding UTF8
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

function Invoke-WranglerJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $webRoot = Join-Path $repositoryRoot "web"
    Push-Location $webRoot
    try {
        $output = @(& npx.cmd wrangler @Arguments --json 2>$null)
        if ($LASTEXITCODE -ne 0) { throw "Wrangler command failed." }
        ($output -join "`n") | ConvertFrom-Json
    } finally { Pop-Location }
}

function Get-CloudflareDeployment {
    Invoke-WranglerJson -Arguments @("deployments", "status", "--name", $workerName)
}

function Get-CloudflareVersions {
    $versions = Invoke-WranglerJson -Arguments @(
        "versions", "list", "--name", $workerName
    )
    # ConvertFrom-Json may return its top-level JSON array as one pipeline
    # object. Emit each version explicitly so sorting/filtering never treats
    # the complete Wrangler response as one synthetic version.
    foreach ($version in @($versions)) { Write-Output $version }
}

function Get-OriginMainRevision {
    if (-not (Test-Path -LiteralPath (Join-Path $repositoryRoot ".git"))) {
        return $null
    }
    $fetch = Invoke-RepositoryRead -Operation "FETCH_ORIGIN" `
        -Arguments @("-C", $repositoryRoot, "fetch", "origin", "--quiet")
    if (-not $fetch.passed) { return $null }
    $result = Invoke-RepositoryRead -Operation "READ_ORIGIN_MAIN" `
        -Arguments @("-C", $repositoryRoot, "rev-parse", "origin/main")
    if (-not $result.passed) { return $null }
    $revision = ([string](@($result.output)[0])).Trim().ToLowerInvariant()
    if ($revision -notmatch '^[0-9a-f]{40}$') { return $null }
    return $revision
}

function Set-CandidateMaterializationState {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][string]$Revision,
        [Parameter(Mandatory = $true)][ValidateSet("PENDING", "MATERIALIZED")]
        [string]$Status,
        [string]$WorkerVersionId = ""
    )
    $receipt = [pscustomobject]@{
        revision = $Revision
        state = $Status
        reason = if ($Status -eq "MATERIALIZED") {
            "EXACT_MAIN_CANDIDATE_MATERIALIZED"
        } else { "EXACT_MAIN_CANDIDATE_PENDING" }
        worker_version_id = if ($WorkerVersionId) { $WorkerVersionId } else { $null }
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    if ($State.PSObject.Properties['candidate_materialization']) {
        $State.candidate_materialization = $receipt
    } else {
        $State | Add-Member -NotePropertyName candidate_materialization `
            -NotePropertyValue $receipt
    }
}

function Get-CloudflareVersionDetails {
    param([Parameter(Mandatory = $true)][string]$VersionId)
    Invoke-WranglerJson -Arguments @(
        "versions", "view", $VersionId, "--name", $workerName
    )
}

function Invoke-CloudflareDeployment {
    param(
        [Parameter(Mandatory = $true)][string]$StableVersionId,
        [string]$CandidateVersionId = "",
        [Parameter(Mandatory = $true)][string]$Message
    )
    $specifications = @("$StableVersionId@100")
    if ($CandidateVersionId) { $specifications += "$CandidateVersionId@0" }
    $webRoot = Join-Path $repositoryRoot "web"
    Push-Location $webRoot
    try {
        $null = @(& npx.cmd wrangler versions deploy @specifications --name $workerName --yes --message $Message 2>&1)
        if ($LASTEXITCODE -ne 0) { throw "Cloudflare deployment failed." }
    } finally { Pop-Location }
}

function Get-ReleaseGitShaFromVersion {
    param([Parameter(Mandatory = $true)][object]$Version)
    $message = [string]$Version.annotations.'workers/message'
    if ($message -match '(?i)release:([0-9a-f]{40})') { return $matches[1].ToLowerInvariant() }
    return $null
}

function Get-ReleaseBranchFromVersion {
    param([Parameter(Mandatory = $true)][object]$Version)
    $message = [string]$Version.annotations.'workers/message'
    if ($message -match '(?i)branch:([^\s]+)') { return $matches[1] }
    return [string]$Version.annotations.'workers/alias'
}

function Get-ReleaseArtifactKindFromVersion {
    param([Parameter(Mandatory = $true)][object]$Version)
    $message = [string]$Version.annotations.'workers/message'
    if ($message -match '(?i)artifact[_-]kind:(PREVIEW|PRODUCTION_CANDIDATE)') {
        return $matches[1].ToUpperInvariant()
    }
    return $unknownArtifactKind
}

function Get-ReleaseTimestampValues {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return }
    if ($Value -is [DateTimeOffset]) {
        Write-Output $Value.ToUniversalTime().ToString("o", [Globalization.CultureInfo]::InvariantCulture)
        return
    }
    if ($Value -is [DateTime]) {
        Write-Output $Value.ToUniversalTime().ToString("o", [Globalization.CultureInfo]::InvariantCulture)
        return
    }
    if ($Value -is [string]) {
        if (-not [string]::IsNullOrWhiteSpace($Value)) { Write-Output $Value }
        return
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        foreach ($item in $Value) { Get-ReleaseTimestampValues -Value $item }
        return
    }
    if (-not [string]::IsNullOrWhiteSpace([string]$Value)) { Write-Output $Value }
}

function Get-ReleaseVersionPreviewUrl {
    param(
        [Parameter(Mandatory = $true)][object]$Version,
        [Parameter(Mandatory = $true)][object]$Candidate
    )
    if (-not [bool]$Version.metadata.has_preview -or
        [string]$Version.id -notmatch '^[0-9a-f]{8}-[0-9a-f-]{27}$' -or
        [string]$Version.id -ne [string]$Candidate.worker_version_id -or
        (Get-ReleaseGitShaFromVersion -Version $Version) -ne [string]$Candidate.git_sha -or
        (Get-ReleaseArtifactKindFromVersion -Version $Version) -ne
            $productionCandidateArtifactKind) { return "" }
    try {
        $production = [Uri]$workerUrl
        $workerPrefix = "$workerName."
        if (-not $production.Host.StartsWith(
            $workerPrefix, [StringComparison]::OrdinalIgnoreCase
        )) { return "" }
        $suffix = $production.Host.Substring($workerPrefix.Length)
        $versionPrefix = ([string]$Version.id).Substring(0, 8)
        return "{0}://{1}-{2}.{3}" -f `
            $production.Scheme, $versionPrefix, $workerName, $suffix
    } catch { return "" }
}

function Get-ReleaseVersionCreatedAtValue {
    param([Parameter(Mandatory = $true)][object]$Version)
    $newest = [DateTimeOffset]::MinValue
    foreach ($candidate in @(Get-ReleaseTimestampValues -Value $Version.metadata.created_on)) {
        $parsed = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse([string]$candidate, [ref]$parsed)) {
            $utc = $parsed.ToUniversalTime()
            if ($utc -gt $newest) { $newest = $utc }
        }
    }
    return $newest
}

function Get-ReleaseVersionCreatedAt {
    param([Parameter(Mandatory = $true)][object]$Version)
    $created = Get-ReleaseVersionCreatedAtValue -Version $Version
    if ($created -eq [DateTimeOffset]::MinValue) { return "" }
    return $created.ToString("o")
}

function Test-VersionAfterDiscoveryWatermark {
    param(
        [Parameter(Mandatory = $true)][object]$Version,
        [Parameter(Mandatory = $true)][object]$Discovery
    )
    if (-not $Discovery.watermark_created_at) { return $true }
    $createdAt = Get-ReleaseVersionCreatedAt -Version $Version
    if (-not $createdAt) { return $false }
    $created = [DateTimeOffset]::Parse($createdAt)
    $watermark = [DateTimeOffset]::Parse([string]$Discovery.watermark_created_at)
    if ($created -gt $watermark) { return $true }
    if ($created -lt $watermark) { return $false }
    return [string]$Version.id -gt [string]$Discovery.watermark_version_id
}

function Set-CandidateDiscoveryWatermark {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][object]$Version
    )
    $State.candidate_discovery.watermark_created_at =
        Get-ReleaseVersionCreatedAt -Version $Version
    $State.candidate_discovery.watermark_version_id = [string]$Version.id
    if (-not $State.candidate_discovery.initialized_at) {
        $State.candidate_discovery.initialized_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Get-DeploymentVersion {
    param([object]$Deployment, [double]$Percentage)
    @($Deployment.versions | Where-Object { [double]$_.percentage -eq $Percentage }) |
        Select-Object -First 1
}

function New-ReleaseControlState {
    param([object]$Stable, [object]$Candidate = $null)
    [pscustomobject]@{
        schema_version = $releaseSchemaVersion
        stable = $Stable
        candidate = $Candidate
        previous_stable = $null
        previous_stable_rollback_eligible = $false
        previous_stable_rollback_reason = "PREVIOUS_STABLE_ROLLBACK_UNAVAILABLE"
        queued_candidate = $null
        transaction = $null
        deployment_status = "READY"
        drift = $null
        last_candidate_check = $null
        candidate_discovery = [pscustomobject]@{
            watermark_created_at = $null
            watermark_version_id = $null
            initialized_at = $null
        }
        candidate_materialization = $null
        updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
}

function Initialize-ReleaseControl {
    $existing = Get-ReleaseControlState
    if ($existing) { return $existing }
    $deployment = Get-CloudflareDeployment
    $stableVersion = Get-DeploymentVersion -Deployment $deployment -Percentage 100
    if (-not $stableVersion) { throw "Exactly one 100% Stable Worker version is required." }
    $runtime = Get-RuntimeCodeState
    $revision = if ($runtime) { [string]$runtime.applied_revision } else { Get-CodeRevision }
    if ($revision -notmatch '^[0-9a-f]{40}$') { throw "Stable Windows revision is unavailable." }
    $stable = New-ReleaseIdentity -GitSha $revision `
        -WorkerVersionId ([string]$stableVersion.version_id) `
        -WindowsRevision $revision -Branch "main" -ValidationState "PASSED" `
        -ArtifactKind $legacyBootstrapStableArtifactKind
    $stable | Add-Member -NotePropertyName worker_git_sha -NotePropertyValue "NOT_RECORDED"
    $stable | Add-Member -NotePropertyName provenance_state `
        -NotePropertyValue "LEGACY_EXACT_WORKER_WINDOWS_PAIR"
    $stable.compatibility_state = "PASSED"
    $acceptedPlacement = @($deployment.versions | Where-Object {
        [string]$_.version_id -eq $bootstrapAcceptedCandidateWorker -and
        [double]$_.percentage -eq 0
    }).Count -eq 1
    $accepted = $null
    if ($acceptedPlacement) {
        $accepted = New-ReleaseIdentity -GitSha $bootstrapAcceptedCandidateRevision `
            -WorkerVersionId $bootstrapAcceptedCandidateWorker `
            -WindowsRevision $bootstrapAcceptedCandidateRevision `
            -Branch "fix/worker-cpu-headroom" -PullRequest "268" `
            -ValidationState "REBASE_REQUIRED" -ArtifactKind $legacyReferenceArtifactKind
        $accepted.compatibility_state = "REBASE_REQUIRED"
        $accepted.validation = [pscustomobject]@{
            key = [string]$accepted.validation_key
            repository = "PASSED"
            windows = "PASSED"
            cloudflare = "PASSED"
            accepted_before_release_control = $true
            acceptance_mode = "LEGACY_ACCEPTED_MANUAL_EVIDENCE"
            source_reference = "PR_268_ACCEPTED_REVIEW_COMMENT"
            source_timestamp = $null
            source_timestamp_status = "NOT_RECORDED_IN_BOOTSTRAP_SOURCE"
            reason = "REBASE_ON_RELEASE_CONTROL_MAIN_REQUIRED"
            cpu_evidence = [pscustomobject]@{
                source = "CLOUDFLARE_WORKERS_OBSERVABILITY"
                acceptance_mode = "LEGACY_ACCEPTED_MANUAL_EVIDENCE"
                invocations = 104
                p50_cpu_ms = 2
                max_cpu_ms = 5
                p95_cpu_ms = 4
                p99_cpu_ms = 4
                exceeded_cpu = 0
                exceeded_memory = 0
                responses_1102 = 0
                responses_5xx = 0
                source_reference = "PR_268_ACCEPTED_REVIEW_COMMENT"
            }
        }
    }
    $state = New-ReleaseControlState -Stable $stable -Candidate $accepted
    $knownVersions = @(Get-CloudflareVersions | Sort-Object `
        @{ Expression = { Get-ReleaseVersionCreatedAtValue -Version $_ } }, `
        @{ Expression = { [string]$_.id } })
    $latestKnownVersion = $knownVersions | Select-Object -Last 1
    if ($latestKnownVersion) {
        $state.candidate_discovery.watermark_created_at =
            Get-ReleaseVersionCreatedAt -Version $latestKnownVersion
        $state.candidate_discovery.watermark_version_id = [string]$latestKnownVersion.id
        $state.candidate_discovery.initialized_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "BOOTSTRAPPED" -Release $stable
    if ($accepted) {
        Write-ReleaseHistory -Event "ACCEPTED_CANDIDATE_IMPORTED" -Release $accepted
    }
    return $state
}

function Test-TransientExternalRepositoryFailure {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [string]$Diagnostic = ""
    )
    if ($ExitCode -eq 0 -or $Operation -notin @(
        "FETCH_ORIGIN", "GITHUB_CHECKS_API"
    )) { return $false }
    $text = [string]$Diagnostic
    if ($text -match '(?i)(authentication failed|could not read username|' +
        'permission denied|repository not found|invalid (ref|reference)|' +
        'bad object|ambiguous argument|unknown revision|not a valid object|' +
        'couldn''t find remote ref|remote ref .* not found|access denied|' +
        'http[^0-9]*40[14])') {
        return $false
    }
    if ($text -match '(?i)rate limit') { return $true }
    if ($text -match '(?i)http[^0-9]*403') { return $false }
    return [bool]($text -match '(?i)(timed? out|timeout|could not connect|' +
        'failed to connect|connection (refused|reset|closed)|' +
        'temporary failure in name resolution|could not resolve host|' +
        'name or service not known|socket (error|hang up)|unexpected eof|' +
        '(tls|ssl).*(handshake|connect|connection|socket|terminated)|' +
        'http[^0-9]*(429|5\d\d)|status( code)?[^0-9]*(429|5\d\d)|' +
        'rate limit)')
}

function Invoke-RepositoryRead {
    param(
        [Parameter(Mandatory = $true)][string]$Operation,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    $raw = @(& git @Arguments 2>&1)
    $exitCode = [int]$LASTEXITCODE
    $lines = @($raw | ForEach-Object { [string]$_ })
    $diagnostic = if ($exitCode -ne 0) {
        Protect-PreflightDiagnosticText ($lines -join "`n")
    } else { $null }
    [pscustomobject]@{
        passed = [bool]($exitCode -eq 0)
        exit_code = $exitCode
        output = if ($exitCode -eq 0) { $lines } else { @() }
        diagnostic = $diagnostic
        failure_class = if (Test-TransientExternalRepositoryFailure `
            -Operation $Operation -ExitCode $exitCode -Diagnostic $diagnostic) {
            "TRANSIENT_EXTERNAL"
        } else { "DETERMINISTIC_FAILURE" }
    }
}

function Get-CandidateChangedFiles {
    param([string]$StableRevision, [string]$CandidateRevision)
    $changed = @(& git -C $repositoryRoot diff --name-only $StableRevision $CandidateRevision 2>$null)
    if ($LASTEXITCODE -ne 0) { throw "Candidate boundary classification failed." }
    @($changed | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
}

function Get-CandidateCompatibilityRequirement {
    param([string[]]$ChangedFiles)
    $storage = @($ChangedFiles | Where-Object {
        $_ -like "web/drizzle/*" -or
        $_ -match '(^|/)migrations?/' -or $_ -match '(?i)(^|/)schema\.(sql|sqlite)$'
    })
    if ($storage.Count -gt 0) {
        return [pscustomobject]@{
            state = "COORDINATED_STORAGE_MIGRATION_REQUIRED"; files = $storage
        }
    }
    $platform = @($ChangedFiles | Where-Object {
        $_ -in @(
            "web/wrangler.jsonc", "web/worker-configuration.d.ts",
            "web/runtime-env.d.ts"
        )
    })
    if ($platform.Count -gt 0) {
        return [pscustomobject]@{
            state = "PLATFORM_CONFIG_REVIEW_REQUIRED"; files = $platform
        }
    }
    return [pscustomobject]@{ state = "AUTOMATIC"; files = @() }
}

function Test-AutomaticStorageCompatibility {
    param([string[]]$ChangedFiles)
    return [bool]((Get-CandidateCompatibilityRequirement `
        -ChangedFiles $ChangedFiles).state -eq "AUTOMATIC")
}

function Test-CandidatePlatformResources {
    param(
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][object]$Candidate
    )
    try {
        $stableVersion = Get-CloudflareVersionDetails -VersionId $Stable.worker_version_id
        $candidateVersion = Get-CloudflareVersionDetails -VersionId $Candidate.worker_version_id
        $externalTypes = @("d1", "kv_namespace", "r2_bucket", "vectorize")
        foreach ($binding in @($candidateVersion.resources.bindings | Where-Object {
            [string]$_.type -in $externalTypes
        })) {
            $match = @($stableVersion.resources.bindings | Where-Object {
                [string]$_.type -eq [string]$binding.type -and
                [string]$_.name -eq [string]$binding.name -and
                [string]$_.id -eq [string]$binding.id -and
                [string]$_.database_id -eq [string]$binding.database_id -and
                [string]$_.namespace_id -eq [string]$binding.namespace_id -and
                [string]$_.bucket_name -eq [string]$binding.bucket_name -and
                [string]$_.index_name -eq [string]$binding.index_name
            })
            if ($match.Count -ne 1) { return $false }
        }
        return $true
    } catch { return $false }
}

function Get-RequiredGitHubChecksResult {
    param([Parameter(Mandatory = $true)][string]$Revision)
    try {
        $raw = @(& gh api --method GET `
            "repos/yiyousiow000814/XAUUSD-Forecaster/commits/$Revision/check-runs?filter=latest&per_page=100" `
            2>&1)
        $exitCode = [int]$LASTEXITCODE
        $json = @($raw | ForEach-Object { [string]$_ }) -join "`n"
        if ($exitCode -ne 0) {
            $diagnostic = Protect-PreflightDiagnosticText $json
            if (Test-TransientExternalRepositoryFailure -Operation "GITHUB_CHECKS_API" `
                -ExitCode $exitCode -Diagnostic $diagnostic) {
                return [pscustomobject]@{
                    state = "REPOSITORY_PENDING"
                    reason = "GITHUB_TEMPORARILY_UNAVAILABLE"
                    exit_code = $exitCode
                    diagnostic = $diagnostic
                }
            }
            return [pscustomobject]@{
                state = "FAILED"
                reason = "GITHUB_CHECKS_ACCESS_FAILED"
                exit_code = $exitCode
                diagnostic = $diagnostic
            }
        }
        $runs = @(($json | ConvertFrom-Json -ErrorAction Stop).check_runs)
        foreach ($name in $requiredGitHubChecks) {
            $matching = @($runs | Where-Object {
                [string]$_.name -eq $name -and
                [string]$_.head_sha -eq $Revision
            })
            if ($matching.Count -eq 0) {
                return [pscustomobject]@{
                    state = "PENDING"; reason = "REQUIRED_GITHUB_CHECKS_PENDING"
                }
            }
            $latest = $matching | Sort-Object `
                @{ Expression = { [string]$_.started_at }; Descending = $true }, `
                @{ Expression = { [long]$_.id }; Descending = $true } | `
                Select-Object -First 1
            if ([string]$latest.status -ne "completed") {
                return [pscustomobject]@{
                    state = "PENDING"; reason = "REQUIRED_GITHUB_CHECKS_PENDING"
                }
            }
            if ([string]$latest.conclusion -ne "success") {
                return [pscustomobject]@{
                    state = "CHECKS_BLOCKED"; reason = "REQUIRED_GITHUB_CHECKS_BLOCKED"
                }
            }
        }
        return [pscustomobject]@{ state = "PASSED"; reason = $null }
    } catch {
        return [pscustomobject]@{
            state = "FAILED"
            reason = "GITHUB_CHECKS_RESPONSE_INVALID"
            exit_code = 0
            diagnostic = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Test-RequiredGitHubChecks {
    param([Parameter(Mandatory = $true)][string]$Revision)
    $script:lastGitHubChecksResult = Get-RequiredGitHubChecksResult -Revision $Revision
    return [string]$script:lastGitHubChecksResult.state
}

function Get-ProductionCandidateProvenanceResult {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$Candidate.branch -ne "main" -or
        [string]$Candidate.git_sha -ne [string]$Candidate.windows_revision) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED"
        }
    }
    $fetch = Invoke-RepositoryRead -Operation "FETCH_ORIGIN" `
        -Arguments @("-C", $repositoryRoot, "fetch", "origin", "--quiet")
    if (-not $fetch.passed) {
        return [pscustomobject]@{
            state = if ($fetch.failure_class -eq "TRANSIENT_EXTERNAL") {
                "REPOSITORY_PENDING"
            } else { "FAILED" }
            reason = if ($fetch.failure_class -eq "TRANSIENT_EXTERNAL") {
                "REPOSITORY_TRANSPORT_UNAVAILABLE"
            } else { "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED" }
            operation = "FETCH_ORIGIN"
            exit_code = [int]$fetch.exit_code
            diagnostic = $fetch.diagnostic
        }
    }
    & git -C $repositoryRoot cat-file -e "$([string]$Candidate.git_sha)^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_COMMIT_REQUIRED"
        }
    }
    $originMain = ([string](@(& git -C $repositoryRoot rev-parse origin/main 2>$null)[0])).Trim().ToLowerInvariant()
    if ($LASTEXITCODE -ne 0 -or $originMain -notmatch '^[0-9a-f]{40}$' -or
        $originMain -ne [string]$Candidate.git_sha) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_EXACT_MAIN_REQUIRED"
        }
    }
    return [pscustomobject]@{ state = "PASSED"; reason = $null }
}

function Test-ProductionCandidateProvenance {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $script:lastRepositoryValidationResult =
        Get-ProductionCandidateProvenanceResult -Candidate $Candidate
    return [bool]($script:lastRepositoryValidationResult.state -eq "PASSED")
}

function Get-CandidateCompatibilityApprovalGate {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $provenance = Get-ProductionCandidateProvenanceResult -Candidate $Candidate
    if ([string]$provenance.state -ne "PASSED") {
        return [pscustomobject]@{
            state = if ([string]$provenance.state -eq "REPOSITORY_PENDING") {
                "RETRYABLE"
            } else { "FAILED" }
            reason = [string]$provenance.reason
            diagnostic = [string]$provenance.diagnostic
        }
    }
    $checks = Get-RequiredGitHubChecksResult -Revision ([string]$Candidate.git_sha)
    return [pscustomobject]@{
        state = if ([string]$checks.state -eq "PASSED") {
            "PASSED"
        } elseif ([string]$checks.state -in @("REPOSITORY_PENDING", "PENDING")) {
            "RETRYABLE"
        } else { "FAILED" }
        reason = [string]$checks.reason
        diagnostic = [string]$checks.diagnostic
    }
}

function Test-SingleProductionOwner {
    foreach ($service in @($services | Where-Object { $_.Key -in $reloadableServiceKeys })) {
        if (@(Get-ForecasterProcesses $service).Count -ne 1) { return $false }
    }
    return $true
}

function Invoke-WorkersObservabilityQuery {
    param(
        [Parameter(Mandatory = $true)][object[]]$Filters,
        [Parameter(Mandatory = $true)][object[]]$Calculations,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To
    )
    $secret = Get-ReleaseSecret -Name "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN"
    $script:lastWorkersObservabilityCredentialSource = [string]$secret.source
    if (-not $secret.available) {
        $script:lastWorkersObservabilityDiagnostic = [string]$secret.diagnostic
        return $null
    }
    $token = [string]$secret.value
    $body = [pscustomobject]@{
        queryId = "aurum-release-candidate-validation"
        timeframe = [pscustomobject]@{
            from = $From.ToUnixTimeMilliseconds()
            to = $To.ToUnixTimeMilliseconds()
        }
        view = "calculations"
        chart = $false
        ignoreSeries = $true
        parameters = [pscustomobject]@{
            datasets = @()
            filterCombination = "and"
            filters = $Filters
            calculations = $Calculations
            limit = 10
        }
    }
    $uri = "https://api.cloudflare.com/client/v4/accounts/$cloudflareAccountId/workers/observability/telemetry/query"
    try {
        $response = Invoke-RestMethod -Method Post -Uri $uri `
            -Headers @{ Authorization = "Bearer $token" } `
            -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 10) `
            -TimeoutSec 30
        if (-not $response.success) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_API_REJECTED"
            return $null
        }
        $script:lastWorkersObservabilityDiagnostic = $null
        return $response.result
    } catch {
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
        $script:lastWorkersObservabilityDiagnostic = if ($statusCode -in @(401, 403)) {
            "OBSERVABILITY_CREDENTIAL_REJECTED"
        } elseif ($statusCode -eq 429) {
            "OBSERVABILITY_RATE_LIMITED"
        } elseif ($statusCode -ge 500 -and $statusCode -le 599) {
            "OBSERVABILITY_TRANSIENT_API_FAILURE"
        } else { "OBSERVABILITY_QUERY_FAILED" }
        return $null
    } finally {
        $token = $null
        $secret = $null
    }
}

function Get-CalculationAggregate {
    param([object]$QueryResult, [string]$Alias)
    $calculation = @($QueryResult.calculations | Where-Object {
        [string]$_.alias -eq $Alias
    }) | Select-Object -First 1
    if (-not $calculation -or @($calculation.aggregates).Count -eq 0) { return $null }
    return $calculation.aggregates[0].value
}

function Get-WorkerCpuGateState {
    param([Parameter(Mandatory = $true)][object]$Evidence, [int]$ExpectedInvocations)
    if ($Evidence.invocations -ne $ExpectedInvocations -or
        $Evidence.exceeded_cpu -gt 0 -or $Evidence.responses_1102 -gt 0 -or
        $Evidence.responses_5xx -gt 0 -or
        $Evidence.p99_cpu_ms -gt $workerCpuPassMaxMs -or
        $Evidence.max_cpu_ms -gt $workerCpuPassMaxMs) { return "FAILED" }
    if ($Evidence.p95_cpu_ms -le $workerCpuPassP95Ms -and
        $Evidence.p99_cpu_ms -le $workerCpuPassP99Ms -and
        $Evidence.max_cpu_ms -lt $workerCpuPassMaxMs) { return "PASSED" }
    return "REVIEW_REQUIRED"
}

function Get-WorkerPlatformFailureReason {
    param([Parameter(Mandatory = $true)][object]$Evidence)
    if ([int]$Evidence.invocations -ne [int]$Evidence.expected_invocations) {
        return "WORKER_INVOCATION_COUNT_MISMATCH"
    }
    if ([int]$Evidence.responses_5xx -gt 0) { return "WORKER_5XX_OBSERVED" }
    if ([int]$Evidence.exceeded_cpu -gt 0 -or [int]$Evidence.responses_1102 -gt 0) {
        return "WORKER_PLATFORM_LIMIT_EXCEEDED"
    }
    if ([double]$Evidence.p99_cpu_ms -gt $workerCpuPassMaxMs -or
        [double]$Evidence.max_cpu_ms -gt $workerCpuPassMaxMs) {
        return "WORKER_CPU_HEADROOM_FAILED"
    }
    return "WORKER_PLATFORM_EVIDENCE_FAILED"
}

function Get-CandidateObservabilityFilters {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [string]$RoutePath = "",
        [string]$RouteMethod = "",
        [string]$ValidationRun = ""
    )
    $filters = @(
        [pscustomobject]@{ key='$metadata.service'; operation='eq'; type='string'; value=$workerName },
        [pscustomobject]@{ key='$workers.scriptVersion.id'; operation='eq'; type='string'; value=[string]$Candidate.worker_version_id },
        [pscustomobject]@{ key='$metadata.type'; operation='eq'; type='string'; value='cf-worker-event' }
    )
    if ($ValidationRun) {
        $filters += @(
            [pscustomobject]@{
                key='$workers.event.request.headers.x-aurum-validation-run'
                operation='eq'; type='string'; value=$ValidationRun
            },
            [pscustomobject]@{
                key='$workers.event.request.headers.x-aurum-validation-phase'
                operation='eq'; type='string'; value='acceptance'
            }
        )
    }
    if ($RoutePath) {
        $filters += [pscustomobject]@{
            key='$workers.event.path'; operation='eq'; type='string'; value=$RoutePath
        }
    }
    if ($RouteMethod) {
        $filters += [pscustomobject]@{
            key='$workers.event.request.method'; operation='eq'; type='string'; value=$RouteMethod
        }
    }
    return $filters
}

function Get-CandidateInvocationCount {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [string]$ValidationRun = ""
    )
    $filters = @(Get-CandidateObservabilityFilters -Candidate $Candidate `
        -ValidationRun $ValidationRun)
    $result = Invoke-WorkersObservabilityQuery -From $From -To $To `
        -Filters $filters `
        -Calculations @([pscustomobject]@{ operator='count'; alias='invocations' })
    if (-not $result) { return $null }
    return Get-CalculationAggregate -QueryResult $result -Alias "invocations"
}

function Get-WorkerValidationManifest {
    param([string]$Revision = "")
    if ($Revision) {
        $object = "{0}:web/worker-validation-manifest.json" -f $Revision
        $raw = (& git -C $repositoryRoot show $object 2>$null) -join "`n"
        if ($LASTEXITCODE -ne 0 -or -not $raw) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_UNAVAILABLE"
        }
    } else {
        $path = Join-Path $repositoryRoot "web\worker-validation-manifest.json"
        if (-not (Test-Path -LiteralPath $path)) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_UNAVAILABLE"
        }
        $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    }
    $manifest = $raw | ConvertFrom-Json
    if ([int]$manifest.schema_version -ne 3 -or @($manifest.routes).Count -eq 0 -or
        -not $manifest.fixture_builder) {
        throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
    }
    $staticPaths = @($manifest.static_assets | ForEach-Object { [string]$_.path })
    if ($staticPaths.Count -eq 0 -or
        @($staticPaths | Sort-Object -Unique).Count -ne $staticPaths.Count) {
        throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
    }
    foreach ($asset in @($manifest.static_assets)) {
        $fields = @($asset.PSObject.Properties.Name)
        $missingFields = @(@(
            "path", "content_type", "body_encoding", "require_html_charset", "marker",
            "redirect_path"
        ) | Where-Object { $_ -notin $fields })
        if ($missingFields.Count -gt 0 -or
            [string]$asset.path -notmatch '^/[^?#]*$' -or
            [string]$asset.content_type -notmatch '^[a-z0-9][a-z0-9.+-]*/[a-z0-9][a-z0-9.+-]*$' -or
            $asset.require_html_charset -isnot [bool] -or
            ([string]$asset.body_encoding -notin @("", "utf-8")) -or
            ([bool]$asset.require_html_charset -and
                [string]$asset.body_encoding -ne "utf-8") -or
            ([string]$asset.content_type -eq "text/html" -and
                (-not [bool]$asset.require_html_charset -or
                    [string]::IsNullOrWhiteSpace([string]$asset.marker))) -or
            ([string]$asset.redirect_path -and
                ([string]$asset.redirect_path -notmatch '^/[^?#]*$' -or
                    [string]$asset.redirect_path -eq [string]$asset.path)) -or
            ($null -ne $asset.worker_expected -and
                $asset.worker_expected -isnot [bool])) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_INVALID"
        }
    }
    return $manifest
}

function Test-ValidationRouteOwnedByChange {
    param([object]$Route, [string[]]$ChangedFiles)
    foreach ($file in $ChangedFiles) {
        foreach ($owner in @($Route.owners)) {
            if ($file -like [string]$owner) { return $true }
        }
        foreach ($producer in @($Route.producers)) {
            if ($file -like [string]$producer) { return $true }
        }
    }
    return $false
}

function Get-CandidateRouteValidationPlan {
    param([string[]]$ChangedFiles, [string]$Revision = "")
    $manifest = Get-WorkerValidationManifest -Revision $Revision
    $manifestChanged = "web/worker-validation-manifest.json" -in $ChangedFiles
    $fixtureBuilderChanged = @($ChangedFiles | Where-Object {
        $_ -like [string]$manifest.fixture_builder -or
        $_ -eq "tests/test_release_validation_fixtures.py"
    }).Count -gt 0
    $workerCodeChanged = @($ChangedFiles | Where-Object {
        $file = $_
        @($manifest.bundle_runtime_roots | Where-Object {
            $file -like [string]$_
        }).Count -gt 0
    }).Count -gt 0
    $selectedRoutes = @($manifest.routes | Where-Object {
        [bool]$_.cpu_required -and (
            $manifestChanged -or $fixtureBuilderChanged -or
            (Test-ValidationRouteOwnedByChange -Route $_ -ChangedFiles $ChangedFiles) -or
            ($workerCodeChanged -and [bool]$_.baseline)
        )
    })
    $selected = @()
    foreach ($route in $selectedRoutes) {
        $scenarios = @($route.scenarios)
        if ($scenarios.Count -eq 0) {
            $scenarios = @([pscustomobject]@{ name = "default" })
        }
        foreach ($scenario in $scenarios) {
            $copy = $route.PSObject.Copy()
            $copy | Add-Member -NotePropertyName scenario `
                -NotePropertyValue ([string]$scenario.name)
            if ($scenario.fixture) { $copy.fixture = [string]$scenario.fixture }
            $selected += $copy
        }
    }
    $contractRoutes = @($manifest.routes | Where-Object {
        $manifestChanged -or (Test-ValidationRouteOwnedByChange -Route $_ -ChangedFiles $ChangedFiles)
    })
    $staticChanged = @($ChangedFiles | Where-Object {
        ($_ -like "web/app/*" -and $_ -notlike "web/app/*/route.ts" -and
            $_ -notlike "web/app/api/_shared/*") -or
        $_ -like "web/public/*" -or
        $_ -in @("web/vite.config.ts", "web/wrangler.jsonc", "web/worker/index.ts")
    }).Count -gt 0
    [pscustomobject]@{
        manifest_schema_version = [int]$manifest.schema_version
        static_assets = @($manifest.static_assets)
        worker_reads = @($selected | Where-Object { [string]$_.boundary -eq "WORKER_READ" })
        worker_writes = @($selected | Where-Object { [string]$_.boundary -eq "WORKER_WRITE" })
        contract_routes = $contractRoutes
        worker_cpu_required = [bool]($selected.Count -gt 0)
        requires_validation = [bool]($selected.Count -gt 0 -or $staticChanged)
    }
}

function New-CandidateValidationFixtureWorkspace {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $stageRoot = Join-Path ([System.IO.Path]::GetTempPath()) `
        ("aurum-release-validation-{0}" -f [guid]::NewGuid().ToString("N"))
    $fixtureRoot = Join-Path $stageRoot ".release-validation-fixtures"
    & git -C $repositoryRoot worktree add --detach --quiet $stageRoot `
        ([string]$Candidate.git_sha) 2>$null
    if ($LASTEXITCODE -ne 0) { throw "Candidate fixture worktree is unavailable." }
    try {
        $python = (Get-Command python.exe -ErrorAction Stop).Source
        & $python (Join-Path $stageRoot "scripts\build_release_validation_fixtures.py") `
            --output $fixtureRoot | Out-Null
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $fixtureRoot)) {
            throw "Production-shaped fixture generation failed."
        }
        return [pscustomobject]@{ stage_root=$stageRoot; fixture_root=$fixtureRoot }
    } catch {
        & git -C $repositoryRoot worktree remove --force $stageRoot 2>$null
        & git -C $repositoryRoot worktree prune 2>$null
        throw
    }
}

function Remove-CandidateValidationFixtureWorkspace {
    param([object]$Workspace)
    if (-not $Workspace -or -not $Workspace.stage_root) { return }
    & git -C $repositoryRoot worktree remove --force ([string]$Workspace.stage_root) 2>$null
    & git -C $repositoryRoot worktree prune 2>$null
}

function Get-CandidateRouteResponseReason {
    param([object]$Payload, [string]$Fallback)
    if ($Payload) {
        foreach ($path in @(
            @("error_code"), @("reason"), @("error", "code"), @("error")
        )) {
            $value = $Payload
            foreach ($name in $path) {
                if ($null -eq $value -or $null -eq $value.PSObject.Properties[$name]) {
                    $value = $null
                    break
                }
                $value = $value.$name
            }
            if ($value -is [string] -and -not [string]::IsNullOrWhiteSpace($value)) {
                return Protect-PreflightDiagnosticText $value
            }
        }
    }
    return $Fallback
}

function Test-CandidateDryRunPayload {
    param([object]$Payload, [string]$ExpectedFamily)
    if (-not $Payload) { return $false }
    $fields = @($Payload.PSObject.Properties.Name)
    $missingFields = @(@("status", "mutated", "route_family") |
        Where-Object { $_ -notin $fields })
    if ($missingFields.Count -gt 0) { return $false }
    return [bool](
        $Payload.status -is [string] -and
        [string]$Payload.status -eq "DRY_RUN_OK" -and
        $Payload.mutated -is [bool] -and
        [bool]$Payload.mutated -eq $false -and
        $Payload.route_family -is [string] -and
        [string]$Payload.route_family -eq $ExpectedFamily
    )
}

function Get-CandidateStaticAssetBaseUri {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $versionId = [string]$Candidate.worker_version_id
    if ($versionId -notmatch '^[0-9a-f]{8}-[0-9a-f-]{27}$') {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    $candidateUri = $null
    if (-not [Uri]::TryCreate([string]$Candidate.browser_url,
            [UriKind]::Absolute, [ref]$candidateUri)) {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    $productionUri = [Uri]$workerUrl
    $workerPrefix = "$workerName."
    if (-not $productionUri.Host.StartsWith(
            $workerPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    $suffix = $productionUri.Host.Substring($workerPrefix.Length)
    $expectedHost = "{0}-{1}.{2}" -f $versionId.Substring(0, 8), $workerName, $suffix
    if ($candidateUri.Scheme -ne "https" -or -not $candidateUri.IsDefaultPort -or
        $candidateUri.Host -ne $expectedHost -or $candidateUri.AbsolutePath -ne "/" -or
        $candidateUri.Query -or $candidateUri.Fragment) {
        throw "CANDIDATE_STATIC_HOST_MISMATCH"
    }
    return $candidateUri
}

function Get-Sha256BytesHex {
    param([byte[]]$Bytes)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return -join ($sha.ComputeHash($Bytes) | ForEach-Object { $_.ToString("x2") })
    } finally { $sha.Dispose() }
}

function Invoke-CandidateStaticAssetRequest {
    param([Parameter(Mandatory = $true)][Uri]$RequestUri)
    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(30)
    $response = $null
    try {
        $response = $client.GetAsync($RequestUri).GetAwaiter().GetResult()
        $contentType = if ($response.Content.Headers.ContentType) {
            [string]$response.Content.Headers.ContentType
        } else { "" }
        $cfCacheStatus = if ($response.Headers.Contains("CF-Cache-Status")) {
            [string]($response.Headers.GetValues("CF-Cache-Status") | Select-Object -First 1)
        } else { "" }
        $age = if ($response.Headers.Contains("Age")) {
            [string]($response.Headers.GetValues("Age") | Select-Object -First 1)
        } else { "" }
        return [pscustomobject]@{
            status = [int]$response.StatusCode
            content_type = $contentType
            body_bytes = $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
            location = [string]$response.Headers.Location
            cf_cache_status = $cfCacheStatus
            etag = [string]$response.Headers.ETag
            age = $age
            worker_version = if ($response.Headers.Contains("X-Aurum-Worker-Version")) {
                [string]($response.Headers.GetValues("X-Aurum-Worker-Version") |
                    Select-Object -First 1)
            } else { "" }
            git_sha = if ($response.Headers.Contains("X-Aurum-Git-SHA")) {
                [string]($response.Headers.GetValues("X-Aurum-Git-SHA") |
                    Select-Object -First 1)
            } else { "" }
            route = if ($response.Headers.Contains("X-Aurum-Route")) {
                [string]($response.Headers.GetValues("X-Aurum-Route") |
                    Select-Object -First 1)
            } else { "" }
        }
    } finally {
        if ($response) { $response.Dispose() }
        $client.Dispose()
        $handler.Dispose()
    }
}

function Invoke-CandidateStaticAssetSample {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Route
    )
    $result = [ordered]@{
        route = [string]$Route.path; path = [string]$Route.path
        method = "GET"; boundary = "STATIC_ASSET"; request_id = $null
        requested_url = ""; requested_host = ""
        requested_worker_version = [string]$Candidate.worker_version_id
        expected_status = 200; status = 0; passed = $false; reason = $null
        expected_content_type = [string]$Route.content_type
        actual_content_type = ""; expected_encoding = [string]$Route.body_encoding
        declared_charset = ""; expected_marker = [string]$Route.marker
        marker_present = $false; body_bytes = 0; body_sha256 = ""
        expected_redirect_path = [string]$Route.redirect_path
        redirect_status = 0; redirect_location = ""; final_url = ""
        cf_cache_status = ""; etag = ""; age = ""
        observed_worker_version = ""; observed_git_sha = ""; observed_route = ""
    }
    try {
        $baseUri = Get-CandidateStaticAssetBaseUri -Candidate $Candidate
        $requestUri = [Uri]::new($baseUri, [string]$Route.path)
        $result.requested_url = $requestUri.AbsoluteUri
        $result.requested_host = $requestUri.Host
        $response = Invoke-CandidateStaticAssetRequest -RequestUri $requestUri
        if ([bool]$Route.worker_expected) {
            $result.observed_worker_version = [string]$response.worker_version
            $result.observed_git_sha = [string]$response.git_sha
            $result.observed_route = [string]$response.route
            if ($result.observed_worker_version -ne [string]$Candidate.worker_version_id -or
                $result.observed_git_sha -ne [string]$Candidate.git_sha -or
                $result.observed_route -ne [string]$Route.path) {
                $result.status = [int]$response.status
                $result.reason = "VERSION_HOST_WORKER_IDENTITY_MISMATCH"
                return [pscustomobject]$result
            }
        }
        if ($Route.redirect_path) {
            $result.redirect_status = [int]$response.status
            $result.redirect_location = [string]$response.location
            $redirectUri = $null
            try { $redirectUri = [Uri]::new($requestUri, [string]$response.location) } catch {}
            if ([int]$response.status -notin @(301, 302, 307, 308) -or
                -not $redirectUri -or $redirectUri.Scheme -ne $requestUri.Scheme -or
                $redirectUri.Host -ne $requestUri.Host -or
                $redirectUri.Port -ne $requestUri.Port -or
                $redirectUri.AbsolutePath -ne [string]$Route.redirect_path -or
                $redirectUri.Query -or $redirectUri.Fragment) {
                $result.status = [int]$response.status
                $result.reason = "REDIRECT_CONTRACT_MISMATCH"
                return [pscustomobject]$result
            }
            $result.final_url = $redirectUri.AbsoluteUri
            $response = Invoke-CandidateStaticAssetRequest -RequestUri $redirectUri
        } else { $result.final_url = $requestUri.AbsoluteUri }
        $result.status = [int]$response.status
        $result.actual_content_type = [string]$response.content_type
        $result.cf_cache_status = [string]$response.cf_cache_status
        $result.etag = [string]$response.etag
        $result.age = [string]$response.age
        $bytes = [byte[]]$response.body_bytes
        $result.body_bytes = $bytes.Length
        if ($bytes.Length -gt 0) { $result.body_sha256 = Get-Sha256BytesHex -Bytes $bytes }
        $mediaType = ([string]$response.content_type -split ';', 2)[0].Trim().ToLowerInvariant()
        $charsetMatch = [regex]::Match([string]$response.content_type,
            '(?i)(?:^|;)\s*charset\s*=\s*"?([^;"\s]+)')
        if ($charsetMatch.Success) {
            $result.declared_charset = $charsetMatch.Groups[1].Value.ToLowerInvariant()
        }
        if ($result.status -ne 200) { $result.reason = "HTTP_STATUS_MISMATCH" }
        elseif ($mediaType -ne ([string]$Route.content_type).ToLowerInvariant()) {
            $result.reason = "CONTENT_TYPE_MISMATCH"
        } elseif ($bytes.Length -eq 0) { $result.reason = "EMPTY_BODY" }
        elseif ($bytes.Length -gt $candidateStaticAssetMaxBytes) { $result.reason = "BODY_TOO_LARGE" }
        else {
            $decoded = $null
            if ([string]$Route.body_encoding -eq "utf-8") {
                try {
                    $decoded = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes)
                } catch { $result.reason = "INVALID_UTF8_BODY" }
            }
            if (-not $result.reason -and [bool]$Route.require_html_charset) {
                $httpCharsetPassed = $result.declared_charset -eq "utf-8"
                $htmlCharsetPassed = $decoded -match '(?i)<meta\b[^>]*\bcharset\s*=\s*["'']?utf-8\b'
                if (-not ($httpCharsetPassed -or $htmlCharsetPassed)) {
                    $result.reason = "HTML_CHARSET_MISMATCH"
                }
            }
            if (-not $result.reason -and $Route.marker) {
                $result.marker_present = $decoded.IndexOf(
                    [string]$Route.marker, [StringComparison]::Ordinal) -ge 0
                if (-not $result.marker_present) { $result.reason = "MARKER_MISSING" }
            } elseif (-not $Route.marker) { $result.marker_present = $true }
        }
        $result.passed = [bool](-not $result.reason)
    } catch {
        $reason = [string]$_.Exception.Message
        $result.reason = if ($reason -eq "CANDIDATE_STATIC_HOST_MISMATCH") {
            $reason
        } else { "VALIDATION_REQUEST_FAILED" }
    }
    return [pscustomobject]$result
}

function Invoke-CandidateRouteSample {
    param(
        [Parameter(Mandatory = $true)][object]$Route,
        [Parameter(Mandatory = $true)][hashtable]$VersionHeaders,
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][string]$FixtureRoot,
        [string]$IngestToken = "",
        [ValidateSet("warmup", "acceptance")][string]$ValidationPhase = "acceptance"
    )
    $requestId = [guid]::NewGuid().ToString()
    $headers = @{} + $VersionHeaders
    $headers["X-Aurum-Validation-Run"] = $ValidationRun
    $headers["X-Aurum-Validation-Phase"] = $ValidationPhase
    $headers["X-Aurum-Request-ID"] = $requestId
    $parameters = @{
        UseBasicParsing=$true; Method=[string]$Route.method
        Uri="$workerUrl$($Route.path)$([string]$Route.request_query)"; Headers=$headers; TimeoutSec=30
    }
    if ([string]$Route.strategy -eq "PRODUCTION_SHAPED_DRY_RUN") {
        if (-not $IngestToken) {
            return [pscustomobject]@{
                request_id=$requestId; status=0; passed=$false
                reason="INGEST_AUTHORITY_UNAVAILABLE"
            }
        }
        $fixture = Join-Path $FixtureRoot ([string]$Route.fixture)
        if (-not (Test-Path -LiteralPath $fixture)) {
            return [pscustomobject]@{
                request_id=$requestId; status=0; passed=$false
                reason="VALIDATION_FIXTURE_UNAVAILABLE"
            }
        }
        $headers.Authorization = "Bearer $IngestToken"
        $headers["X-Aurum-Release-Validation"] = "dry-run"
        $parameters.ContentType = "application/json"
        $parameters.Body = [System.IO.File]::ReadAllBytes($fixture)
    }
    try {
        $response = Invoke-WebRequest @parameters
        $payload = $null
        try { $payload = $response.Content | ConvertFrom-Json } catch {}
        $observedVersion = [string]$response.Headers["X-Aurum-Worker-Version"]
        $observedGit = [string]$response.Headers["X-Aurum-Git-SHA"]
        $identityPassed = [bool](
            $observedVersion -eq [string]$Route.expected_worker_version -and
            $observedGit -eq [string]$Route.expected_git_sha
        )
        $dryRunPassed = $true
        if ([string]$Route.strategy -eq "PRODUCTION_SHAPED_DRY_RUN") {
            $dryRunPassed = Test-CandidateDryRunPayload -Payload $payload `
                -ExpectedFamily ([string]$Route.family)
        }
        $passed = [bool]($response.StatusCode -eq 200 -and $identityPassed -and $dryRunPassed)
        $reason = if ([int]$response.StatusCode -ne 200) {
            Get-CandidateRouteResponseReason -Payload $payload -Fallback "HTTP_STATUS_MISMATCH"
        } elseif (-not $identityPassed) {
            "WORKER_IDENTITY_MISMATCH"
        } elseif (-not $dryRunPassed) {
            "RELEASE_DRY_RUN_CONTRACT_MISMATCH"
        } else { $null }
        return [pscustomobject]@{
            request_id=$requestId; method=[string]$Route.method
            path="$([string]$Route.path)$([string]$Route.request_query)"
            expected_status=200; status=[int]$response.StatusCode; passed=$passed
            reason=$reason
            requested_worker_version=[string]$Route.expected_worker_version
            observed_worker_version=$observedVersion; observed_git_sha=$observedGit
            route=[string]$response.Headers["X-Aurum-Route"]
            resource=[string]$response.Headers["X-Aurum-Resource"]
            d1_operations=[string]$response.Headers["X-Aurum-D1-Operations"]
            request_bytes=[string]$response.Headers["X-Aurum-Request-Bytes"]
            response_bytes=[string]$response.Headers["X-Aurum-Response-Bytes"]
            failure_stage=[string]$response.Headers["X-Aurum-Failure-Stage"]
            server_timing=[string]$response.Headers["Server-Timing"]
            validation_run=$ValidationRun
        }
    } catch {
        $errorResponse = $_.Exception.Response
        $status = if ($errorResponse) {
            [int]$errorResponse.StatusCode
        } else { 0 }
        $payload = $null
        try { $payload = $_.ErrorDetails.Message | ConvertFrom-Json } catch {}
        return [pscustomobject]@{
            request_id=$requestId; method=[string]$Route.method
            path="$([string]$Route.path)$([string]$Route.request_query)"
            expected_status=200; status=$status; passed=$false
            reason=(Get-CandidateRouteResponseReason -Payload $payload `
                -Fallback "VALIDATION_REQUEST_FAILED")
            requested_worker_version=[string]$Route.expected_worker_version
            observed_worker_version=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Worker-Version"] } else { "" }
            observed_git_sha=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Git-SHA"] } else { "" }
            route=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Route"] } else { "" }
            resource=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Resource"] } else { "" }
            d1_operations=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-D1-Operations"] } else { "" }
            request_bytes=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Request-Bytes"] } else { "" }
            response_bytes=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Response-Bytes"] } else { "" }
            failure_stage=if ($errorResponse) { [string]$errorResponse.Headers["X-Aurum-Failure-Stage"] } else { "request" }
            server_timing=if ($errorResponse) { [string]$errorResponse.Headers["Server-Timing"] } else { "" }
            validation_run=$ValidationRun
        }
    }
}

function Invoke-CandidateWorkerValidation {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan
    )
    $script:lastWorkersObservabilityDiagnostic = $null
    $script:lastWorkersObservabilityCredentialSource = "UNAVAILABLE"
    $header = @{
        "Cloudflare-Workers-Version-Overrides" =
            "$workerName=`"$([string]$Candidate.worker_version_id)`""
    }
    foreach ($route in @($RoutePlan.worker_reads) + @($RoutePlan.worker_writes)) {
        $route | Add-Member -NotePropertyName expected_worker_version `
            -NotePropertyValue ([string]$Candidate.worker_version_id) -Force
        $route | Add-Member -NotePropertyName expected_git_sha `
            -NotePropertyValue ([string]$Candidate.git_sha) -Force
    }
    $results = @()
    foreach ($route in @($RoutePlan.static_assets)) {
        $results += Invoke-CandidateStaticAssetSample -Candidate $Candidate -Route $route
    }
    $expectedVersionRouteInvocations = @($RoutePlan.static_assets | Where-Object {
        [bool]$_.worker_expected
    }).Count
    $workerExpectedPaths = @($RoutePlan.static_assets | Where-Object {
        [bool]$_.worker_expected
    } | ForEach-Object { [string]$_.path })
    $staticInvocations = @($results | Where-Object {
        [string]$_.route -in $workerExpectedPaths -and [bool]$_.passed -and
        [string]$_.observed_worker_version -eq [string]$Candidate.worker_version_id -and
        [string]$_.observed_git_sha -eq [string]$Candidate.git_sha -and
        [string]$_.observed_route -eq [string]$_.route
    }).Count
    $staticObservabilityState = if ([int]$staticInvocations -eq $expectedVersionRouteInvocations) {
        "PASSED"
    } else { "FAILED" }
    if ([int]$staticInvocations -ne $expectedVersionRouteInvocations) {
        $results += [pscustomobject]@{
            route = "VERSION_HOST_ROUTE_INVOCATIONS"; boundary = "VERSION_HOST_ROUTE"
            method = "GET"; request_id = $null; status = 0; passed = $false
            reason = "VERSION_HOST_ROUTE_WORKER_INVOCATION_MISMATCH"
            expected_invocations = $expectedVersionRouteInvocations
            observed_invocations = $staticInvocations
        }
    }
    $workerRoutes = @($RoutePlan.worker_reads) + @($RoutePlan.worker_writes)
    if ($workerRoutes.Count -eq 0) {
        return [pscustomobject]@{
            channel = "VERSION_HOST_RESULT"
            passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
            validation_run = $null; expected_worker_invocations = $expectedVersionRouteInvocations
            static_worker_invocations = $staticInvocations; routes = $results
            static_observability_state = $staticObservabilityState
            cpu_evidence = "NOT_REQUIRED"
        }
    }
    $workspace = $null
    $validationRun = [guid]::NewGuid().ToString()
    $ingestToken = [Environment]::GetEnvironmentVariable("CLOUDFLARE_INGEST_TOKEN", "User")
    try {
        if (@($RoutePlan.worker_writes).Count -gt 0) {
            $workspace = New-CandidateValidationFixtureWorkspace -Candidate $Candidate
        }
        $fixtureRoot = if ($workspace) { [string]$workspace.fixture_root } else { "" }
        foreach ($route in $workerRoutes) {
            $warmups = @()
            for ($index = 0; $index -lt [int]$route.warmup_samples; $index++) {
                $warmups += Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken `
                    -ValidationPhase "warmup"
            }
            if (@($warmups | Where-Object { -not $_.passed }).Count -gt 0) {
                $firstWarmupFailure = @($warmups | Where-Object { -not $_.passed })[0]
                $results += [pscustomobject]@{
                    route=$route.path; method=$route.method; family=$route.family
                    scenario=$route.scenario
                    boundary=$route.boundary; warmup_samples=$warmups.Count
                    acceptance_samples=0; passed=$false; reason="WARMUP_FAILED"
                    first_failure=$firstWarmupFailure
                }
            }
        }
        $workerStartedAt = [DateTimeOffset]::UtcNow
        foreach ($route in $workerRoutes) {
            $samples = @()
            for ($index = 0; $index -lt [int]$route.acceptance_samples; $index++) {
                $samples += Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken `
                    -ValidationPhase "acceptance"
            }
            $failures = @($samples | Where-Object { -not $_.passed })
            $sampleReason = if ($failures.Count) {
                [string]$failures[0].reason
            } else { $null }
            $results += [pscustomobject]@{
                route=$route.path; path="$([string]$route.path)$([string]$route.request_query)"
                method=$route.method; family=$route.family
                scenario=$route.scenario
                boundary=$route.boundary; warmup_samples=[int]$route.warmup_samples
                acceptance_samples=$samples.Count
                request_ids=@($samples | ForEach-Object { $_.request_id })
                statuses=@($samples | Group-Object status | ForEach-Object {
                    [pscustomobject]@{ status=[int]$_.Name; count=$_.Count }
                })
                passed=[bool]($failures.Count -eq 0)
                reason=$sampleReason
                first_failure=if ($failures.Count) { $failures[0] } else { $null }
            }
        }
        $workerEndedAt = [DateTimeOffset]::UtcNow
        $platform = $null
        if (@($results | Where-Object { -not $_.passed }).Count -eq 0) {
            $expectedInvocations = [int](($workerRoutes |
                Measure-Object -Property acceptance_samples -Sum).Sum)
            $expectedRequests = @($results | Where-Object {
                $_.boundary -in @('WORKER_READ', 'WORKER_WRITE') -and $_.request_ids
            } | ForEach-Object {
                $result = $_
                @($result.request_ids | ForEach-Object {
                    [pscustomobject]@{
                        request_id = [string]$_
                        family = [string]$result.family
                        scenario = [string]$result.scenario
                        method = [string]$result.method
                        path = [string]$result.route
                    }
                })
            })
            Start-Sleep -Seconds 8
            $platform = Get-CandidateFrozenPlatformEvidence -Candidate $Candidate `
                -From $workerStartedAt -To ([DateTimeOffset]::UtcNow) `
                -ExpectedRequests $expectedRequests -ValidationRun $validationRun
        } else {
            $platform = "NOT_RUN"
        }
    } finally {
        Remove-CandidateValidationFixtureWorkspace -Workspace $workspace
    }
    $expectedInvocations = [int](($workerRoutes |
        Measure-Object -Property acceptance_samples -Sum).Sum)
    [pscustomobject]@{
        channel = "VERSION_HOST_RESULT"
        passed = [bool](@($results | Where-Object { -not $_.passed }).Count -eq 0)
        validation_run = $validationRun
        expected_worker_invocations = $expectedInvocations
        observed_worker_invocations = if ($platform -and $platform -ne "NOT_RUN") {
            $platform.invocations
        } else { $null }
        static_worker_invocations = $staticInvocations
        static_observability_state = $staticObservabilityState
        observability_credential_source = [string]$script:lastWorkersObservabilityCredentialSource
        observability_diagnostic = [string]$script:lastWorkersObservabilityDiagnostic
        routes = $results
        cpu_evidence = $platform
    }
}

function Set-CloudflareCandidatePointer {
    param([object]$Stable, [object]$Candidate)
    Invoke-CloudflareDeployment `
        -StableVersionId ([string]$Stable.worker_version_id) `
        -CandidateVersionId ([string]$Candidate.worker_version_id) `
        -Message "stage release candidate $([string]$Candidate.validation_key)"
}

function Invoke-ExactVersionJson {
    param(
        [Parameter(Mandatory = $true)][string]$VersionId,
        [Parameter(Mandatory = $true)][string]$Path
    )
    $headers = @{
        "Cloudflare-Workers-Version-Overrides" = "$workerName=`"$VersionId`""
    }
    $response = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$workerUrl$Path" -Headers $headers -TimeoutSec 30
    if ([int]$response.StatusCode -ne 200) {
        throw "Exact-version read $Path returned $([int]$response.StatusCode)."
    }
    return [pscustomobject]@{
        payload = $response.Content | ConvertFrom-Json
        requested_version_id = $VersionId
        observed_version_id = [string]$response.Headers["X-Aurum-Worker-Version"]
        observed_git_sha = [string]$response.Headers["X-Aurum-Git-SHA"]
        server_timing = [string]$response.Headers["Server-Timing"]
    }
}

function Wait-CandidatePlacementPropagation {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $deadline = [DateTimeOffset]::UtcNow + $candidatePlacementPropagationTimeout
    do {
        try {
            $read = Invoke-ExactVersionJson `
                -VersionId ([string]$Candidate.worker_version_id) -Path "/api/ingest"
            if ([string]$read.observed_version_id -eq
                    [string]$Candidate.worker_version_id -and
                [string]$read.observed_git_sha -eq [string]$Candidate.git_sha) {
                return [pscustomobject]@{
                    passed = $true; state = "PASSED"; reason = "PASSED"
                    observed_version_id = [string]$read.observed_version_id
                    observed_git_sha = [string]$read.observed_git_sha
                }
            }
        } catch {
            $lastError = Protect-PreflightDiagnosticText $_.Exception.Message
        }
        if ([DateTimeOffset]::UtcNow -lt $deadline) {
            Start-Sleep -Seconds $candidatePlacementProbeIntervalSeconds
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    return [pscustomobject]@{
        passed = $false; state = "RETRYABLE"
        reason = "CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
        diagnostic = $lastError
    }
}

function ConvertTo-ReleaseSemanticProjection {
    param([Parameter(Mandatory = $true)][string]$Path, [object]$Payload)
    switch ($Path) {
        "/api/status" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                forward_epoch = $Payload.forward_epoch
                counts = $Payload.counts
                latest = $Payload.latest
                training = $Payload.training
            }
        }
        "/api/audit" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                news_metrics = $Payload.news_metrics
                daily_news_brief_summary = $Payload.daily_news_brief_summary
                storyline_summary = $Payload.storyline_summary
            }
        }
        "/api/learning" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                training = $Payload.training
                learning_curves = $Payload.learning_curves
            }
        }
        "/api/market-chart" {
            return [ordered]@{
                generated_at = $Payload.generated_at
                decisions = $Payload.decisions
                training_markers = $Payload.training_markers
            }
        }
        default { return $Payload }
    }
}

function Test-ReleaseJsonProperty {
    param([object]$Object, [Parameter(Mandatory = $true)][string]$Name)
    return [bool]($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name])
}

function ConvertTo-RequiredReleaseTime {
    param([object]$Value)
    if ($null -eq $Value -or [string]::IsNullOrWhiteSpace([string]$Value)) {
        throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
        [string]$Value,
        [Globalization.CultureInfo]::InvariantCulture,
        [Globalization.DateTimeStyles]::RoundtripKind,
        [ref]$parsed
    )) {
        throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
    }
    return $parsed
}

function Get-ReleaseDatasetCount {
    param([Parameter(Mandatory = $true)][string]$Path, [object]$Payload)
    $properties = switch -Wildcard ($Path) {
        "/api/audit-briefs*" { @("daily_news_briefs") }
        "/api/audit-stories*" { @("storylines", "market_narrative_candidates", "story_event_candidates") }
        "/api/audit-decisions*" { @("recent_decisions", "predictions") }
        "/api/learning*" { @("learning_curves", "models") }
        "/api/market-chart*" { @("decisions", "points") }
        "/api/market-history*" { @("items", "points", "decisions") }
        "/api/news-index*" { @("items", "articles") }
        "/api/news-evidence*" { @("items", "news_evidence") }
        default { @() }
    }
    $count = 0
    foreach ($name in $properties) {
        if (Test-ReleaseJsonProperty -Object $Payload -Name $name) {
            $count += @($Payload.$name).Count
        }
    }
    return $count
}

function Test-CandidateStatusPayload {
    param([object]$StablePayload, [object]$CandidatePayload)
    try {
        foreach ($payload in @($StablePayload, $CandidatePayload)) {
            foreach ($name in @("generated_at", "forward_epoch", "counts", "latest", "system")) {
                if (-not (Test-ReleaseJsonProperty -Object $payload -Name $name)) {
                    throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
                }
            }
            if (-not (Test-ReleaseJsonProperty -Object $payload.counts -Name "decision_events") -or
                -not (Test-ReleaseJsonProperty -Object $payload.latest -Name "decision_time") -or
                -not (Test-ReleaseJsonProperty -Object $payload.system -Name "market_session")) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
            if ($null -eq $payload.counts.decision_events -or
                -not ([string]$payload.system.market_session -in
                    @("OPEN", "CLOSED", "WEEKLY_CLOSED", "DATA_UNAVAILABLE"))) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
        }
        if ([string]::IsNullOrWhiteSpace([string]$StablePayload.forward_epoch) -or
            [string]::IsNullOrWhiteSpace([string]$CandidatePayload.forward_epoch)) {
            throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
        }
        if ([string]$StablePayload.forward_epoch -ne [string]$CandidatePayload.forward_epoch) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_STATUS_SCHEMA_MISMATCH" }
        }
        $stableGenerated = ConvertTo-RequiredReleaseTime $StablePayload.generated_at
        $candidateGenerated = ConvertTo-RequiredReleaseTime $CandidatePayload.generated_at
        $stableDecision = ConvertTo-RequiredReleaseTime $StablePayload.latest.decision_time
        $candidateDecision = ConvertTo-RequiredReleaseTime $CandidatePayload.latest.decision_time
        $stableCount = [long]$StablePayload.counts.decision_events
        $candidateCount = [long]$CandidatePayload.counts.decision_events
        if ($candidateCount -lt $stableCount) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_COUNT_REGRESSION" }
        }
        if (($stableGenerated - $candidateGenerated).TotalSeconds -gt 420) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_STATUS_STALE" }
        }
        if (($stableDecision - $candidateDecision).TotalSeconds -gt 420) {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_DECISION_BEHIND_STABLE" }
        }
        $stableSession = [string]$StablePayload.system.market_session
        $candidateSession = [string]$CandidatePayload.system.market_session
        if ($stableSession -eq "OPEN" -and $candidateSession -ne "OPEN") {
            return [pscustomobject]@{ passed=$false; reason="CANDIDATE_QUOTE_STALE" }
        }
        if ($candidateSession -eq "OPEN") {
            if (-not (Test-ReleaseJsonProperty -Object $CandidatePayload.system -Name "quote_age_seconds") -or
                $null -eq $CandidatePayload.system.quote_age_seconds) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
            $quoteAge = 0.0
            if (-not [double]::TryParse(
                [string]$CandidatePayload.system.quote_age_seconds,
                [Globalization.NumberStyles]::Float,
                [Globalization.CultureInfo]::InvariantCulture,
                [ref]$quoteAge
            ) -or $quoteAge -lt 0) {
                throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            }
            if ($quoteAge -gt 75) {
                return [pscustomobject]@{ passed=$false; reason="CANDIDATE_QUOTE_STALE" }
            }
        }
        return [pscustomobject]@{ passed=$true; reason="PASSED" }
    } catch {
        return [pscustomobject]@{
            passed=$false
            reason=if ($_.Exception.Message -eq "CANDIDATE_STATUS_SCHEMA_MISMATCH") {
                "CANDIDATE_STATUS_SCHEMA_MISMATCH"
            } else { "CANDIDATE_STATUS_SCHEMA_MISMATCH" }
        }
    }
}

function Test-CandidateDataParity {
    param([Parameter(Mandatory = $true)][object]$Stable,
          [Parameter(Mandatory = $true)][object]$Candidate)
    $routes = @(
        "/api/status", "/api/audit", "/api/audit-briefs",
        "/api/audit-stories", "/api/audit-decisions", "/api/learning",
        "/api/market-chart", "/api/market-history?limit=20",
        "/api/news-index?page=1&limit=20",
        "/api/news-evidence?mode=all&page=1&limit=20"
    )
    $legacyMode = [string]$Stable.artifact_kind -eq $legacyBootstrapStableArtifactKind
    $identityMode = if ($legacyMode) {
        "LEGACY_BOOTSTRAP_STABLE_COMPAT"
    } else { "EXACT_VERSION" }
    if ($legacyMode) {
        try { $deployment = Get-CloudflareDeployment } catch { $deployment = $null }
        $stablePlacement = @($deployment.versions | Where-Object {
            [string]$_.version_id -eq [string]$Stable.worker_version_id -and
            [double]$_.percentage -eq 100
        })
        $candidatePlacement = @($deployment.versions | Where-Object {
            [string]$_.version_id -eq [string]$Candidate.worker_version_id -and
            [double]$_.percentage -eq 0
        })
        $runtime = Get-RuntimeCodeState
        $legacyEvidencePassed = [bool](
            $stablePlacement.Count -eq 1 -and
            $candidatePlacement.Count -eq 1 -and
            [string]$Stable.git_sha -match '^[0-9a-f]{40}$' -and
            [string]$Stable.windows_revision -eq [string]$Stable.git_sha -and
            $runtime -and
            [string]$runtime.applied_revision -eq [string]$Stable.windows_revision
        )
        if (-not $legacyEvidencePassed) {
            return [pscustomobject]@{
                state = "FAILED"; passed = $false; identity_mode = $identityMode
                reason = "LEGACY_STABLE_DEPLOYMENT_EVIDENCE_UNPROVEN"
                stable_version_id = [string]$Stable.worker_version_id
                candidate_version_id = [string]$Candidate.worker_version_id
                routes = @()
            }
        }
    }
    $results = @()
    $legacyAuditTime = $null
    foreach ($path in $routes) {
        if ($legacyMode -and $path -in @(
            "/api/audit-briefs", "/api/audit-stories", "/api/audit-decisions"
        )) {
            try {
                $candidateRead = Invoke-ExactVersionJson `
                    -VersionId ([string]$Candidate.worker_version_id) -Path $path
                if ([string]$candidateRead.observed_version_id -ne
                        [string]$Candidate.worker_version_id -or
                    [string]::IsNullOrWhiteSpace([string]$candidateRead.observed_git_sha) -or
                    [string]$candidateRead.observed_git_sha -ne [string]$Candidate.git_sha) {
                    throw "EXACT_VERSION_IDENTITY_MISMATCH"
                }
                $payload = $candidateRead.payload
                $generated = ConvertTo-RequiredReleaseTime $payload.generated_at
                $knownFields = switch ($path) {
                    "/api/audit-briefs" { @("daily_news_briefs") }
                    "/api/audit-stories" { @("storylines", "market_narrative_candidates", "story_event_candidates") }
                    default { @("recent_decisions", "predictions") }
                }
                if (@($knownFields | Where-Object {
                    Test-ReleaseJsonProperty -Object $payload -Name $_
                }).Count -eq 0) {
                    throw "LEGACY_AUDIT_SPLIT_SCHEMA_MISMATCH"
                }
                if ($null -eq $legacyAuditTime) {
                    throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
                }
                # The legacy Windows producer cannot own these resources even
                # when a retained D1 snapshot happens to be recent.
                $deferred = $true
                $results += [pscustomobject]@{
                    route = $path
                    state = if ($deferred) {
                        "DEFERRED_TO_POST_CUTOVER_OBSERVATION"
                    } else { "PASSED" }
                    passed = -not $deferred
                    blocking = $false
                    reason = if ($deferred) {
                        "CANDIDATE_PROJECTION_PRODUCER_NOT_ACTIVE"
                    } else { "PASSED" }
                    required_producer_revision = [string]$Candidate.windows_revision
                    validation_key = [string]$Candidate.validation_key
                    observed_generated_at = $generated.ToString("o")
                    authority_generated_at = $legacyAuditTime.ToString("o")
                    stable_version_id = [string]$Stable.worker_version_id
                    candidate_version_id = [string]$candidateRead.observed_version_id
                }
            } catch {
                $reason = if ($_.Exception.Message -in @(
                    "EXACT_VERSION_IDENTITY_MISMATCH", "CANDIDATE_AUDIT_TRANSITION_STALE",
                    "LEGACY_AUDIT_SPLIT_SCHEMA_MISMATCH"
                )) { $_.Exception.Message } else { "EXACT_VERSION_READ_FAILED" }
                $results += [pscustomobject]@{
                    route = $path; state = "FAILED"; passed = $false; reason = $reason
                    error = Protect-PreflightDiagnosticText $_.Exception.Message
                    stable_version_id = [string]$Stable.worker_version_id
                    candidate_version_id = [string]$Candidate.worker_version_id
                }
            }
            continue
        }
        try {
            $stableRead = Invoke-ExactVersionJson `
                -VersionId ([string]$Stable.worker_version_id) -Path $path
            $candidateRead = Invoke-ExactVersionJson `
                -VersionId ([string]$Candidate.worker_version_id) -Path $path
            if ((-not $legacyMode -and
                    [string]$stableRead.observed_version_id -ne [string]$Stable.worker_version_id) -or
                [string]$candidateRead.observed_version_id -ne [string]$Candidate.worker_version_id -or
                (-not $legacyMode -and (
                    [string]::IsNullOrWhiteSpace([string]$stableRead.observed_git_sha) -or
                    [string]$stableRead.observed_git_sha -ne [string]$Stable.git_sha)) -or
                [string]::IsNullOrWhiteSpace([string]$candidateRead.observed_git_sha) -or
                [string]$candidateRead.observed_git_sha -ne [string]$Candidate.git_sha) {
                throw "EXACT_VERSION_IDENTITY_MISMATCH"
            }
            $stablePayload = $stableRead.payload
            $candidatePayload = $candidateRead.payload
            $stableProjection = ConvertTo-ReleaseSemanticProjection -Path $path -Payload $stablePayload
            $candidateProjection = ConvertTo-ReleaseSemanticProjection -Path $path -Payload $candidatePayload
            $passed = [bool]((@($stableProjection.Keys) -join ",") -ceq
                (@($candidateProjection.Keys) -join ","))
            $reason = if ($passed) { "PASSED" } else { "CANDIDATE_DATA_PARITY_FAILED" }
            if ($path -eq "/api/status") {
                $statusResult = Test-CandidateStatusPayload -StablePayload $stablePayload `
                    -CandidatePayload $candidatePayload
                $passed = [bool]$statusResult.passed; $reason = [string]$statusResult.reason
            }
            if ($path -eq "/api/audit") {
                try {
                    $stableAuditTime = ConvertTo-RequiredReleaseTime $stablePayload.generated_at
                    $candidateAuditTime = ConvertTo-RequiredReleaseTime $candidatePayload.generated_at
                    if (($stableAuditTime - $candidateAuditTime).TotalMinutes -gt 15) {
                        $passed = $false; $reason = "CANDIDATE_AUDIT_TRANSITION_STALE"
                    }
                    if ($legacyMode) { $legacyAuditTime = $stableAuditTime }
                } catch {
                    $passed = $false; $reason = "CANDIDATE_STATUS_SCHEMA_MISMATCH"
                }
            }
            if ($path -notin @("/api/status", "/api/audit")) {
                $stableCount = Get-ReleaseDatasetCount -Path $path -Payload $stablePayload
                $candidateCount = Get-ReleaseDatasetCount -Path $path -Payload $candidatePayload
                if ($stableCount -gt 0 -and $candidateCount -eq 0) {
                    $passed = $false; $reason = "CANDIDATE_DATASET_UNEXPECTEDLY_EMPTY"
                }
            }
            $results += [pscustomobject]@{
                route = $path; state = if ($passed) { "PASSED" } else { "FAILED" }
                passed = $passed; reason = $reason
                stable_version_id = if ($legacyMode) { [string]$Stable.worker_version_id } else { [string]$stableRead.observed_version_id }
                candidate_version_id = [string]$candidateRead.observed_version_id
            }
        } catch {
            $results += [pscustomobject]@{
                route = $path; state = "FAILED"; passed = $false
                reason = if ($_.Exception.Message -eq "EXACT_VERSION_IDENTITY_MISMATCH") {
                    "EXACT_VERSION_IDENTITY_MISMATCH"
                } else { "EXACT_VERSION_READ_FAILED" }
                error = Protect-PreflightDiagnosticText $_.Exception.Message
            }
        }
    }
    $deferred = @($results | Where-Object {
        [string]$_.state -eq "DEFERRED_TO_POST_CUTOVER_OBSERVATION"
    })
    $blocking = @($results | Where-Object {
        -not $_.passed -and [string]$_.state -ne
            "DEFERRED_TO_POST_CUTOVER_OBSERVATION"
    })
    return [pscustomobject]@{
        state = if ($blocking.Count -gt 0) { "FAILED" } elseif ($deferred.Count -gt 0) {
            "PASSED_WITH_DEFERRED_OBLIGATIONS"
        } else { "PASSED" }
        passed = [bool]($blocking.Count -eq 0)
        identity_mode = $identityMode
        stable_version_id = [string]$Stable.worker_version_id
        candidate_version_id = [string]$Candidate.worker_version_id
        routes = $results
        deferred_obligations = @($deferred | ForEach-Object {
            [pscustomobject]@{
                route = [string]$_.route
                state = [string]$_.state
                validation_key = [string]$_.validation_key
                required_producer_revision = [string]$_.required_producer_revision
                authority_generated_at = [string]$_.authority_generated_at
            }
        })
    }
}

function Get-CandidateAuthInspection {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    # workers.dev version URLs are not the Access-protected production host.
    # They may prove application behavior, never a successful human login.
    $result = [ordered]@{
        state = "AUTH_BOUNDARY_NOT_TESTABLE"
        version_id = [string]$Candidate.worker_version_id
        versioned_workers_dev = "UNPROTECTED_TEST_SURFACE"
        production_host_probe = "NOT_OBSERVED"
    }
    try {
        $headers = @{
            "Cloudflare-Workers-Version-Overrides" =
                "$workerName=`"$([string]$Candidate.worker_version_id)`""
        }
        $response = Invoke-WebRequest -UseBasicParsing -Method Get `
            -Uri "$dashboardUrl/admin/api/session" -Headers $headers `
            -MaximumRedirection 0 -TimeoutSec 30
        $result.production_host_probe = "HTTP_$([int]$response.StatusCode)"
        if ([int]$response.StatusCode -in @(401, 403)) {
            $result.state = "UNAUTHENTICATED_BOUNDARY_CONFIRMED"
        }
    } catch {
        $status = if ($_.Exception.Response) {
            [int]$_.Exception.Response.StatusCode
        } else { 0 }
        $result.production_host_probe = if ($status) { "HTTP_$status" } `
            else { "PROBE_UNAVAILABLE" }
        if ($status -in @(401, 403)) {
            $result.state = "UNAUTHENTICATED_BOUNDARY_CONFIRMED"
        }
    }
    return [pscustomobject]$result
}

function Set-CandidateRepositoryPending {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][string]$Reason,
        [Parameter(Mandatory = $true)][string]$Operation,
        [int]$ExitCode = 1,
        [string]$Diagnostic = "",
        [bool]$WindowsPassed = $false
    )
    $state = Get-ReleaseControlState
    if (-not $state -or -not (Test-ReleaseIdentity $state.candidate $Candidate)) {
        return $false
    }
    $priorState = [string]$state.candidate.validation_state
    $priorReason = [string]$state.candidate.validation.reason
    $windowsState = if ($WindowsPassed -or
        [string]$state.candidate.validation.windows -eq "PASSED") {
        "PASSED"
    } else { "NOT_RUN" }
    $state.candidate.validation_state = "CHECKS_PENDING"
    $state.candidate.validation = [pscustomobject]@{
        key = [string]$Candidate.validation_key
        repository = "PENDING"
        repository_retryable = $true
        windows = $windowsState
        cloudflare = "NOT_RUN"
        reason = $Reason
        operation = $Operation
        exit_code = $ExitCode
        diagnostic = Protect-PreflightDiagnosticText $Diagnostic
        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    if ($priorState -ne "CHECKS_PENDING" -or $priorReason -ne $Reason) {
        Write-ReleaseHistory -Event "CANDIDATE_REPOSITORY_PENDING" `
            -Release $state.candidate -Detail @{
                reason = $Reason
                operation = $Operation
                exit_code = $ExitCode
                diagnostic = Protect-PreflightDiagnosticText $Diagnostic
                retryable = $true
            }
    }
    return $true
}

function Invoke-AutomaticCandidateValidation {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $state = Get-ReleaseControlState
    if (-not $state -or -not (Test-ReleaseIdentity $state.candidate $Candidate)) { return $false }
    $priorValidationState = [string]$state.candidate.validation_state
    $windowsAlreadyPassed = [bool](
        [string]$state.candidate.validation.key -eq [string]$Candidate.validation_key -and
        $priorValidationState -in @(
            "NEW", "CHECKS_BLOCKED", "CHECKS_PENDING", "PLATFORM_PENDING"
        ) -and
        [string]$state.candidate.validation.windows -eq "PASSED"
    )
    $startingValidationState = [string]$state.candidate.validation_state
    $startingValidationReason = if ($state.candidate.validation) {
        [string]$state.candidate.validation.reason
    } else { "" }
    $state.candidate.validation_state = "STAGING"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    try {
        if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind) {
            throw "Only a PRODUCTION_CANDIDATE artifact can enter validation."
        }
        $script:lastRepositoryValidationResult = $null
        if (-not (Test-ProductionCandidateProvenance -Candidate $Candidate)) {
            $repositoryResult = $script:lastRepositoryValidationResult
            if ($repositoryResult -and
                [string]$repositoryResult.state -eq "REPOSITORY_PENDING") {
                Set-CandidateRepositoryPending -Candidate $Candidate `
                    -Reason ([string]$repositoryResult.reason) `
                    -Operation ([string]$repositoryResult.operation) `
                    -ExitCode ([int]$repositoryResult.exit_code) `
                    -Diagnostic ([string]$repositoryResult.diagnostic) `
                    -WindowsPassed $windowsAlreadyPassed
                return $false
            }
            $repositoryFailure = if ($repositoryResult -and $repositoryResult.reason) {
                [string]$repositoryResult.reason
            } else { "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED" }
            if ($repositoryResult -and $repositoryResult.diagnostic) {
                $repositoryFailure += ": " +
                    (Protect-PreflightDiagnosticText $repositoryResult.diagnostic)
            }
            throw $repositoryFailure
        }
        $state.candidate.validation_state = "TESTING"
        Write-ReleaseControlState -State $state
        if (-not $windowsAlreadyPassed -and
            -not (Invoke-ProductionShapePreflight -Revision ([string]$Candidate.windows_revision))) {
            throw "Isolated Windows preflight failed."
        }
        $script:lastGitHubChecksResult = $null
        $checks = Test-RequiredGitHubChecks -Revision ([string]$Candidate.git_sha)
        $checksResult = $script:lastGitHubChecksResult
        if ($checks -eq "REPOSITORY_PENDING") {
            Set-CandidateRepositoryPending -Candidate $Candidate `
                -Reason $(if ($checksResult -and $checksResult.reason) {
                    [string]$checksResult.reason
                } else { "GITHUB_TEMPORARILY_UNAVAILABLE" }) `
                -Operation "GITHUB_CHECKS_API" `
                -ExitCode $(if ($checksResult) { [int]$checksResult.exit_code } else { 1 }) `
                -Diagnostic $(if ($checksResult) {
                    [string]$checksResult.diagnostic
                } else { "" }) `
                -WindowsPassed $true
            return $false
        }
        if ($checks -eq "FAILED") {
            throw $(if ($checksResult -and $checksResult.reason) {
                [string]$checksResult.reason
            } else { "GITHUB_CHECKS_ACCESS_FAILED" })
        }
        if ($checks -eq "CHECKS_BLOCKED") {
            $state.candidate.validation_state = "CHECKS_BLOCKED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "FAILED"
                repository_retryable = $true
                windows = "PASSED"
                cloudflare = "NOT_RUN"
                reason = "REQUIRED_GITHUB_CHECKS_BLOCKED"
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            if ($priorValidationState -ne "CHECKS_BLOCKED") {
                Write-ReleaseHistory -Event "CANDIDATE_CHECKS_BLOCKED" `
                    -Release $state.candidate -Detail @{ retryable = $true }
            }
            return $false
        }
        if ($checks -eq "PENDING") {
            $state.candidate.validation_state = "CHECKS_PENDING"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PENDING"
                repository_retryable = $true
                windows = "PASSED"
                cloudflare = "NOT_RUN"
                reason = "REQUIRED_GITHUB_CHECKS_PENDING"
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
        if ($priorValidationState -in @("CHECKS_BLOCKED", "CHECKS_PENDING")) {
            Write-ReleaseHistory -Event "CANDIDATE_CHECKS_RECOVERED" `
                -Release $state.candidate -Detail @{
                    exact_sha = [string]$Candidate.git_sha
                    prior_reason = [string]$Candidate.validation.reason
                }
        }
        $changed = @(Get-CandidateChangedFiles `
            -StableRevision ([string]$state.stable.git_sha) `
            -CandidateRevision ([string]$Candidate.git_sha))
        $broadcastRequired = [bool](@($changed | Where-Object {
            [string]$_ -match '^(broadcast/|web/app/_lib/live-broadcast\.ts$)'
        }).Count -gt 0)
        $broadcast = [pscustomobject]@{
            state = "NOT_REQUIRED"; passed = $true; reason = "NOT_REQUIRED"
        }
        if ($broadcastRequired) {
            $broadcast = Test-BroadcastServiceReadiness `
                -CandidateRevision ([string]$Candidate.git_sha)
            if (-not $broadcast.passed) {
                $state.candidate.validation_state = [string]$broadcast.state
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                    broadcast = $broadcast
                    reason = [string]$broadcast.reason
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                $event = if ([bool]$broadcast.retryable) {
                    "CANDIDATE_BROADCAST_BLOCKED"
                } else { "CANDIDATE_FAILED" }
                if ($startingValidationState -ne [string]$broadcast.state -or
                    $startingValidationReason -ne [string]$broadcast.reason) {
                    Write-ReleaseHistory -Event $event -Release $state.candidate `
                        -Detail @{
                            reason = [string]$broadcast.reason
                            broadcast = $broadcast
                        }
                }
                return $false
            }
            if ($startingValidationState -in @("BROADCAST_PENDING", "BROADCAST_BLOCKED")) {
                Write-ReleaseHistory -Event "CANDIDATE_BROADCAST_RECOVERED" `
                    -Release $state.candidate -Detail @{ broadcast = $broadcast }
            }
        }
        $compatibility = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
        if ([string]$compatibility.state -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
            $accepted = [bool]($state.candidate.migration_acceptance -and
                [string]$state.candidate.migration_acceptance.validation_key -eq
                    [string]$Candidate.validation_key)
            if ($accepted) {
                try {
                    $receipt = Assert-CoordinatedMigrationReceipt `
                        -Candidate $Candidate -Stable $state.stable `
                        -MigrationFiles @($compatibility.files)
                    if ([string]$receipt.receipt_digest -ne
                        [string]$state.candidate.migration_acceptance.receipt_digest) {
                        throw "MIGRATION_RECEIPT_AUTHORITY_MISMATCH"
                    }
                    $state.candidate.compatibility_state =
                        "COORDINATED_STORAGE_MIGRATION_PASSED"
                } catch {
                    $state.candidate.compatibility_state = "REVIEW_REQUIRED"
                    $state.candidate.validation_state = "REVIEW_REQUIRED"
                    $state.candidate.validation = [pscustomobject]@{
                        key = [string]$Candidate.validation_key
                        repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                        reason = "COORDINATED_STORAGE_MIGRATION_EVIDENCE_INVALID"
                        migration_reason = Protect-PreflightDiagnosticText $_.Exception.Message
                        review_files = @($compatibility.files)
                        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                    }
                    Write-ReleaseControlState -State $state
                    return $false
                }
            } else {
                $state.candidate.compatibility_state = "REVIEW_REQUIRED"
                $state.candidate.validation_state = "REVIEW_REQUIRED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = "PENDING"
                    reason = "COORDINATED_STORAGE_MIGRATION_REQUIRED"
                    review_files = @($compatibility.files)
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                return $false
            }
        }
        if ([string]$compatibility.state -eq "PLATFORM_CONFIG_REVIEW_REQUIRED") {
            $approved = [bool]($state.candidate.compatibility_approval -and
                [string]$state.candidate.compatibility_approval.validation_key -eq
                    [string]$Candidate.validation_key)
            if (-not $approved) {
                $resourcesVerified = Test-CandidatePlatformResources `
                    -Stable $state.stable -Candidate $Candidate
                $state.candidate.compatibility_state = "REVIEW_REQUIRED"
                $state.candidate.validation_state = "REVIEW_REQUIRED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                    reason = if ($resourcesVerified) {
                        "PLATFORM_CONFIG_REVIEW_REQUIRED"
                    } else { "PLATFORM_RESOURCE_VERIFICATION_FAILED" }
                    review_files = @($compatibility.files)
                    resources_verified = $resourcesVerified
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                Write-ReleaseHistory -Event "CANDIDATE_COMPATIBILITY_REVIEW_REQUIRED" `
                    -Release $state.candidate -Detail @{
                        validation_key = [string]$Candidate.validation_key
                        files = @($compatibility.files)
                        resources_verified = $resourcesVerified
                    }
                return $false
            }
        }
        $routePlan = Get-CandidateRouteValidationPlan -ChangedFiles $changed `
            -Revision ([string]$Candidate.git_sha)
        $workerChanged = [bool]$routePlan.worker_cpu_required
        $cloudflareChanged = [bool]$routePlan.requires_validation
        Set-CloudflareCandidatePointer -Stable $state.stable -Candidate $Candidate
        $placementPropagation = Wait-CandidatePlacementPropagation -Candidate $Candidate
        if (-not $placementPropagation.passed) {
            $state.candidate.validation_state = "PLATFORM_PENDING"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
                compatibility = [string]$state.candidate.compatibility_state
                reason = "CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
                placement_propagation = $placementPropagation
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            Write-ReleaseHistory -Event "CANDIDATE_PLATFORM_PENDING" `
                -Release $state.candidate -Detail @{
                    reason = "CANDIDATE_PLACEMENT_PROPAGATION_PENDING"
                    retryable = $true
                }
            return $false
        }
        $cloudflare = [pscustomobject]@{ passed = $true; routes = @(); cpu_evidence = "NOT_REQUIRED" }
        if ($cloudflareChanged) {
            $cloudflare = Invoke-CandidateWorkerValidation -Candidate $Candidate `
                -RoutePlan $routePlan
            if (-not $cloudflare.passed) {
                $failedRoutes = @($cloudflare.routes | Where-Object { -not $_.passed })
                $passedRoutes = @($cloudflare.routes | Where-Object { $_.passed })
                $firstFailedRoute = $failedRoutes | Select-Object -First 1
                $firstFailure = if ($firstFailedRoute.first_failure) {
                    $firstFailedRoute.first_failure
                } else {
                    $firstFailedRoute.PSObject.Copy()
                }
                $state.candidate.validation_state = "FAILED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = "FAILED"
                    compatibility = [string]$state.candidate.compatibility_state
                    reason = "DIRECTED_WORKER_VALIDATION_FAILED"
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan
                    routes = $cloudflare.routes
                    routes_tested = [int]($passedRoutes.Count + $failedRoutes.Count)
                    routes_passed = [int]$passedRoutes.Count
                    routes_failed = [int]$failedRoutes.Count
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_worker_invocations = $cloudflare.static_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    first_failure = $firstFailure
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "NOT_RUN" }
                    worker_failures = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_evidence = "NOT_RUN"
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                Write-ReleaseHistory -Event "CANDIDATE_FAILED" `
                    -Release $state.candidate -Detail @{
                        reason = "DIRECTED_WORKER_VALIDATION_FAILED"
                        validation_run = $cloudflare.validation_run
                    }
                return $false
            }
            if (-not $cloudflare.cpu_evidence) {
                $telemetryPending = [bool](
                    [string]$cloudflare.observability_diagnostic -eq
                        "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"
                )
                if ($telemetryPending) {
                    $state.candidate.validation_state = "PLATFORM_PENDING"
                }
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"
                    windows = "PASSED"
                    cloudflare = if ($telemetryPending) { "PENDING" } else { "TESTING" }
                    reason = if ($telemetryPending) {
                        "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"
                    } else { "PLATFORM_CPU_EVIDENCE_REQUIRED" }
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan
                    routes = $cloudflare.routes
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    observability_credential_source = $cloudflare.observability_credential_source
                    observability_diagnostic = $cloudflare.observability_diagnostic
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "DIAGNOSTIC_UNAVAILABLE" }
                    worker_failures = [pscustomobject]@{ state = "DIAGNOSTIC_UNAVAILABLE" }
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                if ($telemetryPending) {
                    Write-ReleaseHistory -Event "CANDIDATE_PLATFORM_PENDING" `
                        -Release $state.candidate -Detail @{
                            reason = "OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING"
                            retryable = $true
                        }
                }
                return $false
            }
            if ([string]$cloudflare.cpu_evidence.gate_state -eq "REVIEW_REQUIRED") {
                $state.candidate.validation_state = "REVIEW_REQUIRED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"
                    cloudflare = "REVIEW_REQUIRED"
                    reason = "WORKER_CPU_HEADROOM_REVIEW_REQUIRED"
                    route_plan = $routePlan; routes = $cloudflare.routes
                    cpu_evidence = $cloudflare.cpu_evidence
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                return $false
            }
            if (-not $cloudflare.cpu_evidence.passed) {
                $platformReason = Get-WorkerPlatformFailureReason `
                    -Evidence $cloudflare.cpu_evidence
                $state.candidate.validation_state = "FAILED"
                $state.candidate.validation = [pscustomobject]@{
                    key = [string]$Candidate.validation_key
                    repository = "PASSED"; windows = "PASSED"; cloudflare = "FAILED"
                    compatibility = [string]$state.candidate.compatibility_state
                    reason = $platformReason
                    validation_run = $cloudflare.validation_run
                    route_plan = $routePlan; routes = $cloudflare.routes
                    expected_worker_invocations = $cloudflare.expected_worker_invocations
                    observed_worker_invocations = $cloudflare.observed_worker_invocations
                    static_observability_state = $cloudflare.static_observability_state
                    observability_credential_source = $cloudflare.observability_credential_source
                    observability_diagnostic = $cloudflare.observability_diagnostic
                    data_parity = [pscustomobject]@{ state = "NOT_RUN" }
                    cpu_headroom = [pscustomobject]@{ state = "FAILED" }
                    worker_failures = [pscustomobject]@{
                        state = if ([int]$cloudflare.cpu_evidence.responses_5xx -gt 0 -or
                            [int]$cloudflare.cpu_evidence.responses_1102 -gt 0) {
                            "FAILED"
                        } else { "PASSED" }
                    }
                    cpu_evidence = $cloudflare.cpu_evidence
                    tested_at = [DateTimeOffset]::UtcNow.ToString("o")
                }
                Write-ReleaseControlState -State $state
                Write-ReleaseHistory -Event "CANDIDATE_FAILED" `
                    -Release $state.candidate -Detail @{
                        reason = $platformReason
                        validation_run = $cloudflare.validation_run
                    }
                return $false
            }
        }
        $dataParity = Test-CandidateDataParity -Stable $state.stable `
            -Candidate $Candidate
        $authInspection = Get-CandidateAuthInspection -Candidate $Candidate
        if (-not $dataParity.passed) {
            $state.candidate.validation_state = "REVIEW_REQUIRED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                repository = "PASSED"; windows = "PASSED"; cloudflare = "PASSED"
                reason = "SEMANTIC_DATA_PARITY_REVIEW_REQUIRED"
                data_parity = $dataParity; auth_inspection = $authInspection
                route_plan = $routePlan; routes = $cloudflare.routes
                cpu_evidence = $cloudflare.cpu_evidence
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            return $false
        }
        $state.candidate.compatibility_state = "PASSED"
        $state.candidate.validation_state = "PASSED"
        $state.candidate.validation = [pscustomobject]@{
            key = [string]$Candidate.validation_key
            repository = "PASSED"
            windows = "PASSED"
            cloudflare = "PASSED"
            worker_changed = $workerChanged
            cloudflare_changed = $cloudflareChanged
            route_plan = $routePlan
            routes = $cloudflare.routes
            cpu_evidence = $cloudflare.cpu_evidence
            data_parity = $dataParity
            auth_inspection = $authInspection
            tested_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_PASSED" -Release $state.candidate
        return $true
    } catch {
        $state = Get-ReleaseControlState
        if ($state -and (Test-ReleaseIdentity $state.candidate $Candidate)) {
            $state.candidate.validation_state = "FAILED"
            $state.candidate.validation = [pscustomobject]@{
                key = [string]$Candidate.validation_key
                error = Protect-PreflightDiagnosticText $_.Exception.Message
                tested_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
            Write-ReleaseControlState -State $state
            Write-ReleaseHistory -Event "CANDIDATE_FAILED" -Release $state.candidate
        }
        return $false
    }
}

function Find-NewCandidateRelease {
    $state = Get-ReleaseControlState
    if (-not $state) { return $null }
    $mainRevision = Get-OriginMainRevision
    if (-not $mainRevision) { return $null }
    $versions = @(Get-CloudflareVersions | Sort-Object `
        @{ Expression = { Get-ReleaseVersionCreatedAtValue -Version $_ } }, `
        @{ Expression = { [string]$_.id } })
    if (@($versions).Count -eq 0) {
        Set-CandidateMaterializationState -State $state -Revision $mainRevision `
            -Status "PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        return $null
    }
    if (-not $state.candidate_discovery.initialized_at) {
        Set-CandidateDiscoveryWatermark -State $state -Version ($versions | Select-Object -Last 1)
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_DISCOVERY_INITIALIZED" -Release $null `
            -Detail @{
                watermark_version_id = [string]$state.candidate_discovery.watermark_version_id
                historical_versions_eligible = $false
            }
        return $null
    }
    $newVersions = @($versions | Where-Object {
        Test-VersionAfterDiscoveryWatermark -Version $_ -Discovery $state.candidate_discovery
    })
    $discovered = $null
    foreach ($version in $newVersions) {
        Set-CandidateDiscoveryWatermark -State $state -Version $version
        $sha = Get-ReleaseGitShaFromVersion -Version $version
        $artifactKind = Get-ReleaseArtifactKindFromVersion -Version $version
        if (-not $sha -or $sha -ne $mainRevision -or
            $sha -eq [string]$state.stable.git_sha -or
            $artifactKind -ne $productionCandidateArtifactKind) { continue }
        $candidate = New-ReleaseIdentity -GitSha $sha `
            -WorkerVersionId ([string]$version.id) -WindowsRevision $sha `
            -Branch (Get-ReleaseBranchFromVersion -Version $version) `
            -ArtifactKind $artifactKind `
            -VersionCreatedAt (Get-ReleaseVersionCreatedAt -Version $version)
        $candidate | Add-Member -NotePropertyName browser_url `
            -NotePropertyValue (Get-ReleaseVersionPreviewUrl `
                -Version $version -Candidate $candidate)
        $discovered = $candidate
    }
    if (-not $discovered) {
        $exactVersion = @($versions | Where-Object {
            (Get-ReleaseGitShaFromVersion -Version $_) -eq $mainRevision -and
            (Get-ReleaseBranchFromVersion -Version $_) -eq "main" -and
            (Get-ReleaseArtifactKindFromVersion -Version $_) -eq
                $productionCandidateArtifactKind
        } | Select-Object -Last 1)
        if ($exactVersion.Count -gt 0 -and $state.candidate -and
            [string]$state.candidate.git_sha -eq $mainRevision -and
            [string]$state.candidate.worker_version_id -eq [string]$exactVersion[0].id) {
            Set-CandidateMaterializationState -State $state -Revision $mainRevision `
                -Status "MATERIALIZED" -WorkerVersionId ([string]$exactVersion[0].id)
            Write-ReleaseControlState -State $state
            if ([string]$state.candidate.validation_state -eq "FAILED") {
                return $null
            }
            return $state.candidate
        }
    }
    if (-not $discovered) {
        Set-CandidateMaterializationState -State $state -Revision $mainRevision `
            -Status "PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        return $null
    }
    Set-CandidateMaterializationState -State $state -Revision $mainRevision `
        -Status "MATERIALIZED" -WorkerVersionId ([string]$discovered.worker_version_id)
    if ($state.transaction) {
        $state.queued_candidate = $discovered
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_QUEUED" -Release $discovered
        return $null
    }
    if ($state.candidate -and (Test-ReleaseIdentity $state.candidate $discovered)) {
        Write-ReleaseControlState -State $state
        return $state.candidate
    }
    if ($state.candidate) {
        Write-ReleaseHistory -Event "CANDIDATE_SUPERSEDED" -Release $state.candidate `
            -Detail @{ replacement_key = [string]$discovered.validation_key }
    }
    $state.candidate = $discovered
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_DISCOVERED" -Release $discovered
    return $discovered
}

function Approve-CandidateCompatibility {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate) {
        throw "Candidate unavailable."
    }
    $candidate = $state.candidate
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        [string]$candidate.validation.reason -ne "PLATFORM_CONFIG_REVIEW_REQUIRED" -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key -or
        -not [bool]$candidate.validation.resources_verified) {
        throw "Only an exact verified non-destructive platform review can be approved."
    }
    $approvalGate = Get-CandidateCompatibilityApprovalGate -Candidate $candidate
    if ([string]$approvalGate.state -eq "RETRYABLE") {
        throw "APPROVAL_RETRYABLE: $([string]$approvalGate.reason). Retry after repository and GitHub checks are available."
    }
    if ([string]$approvalGate.state -ne "PASSED") {
        throw "APPROVAL_REJECTED: $([string]$approvalGate.reason)."
    }
    $changed = @(Get-CandidateChangedFiles `
        -StableRevision ([string]$state.stable.git_sha) `
        -CandidateRevision ([string]$candidate.git_sha))
    $requirement = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
    if ([string]$requirement.state -ne "PLATFORM_CONFIG_REVIEW_REQUIRED" -or
        -not (Test-CandidatePlatformResources -Stable $state.stable -Candidate $candidate)) {
        throw "Candidate is not eligible for non-destructive compatibility approval."
    }
    $approval = @{
        validation_key = [string]$candidate.validation_key
        approved_at = [DateTimeOffset]::UtcNow.ToString("o")
        approved_by = [Environment]::UserName
        reason = "PLATFORM_CONFIG_REVIEW_APPROVED"
        files = @($requirement.files)
        resources_verified = $true
    }
    $candidate | Add-Member -Force -NotePropertyName compatibility_approval `
        -NotePropertyValue $approval
    $candidate.compatibility_state = "APPROVED"
    $candidate.validation_state = "NEW"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_COMPATIBILITY_APPROVED" `
        -Release $candidate -Detail $approval
    return $candidate
}

function Invoke-CandidateDiscovery {
    if (-not (Enter-ReleaseTransactionLock)) { return $false }
    try {
        $state = Get-ReleaseControlState
        if ($state) {
            $state.last_candidate_check = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $state
        }
        $null = Reconcile-ReleaseControlState
        $state = Get-ReleaseControlState
        $candidate = Find-NewCandidateRelease
        if (-not $candidate) {
            $state = Get-ReleaseControlState
            $materialization = if ($state) { $state.candidate_materialization } else { $null }
            if ($state -and -not $state.transaction -and $state.candidate -and
                $materialization -and
                [string]$materialization.state -eq "MATERIALIZED" -and
                [string]$materialization.revision -eq [string]$state.candidate.git_sha -and
                [string]$materialization.worker_version_id -eq
                    [string]$state.candidate.worker_version_id) {
                $candidate = $state.candidate
            }
        }
        if (-not $candidate) { return $false }
        if ([string]$candidate.validation_state -in @(
            "PASSED", "FAILED", "REVIEW_REQUIRED", "REBASE_REQUIRED"
        )) { return $true }
        return Invoke-AutomaticCandidateValidation -Candidate $candidate
    } finally { Exit-ReleaseTransactionLock }
}

function Start-CandidateDiscovery {
    $state = Get-ReleaseControlState
    if (-not $state) { return }
    $lastCheck = [DateTimeOffset]::MinValue
    if ($state.last_candidate_check) {
        [DateTimeOffset]::TryParse([string]$state.last_candidate_check, [ref]$lastCheck) | Out-Null
    }
    if (([DateTimeOffset]::UtcNow - $lastCheck) -lt $candidateDiscoveryInterval) { return }
    $arguments = @(
        "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath), "-Action", "DiscoverCandidate",
        "-RuntimeRoot", ('"{0}"' -f $moduleRoot),
        "-RepositoryRoot", ('"{0}"' -f $repositoryRoot)
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
        -WorkingDirectory $moduleRoot -WindowStyle Hidden | Out-Null
}

function Write-RuntimeUpdateFailure {
    param(
        [string]$Revision,
        [string]$Status,
        [string]$Message,
        [string]$ErrorCode = "RUNTIME_UPDATE_FAILED",
        [string]$Phase = "UNKNOWN",
        [hashtable]$Diagnostics = @{}
    )
    Write-RuntimeUpdateState @{
        update_status = $Status
        failed_revision = $Revision
        failed_preflight_contract = $runtimePreflightContractVersion
        failed_at = [DateTimeOffset]::UtcNow.ToString("o")
        user_visible_failure = $true
        failure_message = $Message
        failure_code = $ErrorCode
        failure_phase = $Phase
        preflight_diagnostics = $Diagnostics
    }
    Write-WatchdogEvent -Event "RUNTIME_UPDATE_FAILED" -Service "all" -State $Message
}

function Get-Sha256Hex {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $stream = [System.IO.File]::OpenRead($LiteralPath)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString(
            $algorithm.ComputeHash($stream)
        ) -replace "-", "").ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Get-RuntimeControlBundleIdentityAtRoot {
    param([Parameter(Mandatory = $true)][string]$ControlRoot)
    $path = Join-Path $ControlRoot $runtimeControlManifestName
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $identity = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
        if (-not [bool]$identity.exact_revision -or
            [string]$identity.source_revision -notmatch '^[0-9a-f]{40}$') {
            return $null
        }
        foreach ($name in $runtimeControlFileNames) {
            $file = Join-Path $ControlRoot $name
            $expected = [string]$identity.files.$name
            if (-not (Test-Path -LiteralPath $file) -or
                $expected -notmatch '^[0-9a-f]{64}$') { return $null }
            $actual = Get-Sha256Hex -LiteralPath $file
            if ($actual -ne $expected) { return $null }
        }
        return $identity
    } catch { return $null }
}

function New-VerifiedRuntimeControlBundleStage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$SourceRevision,
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [switch]$RequireImmutableSource
    )
    if ($SourceRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
    $revisionOutput = @(& git -C $SourceRoot rev-parse HEAD 2>$null)
    $revisionExitCode = $LASTEXITCODE
    $observedRevision = if ($revisionOutput.Count -gt 0) {
        ([string]$revisionOutput[0]).Trim()
    } else { "" }
    if ($revisionExitCode -ne 0 -or $observedRevision -ne $SourceRevision) {
        throw "CONTROL_BUNDLE_SOURCE_REVISION_MISMATCH"
    }
    if ($RequireImmutableSource) {
        $dirty = @(& git -C $SourceRoot status --porcelain 2>$null)
        if ($LASTEXITCODE -ne 0 -or $dirty.Count -ne 0) {
            throw "CONTROL_BUNDLE_IMMUTABLE_SOURCE_REQUIRED"
        }
        & git -C $SourceRoot symbolic-ref -q HEAD 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            throw "CONTROL_BUNDLE_DETACHED_SOURCE_REQUIRED"
        }
    }
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
    foreach ($name in $runtimeControlFileNames) {
        $source = Join-Path $SourceRoot ("scripts\{0}" -f $name)
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Missing runtime control file: $source"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $StageRoot $name) -Force
    }
    $hashes = @{}
    foreach ($name in $runtimeControlFileNames) {
        $hashes[$name] = Get-Sha256Hex -LiteralPath (Join-Path $StageRoot $name)
    }
    [pscustomobject]@{
        schema_version = 1
        source_revision = $SourceRevision
        exact_revision = $true
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
        files = $hashes
    } | ConvertTo-Json -Depth 5 | Set-Content `
        -LiteralPath (Join-Path $StageRoot $runtimeControlManifestName) -Encoding UTF8
    $identity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $StageRoot
    if (-not $identity -or [string]$identity.source_revision -ne $SourceRevision) {
        throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED"
    }
    return $identity
}

function Install-VerifiedRuntimeControlBundleStage {
    param(
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [Parameter(Mandatory = $true)][string]$ControlRoot,
        [Parameter(Mandatory = $true)][string]$BackupRoot
    )
    $stagedIdentity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $StageRoot
    if (-not $stagedIdentity) { throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED" }
    New-Item -ItemType Directory -Path $ControlRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $payloadNames = @($runtimeControlFileNames) + @($runtimeControlManifestName)
    foreach ($name in $payloadNames) {
        $destination = Join-Path $ControlRoot $name
        if (Test-Path -LiteralPath $destination) {
            Copy-Item -LiteralPath $destination -Destination (Join-Path $BackupRoot $name) -Force
        }
    }
    try {
        foreach ($name in $payloadNames) {
            Copy-Item -LiteralPath (Join-Path $StageRoot $name) `
                -Destination (Join-Path $ControlRoot $name) -Force
        }
        $installed = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $ControlRoot
        if (-not $installed -or
            [string]$installed.source_revision -ne [string]$stagedIdentity.source_revision) {
            throw "CONTROL_BUNDLE_INSTALLED_HASH_VERIFICATION_FAILED"
        }
        return $installed
    } catch {
        Restore-RuntimeControlBundleBackup -BackupRoot $BackupRoot -ControlRoot $ControlRoot
        throw
    }
}

function Restore-RuntimeControlBundleBackup {
    param(
        [Parameter(Mandatory = $true)][string]$BackupRoot,
        [Parameter(Mandatory = $true)][string]$ControlRoot
    )
    $backupIdentity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $BackupRoot
    if (-not $backupIdentity) { throw "CONTROL_BUNDLE_BACKUP_VERIFICATION_FAILED" }
    foreach ($name in @($runtimeControlFileNames) + @($runtimeControlManifestName)) {
        Copy-Item -LiteralPath (Join-Path $BackupRoot $name) `
            -Destination (Join-Path $ControlRoot $name) -Force
    }
    $restored = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $ControlRoot
    if (-not $restored -or
        [string]$restored.source_revision -ne [string]$backupIdentity.source_revision) {
        throw "CONTROL_BUNDLE_ROLLBACK_VERIFICATION_FAILED"
    }
    return $restored
}

function Sync-StableRuntimeControlFiles {
    param(
        [string]$SourceRoot = $moduleRoot,
        [string]$ControlRoot = (Join-Path $repositoryRoot ".local\runtime-control"),
        [string]$SourceRevision = ""
    )
    # Keep transactional paths beside the bundle and short enough for Windows
    # PowerShell/.NET installations that still enforce legacy MAX_PATH limits.
    $controlParent = Split-Path -Parent $ControlRoot
    $transactionId = [guid]::NewGuid().ToString("N")
    $stageRoot = Join-Path $controlParent (".rcs-{0}" -f $transactionId)
    $backupRoot = Join-Path $controlParent (".rcb-{0}" -f $transactionId)
    try {
        if (-not $SourceRevision) {
            $SourceRevision = (& git -C $SourceRoot rev-parse HEAD 2>$null | Select-Object -First 1)
        }
        $null = New-VerifiedRuntimeControlBundleStage -SourceRoot $SourceRoot `
            -SourceRevision $SourceRevision -StageRoot $stageRoot
        $null = Install-VerifiedRuntimeControlBundleStage -StageRoot $stageRoot `
            -ControlRoot $ControlRoot -BackupRoot $backupRoot
    } finally {
        if (Test-Path -LiteralPath $stageRoot) {
            Remove-Item -LiteralPath $stageRoot -Recurse -Force
        }
        if (Test-Path -LiteralPath $backupRoot) {
            Remove-Item -LiteralPath $backupRoot -Recurse -Force
        }
    }
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
    $result = & $Python -c $copy $SourceDatabase $TargetDatabase 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "candidate evidence copy failed: $result"
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
    $result = & $Python -c $migration $StageRoot $TargetDatabase 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "candidate evidence migration failed: $result"
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
        $tail = (Get-Content -LiteralPath $Path -Tail 40 -ErrorAction Stop) -join "`n"
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
    $database = Join-Path $moduleRoot ".local\forward\forward-evidence.sqlite3"
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
        $candidateDatabase = Join-Path $stageRoot ".local\preflight\forward-evidence.sqlite3"
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
        $process = Start-Process -FilePath $python -ArgumentList @(
            (Join-Path $stageRoot "scripts\run_dashboard_api.py"),
            "--database", $candidateDatabase, "--host", "127.0.0.1",
            "--port", [string]$preflightPort
        ) -WorkingDirectory $stageRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
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
        $result = & $python @arguments 2>&1
        if ($LASTEXITCODE -ne 0) {
            $failureCode = "PRODUCTION_SHAPE_REJECTED"
            $productionShapeOutput = Protect-PreflightDiagnosticText ($result -join "`n")
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
        & git -C $repositoryRoot worktree prune 2>$null
    }
}

function Update-RuntimeCheckout {
    param([string]$Revision)
    if (-not $RuntimeRoot) { return $false }
    $previousRevision = Get-CodeRevision
    $releaseState = Get-ReleaseControlState
    if (-not $releaseState -or -not $releaseState.candidate -or
        [string]$releaseState.candidate.validation_state -ne "PASSED" -or
        [string]$releaseState.candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$releaseState.candidate.windows_revision -ne $Revision -or
        [string]$releaseState.candidate.validation.key -ne
            [string]$releaseState.candidate.validation_key) { return $false }
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
        }
        Write-RuntimeUpdateFailure -Revision $Revision -Status "SWITCH_FAILED" `
            -Message "Candidate switch failed before service reload; the current version is still running: $reason"
        return $false
    }
}
function Get-ReleaseSecret {
    param([Parameter(Mandatory = $true)][string]$Name)
    if ([System.IO.Path]::GetDirectoryName($releaseSecretsPath) -ne $releaseSecretsRoot) {
        return [pscustomobject]@{
            available = $false; value = ""; source = "UNAVAILABLE"
            diagnostic = "LOCAL_SECRET_PATH_INVALID"
        }
    }
    if (Test-Path -LiteralPath $releaseSecretsPath) {
        try {
            $secrets = Get-Content -LiteralPath $releaseSecretsPath -Raw -Encoding UTF8 |
                ConvertFrom-Json
        } catch {
            return [pscustomobject]@{
                available = $false; value = ""; source = "UNAVAILABLE"
                diagnostic = "LOCAL_SECRET_FILE_MALFORMED_JSON"
            }
        }
        $property = $secrets.PSObject.Properties[$Name]
        if (-not $property) {
            return [pscustomobject]@{
                available = $false; value = ""; source = "UNAVAILABLE"
                diagnostic = "LOCAL_SECRET_KEY_MISSING"
            }
        }
        $value = ([string]$property.Value).Trim()
        if (-not $value) {
            return [pscustomobject]@{
                available = $false; value = ""; source = "UNAVAILABLE"
                diagnostic = "LOCAL_SECRET_VALUE_EMPTY"
            }
        }
        return [pscustomobject]@{
            available = $true; value = $value; source = "LOCAL_SECRET_FILE"
            diagnostic = $null
        }
    }
    $value = ([string](Get-UserEnvironmentValue -Name $Name)).Trim()
    if ($value) {
        return [pscustomobject]@{
            available = $true; value = $value; source = "USER_ENVIRONMENT"
            diagnostic = $null
        }
    }
    return [pscustomobject]@{
        available = $false; value = ""; source = "UNAVAILABLE"
        diagnostic = "RELEASE_SECRET_UNAVAILABLE"
    }
}

function Get-MigrationD1Binding {
    param([Parameter(Mandatory = $true)][object]$Version)
    $bindings = @($Version.resources.bindings | Where-Object {
        [string]$_.type -eq "d1" -and [string]$_.name -eq "DB"
    })
    if ($bindings.Count -ne 1 -or
        [string]$bindings[0].database_id -notmatch '^[0-9a-f-]{36}$') {
        throw "MIGRATION_D1_BINDING_IDENTITY_INVALID"
    }
    return $bindings[0]
}

function Get-CoordinatedMigrationFiles {
    param(
        [Parameter(Mandatory = $true)][string[]]$ChangedFiles,
        [Parameter(Mandatory = $true)][string]$CandidateRevision
    )
    $requirement = Get-CandidateCompatibilityRequirement -ChangedFiles $ChangedFiles
    if ([string]$requirement.state -ne "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
        throw "COORDINATED_STORAGE_MIGRATION_NOT_REQUIRED"
    }
    $files = @($requirement.files | Sort-Object -Unique)
    if ($files.Count -eq 0 -or @($files | Where-Object {
        $_ -notmatch '^web/drizzle/[0-9]{4}_[A-Za-z0-9_-]+\.sql$'
    }).Count -gt 0) {
        throw "MIGRATION_FILE_SCOPE_INVALID"
    }
    foreach ($file in $files) {
        $exists = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION" `
            -Arguments @("-C", $repositoryRoot, "cat-file", "-e", "${CandidateRevision}:$file")
        if (-not $exists.passed) {
            throw "MIGRATION_FILE_MISSING:$file"
        }
    }
    return $files
}

function Assert-CoordinatedMigrationCapabilityContract {
    param(
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [Parameter(Mandatory = $true)][string]$CandidateRevision
    )
    $supported = @(
        "web/drizzle/0022_news_projection_generation.sql",
        "web/drizzle/0023_operator_retry_sync_digest.sql",
        "web/drizzle/0024_seed_bounded_audit_news_metrics.sql",
        "web/drizzle/0025_seed_legacy_news_reverse_projection.sql"
    )
    $unknown = @($MigrationFiles | Where-Object { $_ -notin $supported })
    if ($unknown.Count -gt 0) {
        throw "MIGRATION_CAPABILITY_CONTRACT_MISSING:$($unknown -join ',')"
    }
    foreach ($file in $MigrationFiles) {
        $read = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION" `
            -Arguments @("-C", $repositoryRoot, "show", "${CandidateRevision}:$file")
        if (-not $read.passed) { throw "MIGRATION_FILE_MISSING:$file" }
        $sql = @($read.output) -join "`n"
        $isBoundedAuditHandover = $file -eq "web/drizzle/0024_seed_bounded_audit_news_metrics.sql" -and
            $sql -match '(?im)ON\s+CONFLICT\s*\(`id`\)\s+DO\s+UPDATE' -and
            $sql -match '(?im)WHERE\s+`id`\s*=\s*4' -and
            $sql -match '(?im)SELECT\s+9,' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        $isLegacyNewsHandover = $file -eq "web/drizzle/0025_seed_legacy_news_reverse_projection.sql" -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_details`' -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_index`' -and
            $sql -match '(?im)FROM\s+`news_projection_details`' -and
            $sql -match '(?im)FROM\s+`news_projection_index`' -and
            $sql -match '(?im)s\.`projection_state`\s*=\s*''CURRENT''' -and
            $sql -match '(?im)s\.`receipt_digest`\s*=\s*g\.`expected_receipt_digest`' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        if (($sql -match '(?im)\b(DROP|DELETE|UPDATE|REPLACE|TRUNCATE|VACUUM)\b') -and
            -not $isBoundedAuditHandover -and -not $isLegacyNewsHandover) {
            throw "MIGRATION_REVERSE_INCOMPATIBLE:$file"
        }
    }
}

function Invoke-CoordinatedMigrationD1Query {
    param([Parameter(Mandatory = $true)][string]$Sql)
    # Windows cmd.exe cannot preserve embedded newlines in an argument passed
    # through npx.cmd.  Keep the SQL as one argument so Wrangler receives the
    # complete statement instead of an incomplete prefix.
    $command = ($Sql -replace "`r`n|`n|`r", " ").Trim()
    $blocks = @(Invoke-WranglerJson -Arguments @(
        "d1", "execute", "DB", "--remote", "--command", $command
    ))
    if ($blocks.Count -eq 0 -or @($blocks | Where-Object { -not [bool]$_.success }).Count -gt 0) {
        throw "MIGRATION_D1_QUERY_FAILED"
    }
    foreach ($block in $blocks) {
        foreach ($row in @($block.results)) { Write-Output $row }
    }
}

function Get-CoordinatedMigrationEndpointEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable
    )
    $candidateStatus = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$([string]$Candidate.browser_url)/api/status" -TimeoutSec 45
    $candidateHealth = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$([string]$Candidate.browser_url)/api/news-index?health_check=1" `
        -TimeoutSec 45
    $stableStatus = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$workerUrl/api/status" -TimeoutSec 45
    $stableNewsHealth = Invoke-WebRequest -UseBasicParsing -Method Get `
        -Uri "$workerUrl/api/news-index?health_check=1" -TimeoutSec 45
    $candidatePayload = $candidateStatus.Content | ConvertFrom-Json
    $healthPayload = $candidateHealth.Content | ConvertFrom-Json
    $stablePayload = $stableStatus.Content | ConvertFrom-Json
    $stableNewsPayload = $stableNewsHealth.Content | ConvertFrom-Json
    $observedVersion = [string]$candidateStatus.Headers["X-Aurum-Worker-Version"]
    $observedGit = [string]$candidateStatus.Headers["X-Aurum-Git-SHA"]
    if ([int]$candidateStatus.StatusCode -ne 200 -or
        $observedVersion -ne [string]$Candidate.worker_version_id -or
        $observedGit -ne [string]$Candidate.git_sha) {
        throw "MIGRATION_CANDIDATE_READ_IDENTITY_FAILED"
    }
    if ([int]$stableStatus.StatusCode -ne 200 -or
        $null -eq $stablePayload.counts.decision_events -or
        [long]$stablePayload.counts.decision_events -le 0) {
        throw "MIGRATION_LEGACY_STABLE_READ_FAILED"
    }
    if ([int]$stableNewsHealth.StatusCode -ne 200 -or
        [string]$stableNewsPayload.status -ne "OK" -or
        [int]$stableNewsPayload.violation_count -ne 0) {
        throw "MIGRATION_LEGACY_NEWS_READ_FAILED"
    }
    if ([int]$candidateHealth.StatusCode -ne 200 -or
        [string]$healthPayload.projection_state -ne "CURRENT" -or
        -not [bool]$healthPayload.verified_complete -or
        [int]$healthPayload.index_count -ne [int]$healthPayload.detail_count -or
        [int]$healthPayload.missing_detail_count -ne 0 -or
        [int]$healthPayload.invariant_violation_count -ne 0 -or
        [string]$healthPayload.receipt_digest -ne
            [string]$healthPayload.source_receipt_digest) {
        throw "MIGRATION_NEWS_CURRENT_INVALID"
    }
    return [ordered]@{
        stable_status = 200
        stable_decision_count_positive = $true
        stable_news_status = [string]$stableNewsPayload.status
        stable_news_violation_count = [int]$stableNewsPayload.violation_count
        candidate_status = 200
        candidate_worker_version = $observedVersion
        candidate_git_sha = $observedGit
        news_generation_id = [string]$healthPayload.active_generation_id
        news_snapshot_id = [string]$healthPayload.snapshot_id
        news_source_digest = [string]$healthPayload.source_digest
        news_receipt_digest = [string]$healthPayload.receipt_digest
        news_index_count = [int]$healthPayload.index_count
        news_detail_count = [int]$healthPayload.detail_count
    }
}

function Get-CoordinatedMigrationLiveEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles
    )
    Assert-CoordinatedMigrationCapabilityContract -MigrationFiles $MigrationFiles `
        -CandidateRevision ([string]$Candidate.git_sha)
    $candidateVersion = Get-CloudflareVersionDetails `
        -VersionId ([string]$Candidate.worker_version_id)
    $stableVersion = Get-CloudflareVersionDetails `
        -VersionId ([string]$Stable.worker_version_id)
    $candidateBinding = Get-MigrationD1Binding -Version $candidateVersion
    $stableBinding = Get-MigrationD1Binding -Version $stableVersion
    if ([string]$candidateBinding.database_id -ne [string]$stableBinding.database_id) {
        throw "MIGRATION_REVERSE_DATABASE_IDENTITY_MISMATCH"
    }
    $database = Invoke-WranglerJson -Arguments @("d1", "info", "DB")
    if ([string]$database.uuid -ne [string]$candidateBinding.database_id) {
        throw "MIGRATION_DATABASE_IDENTITY_MISMATCH"
    }
    $ledger = @(Invoke-CoordinatedMigrationD1Query -Sql `
        "SELECT name,applied_at FROM d1_migrations ORDER BY id")
    $ledgerNames = @($ledger | ForEach-Object { [string]$_.name })
    $migrationTree = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION_TREE" `
        -Arguments @("-C", $repositoryRoot, "ls-tree", "-r", "--name-only",
            ([string]$Candidate.git_sha), "--", "web/drizzle")
    if (-not $migrationTree.passed) { throw "MIGRATION_FILE_SCOPE_INVALID" }
    $candidateMigrationNames = @($migrationTree.output | Where-Object {
        [string]$_ -match '^web/drizzle/[^/]+\.sql$'
    } | ForEach-Object { Split-Path ([string]$_) -Leaf } | Sort-Object -Unique)
    $pending = @($candidateMigrationNames | Where-Object { $_ -notin $ledgerNames })
    if ($pending.Count -gt 0) {
        throw "MIGRATION_LEDGER_PENDING:$($pending -join ',')"
    }
    $requiredNames = @($MigrationFiles | ForEach-Object { Split-Path $_ -Leaf })
    $missingRequired = @($requiredNames | Where-Object { $_ -notin $ledgerNames })
    if ($missingRequired.Count -gt 0) {
        throw "MIGRATION_LEDGER_REQUIRED_MISSING:$($missingRequired -join ',')"
    }
    $capabilitySql = @"
SELECT
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('news_projection_generations','news_projection_index','news_projection_details',
   'news_projection_batches','news_projection_state')) AS projection_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='index' AND name IN
  ('news_projection_generations_state_idx','news_projection_index_ordinal_idx',
   'news_projection_index_page_idx','news_projection_index_category_idx')) AS projection_indexes,
 (SELECT count(*) FROM pragma_table_info('operator_retry_sync_state') WHERE name IN
  ('id','payload_digest','item_count','synced_at')) AS retry_columns,
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('dashboard_snapshots','news_index','news_details','news_evidence_records')) AS legacy_tables,
 coalesce((SELECT json_array_length(json_extract(payload,'$.recent_decisions'))
   FROM dashboard_snapshots WHERE id=4 AND json_valid(payload)),0) AS legacy_decisions,
 (SELECT count(*) FROM news_projection_index pi
   WHERE pi.generation_id=s.active_generation_id
     AND EXISTS(SELECT 1 FROM news_index li WHERE li.detail_key=pi.detail_key))
   AS legacy_current_index_count,
 (SELECT count(*) FROM news_projection_details pd
   WHERE pd.generation_id=s.active_generation_id
     AND EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=pd.detail_key))
   AS legacy_current_detail_count,
 (SELECT count(*) FROM news_index li
   WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
     AND NOT EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=li.detail_key))
   AS legacy_missing_detail_count,
 (SELECT count(*) FROM news_index li
   WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
     AND NOT (
       (json_extract(li.payload,'$.annotation_status')='NOT_REQUIRED'
         AND json_extract(li.payload,'$.model_visibility')='MODEL_INELIGIBLE'
         AND json_extract(li.payload,'$.parsed_at') IS NULL)
       OR (json_extract(li.payload,'$.annotation_status')='QUEUED'
         AND json_extract(li.payload,'$.model_visibility')='NOT_YET_PARSED'
         AND json_extract(li.payload,'$.parsed_at') IS NULL)
       OR (json_extract(li.payload,'$.annotation_status')='READY'
         AND json_extract(li.payload,'$.model_visibility')<>'NOT_YET_PARSED'
         AND json_extract(li.payload,'$.parsed_at') IS NOT NULL)
       OR (json_extract(li.payload,'$.annotation_status') IN
         ('REPAIRING_DISPLAY','BACKING_OFF','DEAD_LETTER','WAITING_CONTENT','CONTENT_UNAVAILABLE')
         AND json_extract(li.payload,'$.model_visibility')=
             json_extract(li.payload,'$.annotation_status')
         AND json_extract(li.payload,'$.parsed_at') IS NULL)))
   AS legacy_review_violation_count,
 (SELECT count(*) FROM news_index li
   WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
     AND li.parsed <> CASE
       WHEN json_extract(li.payload,'$.parsed_at') IS NOT NULL THEN 1 ELSE 0 END)
   AS legacy_parsed_flag_mismatch_count,
 (SELECT count(*) FROM news_index li
   WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
     AND li.model_candidate <> CASE
       WHEN json_extract(li.payload,'$.model_visibility')='MODEL_VISIBLE' THEN 1 ELSE 0 END)
   AS legacy_candidate_flag_mismatch_count,
 (SELECT count(*) FROM (
   SELECT cluster_id FROM news_index li
    WHERE COALESCE(json_extract(li.payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
    GROUP BY cluster_id HAVING count(*) > 1))
   AS legacy_duplicate_cluster_count,
 s.projection_state,s.active_generation_id,s.snapshot_id,s.source_digest,s.receipt_digest,
 s.index_count,s.detail_count,s.missing_detail_count,s.invariant_violation_count,
 g.state AS generation_state,g.expected_receipt_digest,g.staged_index_count,
 g.staged_detail_count
FROM news_projection_state s JOIN news_projection_generations g
 ON g.generation_id=s.active_generation_id WHERE s.id=1
"@
    $capabilities = @(Invoke-CoordinatedMigrationD1Query -Sql $capabilitySql)
    if ($capabilities.Count -ne 1 -or
        [int]$capabilities[0].projection_tables -ne 5 -or
        [int]$capabilities[0].projection_indexes -ne 4 -or
        [int]$capabilities[0].retry_columns -ne 4) {
        throw "MIGRATION_SCHEMA_CAPABILITY_MISSING"
    }
    $state = $capabilities[0]
    if ([int]$state.legacy_tables -ne 4 -or [int]$state.legacy_decisions -le 0) {
        throw "MIGRATION_LEGACY_COMPATIBILITY_FAILED"
    }
    if ([string]$state.projection_state -ne "CURRENT" -or
        [string]$state.generation_state -ne "CURRENT" -or
        [int]$state.index_count -ne [int]$state.detail_count -or
        [int]$state.index_count -ne [int]$state.staged_index_count -or
        [int]$state.detail_count -ne [int]$state.staged_detail_count -or
        [int]$state.missing_detail_count -ne 0 -or
        [int]$state.invariant_violation_count -ne 0 -or
        [string]$state.receipt_digest -ne [string]$state.expected_receipt_digest) {
        throw "MIGRATION_NEWS_CURRENT_INVALID"
    }
    if ([int]$state.legacy_current_index_count -ne [int]$state.index_count -or
        [int]$state.legacy_current_detail_count -ne [int]$state.detail_count -or
        [int]$state.legacy_missing_detail_count -ne 0 -or
        [int]$state.legacy_review_violation_count -ne 0 -or
        [int]$state.legacy_parsed_flag_mismatch_count -ne 0 -or
        [int]$state.legacy_candidate_flag_mismatch_count -ne 0 -or
        [int]$state.legacy_duplicate_cluster_count -ne 0) {
        throw "MIGRATION_LEGACY_NEWS_COMPATIBILITY_FAILED"
    }
    $endpoints = Get-CoordinatedMigrationEndpointEvidence `
        -Candidate $Candidate -Stable $Stable
    if ([string]$endpoints.news_generation_id -ne [string]$state.active_generation_id -or
        [string]$endpoints.news_snapshot_id -ne [string]$state.snapshot_id -or
        [string]$endpoints.news_source_digest -ne [string]$state.source_digest -or
        [string]$endpoints.news_receipt_digest -ne [string]$state.receipt_digest -or
        [int]$endpoints.news_index_count -ne [int]$state.index_count -or
        [int]$endpoints.news_detail_count -ne [int]$state.detail_count) {
        throw "MIGRATION_NEWS_CURRENT_IDENTITY_MISMATCH"
    }
    $migrationHashes = @($MigrationFiles | ForEach-Object {
        $blob = Invoke-RepositoryRead -Operation "READ_CANDIDATE_MIGRATION_BLOB" `
            -Arguments @("-C", $repositoryRoot, "rev-parse",
                "$([string]$Candidate.git_sha):$_")
        $blobId = if ($blob.passed) { ([string]@($blob.output)[0]).Trim() } else { "" }
        if ($blobId -notmatch '^[0-9a-f]{40,64}$') {
            throw "MIGRATION_FILE_HASH_INVALID:$_"
        }
        [ordered]@{
            path = $_
            git_blob_oid = $blobId
        }
    })
    return [ordered]@{
        validation_key = [string]$Candidate.validation_key
        candidate_git_sha = [string]$Candidate.git_sha
        candidate_worker_version = [string]$Candidate.worker_version_id
        stable_git_sha = [string]$Stable.git_sha
        stable_worker_version = [string]$Stable.worker_version_id
        database_id = [string]$database.uuid
        database_name = [string]$database.name
        migration_files = $migrationHashes
        applied_migrations = @($ledgerNames)
        pending_migrations = @()
        projection_tables = [int]$state.projection_tables
        projection_indexes = [int]$state.projection_indexes
        operator_retry_columns = [int]$state.retry_columns
        legacy_tables = [int]$state.legacy_tables
        legacy_decisions = [int]$state.legacy_decisions
        legacy_news_index_count = [int]$state.legacy_current_index_count
        legacy_news_detail_count = [int]$state.legacy_current_detail_count
        legacy_news_missing_detail_count = [int]$state.legacy_missing_detail_count
        legacy_news_invariant_violation_count = [int]$state.legacy_review_violation_count
        legacy_news_parsed_flag_mismatch_count = [int]$state.legacy_parsed_flag_mismatch_count
        legacy_news_candidate_flag_mismatch_count = [int]$state.legacy_candidate_flag_mismatch_count
        legacy_news_duplicate_cluster_count = [int]$state.legacy_duplicate_cluster_count
        stable_news_status = [string]$endpoints.stable_news_status
        news_generation_id = [string]$state.active_generation_id
        news_snapshot_id = [string]$state.snapshot_id
        news_source_digest = [string]$state.source_digest
        news_receipt_digest = [string]$state.receipt_digest
        news_index_count = [int]$state.index_count
        news_detail_count = [int]$state.detail_count
        stable_read = [int]$endpoints.stable_status
        candidate_read = [int]$endpoints.candidate_status
        reverse_safe = $true
    }
}

function Get-CoordinatedMigrationReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 12
    Get-Sha256BytesHex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

function New-CoordinatedMigrationReceipt {
    param([Parameter(Mandatory = $true)][object]$Evidence)
    $checkedAt = [DateTimeOffset]::UtcNow
    $core = [ordered]@{
        schema_version = "coordinated-storage-migration-receipt-v1"
        checked_at = $checkedAt.ToString("o")
        expires_at = $checkedAt.Add($coordinatedMigrationReceiptMaxAge).ToString("o")
        evidence = $Evidence
    }
    [pscustomobject]@{
        schema_version = $core.schema_version
        checked_at = $core.checked_at
        expires_at = $core.expires_at
        evidence = $core.evidence
        receipt_digest = Get-CoordinatedMigrationReceiptDigest -Core $core
    }
}

function Write-CoordinatedMigrationReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $directory = Split-Path -Parent $coordinatedMigrationReceiptPath
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$coordinatedMigrationReceiptPath.tmp"
    $Receipt | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $coordinatedMigrationReceiptPath -Force
}

function Assert-CoordinatedMigrationReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles
    )
    if (-not (Test-Path -LiteralPath $coordinatedMigrationReceiptPath)) {
        throw "MIGRATION_RECEIPT_MISSING"
    }
    $receipt = Get-Content -LiteralPath $coordinatedMigrationReceiptPath -Raw |
        ConvertFrom-Json
    $core = [ordered]@{
        schema_version = [string]$receipt.schema_version
        checked_at = [string]$receipt.checked_at
        expires_at = [string]$receipt.expires_at
        evidence = $receipt.evidence
    }
    if ([string]$receipt.schema_version -ne "coordinated-storage-migration-receipt-v1" -or
        [string]$receipt.receipt_digest -ne
            (Get-CoordinatedMigrationReceiptDigest -Core $core)) {
        throw "MIGRATION_RECEIPT_TAMPERED"
    }
    $expires = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$receipt.expires_at, [ref]$expires) -or
        $expires -le [DateTimeOffset]::UtcNow) {
        throw "MIGRATION_RECEIPT_STALE"
    }
    if ([string]$receipt.evidence.validation_key -ne [string]$Candidate.validation_key -or
        [string]$receipt.evidence.candidate_git_sha -ne [string]$Candidate.git_sha -or
        [string]$receipt.evidence.candidate_worker_version -ne
            [string]$Candidate.worker_version_id) {
        throw "MIGRATION_RECEIPT_CANDIDATE_MISMATCH"
    }
    $live = Get-CoordinatedMigrationLiveEvidence -Candidate $Candidate `
        -Stable $Stable -MigrationFiles $MigrationFiles
    $recordedDigest = Get-CoordinatedMigrationReceiptDigest -Core `
        ([ordered]@{ schema_version="migration-evidence-v1"; evidence=$receipt.evidence })
    $liveDigest = Get-CoordinatedMigrationReceiptDigest -Core `
        ([ordered]@{ schema_version="migration-evidence-v1"; evidence=$live })
    if ($recordedDigest -ne $liveDigest) {
        throw "MIGRATION_RECEIPT_LIVE_EVIDENCE_MISMATCH"
    }
    return $receipt
}

function Test-CoordinatedMigrationSyncHold {
    param([object]$ReleaseState)
    if (-not $ReleaseState -or -not $ReleaseState.candidate -or
        -not $ReleaseState.migration_sync_hold) {
        return $false
    }
    $candidate = $ReleaseState.candidate
    $hold = $ReleaseState.migration_sync_hold
    if ([string]$candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$candidate.branch -ne "main" -or
        [string]$hold.validation_key -ne [string]$candidate.validation_key) {
        return $false
    }
    $expiresAt = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse([string]$hold.expires_at, [ref]$expiresAt) -or
        $expiresAt -le [DateTimeOffset]::UtcNow) {
        return $false
    }
    return $true
}

function Enter-CoordinatedMigrationSyncHold {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][object]$Candidate
    )
    $enteredAt = [DateTimeOffset]::UtcNow
    $hold = [pscustomobject]@{
        validation_key = [string]$Candidate.validation_key
        reason = "COORDINATED_STORAGE_MIGRATION_VERIFICATION"
        entered_at = $enteredAt.ToString("o")
        expires_at = $enteredAt.AddHours(2).ToString("o")
    }
    if ($State.PSObject.Properties['migration_sync_hold']) {
        $State.migration_sync_hold = $hold
    } else {
        $State | Add-Member -NotePropertyName migration_sync_hold `
            -NotePropertyValue $hold
    }
    $State.updated_at = $enteredAt.ToString("o")
    Write-ReleaseControlState -State $State
    $syncService = $services | Where-Object Key -eq "sync" | Select-Object -First 1
    if ($syncService) { Stop-ForecasterService $syncService }
    Write-ReleaseHistory -Event "COORDINATED_STORAGE_MIGRATION_SYNC_HELD" `
        -Release $Candidate -Detail @{
            validation_key = [string]$Candidate.validation_key
            expires_at = [string]$hold.expires_at
        }
    return $hold
}

function Exit-CoordinatedMigrationSyncHold {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.migration_sync_hold) { return }
    $validationKey = [string]$state.migration_sync_hold.validation_key
    $state.migration_sync_hold = $null
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    if ($state.candidate) {
        Write-ReleaseHistory -Event "COORDINATED_STORAGE_MIGRATION_SYNC_RELEASED" `
            -Release $state.candidate -Detail @{ validation_key = $validationKey }
    }
}

function Verify-CandidateCoordinatedMigration {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate -or -not $state.stable) {
        throw "MIGRATION_CANDIDATE_UNAVAILABLE"
    }
    $candidate = $state.candidate
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        [string]$candidate.validation.reason -notin @(
            "COORDINATED_STORAGE_MIGRATION_REQUIRED",
            "COORDINATED_STORAGE_MIGRATION_EVIDENCE_INVALID"
        ) -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key) {
        throw "MIGRATION_EXACT_REVIEW_REQUIRED"
    }
    $approvalGate = Get-CandidateCompatibilityApprovalGate -Candidate $candidate
    if ([string]$approvalGate.state -ne "PASSED") {
        throw "MIGRATION_APPROVAL_REJECTED:$([string]$approvalGate.reason)"
    }
    $changed = @(Get-CandidateChangedFiles -StableRevision ([string]$state.stable.git_sha) `
        -CandidateRevision ([string]$candidate.git_sha))
    $files = @(Get-CoordinatedMigrationFiles -ChangedFiles $changed `
        -CandidateRevision ([string]$candidate.git_sha))
    $null = Enter-CoordinatedMigrationSyncHold -State $state -Candidate $candidate
    $evidence = Get-CoordinatedMigrationLiveEvidence -Candidate $candidate `
        -Stable $state.stable -MigrationFiles $files
    $receipt = New-CoordinatedMigrationReceipt -Evidence $evidence
    Write-CoordinatedMigrationReceipt -Receipt $receipt
    $verified = Assert-CoordinatedMigrationReceipt -Candidate $candidate `
        -Stable $state.stable -MigrationFiles $files
    $candidate.compatibility_state = "COORDINATED_STORAGE_MIGRATION_PASSED"
    $candidate.validation_state = "NEW"
    $candidate.validation = [pscustomobject]@{
        key = [string]$candidate.validation_key
        repository = "PASSED"; windows = "PASSED"; cloudflare = "PENDING"
        reason = "COORDINATED_STORAGE_MIGRATION_PASSED"
        migration_receipt_digest = [string]$verified.receipt_digest
        migration_database_id = [string]$verified.evidence.database_id
        migration_files = @($verified.evidence.migration_files)
        tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $candidate | Add-Member -NotePropertyName migration_acceptance `
        -NotePropertyValue ([pscustomobject]@{
            validation_key = [string]$candidate.validation_key
            receipt_digest = [string]$verified.receipt_digest
            database_id = [string]$verified.evidence.database_id
            checked_at = [string]$verified.checked_at
            expires_at = [string]$verified.expires_at
        }) -Force
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "COORDINATED_STORAGE_MIGRATION_PASSED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            receipt_digest = [string]$verified.receipt_digest
            database_id = [string]$verified.evidence.database_id
            migration_files = @($files)
        }
    return $candidate
}

function Invoke-WorkersObservabilityEventsQuery {
    param(
        [Parameter(Mandatory = $true)][object[]]$Filters,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [string]$Offset = ""
    )
    $secret = Get-ReleaseSecret -Name "CLOUDFLARE_RELEASE_OBSERVABILITY_TOKEN"
    $script:lastWorkersObservabilityCredentialSource = [string]$secret.source
    if (-not $secret.available) {
        $script:lastWorkersObservabilityDiagnostic = [string]$secret.diagnostic
        return $null
    }
    $token = [string]$secret.value
    $body = [ordered]@{
        queryId = "aurum-release-candidate-validation-events"
        timeframe = [ordered]@{
            from = $From.ToUnixTimeMilliseconds()
            to = $To.ToUnixTimeMilliseconds()
        }
        view = "events"
        limit = 2000
        parameters = [ordered]@{
            datasets = @()
            filterCombination = "and"
            filters = $Filters
            calculations = @()
        }
    }
    if ($Offset) { $body.offset = $Offset }
    $uri = "https://api.cloudflare.com/client/v4/accounts/$cloudflareAccountId/workers/observability/telemetry/query"
    try {
        $response = Invoke-RestMethod -Method Post -Uri $uri `
            -Headers @{ Authorization = "Bearer $token" } `
            -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 12) `
            -TimeoutSec 30
        if (-not $response.success -or -not $response.result.events) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_API_REJECTED"
            return $null
        }
        $script:lastWorkersObservabilityDiagnostic = $null
        return $response.result.events
    } catch {
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
        $script:lastWorkersObservabilityDiagnostic = if ($statusCode -in @(401, 403)) {
            "OBSERVABILITY_CREDENTIAL_REJECTED"
        } elseif ($statusCode -eq 429) {
            "OBSERVABILITY_RATE_LIMITED"
        } elseif ($statusCode -ge 500 -and $statusCode -le 599) {
            "OBSERVABILITY_TRANSIENT_API_FAILURE"
        } else { "OBSERVABILITY_QUERY_FAILED" }
        return $null
    } finally {
        $token = $null
        $secret = $null
    }
}

function Get-ReleaseTelemetryProperty {
    param([object]$Object, [Parameter(Mandatory = $true)][string]$Name)
    if ($null -eq $Object -or $null -eq $Object.PSObject.Properties[$Name]) {
        return $null
    }
    return $Object.PSObject.Properties[$Name].Value
}

function ConvertTo-ReleaseTelemetryRecord {
    param([Parameter(Mandatory = $true)][object]$Event)
    $metadata = Get-ReleaseTelemetryProperty -Object $Event -Name '$metadata'
    $workers = Get-ReleaseTelemetryProperty -Object $Event -Name '$workers'
    $workerEvent = Get-ReleaseTelemetryProperty -Object $workers -Name 'event'
    $request = Get-ReleaseTelemetryProperty -Object $workerEvent -Name 'request'
    $response = Get-ReleaseTelemetryProperty -Object $workerEvent -Name 'response'
    $headers = Get-ReleaseTelemetryProperty -Object $request -Name 'headers'
    $scriptVersion = Get-ReleaseTelemetryProperty -Object $workers -Name 'scriptVersion'
    $cpu = Get-ReleaseTelemetryProperty -Object $workers -Name 'cpuTimeMs'
    $wall = Get-ReleaseTelemetryProperty -Object $workers -Name 'wallTimeMs'
    $record = [pscustomobject]@{
        event_id = [string](Get-ReleaseTelemetryProperty -Object $metadata -Name 'id')
        event_type = [string](Get-ReleaseTelemetryProperty -Object $metadata -Name 'type')
        worker_version_id = [string](Get-ReleaseTelemetryProperty -Object $scriptVersion -Name 'id')
        request_id = [string](Get-ReleaseTelemetryProperty -Object $headers -Name 'x-aurum-request-id')
        validation_run = [string](Get-ReleaseTelemetryProperty -Object $headers -Name 'x-aurum-validation-run')
        validation_phase = [string](Get-ReleaseTelemetryProperty -Object $headers -Name 'x-aurum-validation-phase')
        method = [string](Get-ReleaseTelemetryProperty -Object $request -Name 'method')
        path = [string](Get-ReleaseTelemetryProperty -Object $workerEvent -Name 'path')
        status = [int](Get-ReleaseTelemetryProperty -Object $response -Name 'status')
        outcome = [string](Get-ReleaseTelemetryProperty -Object $workers -Name 'outcome')
        cpu_ms = if ($null -eq $cpu) { $null } else { [double]$cpu }
        wall_ms = if ($null -eq $wall) { $null } else { [double]$wall }
    }
    if (-not $record.event_id -or -not $record.request_id -or
        $null -eq $record.cpu_ms -or $null -eq $record.wall_ms) {
        throw "OBSERVABILITY_SCHEMA_INVALID"
    }
    return $record
}

function Get-ReleaseTelemetryDigest {
    param([Parameter(Mandatory = $true)][object[]]$Records)
    $lines = @($Records | Sort-Object event_id | ForEach-Object {
        @($_.event_id, $_.request_id, $_.worker_version_id, $_.validation_run,
            $_.validation_phase, $_.method, $_.path, $_.status, $_.outcome,
            $_.cpu_ms, $_.wall_ms) -join "|"
    })
    $bytes = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n"))
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $hasher.Dispose() }
}

function Get-ReleaseTelemetryPercentile {
    param([Parameter(Mandatory = $true)][double[]]$Values, [double]$Percentile)
    if ($Values.Count -eq 0) { return $null }
    $ordered = @($Values | Sort-Object)
    $index = [Math]::Max(0, [Math]::Ceiling($Percentile * $ordered.Count) - 1)
    return [double]$ordered[$index]
}

function Get-ReleaseTelemetryMetrics {
    param(
        [Parameter(Mandatory = $true)][object[]]$Records,
        [Parameter(Mandatory = $true)][string]$RouteFamily,
        [int]$ExpectedInvocations
    )
    $cpu = [double[]]@($Records | ForEach-Object { [double]$_.cpu_ms })
    $wall = [double[]]@($Records | ForEach-Object { [double]$_.wall_ms })
    $evidence = [pscustomobject]@{
        route_family = $RouteFamily
        invocations = $Records.Count
        max_cpu_ms = [double](($cpu | Measure-Object -Maximum).Maximum)
        p95_cpu_ms = Get-ReleaseTelemetryPercentile -Values $cpu -Percentile 0.95
        p99_cpu_ms = Get-ReleaseTelemetryPercentile -Values $cpu -Percentile 0.99
        max_wall_ms = [double](($wall | Measure-Object -Maximum).Maximum)
        exceeded_cpu = @($Records | Where-Object { $_.outcome -eq 'exceededCpu' }).Count
        exceeded_memory = @($Records | Where-Object { $_.outcome -eq 'exceededMemory' }).Count
        responses_1102 = @($Records | Where-Object {
            $_.outcome -in @('exceededCpu', 'exceededMemory')
        }).Count
        responses_5xx = @($Records | Where-Object { $_.status -ge 500 -and $_.status -le 599 }).Count
    }
    $gateState = Get-WorkerCpuGateState -Evidence $evidence -ExpectedInvocations $ExpectedInvocations
    $evidence | Add-Member gate_state $gateState
    $evidence | Add-Member passed ([bool]($gateState -eq 'PASSED'))
    return $evidence
}

function Get-CandidateFrozenPlatformEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [Parameter(Mandatory = $true)][object[]]$ExpectedRequests,
        [Parameter(Mandatory = $true)][string]$ValidationRun
    )
    $filters = @(Get-CandidateObservabilityFilters -Candidate $Candidate -ValidationRun $ValidationRun)
    $expectedIds = @($ExpectedRequests | ForEach-Object { [string]$_.request_id } | Sort-Object)
    if (@($expectedIds | Where-Object { -not $_ }).Count -gt 0 -or
        @($expectedIds | Select-Object -Unique).Count -ne $expectedIds.Count) {
        $script:lastWorkersObservabilityDiagnostic = 'EXPECTED_REQUEST_UNIVERSE_INVALID'
        return $null
    }
    $stableDigest = ''
    $stableEventIds = ''
    $stableRequestIds = ''
    $stableReads = 0
    $records = @()
    $frozenTo = $To
    $frozen = $false
    for ($attempt = 0; $attempt -lt 24; $attempt++) {
        if (-not $frozen) { $frozenTo = [DateTimeOffset]::UtcNow }
        $events = @()
        $offset = ''
        for ($pageNumber = 0; $pageNumber -lt 20; $pageNumber++) {
            $page = Invoke-WorkersObservabilityEventsQuery -Filters $filters `
                -From $From -To $frozenTo -Offset $offset
            if ($null -eq $page) { return $null }
            $pageEvents = @($page.events)
            $events += $pageEvents
            if ($pageEvents.Count -lt 2000) { break }
            $lastMetadata = Get-ReleaseTelemetryProperty -Object $pageEvents[-1] -Name '$metadata'
            $nextOffset = [string](Get-ReleaseTelemetryProperty -Object $lastMetadata -Name 'id')
            if (-not $nextOffset -or $nextOffset -eq $offset) {
                $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_EVENT_CURSOR_INVALID'
                return $null
            }
            $offset = $nextOffset
        }
        if ($events.Count -ge 40000) {
            $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_EVENT_PAGE_BOUND_EXCEEDED'
            return $null
        }
        try { $candidateRecords = @($events | ForEach-Object { ConvertTo-ReleaseTelemetryRecord $_ }) }
        catch {
            $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_SCHEMA_INVALID'
            return $null
        }
        $actualIds = @($candidateRecords | ForEach-Object { $_.request_id } | Sort-Object)
        $eventIds = @($candidateRecords | ForEach-Object { $_.event_id })
        $identityValid = @($candidateRecords | Where-Object {
            $_.worker_version_id -ne [string]$Candidate.worker_version_id -or
            $_.validation_run -ne $ValidationRun -or $_.validation_phase -ne 'acceptance' -or
            $_.event_type -ne 'cf-worker-event'
        }).Count -eq 0
        $complete = $identityValid -and $actualIds.Count -eq $expectedIds.Count -and
            @($actualIds | Select-Object -Unique).Count -eq $actualIds.Count -and
            @($eventIds | Select-Object -Unique).Count -eq $eventIds.Count -and
            (($actualIds -join "`n") -ceq ($expectedIds -join "`n"))
        if ($complete) {
            $frozen = $true
            $digest = Get-ReleaseTelemetryDigest -Records $candidateRecords
            $eventIdSet = @($eventIds | Sort-Object) -join "`n"
            $requestIdSet = $actualIds -join "`n"
            if ($digest -eq $stableDigest -and $eventIdSet -ceq $stableEventIds -and
                $requestIdSet -ceq $stableRequestIds) {
                $stableReads++
            } else {
                $stableDigest = $digest
                $stableEventIds = $eventIdSet
                $stableRequestIds = $requestIdSet
                $stableReads = 1
            }
            if ($stableReads -ge 2) { $records = $candidateRecords; break }
        } else {
            $stableDigest = ''
            $stableEventIds = ''
            $stableRequestIds = ''
            $stableReads = 0
        }
        if ($attempt -lt 23) { Start-Sleep -Seconds 10 }
    }
    if ($records.Count -ne $expectedIds.Count) {
        $script:lastWorkersObservabilityDiagnostic = 'OBSERVABILITY_TELEMETRY_PROPAGATION_PENDING'
        return $null
    }
    $expectedById = @{}
    foreach ($expected in $ExpectedRequests) { $expectedById[[string]$expected.request_id] = $expected }
    $families = @()
    foreach ($group in @($records | Group-Object { [string]$expectedById[$_.request_id].family })) {
        $familyExpected = @($ExpectedRequests | Where-Object { [string]$_.family -eq $group.Name }).Count
        $families += Get-ReleaseTelemetryMetrics -Records @($group.Group) `
            -RouteFamily $group.Name -ExpectedInvocations $familyExpected
    }
    $familyReconciliation = @($ExpectedRequests | Group-Object { [string]$_.family } |
        ForEach-Object {
            $name = [string]$_.Name
            $expectedCount = $_.Count
            $actualCount = @($records | Where-Object {
                [string]$expectedById[$_.request_id].family -eq $name
            }).Count
            [pscustomobject]@{
                family = $name; expected = $expectedCount; actual = $actualCount
                matched = [bool]($expectedCount -eq $actualCount)
            }
        })
    $scenarioReconciliation = @($ExpectedRequests | Group-Object {
            "{0}|{1}" -f [string]$_.family, [string]$_.scenario
        } | ForEach-Object {
            $family = [string]$_.Group[0].family
            $scenario = [string]$_.Group[0].scenario
            $expectedCount = $_.Count
            $actualCount = @($records | Where-Object {
                $row = $expectedById[$_.request_id]
                [string]$row.family -eq $family -and [string]$row.scenario -eq $scenario
            }).Count
            [pscustomobject]@{
                family = $family; scenario = $scenario
                expected = $expectedCount; actual = $actualCount
                matched = [bool]($expectedCount -eq $actualCount)
            }
        })
    $global = Get-ReleaseTelemetryMetrics -Records $records -RouteFamily 'GLOBAL' `
        -ExpectedInvocations $expectedIds.Count
    $failed = @($families | Where-Object { $_.gate_state -eq 'FAILED' }).Count -gt 0
    $review = @($families | Where-Object { $_.gate_state -eq 'REVIEW_REQUIRED' }).Count -gt 0
    $gateState = if ($failed -or $global.gate_state -eq 'FAILED') { 'FAILED' }
        elseif ($review -or $global.gate_state -eq 'REVIEW_REQUIRED') { 'REVIEW_REQUIRED' }
        else { 'PASSED' }
    return [pscustomobject]@{
        source = 'CLOUDFLARE_WORKERS_OBSERVABILITY_RAW_EVENTS'
        credential_source = [string]$script:lastWorkersObservabilityCredentialSource
        worker_version_id = [string]$Candidate.worker_version_id
        validation_run = $ValidationRun
        frozen_from = $From.ToString('o')
        frozen_to = $frozenTo.ToString('o')
        universe_digest = $stableDigest
        stable_reads = $stableReads
        expected_invocations = $expectedIds.Count
        invocations = $records.Count
        expected_requests = @($ExpectedRequests)
        request_reconciliation = [pscustomobject]@{ expected=$expectedIds.Count; actual=$records.Count; matched=$true }
        family_reconciliation = $familyReconciliation
        scenario_reconciliation = $scenarioReconciliation
        global = $global
        routes = $families
        max_cpu_ms = $global.max_cpu_ms
        p95_cpu_ms = $global.p95_cpu_ms
        p99_cpu_ms = $global.p99_cpu_ms
        max_wall_ms = $global.max_wall_ms
        exceeded_cpu = $global.exceeded_cpu
        exceeded_memory = $global.exceeded_memory
        responses_1102 = $global.responses_1102
        responses_5xx = $global.responses_5xx
        gate_state = $gateState
        passed = [bool]($gateState -eq 'PASSED')
    }
}

function Test-BroadcastServiceReadiness {
    param([Parameter(Mandatory = $true)][string]$CandidateRevision)
    $compatibleRevision = [string]$env:AURUM_LIVE_BROADCAST_COMPATIBLE_REVISION
    $token = Get-BroadcastPublisherToken
    if ([string]::IsNullOrWhiteSpace($token)) {
        return [pscustomobject]@{
            state = "BROADCAST_PENDING"; passed = $false; retryable = $true
            reason = "BROADCAST_PUBLISH_TOKEN_NOT_CONFIGURED"
            authority = $broadcastHealthUrl
        }
    }
    try {
        $health = Invoke-RestMethod -Method Get -Uri $broadcastHealthUrl -TimeoutSec 15
        if ([string]$health.service -ne "aurum-live-broadcast") {
            return [pscustomobject]@{
                state = "FAILED"; passed = $false; retryable = $false
                reason = "BROADCAST_AUTHORITY_MISMATCH"
                authority = $broadcastHealthUrl
            }
        }
        if ([string]$health.schema_version -ne "PUBLIC_LIVE_V1") {
            return [pscustomobject]@{
                state = "FAILED"; passed = $false; retryable = $false
                reason = "BROADCAST_SCHEMA_INCOMPATIBLE"
                authority = $broadcastHealthUrl
                schema_version = [string]$health.schema_version
            }
        }
        $revisionAccepted = (
            [string]$health.code_revision -eq $CandidateRevision -or
            (-not [string]::IsNullOrWhiteSpace($compatibleRevision) -and
                [string]$health.code_revision -eq $compatibleRevision)
        )
        if (-not [bool]$health.binding_ready -or -not $revisionAccepted) {
            return [pscustomobject]@{
                state = "BROADCAST_BLOCKED"; passed = $false; retryable = $true
                reason = "BROADCAST_PLATFORM_NOT_READY"
                authority = $broadcastHealthUrl
                code_revision = [string]$health.code_revision
                binding_ready = [bool]$health.binding_ready
                revision_accepted = $revisionAccepted
            }
        }
        $dryRunState = [ordered]@{
            schema_version = "PUBLIC_LIVE_V1"; sequence = 1
            generated_at = [DateTimeOffset]::UtcNow.ToString("o")
            source_revision = $CandidateRevision; market_session = "DATA_UNAVAILABLE"
            freshness = @{ online = $false; state = "STALE" }
            quote = @{
                bid = 0.0; ask = 0.0; spread = 0.0
                source_received_time = [DateTimeOffset]::UtcNow.ToString("o")
            }
            forecast = @{
                model_identity = $null; model_version = $null
                recommended_action = "WAIT"; prediction_status = "DRY_RUN"
                ev_long_u5 = $null; ev_short_u5 = $null; interval_width = $null
                decision_time = $null; signal_expiry_seconds = 20
                forecast_horizon_seconds = 1800; directional_bias = "NEUTRAL"
                frozen_record = $false
            }
            health = @{ status = "DRY_RUN"; alerts = @() }
        }
        $headers = @{ Authorization = "Bearer $token" }
        $dryRun = Invoke-RestMethod -Method Post -Uri $broadcastPublishDryRunUrl `
            -Headers $headers -ContentType "application/json" `
            -Body ($dryRunState | ConvertTo-Json -Depth 8 -Compress) -TimeoutSec 15
        $passed = [bool]$dryRun.valid -and [bool]$dryRun.dry_run -and
            [string]$dryRun.schema_version -eq "PUBLIC_LIVE_V1"
        return [pscustomobject]@{
            state = if ($passed) { "PASSED" } else { "BROADCAST_BLOCKED" }
            passed = $passed
            retryable = -not $passed
            reason = if ($passed) { "PASSED" } else { "BROADCAST_DRY_RUN_REJECTED" }
            authority = $broadcastHealthUrl
            schema_version = [string]$health.schema_version
            code_revision = [string]$health.code_revision
            binding_ready = [bool]$health.binding_ready
            revision_accepted = $revisionAccepted
            dry_run_valid = $passed
            dry_run_storage_mutation = $false
            dry_run_broadcast = $false
        }
    } catch {
        return [pscustomobject]@{
            state = "BROADCAST_BLOCKED"; passed = $false; retryable = $true
            reason = "BROADCAST_HEALTH_PROBE_FAILED"
            authority = $broadcastHealthUrl
            error = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Test-BroadcastLiveDeliveryReadiness {
    param([Parameter(Mandatory = $true)][string]$ExpectedRevision)
    try {
        $health = Invoke-RestMethod -Method Get -Uri $broadcastHealthUrl -TimeoutSec 15
        $publishedAt = [DateTimeOffset]::MinValue
        $publishedValid = [DateTimeOffset]::TryParse(
            [string]$health.latest_published_at, [ref]$publishedAt
        )
        $age = if ($publishedValid) {
            [DateTimeOffset]::UtcNow - $publishedAt
        } else { [TimeSpan]::MaxValue }
        $publisherService = $services | Where-Object Key -eq "broadcast" | Select-Object -First 1
        $processes = if ($publisherService) { @(Get-ForecasterProcesses $publisherService) } else { @() }
        $publisherState = if ($publisherService) {
            Get-ServiceState -Service $publisherService -Processes $processes
        } else { "NOT_CONFIGURED" }
        $passed = (
            [string]$health.service -eq "aurum-live-broadcast" -and
            [string]$health.schema_version -eq "PUBLIC_LIVE_V1" -and
            [bool]$health.binding_ready -and [bool]$health.latest_available -and
            [string]$health.latest_source_revision -eq $ExpectedRevision -and
            $publishedValid -and $age -ge [TimeSpan]::Zero -and
            $age -le $broadcastFreshnessThreshold -and
            [string]$publisherState -eq "RUNNING"
        )
        return [pscustomobject]@{
            state = if ($passed) { "PASSED" } else { "BROADCAST_LIVE_BLOCKED" }
            passed = $passed; reason = if ($passed) { "PASSED" } else { "BROADCAST_LIVE_NOT_READY" }
            latest_sequence = $health.latest_sequence
            latest_generated_at = [string]$health.latest_generated_at
            latest_published_at = [string]$health.latest_published_at
            latest_source_revision = [string]$health.latest_source_revision
            freshness_threshold_seconds = [int]$broadcastFreshnessThreshold.TotalSeconds
            publisher_state = [string]$publisherState
        }
    } catch {
        return [pscustomobject]@{
            state = "BROADCAST_LIVE_BLOCKED"; passed = $false
            reason = "BROADCAST_LIVE_PROBE_FAILED"
            error = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}
