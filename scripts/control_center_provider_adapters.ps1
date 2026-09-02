# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
function Invoke-WranglerJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $webRoot = Join-Path $repositoryRoot "web"
    $wranglerCli = Join-Path $webRoot "node_modules\wrangler\bin\wrangler.js"
    if (-not (Test-Path -LiteralPath $wranglerCli)) {
        throw "Wrangler CLI is unavailable."
    }
    $argumentLength = $wranglerCli.Length + 1
    foreach ($argument in $Arguments) {
        $argumentLength += ([string]$argument).Length + 1
    }
    if ($argumentLength -gt 24000) {
        throw "WRANGLER_ARGUMENT_BOUND_EXCEEDED"
    }
    # Invoke the pinned CLI through Node directly. npx.cmd truncates or rejects
    # otherwise-valid bounded arguments at cmd.exe's lower limit. The release
    # boundary owns strict UTF-8 decoding rather than inheriting a shell codepage.
    $priorAccountScope = [Environment]::GetEnvironmentVariable(
        "CLOUDFLARE_ACCOUNT_ID", "Process"
    )
    try {
        [Environment]::SetEnvironmentVariable(
            "CLOUDFLARE_ACCOUNT_ID", $cloudflareAccountId, "Process"
        )
        $read = Invoke-Utf8NativeProcess -FilePath "node.exe" `
            -Arguments (@($wranglerCli) + @($Arguments) + @("--json")) `
            -WorkingDirectory $webRoot `
            -TimeoutMilliseconds $releaseProviderCommandTimeoutMilliseconds
    } finally {
        [Environment]::SetEnvironmentVariable(
            "CLOUDFLARE_ACCOUNT_ID", $priorAccountScope, "Process"
        )
    }
    if ($read.exit_code -ne 0) {
        $diagnostic = Protect-PreflightDiagnosticText (
            @([string]$read.stdout, [string]$read.stderr) -join "`n"
        )
        $classification = if ($diagnostic -cmatch '(^|[^A-Z0-9_])VERSION_NOT_FOUND([^A-Z0-9_]|$)') {
            "CLOUDFLARE_VERSION_NOT_FOUND"
        } elseif ($diagnostic -match '(?i)\bHTTP(?:_STATUS| STATUS| STATUS CODE)?[ :=]+404\b') {
            "CLOUDFLARE_HTTP_404"
        } elseif ($diagnostic -match '(?i)\bHTTP(?:_STATUS| STATUS| STATUS CODE)?[ :=]+401\b') {
            "CLOUDFLARE_HTTP_401"
        } elseif ($diagnostic -match '(?i)\bHTTP(?:_STATUS| STATUS| STATUS CODE)?[ :=]+403\b') {
            "CLOUDFLARE_HTTP_403"
        } elseif ($diagnostic -match '(?i)(\bHTTP(?:_STATUS| STATUS| STATUS CODE)?[ :=]+429\b|rate limit)') {
            "CLOUDFLARE_RATE_LIMITED"
        } else { "CLOUDFLARE_WRANGLER_COMMAND_FAILED" }
        throw $classification
    }
    $read.stdout | ConvertFrom-ReleaseControlJson
}

function Get-CloudflareDeployment {
    Invoke-WranglerJson -Arguments @("deployments", "status", "--name", $workerName)
}

function Get-CloudflareVersions {
    $versions = Invoke-WranglerJson -Arguments @(
        "versions", "list", "--name", $workerName
    )
    # JSON parsing may return its top-level array as one pipeline
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
        [Parameter(Mandatory = $true)][ValidateSet("PENDING", "MATERIALIZED", "PRESERVED")]
        [string]$Status,
        [string]$WorkerVersionId = ""
    )
    $receipt = [pscustomobject]@{
        revision = $Revision
        state = $Status
        reason = if ($Status -eq "MATERIALIZED") {
            "EXACT_MAIN_CANDIDATE_MATERIALIZED"
        } elseif ($Status -eq "PRESERVED") {
            "CONTROL_PLANE_ONLY_MAIN_ADVANCE_PRESERVED"
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
        Write-Output ($Value.ToUniversalTime().ToString(
            "o", [Globalization.CultureInfo]::InvariantCulture
        ))
        return
    }
    if ($Value -is [DateTime]) {
        Write-Output ($Value.ToUniversalTime().ToString(
            "o", [Globalization.CultureInfo]::InvariantCulture
        ))
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

function ConvertTo-ReleaseTimestampUtc {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return [DateTimeOffset]::MinValue }
    if ($Value -is [DateTimeOffset]) { return $Value.ToUniversalTime() }
    if ($Value -is [DateTime]) {
        return ([DateTimeOffset]$Value.ToUniversalTime()).ToUniversalTime()
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) { return [DateTimeOffset]::MinValue }
    $parsed = [DateTimeOffset]::MinValue
    $styles = [Globalization.DateTimeStyles]::AllowWhiteSpaces -bor
        [Globalization.DateTimeStyles]::AssumeUniversal
    if ([DateTimeOffset]::TryParse(
        $text, [Globalization.CultureInfo]::InvariantCulture,
        $styles, [ref]$parsed
    )) { return $parsed.ToUniversalTime() }
    if ([DateTimeOffset]::TryParse($text, [ref]$parsed)) {
        return $parsed.ToUniversalTime()
    }
    return [DateTimeOffset]::MinValue
}

function Test-ControlPlaneStartTokenEqual {
    param([AllowNull()][object]$Left, [AllowNull()][object]$Right)
    $leftInstant = ConvertTo-ReleaseTimestampUtc -Value $Left
    $rightInstant = ConvertTo-ReleaseTimestampUtc -Value $Right
    return [bool](
        $leftInstant -ne [DateTimeOffset]::MinValue -and
        $rightInstant -ne [DateTimeOffset]::MinValue -and
        $leftInstant.UtcTicks -eq $rightInstant.UtcTicks
    )
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
        $utc = ConvertTo-ReleaseTimestampUtc -Value $candidate
        if ($utc -gt $newest) { $newest = $utc }
    }
    return $newest
}

function Get-ReleaseVersionCreatedAt {
    param([Parameter(Mandatory = $true)][object]$Version)
    $created = Get-ReleaseVersionCreatedAtValue -Version $Version
    if ($created -eq [DateTimeOffset]::MinValue) { return "" }
    return $created.ToUniversalTime().ToString(
        "o", [Globalization.CultureInfo]::InvariantCulture
    )
}

function Test-VersionAfterDiscoveryWatermark {
    param(
        [Parameter(Mandatory = $true)][object]$Version,
        [Parameter(Mandatory = $true)][object]$Discovery
    )
    if (-not $Discovery.watermark_created_at) { return $true }
    $createdAt = Get-ReleaseVersionCreatedAt -Version $Version
    if (-not $createdAt) { return $false }
    $created = ConvertTo-ReleaseTimestampUtc -Value $createdAt
    $watermark = ConvertTo-ReleaseTimestampUtc `
        -Value $Discovery.watermark_created_at
    if ($created -eq [DateTimeOffset]::MinValue -or
        $watermark -eq [DateTimeOffset]::MinValue) { return $false }
    if ($created -gt $watermark) { return $true }
    if ($created -lt $watermark) { return $false }
    return [string]$Version.id -gt [string]$Discovery.watermark_version_id
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
    $native = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments $Arguments
    $exitCode = [int]$native.exit_code
    $lines = @($native.stdout_lines)
    if ($exitCode -ne 0 -and $native.stderr) {
        $lines += @($native.stderr_lines)
    }
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

function Get-RequiredGitHubChecksResult {
    param([Parameter(Mandatory = $true)][string]$Revision)
    try {
        $read = Invoke-Utf8NativeProcess -FilePath "gh.exe" -Arguments @(
            "api", "--method", "GET",
            "repos/yiyousiow000814/XAUUSD-Forecaster/commits/$Revision/check-runs?filter=latest&per_page=100"
        )
        $exitCode = [int]$read.exit_code
        $json = if ($exitCode -eq 0) { [string]$read.stdout } else {
            ((@($read.stdout_lines) + @($read.stderr_lines)) -join "`n")
        }
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
        $runs = @(($json | ConvertFrom-ReleaseControlJson).check_runs)
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
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [string]$VerifiedOriginMainRevision = ""
    )
    if ([string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        [string]$Candidate.branch -ne "main" -or
        [string]$Candidate.git_sha -ne [string]$Candidate.windows_revision) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_MAIN_PROVENANCE_REQUIRED"
        }
    }
    $originMain = $VerifiedOriginMainRevision.Trim().ToLowerInvariant()
    if ($originMain) {
        if ($originMain -notmatch '^[0-9a-f]{40}$') {
            return [pscustomobject]@{
                state = "FAILED"; reason = "PRODUCTION_CANDIDATE_EXACT_MAIN_REQUIRED"
            }
        }
    } else {
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
    }
    $commit = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "cat-file", "-e", "$([string]$Candidate.git_sha)^{commit}"
    )
    if ($commit.exit_code -ne 0) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_COMMIT_REQUIRED"
        }
    }
    if (-not $originMain) {
        $mainRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $repositoryRoot, "rev-parse", "origin/main")
        $originMain = ([string]$mainRead.stdout).Trim().ToLowerInvariant()
        if ($mainRead.exit_code -ne 0 -or $originMain -notmatch '^[0-9a-f]{40}$') {
            return [pscustomobject]@{
                state = "FAILED"; reason = "PRODUCTION_CANDIDATE_EXACT_MAIN_REQUIRED"
            }
        }
    }
    if ($originMain -eq [string]$Candidate.git_sha) {
        return [pscustomobject]@{ state = "PASSED"; reason = $null; mode = "EXACT_MAIN" }
    }
    $ancestor = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "merge-base", "--is-ancestor",
        [string]$Candidate.git_sha, $originMain
    )
    $diff = if ([int]$ancestor.exit_code -eq 0) {
        Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
            "-C", $repositoryRoot, "diff", "--name-only",
            [string]$Candidate.git_sha, $originMain
        )
    } else { $null }
    $changed = if ($diff -and [int]$diff.exit_code -eq 0) {
        @($diff.stdout_lines | ForEach-Object { ([string]$_).Trim().Replace('\', '/') } |
            Where-Object { $_ })
    } else { @() }
    $allowed = @($changed | Where-Object {
        $_ -eq "AGENTS.md" -or
        $_ -like ".agents/*" -or $_ -like ".github/*" -or
        $_ -like "docs/*" -or $_ -like "formal/*" -or $_ -like "tests/*" -or
        $_ -in @(
            "scripts/access-qualification-contract.json",
            "scripts/check_deferred_projection_parity.py",
            "scripts/runtime-control-files.json",
            "scripts/xauusd_control_center.ps1"
        )
    })
    if ([int]$ancestor.exit_code -ne 0 -or -not $diff -or
        [int]$diff.exit_code -ne 0 -or $changed.Count -eq 0 -or
        $allowed.Count -ne $changed.Count) {
        return [pscustomobject]@{
            state = "FAILED"; reason = "PRODUCTION_CANDIDATE_EXACT_MAIN_REQUIRED"
        }
    }
    return [pscustomobject]@{
        state = "PASSED"
        reason = $null
        mode = "CONTROL_PLANE_ONLY_MAIN_ADVANCE"
        candidate_git_sha = [string]$Candidate.git_sha
        current_main_git_sha = $originMain
        changed_control_artifacts = $changed
    }
}

function Test-ProductionCandidateProvenance {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $script:lastRepositoryValidationResult =
        Get-ProductionCandidateProvenanceResult -Candidate $Candidate
    return [bool]($script:lastRepositoryValidationResult.state -eq "PASSED")
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
        if (-not $response.success -or -not $response.result -or
            $null -eq $response.result.PSObject.Properties['calculations']) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_API_REJECTED"
            return $null
        }
        $aggregates = @()
        foreach ($calculation in @($response.result.calculations)) {
            if ([string]::IsNullOrWhiteSpace([string]$calculation.alias) -or
                @($calculation.aggregates).Count -ne 1 -or
                $null -eq $calculation.aggregates[0].value) {
                $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_SCHEMA_INVALID"
                return $null
            }
            $aggregates += [pscustomobject]@{
                alias = [string]$calculation.alias
                value = $calculation.aggregates[0].value
            }
        }
        $script:lastWorkersObservabilityDiagnostic = $null
        return [pscustomobject]@{ aggregates = $aggregates }
    } catch {
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
        $script:lastWorkersObservabilityDiagnostic = if ($statusCode -in @(401, 403)) {
            "OBSERVABILITY_CREDENTIAL_REJECTED"
        } elseif ($statusCode -eq 429) {
            "OBSERVABILITY_RATE_LIMITED"
        } elseif ($statusCode -in @(0, 408)) {
            "OBSERVABILITY_TRANSIENT_API_FAILURE"
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
    $calculation = @($QueryResult.aggregates | Where-Object {
        [string]$_.alias -eq $Alias
    }) | Select-Object -First 1
    if (-not $calculation) { return $null }
    return $calculation.value
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
    $quotaPolicyEvidence = [bool]$Evidence.PSObject.Properties['qualification_state']
    if (-not $quotaPolicyEvidence -and
        [int]$Evidence.invocations -ne [int]$Evidence.expected_invocations) {
        return "WORKER_INVOCATION_COUNT_MISMATCH"
    }
    if ([int]$Evidence.responses_5xx -gt 0) { return "WORKER_5XX_OBSERVED" }
    if ([int]$Evidence.exceeded_cpu -gt 0 -or
        [int]$Evidence.exceeded_memory -gt 0 -or
        [int]$Evidence.responses_1102 -gt 0) {
        return "WORKER_PLATFORM_LIMIT_EXCEEDED"
    }
    if ($quotaPolicyEvidence -and
        [string]$Evidence.qualification_state -eq "CPU_OUTLIER_REVIEW_REQUIRED") {
        return "CPU_OUTLIER_REVIEW_REQUIRED"
    }
    $isolatedOutlierQualified = [bool]($quotaPolicyEvidence -and
        [string]$Evidence.qualification_state -eq
            "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER")
    $qualificationP99 = if ($isolatedOutlierQualified -and
        $Evidence.qualification_global) {
        [double]$Evidence.qualification_global.p99_cpu_ms
    } else { [double]$Evidence.p99_cpu_ms }
    $qualificationMax = if ($isolatedOutlierQualified -and
        $Evidence.qualification_global) {
        [double]$Evidence.qualification_global.max_cpu_ms
    } else { [double]$Evidence.max_cpu_ms }
    if ($qualificationP99 -gt $workerCpuPassMaxMs -or
        $qualificationMax -gt $workerCpuPassMaxMs -or
        ($quotaPolicyEvidence -and $qualificationMax -ge 10)) {
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
        $envelope = if ($response.result) { $response.result.events } else { $null }
        $properties = if ($envelope) { @($envelope.PSObject.Properties.Name) } else { @() }
        if (-not $response.success -or -not $envelope -or
            @(@('events', 'fields', 'count', 'series') |
                Where-Object { $_ -notin $properties }).Count -gt 0) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_API_REJECTED"
            return $null
        }
        $providerEvents = @($envelope.events)
        $totalCount = 0
        if ($null -eq $envelope.count -or
            -not [int]::TryParse([string]$envelope.count, [ref]$totalCount) -or
            $totalCount -lt 0 -or $totalCount -lt $providerEvents.Count) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_SCHEMA_INVALID"
            return $null
        }
        try {
            $records = @($providerEvents | ForEach-Object {
                ConvertTo-ReleaseTelemetryRecord -Event $_
            })
        } catch {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_SCHEMA_INVALID"
            return $null
        }
        $nextOffset = ""
        if ($providerEvents.Count -gt 0 -and $totalCount -gt $providerEvents.Count) {
            $lastMetadata = Get-ReleaseTelemetryProperty `
                -Object $providerEvents[-1] -Name '$metadata'
            $nextOffset = [string](Get-ReleaseTelemetryProperty `
                -Object $lastMetadata -Name 'id')
            if (-not $nextOffset -or $nextOffset -eq $Offset) {
                $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_EVENT_CURSOR_INVALID"
                return $null
            }
        }
        $script:lastWorkersObservabilityDiagnostic = $null
        return [pscustomobject]@{
            records = $records
            total_count = $totalCount
            page_count = $records.Count
            next_offset = $nextOffset
        }
    } catch {
        $statusCode = 0
        try { $statusCode = [int]$_.Exception.Response.StatusCode } catch { $statusCode = 0 }
        $script:lastWorkersObservabilityDiagnostic = if ($statusCode -in @(401, 403)) {
            "OBSERVABILITY_CREDENTIAL_REJECTED"
        } elseif ($statusCode -eq 429) {
            "OBSERVABILITY_RATE_LIMITED"
        } elseif ($statusCode -in @(0, 408)) {
            "OBSERVABILITY_TRANSIENT_API_FAILURE"
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
    param([Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Records)
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

function Get-WorkersObservabilityEventPageSet {
    param(
        [Parameter(Mandatory = $true)][object[]]$Filters,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To
    )
    $events = @()
    $offset = ""
    $page = $null
    for ($pageNumber = 0; $pageNumber -lt 20; $pageNumber++) {
        $page = Invoke-WorkersObservabilityEventsQuery -Filters $Filters `
            -From $From -To $To -Offset $offset
        if ($null -eq $page) { return $null }
        $events += @($page.records)
        if ($events.Count -ge [int]$page.total_count) { $offset = ""; break }
        if (-not $page.next_offset) {
            $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_EVENT_CURSOR_INVALID"
            return $null
        }
        $offset = [string]$page.next_offset
    }
    if ($offset) {
        $script:lastWorkersObservabilityDiagnostic = "OBSERVABILITY_EVENT_PAGE_BOUND_EXCEEDED"
        return $null
    }
    return [pscustomobject]@{ records=@($events); total_count=[int]$page.total_count }
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
    $expectedIds = @($ExpectedRequests | Where-Object { [string]$_.phase -eq "acceptance" } |
        ForEach-Object { [string]$_.request_id } | Sort-Object)
    if ($expectedIds.Count -eq 0 -or @($expectedIds | Where-Object { -not $_ }).Count -gt 0 -or
        @($expectedIds | Select-Object -Unique).Count -ne $expectedIds.Count) {
        $script:lastWorkersObservabilityDiagnostic = "EXPECTED_REQUEST_UNIVERSE_INVALID"
        return $null
    }
    $stored = Read-WorkerCpuRunArtifact -ValidationRun $ValidationRun -Name "provider-evidence.json"
    $records = if ($stored) { @($stored.records) } else { @() }
    $recovery = if ($stored -and $stored.recovery) { $stored.recovery } else {
        [pscustomobject]@{
            active_reads=0; background_reads=0; deficit_top_ups=0; headroom_top_ups=0
            outlier_confirmations=0
            deficit_repair_preflight_reads=0; deficit_repair_preflight_last_at=""
        }
    }
    $policy = Get-WorkerCpuEvidencePolicy
    $decision = $null
    $aggregate = $null
    $delays = @()
    $activeBackoff = @($policy.active_read_backoff_seconds)
    if ([int]$recovery.active_reads -lt $activeBackoff.Count) {
        $firstActiveRead = [int]$recovery.active_reads
        $delays = @($activeBackoff[$firstActiveRead..($activeBackoff.Count - 1)])
    } else {
        $lastRead = ConvertTo-ReleaseTimestampUtc -Value ([string]$recovery.last_read_at)
        $backgroundDue = $lastRead -eq [DateTimeOffset]::MinValue -or
            ([DateTimeOffset]::UtcNow - $lastRead).TotalSeconds -ge
                [int]$policy.background_read_interval_seconds
        if ([int]$recovery.background_reads -lt [int]$policy.maximum_background_reads -and $backgroundDue) {
            $delays = @(0)
        }
    }
    foreach ($delay in $delays) {
        if ([int]$delay -gt 0) { Start-Sleep -Seconds ([int]$delay) }
        $pageSet = Get-WorkersObservabilityEventPageSet -Filters $filters -From $From -To $To
        $providerReadSucceeded = $null -ne $pageSet
        if ($providerReadSucceeded) {
            try {
                $records = @(Merge-WorkerCpuProviderEvidence -AcceptedRecords $records `
                    -NewRecords @($pageSet.records) `
                    -ExpectedRequests $ExpectedRequests -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                    -ValidationRun $ValidationRun)
            } catch {
                $script:lastWorkersObservabilityDiagnostic = [string]$_.Exception.Message
                throw
            }
        }
        if ([int]$recovery.active_reads -lt $activeBackoff.Count) {
            $recovery.active_reads = [int]$recovery.active_reads + 1
        } else {
            $recovery.background_reads = [int]$recovery.background_reads + 1
        }
        $recovery | Add-Member -NotePropertyName last_read_at `
            -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
        $stored = Write-WorkerCpuProviderEvidence -ValidationRun $ValidationRun `
            -Records @($records) -RecoveryState $recovery `
            -ProviderReadSucceeded $providerReadSucceeded
        if ($providerReadSucceeded) {
            $count = Get-CandidateInvocationCount -Candidate $Candidate -From $From -To $To -ValidationRun $ValidationRun
            if ($null -ne $count) {
                $aggregate = [pscustomobject]@{
                    source="CLOUDFLARE_WORKERS_OBSERVABILITY_CALCULATIONS"
                    evidence_class="EXTERNAL_ADVISORY"
                    invocations=[int]$count
                }
            }
        }
        $decision = Get-WorkerCpuQualificationDecision -ExpectedRequests $ExpectedRequests `
            -ProviderRecords $records -DirectResponsesComplete $true -AggregateEvidence $aggregate
        if ([string]$decision.state -notin @("PROVIDER_EVIDENCE_PENDING")) { break }
    }
    if (-not $decision -or [string]$decision.state -eq "PROVIDER_EVIDENCE_PENDING") {
        $decision = Get-WorkerCpuQualificationDecision -ExpectedRequests $ExpectedRequests `
            -ProviderRecords $records -DirectResponsesComplete $true `
            -RecoveryBudgetExhausted ([bool]([int]$recovery.background_reads -ge
                [int]$policy.maximum_background_reads))
    }
    if ([string]$decision.state -in @("PROVIDER_EVIDENCE_PENDING", "PROVIDER_EVIDENCE_INSUFFICIENT")) {
        $script:lastWorkersObservabilityDiagnostic = [string]$decision.state
        return $null
    }
    $global = $decision.global
    $gateState = switch ([string]$decision.state) {
        "QUALIFIED" { "PASSED" }
        "QUALIFIED_WITH_PROVIDER_OMISSION" { "PASSED" }
        "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER" { "PASSED" }
        "CPU_OUTLIER_REVIEW_REQUIRED" { "REVIEW_REQUIRED" }
        "HEADROOM_REVIEW" { "REVIEW_REQUIRED" }
        default { "FAILED" }
    }
    return [pscustomobject]@{
        source = "CLOUDFLARE_WORKERS_OBSERVABILITY_MONOTONIC_EVENTS"
        evidence_class = "EXTERNAL_AUTHORITATIVE_EVENTUAL"
        qualification_state = [string]$decision.state
        credential_source = [string]$script:lastWorkersObservabilityCredentialSource
        worker_version_id = [string]$Candidate.worker_version_id
        validation_run = $ValidationRun
        frozen_from = $From.ToString("o")
        frozen_to = $To.ToString("o")
        universe_digest = Get-ReleaseTelemetryDigest -Records $records
        expected_invocations = $expectedIds.Count
        invocations = $records.Count
        expected_requests = @($ExpectedRequests)
        missing_request_ids = @($decision.missing_request_ids)
        family_reconciliation = @($decision.groups)
        scenario_reconciliation = @($decision.groups)
        global = $global
        qualification_global = $decision.qualification_global
        isolated_cpu_outlier = $decision.isolated_cpu_outlier
        outlier_confirmation = $decision.outlier_confirmation
        routes = @($decision.groups | ForEach-Object {
            $metric = $_.metrics
            [pscustomobject]@{
                route_family=$_.family; scenario=$_.scenario; invocations=$_.observed
                sent=$_.sent; required=$_.required; reserve=$_.reserve; missing=$_.missing
                p95_cpu_ms=$metric.p95_cpu_ms; p99_cpu_ms=$metric.p99_cpu_ms
                max_cpu_ms=$metric.max_cpu_ms; responses_5xx=$metric.responses_5xx
                responses_1102=$metric.responses_1102; exceeded_cpu=$metric.exceeded_cpu
                exceeded_memory=$metric.exceeded_memory
            }
        })
        provider_corroboration = $aggregate
        max_cpu_ms = $global.max_cpu_ms
        p95_cpu_ms = $global.p95_cpu_ms
        p99_cpu_ms = $global.p99_cpu_ms
        exceeded_cpu = $global.exceeded_cpu
        exceeded_memory = $global.exceeded_memory
        responses_1102 = $global.responses_1102
        responses_5xx = $global.responses_5xx
        gate_state = $gateState
        passed = [bool]($gateState -eq "PASSED")
    }
}

function Get-CandidateDeficitRepairProviderPreflight {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [Parameter(Mandatory = $true)][object[]]$ExpectedRequests,
        [Parameter(Mandatory = $true)][string]$ValidationRun
    )
    $stored = Read-WorkerCpuRunArtifact -ValidationRun $ValidationRun `
        -Name "provider-evidence.json"
    if (-not $stored) { throw "CANDIDATE_DEFICIT_REPAIR_EVIDENCE_UNAVAILABLE" }
    $repairPolicy = Get-WorkerCpuDeficitRepairPolicy
    $preflightReads = [int]$stored.recovery.deficit_repair_preflight_reads
    $lastPreflight = ConvertTo-ReleaseTimestampUtc `
        -Value ([string]$stored.recovery.deficit_repair_preflight_last_at)
    $preflightDue = $lastPreflight -eq [DateTimeOffset]::MinValue -or
        ([DateTimeOffset]::UtcNow - $lastPreflight).TotalSeconds -ge
            [int]$repairPolicy.provider_preflight_interval_seconds
    if ($preflightReads -ge [int]$repairPolicy.maximum_provider_preflight_reads -or
        -not $preflightDue) {
        $diagnostic = if (-not $preflightDue) {
            "DEFICIT_REPAIR_PROVIDER_PREFLIGHT_BACKOFF"
        } else { "DEFICIT_REPAIR_PROVIDER_PREFLIGHT_EXHAUSTED" }
        $script:lastWorkersObservabilityDiagnostic = $diagnostic
        return [pscustomobject]@{
            available=$false; plateau_stable=$false; decision=$null
            provider_evidence=$stored; digest_changed=$false; diagnostic=$diagnostic
        }
    }
    $stored.recovery | Add-Member -NotePropertyName deficit_repair_preflight_reads `
        -NotePropertyValue ($preflightReads + 1) -Force
    $stored.recovery | Add-Member -NotePropertyName deficit_repair_preflight_last_at `
        -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
    $stored = Write-WorkerCpuProviderEvidence -ValidationRun $ValidationRun `
        -Records @($stored.records) -RecoveryState $stored.recovery
    $priorDigest = [string]$stored.observed_universe_digest
    $plateau = Get-WorkerCpuProviderPlateauState -ValidationRun $ValidationRun `
        -CurrentDigest $priorDigest
    $filters = @(Get-CandidateObservabilityFilters -Candidate $Candidate `
        -ValidationRun $ValidationRun)
    $pageSet = Get-WorkersObservabilityEventPageSet -Filters $filters -From $From -To $To
    if (-not $pageSet) {
        Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun `
            -Event "DEFICIT_REPAIR_PROVIDER_PREFLIGHT_UNAVAILABLE" `
            -Detail ([pscustomobject]@{
                diagnostic=[string]$script:lastWorkersObservabilityDiagnostic
                prior_provider_digest=$priorDigest
            })
        return [pscustomobject]@{
            available=$false; plateau_stable=$false; decision=$null
            provider_evidence=$stored; digest_changed=$false
            diagnostic=[string]$script:lastWorkersObservabilityDiagnostic
        }
    }
    $records = @(Merge-WorkerCpuProviderEvidence -AcceptedRecords @($stored.records) `
        -NewRecords @($pageSet.records) -ExpectedRequests $ExpectedRequests `
        -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
        -ValidationRun $ValidationRun)
    $updated = Write-WorkerCpuProviderEvidence -ValidationRun $ValidationRun `
        -Records $records -RecoveryState $stored.recovery -ProviderReadSucceeded $true
    $digestChanged = [string]$updated.observed_universe_digest -ne $priorDigest
    $decision = Get-WorkerCpuQualificationDecision -ExpectedRequests $ExpectedRequests `
        -ProviderRecords $records -DirectResponsesComplete $true `
        -RecoveryBudgetExhausted $true
    Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun `
        -Event "DEFICIT_REPAIR_PROVIDER_PREFLIGHT_PASSED" `
        -Detail ([pscustomobject]@{
            prior_provider_digest=$priorDigest
            observed_provider_digest=[string]$updated.observed_universe_digest
            observed=$records.Count; digest_changed=$digestChanged
            prior_matching_plateau_reads=[int]$plateau.matching_reads
        })
    return [pscustomobject]@{
        available=$true
        plateau_stable=[bool]($plateau.stable -and -not $digestChanged)
        decision=$decision
        provider_evidence=$updated
        digest_changed=$digestChanged
        diagnostic=$null
    }
}

function Test-RetryableObservabilityDiagnostic {
    param([string]$Diagnostic)
    return [bool]($Diagnostic -in @(
        "PROVIDER_EVIDENCE_PENDING",
        "PROVIDER_EVIDENCE_INSUFFICIENT",
        "OBSERVABILITY_RATE_LIMITED",
        "OBSERVABILITY_TRANSIENT_API_FAILURE"
    ))
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
        payload = $response.Content | ConvertFrom-ReleaseControlJson
        requested_version_id = $VersionId
        observed_version_id = [string]$response.Headers["X-Aurum-Worker-Version"]
        observed_git_sha = [string]$response.Headers["X-Aurum-Git-SHA"]
        server_timing = [string]$response.Headers["Server-Timing"]
    }
}

function Get-ReleaseResponseHeaderValue {
    param([object]$Response, [Parameter(Mandatory = $true)][string]$Name)
    if (-not $Response) { return "" }
    try {
        $value = if ($Response.Headers.PSObject.Methods['GetValues'] -and
            $Response.Headers.Contains($Name)) {
            @($Response.Headers.GetValues($Name)) -join ","
        } else { $Response.Headers[$Name] }
        if ($value) { return [string]$value }
    } catch {}
    try {
        $value = if ($Name -eq "Content-Type" -and
            $Response.Content.Headers.ContentType) {
            $Response.Content.Headers.ContentType.ToString()
        } elseif ($Response.Content.Headers.PSObject.Methods['GetValues'] -and
            $Response.Content.Headers.Contains($Name)) {
            @($Response.Content.Headers.GetValues($Name)) -join ","
        } else { $Response.Content.Headers[$Name] }
        if ($value) { return [string]$value }
    } catch {}
    return ""
}

function Get-BoundedReleaseErrorBody {
    param([object]$Response, [int]$MaxBytes = 65536)
    if (-not $Response) { return $null }
    try {
        $body = $null
        if ($Response.PSObject.Properties['Content'] -and
            $Response.Content -is [string]) {
            $body = [string]$Response.Content
        } elseif ($Response.PSObject.Properties['Content'] -and
            $Response.Content -and
            $Response.Content.PSObject.Methods['ReadAsStringAsync']) {
            $readTask = $Response.Content.ReadAsStringAsync()
            $body = [string]$readTask.GetAwaiter().GetResult()
        } elseif ($Response.PSObject.Methods['GetResponseStream']) {
            $stream = $Response.GetResponseStream()
            if ($stream) {
                $reader = [System.IO.StreamReader]::new(
                    $stream, [System.Text.Encoding]::UTF8, $true, 1024, $true
                )
                try { $body = $reader.ReadToEnd() } finally { $reader.Dispose() }
            }
        }
        if ([string]::IsNullOrWhiteSpace([string]$body)) { return $null }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes([string]$body)
        if ($bytes.Length -gt $MaxBytes) { return $null }
        return [string]$body
    } catch { return $null }
}

function Get-ReleaseFailureFingerprint {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedPath,
        [Parameter(Mandatory = $true)][int]$StatusCode,
        [string]$ObservedRoute,
        [string]$Resource,
        [string]$FailureStage,
        [string]$ContentType,
        [AllowNull()][string]$Body
    )
    $basePath = ($ExpectedPath -split '\?', 2)[0]
    $unsafeStages = @("exception", "framework_fallback", "ssr")
    if ($StatusCode -lt 400 -or $ObservedRoute -cne $basePath -or
        [string]::IsNullOrWhiteSpace($Resource) -or
        [string]::IsNullOrWhiteSpace($FailureStage) -or
        $FailureStage -in $unsafeStages -or
        $ContentType -notmatch '^application/(?:[a-z0-9.+-]*\+)?json(?:\s*;|$)' -or
        [string]::IsNullOrWhiteSpace($Body)) {
        return [pscustomobject]@{
            available = $false; digest = $null; machine_reason = $null
            hard_safety_failure = $false
        }
    }
    try {
        $payload = $Body | ConvertFrom-ReleaseControlJson
        if (-not $payload -or -not $payload.PSObject.Properties) { throw "NOT_OBJECT" }
        $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
        if ($bodyBytes.Length -gt 65536) { throw "BODY_TOO_LARGE" }
        $bodyDigest = Get-Sha256BytesHex -Bytes $bodyBytes
        $machineReason = ""
        foreach ($field in @("error_code", "code", "reason")) {
            if ($payload.PSObject.Properties[$field] -and
                $payload.$field -is [string] -and
                [string]$payload.$field -match '^[A-Z][A-Z0-9_]{2,127}$') {
                $machineReason = [string]$payload.$field
                break
            }
        }
        if ([string]::IsNullOrWhiteSpace($machineReason)) {
            throw "MACHINE_REASON_REQUIRED"
        }
        $material = @(
            "release-debt-fingerprint-v1", $basePath, [string]$StatusCode,
            $Resource, $FailureStage, $bodyDigest
        ) -join "`n"
        $digest = Get-Sha256BytesHex -Bytes `
            ([System.Text.Encoding]::UTF8.GetBytes($material))
        $hardReason = $machineReason -match `
            '(AUTH|UNAUTHORIZED|FORBIDDEN|IDENTITY|INTEGRITY|CORRUPT|INVARIANT|SCHEMA|CAPABILITY|MIGRATION|RECEIPT)'
        $hardStage = $FailureStage -in @(
            "authorization", "release_validation_identity", "json_validation"
        )
        return [pscustomobject]@{
            available = $true; digest = $digest
            machine_reason = $machineReason
            hard_safety_failure = [bool]($hardReason -or $hardStage)
        }
    } catch {
        return [pscustomobject]@{
            available = $false; digest = $null; machine_reason = $null
            hard_safety_failure = $false
        }
    }
}

function Get-ExactVersionJsonObservation {
    param(
        [Parameter(Mandatory = $true)][string]$VersionId,
        [Parameter(Mandatory = $true)][string]$GitSha,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowLegacyIdentity
    )
    try {
        $read = Invoke-ExactVersionJson -VersionId $VersionId -Path $Path
        $identityPassed = [bool](
            ([string]$read.observed_version_id -eq $VersionId -or
             ($AllowLegacyIdentity -and
              [string]::IsNullOrWhiteSpace([string]$read.observed_version_id))) -and
            ([string]$read.observed_git_sha -eq $GitSha -or
             ($AllowLegacyIdentity -and
              [string]::IsNullOrWhiteSpace([string]$read.observed_git_sha)))
        )
        return [pscustomobject]@{
            passed = $true
            identity_passed = $identityPassed
            failure_class = $null
            failure_fingerprint = $null
            failure_fingerprint_available = $false
            hard_safety_failure = $false
            payload = $read.payload
            observed_version_id = [string]$read.observed_version_id
            observed_git_sha = [string]$read.observed_git_sha
        }
    } catch {
        $statusCode = 0
        $observedVersion = ""
        $observedGit = ""
        $observedRoute = ""
        $resource = ""
        $failureStage = ""
        $contentType = ""
        $body = $null
        try {
            $response = $_.Exception.Response
            if ($response) {
                $statusCode = [int]$response.StatusCode
                $observedVersion = Get-ReleaseResponseHeaderValue $response `
                    "X-Aurum-Worker-Version"
                $observedGit = Get-ReleaseResponseHeaderValue $response "X-Aurum-Git-SHA"
                $observedRoute = Get-ReleaseResponseHeaderValue $response "X-Aurum-Route"
                $resource = Get-ReleaseResponseHeaderValue $response "X-Aurum-Resource"
                $failureStage = Get-ReleaseResponseHeaderValue $response `
                    "X-Aurum-Failure-Stage"
                $contentType = Get-ReleaseResponseHeaderValue $response "Content-Type"
                $body = Get-BoundedReleaseErrorBody $response
            }
        } catch {}
        $fingerprint = Get-ReleaseFailureFingerprint -ExpectedPath $Path `
            -StatusCode $statusCode -ObservedRoute $observedRoute -Resource $resource `
            -FailureStage $failureStage -ContentType $contentType -Body $body
        $identityPassed = [bool](
            ([string]$observedVersion -eq $VersionId -or
             ($AllowLegacyIdentity -and [string]::IsNullOrWhiteSpace($observedVersion))) -and
            ([string]$observedGit -eq $GitSha -or
             ($AllowLegacyIdentity -and [string]::IsNullOrWhiteSpace($observedGit)))
        )
        return [pscustomobject]@{
            passed = $false
            identity_passed = $identityPassed
            failure_class = if ($statusCode -gt 0) { "HTTP_$statusCode" } `
                else { "EXACT_VERSION_READ_FAILED" }
            failure_fingerprint = [string]$fingerprint.digest
            failure_fingerprint_available = [bool]$fingerprint.available
            failure_reason_code = [string]$fingerprint.machine_reason
            failure_stage = $failureStage
            hard_safety_failure = [bool]$fingerprint.hard_safety_failure
            payload = $null
            observed_version_id = $observedVersion
            observed_git_sha = $observedGit
            diagnostic = Protect-PreflightDiagnosticText $_.Exception.Message
        }
    }
}

function Get-CandidateParityClass {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$RoutePlan
    )
    $basePath = ([string]$Path -split '\?', 2)[0]
    if ($basePath -eq "/api/status") { return "A" }
    if (@($RoutePlan.contract_routes | Where-Object {
        [string]$_.path -eq $basePath
    }).Count -gt 0) { return "B" }
    return "C"
}

function Test-CandidateAuthBoundaryChanged {
    param([Parameter(Mandatory = $true)][object]$RoutePlan)
    return [bool](@($RoutePlan.contract_routes | Where-Object {
        [bool]$_.auth_required
    }).Count -gt 0)
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

function Invoke-CloudflareAccessRead {
    param([Parameter(Mandatory = $true)][string]$PathAndQuery)
    if (-not $PathAndQuery.StartsWith("/", [StringComparison]::Ordinal)) {
        throw "ACCESS_PROVIDER_READ_PATH_INVALID"
    }
    $secret = Get-ReleaseSecret -Name "CLOUDFLARE_ACCESS_READ_TOKEN"
    if (-not $secret.available) {
        throw "ACCESS_PROVIDER_READ_CREDENTIAL_UNAVAILABLE:$($secret.diagnostic)"
    }
    try {
        $response = Invoke-RestMethod -Method Get `
            -Uri "https://api.cloudflare.com/client/v4$PathAndQuery" `
            -Headers @{ Authorization = "Bearer $($secret.value)" } -TimeoutSec 15
    } catch {
        throw "ACCESS_PROVIDER_READ_UNAVAILABLE"
    }
    if (-not $response -or -not [bool]$response.success) {
        throw "ACCESS_PROVIDER_READ_FAILED"
    }
    return $response
}

function Get-CloudflareAccessAuditInterval {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset]$From,
        [Parameter(Mandatory = $true)][DateTimeOffset]$To,
        [Parameter(Mandatory = $true)][string]$ApplicationId,
        [Parameter(Mandatory = $true)][string]$PolicyId
    )
    if ($From -ge $To -or ($To - $From) -gt $accessProviderAuditMaximumLookback) {
        throw "ACCESS_PROVIDER_AUDIT_INTERVAL_UNCOVERED"
    }
    $events = @()
    $cursor = ""
    $seenCursors = @{}
    $pages = 0
    do {
        if ($pages -ge $accessProviderAuditMaximumPages) {
            throw "ACCESS_PROVIDER_AUDIT_PAGINATION_UNBOUNDED"
        }
        $query = "?since=$([Uri]::EscapeDataString($From.ToUniversalTime().ToString('o')))" +
            "&before=$([Uri]::EscapeDataString($To.ToUniversalTime().ToString('o')))" +
            "&direction=desc&limit=1000"
        if ($cursor) { $query += "&cursor=$([Uri]::EscapeDataString($cursor))" }
        $response = Invoke-CloudflareAccessRead `
            -PathAndQuery "/accounts/$cloudflareAccountId/logs/audit$query"
        $pages++
        $events += @($response.result | Where-Object { $null -ne $_ })
        $next = if ($response.result_info -and $response.result_info.cursor) {
            [string]$response.result_info.cursor
        } else { "" }
        if ($next) {
            if ($seenCursors.ContainsKey($next)) {
                throw "ACCESS_PROVIDER_AUDIT_PAGINATION_CYCLE"
            }
            $seenCursors[$next] = $true
        }
        $cursor = $next
    } while ($cursor)

    $relevant = @($events | Where-Object {
        $actionType = ([string]$_.action.type).ToLowerInvariant()
        $method = ([string]$_.raw.method).ToUpperInvariant()
        if ($actionType -eq "view" -and $method -eq "GET") { return $false }
        $identity = "$([string]$_.resource.id)|$([string]$_.resource.product)|" +
            "$([string]$_.resource.type)|$([string]$_.raw.uri)"
        return $identity -match [regex]::Escape($ApplicationId) -or
            $identity -match [regex]::Escape($PolicyId) -or
            $identity -match '(?i)/access/identity_providers(?:/|$)'
    })
    $failures = @($relevant | Where-Object {
        ([string]$_.action.result).ToLowerInvariant() -ne "success" -or
        ([int]$_.raw.status_code -ge 400 -and [int]$_.raw.status_code -ne 0)
    })
    return [pscustomobject]@{
        complete = $true
        page_count = $pages
        event_count = $events.Count
        relevant_change_count = $relevant.Count
        relevant_failure_count = $failures.Count
    }
}

function Test-AccessProviderPolicyTimestampCompatible {
    param(
        [Parameter(Mandatory = $true)][DateTimeOffset]$Current,
        [Parameter(Mandatory = $true)][DateTimeOffset]$Previous,
        [Parameter(Mandatory = $true)][object]$PreviousInspection
    )
    if ($Current -eq $Previous) { return $true }
    return [bool](
        [string]$PreviousInspection.schema_version -eq
            "access-provider-inspection-v1" -and
        [string]$PreviousInspection.inspection_method -eq
            "CLOUDFLARE_AUTHENTICATED_DASHBOARD_READ_ONLY" -and
        $Previous.Second -eq 0 -and $Previous.Millisecond -eq 0 -and
        $Current -ge $Previous -and $Current -lt $Previous.AddMinutes(1)
    )
}

function ConvertFrom-CloudflareAccessResources {
    param(
        [Parameter(Mandatory = $true)][object]$Application,
        [Parameter(Mandatory = $true)][object]$Policy,
        [Parameter(Mandatory = $true)][object[]]$IdentityProviders,
        [Parameter(Mandatory = $true)][object]$PreviousInspection
    )
    $previous = Assert-AccessProviderInspectionReceipt -Receipt $PreviousInspection
    $allowed = @($Application.allowed_idps | ForEach-Object {
        if ($_ -is [string]) { [string]$_ } elseif ($_.id) { [string]$_.id }
    } | Where-Object { $_ })
    $selectedProviders = if ($allowed.Count -gt 0) {
        @($IdentityProviders | Where-Object { [string]$_.id -in $allowed })
    } else { @($IdentityProviders) }
    $providerTypes = @($selectedProviders | ForEach-Object {
        ([string]$_.type).Trim().ToLowerInvariant()
    } | Where-Object { $_ } | Sort-Object -Unique)
    $protectedHost = (Get-ProtectedAccessBoundaryIdentity).host
    $destinations = @($Application.destinations | ForEach-Object {
        $raw = if ($_ -is [string]) { [string]$_ } else { [string]$_.uri }
        if (-not $raw) { return }
        if ($raw -match '^https?://') {
            $withoutScheme = $raw -replace '^https?://', ''
            $slash = $withoutScheme.IndexOf('/')
            if ($slash -lt 0) { '/' } else { $withoutScheme.Substring($slash) }
        } elseif ($raw.StartsWith("$protectedHost/", [StringComparison]::OrdinalIgnoreCase)) {
            $raw.Substring($protectedHost.Length)
        } elseif ($raw -ieq $protectedHost) { '/' } else { $raw }
    } | Sort-Object -Unique)
    $policyUpdated = ConvertTo-ReleaseTimestampUtc -Value $Policy.updated_at
    $previousUpdated = ConvertTo-ReleaseTimestampUtc `
        -Value $PreviousInspection.policy_last_updated_at
    $policyTimestampCompatible = Test-AccessProviderPolicyTimestampCompatible `
        -Current $policyUpdated -Previous $previousUpdated `
        -PreviousInspection $PreviousInspection
    $behavior = [pscustomobject]@{
        application_id = [string]$Application.id
        application_audience = [string]$Application.aud
        application_name = [string]$Application.name
        application_type = [string]$Application.type
        application_session_duration = [string]$Application.session_duration
        destinations = $destinations
        policy_id = [string]$Policy.id
        policy_name = [string]$Policy.name
        policy_action = [string]$Policy.decision
        policy_order = [int]$Policy.precedence
        policy_rule_count = @($Policy.include).Count
        policy_session_duration = [string]$Policy.session_duration
        owner_rule_sha256 = [string]$previous.behavior.owner_rule_sha256
        identity_providers = $providerTypes
        mfa_required = [bool](@($Policy.require | Where-Object {
            $_.PSObject.Properties['auth_method'] -or $_.PSObject.Properties['auth_context']
        }).Count -gt 0)
        browser_isolation = [bool]$Policy.isolation_required
        purpose_justification = [bool]$Policy.purpose_justification_required
        temporary_authentication = [bool]$Policy.approval_required
    }
    if ($policyUpdated -eq [DateTimeOffset]::MinValue -or
        $previousUpdated -eq [DateTimeOffset]::MinValue -or
        -not $policyTimestampCompatible) {
        throw "ACCESS_PROVIDER_CONFIGURATION_CHANGED"
    }
    return Assert-AccessProviderInspectionMatchesContract -Inspection $behavior
}

function Invoke-AccessProviderContinuousInspection {
    param([Parameter(Mandatory = $true)][object]$PreviousInspection)
    $previous = Assert-AccessProviderInspectionReceipt -Receipt $PreviousInspection
    $contract = Get-AccessQualificationContract
    $applicationId = [string]$contract.provider_boundary.application_id
    $policyId = [string]$contract.provider_boundary.policy_id
    $windowStart = ConvertTo-ReleaseTimestampUtc -Value $previous.audit_window_end
    if ($windowStart -eq [DateTimeOffset]::MinValue -or
        $windowStart -ge (Get-AccessEvidenceUtcNow)) {
        throw "ACCESS_PROVIDER_AUDIT_INTERVAL_UNCOVERED"
    }
    $app = Invoke-CloudflareAccessRead `
        -PathAndQuery "/accounts/$cloudflareAccountId/access/apps/$applicationId"
    $policy = Invoke-CloudflareAccessRead `
        -PathAndQuery "/accounts/$cloudflareAccountId/access/apps/$applicationId/policies/$policyId"
    $idps = Invoke-CloudflareAccessRead `
        -PathAndQuery "/accounts/$cloudflareAccountId/access/identity_providers"
    $behavior = ConvertFrom-CloudflareAccessResources -Application $app.result `
        -Policy $policy.result -IdentityProviders @($idps.result) `
        -PreviousInspection $previous
    $windowEnd = Get-AccessEvidenceUtcNow
    $audit = Get-CloudflareAccessAuditInterval -From $windowStart -To $windowEnd `
        -ApplicationId $applicationId -PolicyId $policyId
    $inspection = [pscustomobject]@{
        inspection_method = "CLOUDFLARE_ACCESS_API_READ_ONLY"
        observed_at = $windowEnd.ToString("o")
        audit_window_start = $windowStart.ToString("o")
        audit_window_end = $windowEnd.ToString("o")
        audit_history_complete = [bool]$audit.complete
        audit_page_count = [int]$audit.page_count
        audit_event_count = [int]$audit.event_count
        application_change_count = [int]$audit.relevant_change_count
        policy_change_count = 0
        access_failure_count = [int]$audit.relevant_failure_count
        policy_last_updated_at = [string]$policy.result.updated_at
    }
    foreach ($name in @($behavior.Keys)) {
        $inspection | Add-Member -NotePropertyName ([string]$name) `
            -NotePropertyValue $behavior[$name]
    }
    return Register-AccessProviderInspection -Inspection $inspection
}

function Register-AccessProviderInspection {
    param([Parameter(Mandatory = $true)][object]$Inspection)
    $behavior = Assert-AccessProviderInspectionMatchesContract -Inspection $Inspection
    $observedAt = ConvertTo-ReleaseTimestampUtc -Value $Inspection.observed_at
    $windowStart = ConvertTo-ReleaseTimestampUtc -Value $Inspection.audit_window_start
    $windowEnd = ConvertTo-ReleaseTimestampUtc -Value $Inspection.audit_window_end
    $policyUpdated = ConvertTo-ReleaseTimestampUtc -Value $Inspection.policy_last_updated_at
    $method = [string]$Inspection.inspection_method
    $isApi = $method -eq "CLOUDFLARE_ACCESS_API_READ_ONLY"
    if ($method -notin @(
            "CLOUDFLARE_AUTHENTICATED_DASHBOARD_READ_ONLY",
            "CLOUDFLARE_ACCESS_API_READ_ONLY"
        ) -or
        $observedAt -eq [DateTimeOffset]::MinValue -or
        $windowStart -eq [DateTimeOffset]::MinValue -or
        $windowEnd -eq [DateTimeOffset]::MinValue -or
        $policyUpdated -eq [DateTimeOffset]::MinValue -or
        $windowStart -gt $windowEnd -or
        [Math]::Abs(($observedAt - $windowEnd).TotalMinutes) -gt 5 -or
        $observedAt -gt (Get-AccessEvidenceUtcNow).AddMinutes(5) -or
        [int]$Inspection.application_change_count -ne 0 -or
        [int]$Inspection.policy_change_count -ne 0 -or
        ($isApi -and (-not [bool]$Inspection.audit_history_complete -or
            [int]$Inspection.audit_page_count -lt 1 -or
            [int]$Inspection.access_failure_count -ne 0))) {
        throw "ACCESS_PROVIDER_INSPECTION_INVALID"
    }
    $core = [ordered]@{
        schema_version = if ($isApi) {
            "access-provider-inspection-v2"
        } else { "access-provider-inspection-v1" }
        observed_at = $observedAt.ToString("o")
        inspection_method = [string]$Inspection.inspection_method
        audit_window_start = $windowStart.ToString("o")
        audit_window_end = $windowEnd.ToString("o")
        application_change_count = [int]$Inspection.application_change_count
        policy_change_count = [int]$Inspection.policy_change_count
        policy_last_updated_at = $policyUpdated.ToString("o")
        behavior = $behavior
        provider_fingerprint = Get-AccessProviderBehaviorFingerprint -Inspection $behavior
    }
    if ($isApi) {
        $core.audit_history_complete = [bool]$Inspection.audit_history_complete
        $core.audit_page_count = [int]$Inspection.audit_page_count
        $core.audit_event_count = [int]$Inspection.audit_event_count
        $core.access_failure_count = [int]$Inspection.access_failure_count
    }
    $receipt = [pscustomobject]@{
        schema_version = $core.schema_version
        observed_at = $core.observed_at
        inspection_method = $core.inspection_method
        audit_window_start = $core.audit_window_start
        audit_window_end = $core.audit_window_end
        application_change_count = $core.application_change_count
        policy_change_count = $core.policy_change_count
        policy_last_updated_at = $core.policy_last_updated_at
        behavior = $core.behavior
        provider_fingerprint = $core.provider_fingerprint
        receipt_digest = Get-AccessProviderInspectionReceiptDigest -Core $core
    }
    if ($isApi) {
        $receipt | Add-Member -NotePropertyName audit_history_complete `
            -NotePropertyValue $core.audit_history_complete
        $receipt | Add-Member -NotePropertyName audit_page_count `
            -NotePropertyValue $core.audit_page_count
        $receipt | Add-Member -NotePropertyName audit_event_count `
            -NotePropertyValue $core.audit_event_count
        $receipt | Add-Member -NotePropertyName access_failure_count `
            -NotePropertyValue $core.access_failure_count
    }
    New-Item -ItemType Directory -Path $accessProviderInspectionRoot -Force | Out-Null
    $path = Join-Path $accessProviderInspectionRoot "$($receipt.receipt_digest).json"
    if (-not (Test-Path -LiteralPath $path)) {
        Write-ControlCenterJsonAtomic -Path $path -Value $receipt `
            -Depth 12 -Immutable
    }
    return $receipt
}

function Test-CloudflareReleasePlacement {
    param([object]$Stable, [object]$Candidate = $null)
    $deployment = Get-CloudflareDeployment
    $stablePlacement = @($deployment.versions | Where-Object {
        [string]$_.version_id -eq [string]$Stable.worker_version_id -and
        [double]$_.percentage -eq 100
    }).Count -eq 1
    if (-not $stablePlacement) { return $false }
    if ($Candidate) {
        return @($deployment.versions | Where-Object {
            [string]$_.version_id -eq [string]$Candidate.worker_version_id -and
            [double]$_.percentage -eq 0
        }).Count -eq 1
    }
    return $true
}

function Test-CloudflareRollbackTarget {
    param([Parameter(Mandatory = $true)][object]$Target)
    $resolution = Resolve-ReleaseRuntimeIdentity $Target $releaseSchemaVersion
    $observation = Get-CloudflareRollbackArtifactObservation `
        -IdentityResolution $resolution -ForceFresh
    return [string]$observation.status -eq "AVAILABLE"
}

function Get-NativeDescendantProcessIds {
    param(
        [Parameter(Mandatory = $true)][int]$RootProcessId,
        [Parameter(Mandatory = $true)][ref]$InventoryAvailable
    )
    $InventoryAvailable.Value = $false
    try {
        $rows = @(Get-CimInstance Win32_Process -OperationTimeoutSec 2 `
            -ErrorAction Stop |
            Select-Object ProcessId,ParentProcessId)
    } catch { return @() }
    $InventoryAvailable.Value = $true
    $pending = @($RootProcessId)
    $result = New-Object System.Collections.Generic.List[int]
    while ($pending.Count -gt 0) {
        $parent = [int]$pending[0]
        $pending = @($pending | Select-Object -Skip 1)
        foreach ($row in @($rows | Where-Object {
            [int]$_.ParentProcessId -eq $parent
        })) {
            $child = [int]$row.ProcessId
            if (-not $result.Contains($child)) {
                $result.Add($child)
                $pending += $child
            }
        }
    }
    return @($result)
}

function Test-NativeProcessIdsExited {
    param([int[]]$ProcessIds)
    foreach ($processIdValue in @($ProcessIds | Select-Object -Unique)) {
        if (Get-Process -Id $processIdValue -ErrorAction SilentlyContinue) {
            return $false
        }
    }
    return $true
}

function Invoke-NativeTreeTerminationCommand {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [int]$TimeoutMilliseconds = 5000
    )
    $killer = $null
    try {
        $taskkill = Get-Command "taskkill.exe" -ErrorAction Stop
        $start = New-Object System.Diagnostics.ProcessStartInfo
        $start.FileName = [string]$taskkill.Source
        $start.Arguments = "/PID $ProcessId /T /F"
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $killer = [System.Diagnostics.Process]::Start($start)
        if (-not $killer.WaitForExit($TimeoutMilliseconds)) {
            try { $killer.Kill() } catch {}
            try { [void]$killer.WaitForExit(1000) } catch {}
            return [pscustomobject]@{ state = "COMMAND_TIMEOUT"; exit_code = $null }
        }
        return [pscustomobject]@{
            state = if ([int]$killer.ExitCode -eq 0) {
                "COMMAND_SUCCEEDED"
            } else { "COMMAND_NONZERO" }
            exit_code = [int]$killer.ExitCode
        }
    } catch {
        return [pscustomobject]@{ state = "COMMAND_START_FAILED"; exit_code = $null }
    } finally {
        if ($killer) { try { $killer.Dispose() } catch {} }
    }
}

function Stop-NativeProcessTree {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    try {
        if ($Process.HasExited) {
            return [pscustomobject]@{
                state = "ALREADY_EXITED"; root_exited = $true
                descendants_exited = $true; command_state = "NOT_REQUIRED"
            }
        }
        $rootId = [int]$Process.Id
    } catch {
        return [pscustomobject]@{
            state = "ALREADY_EXITED"; root_exited = $true
            descendants_exited = $true; command_state = "NOT_REQUIRED"
        }
    }
    $inventoryAvailable = $false
    $descendantIds = @(Get-NativeDescendantProcessIds -RootProcessId $rootId `
        -InventoryAvailable ([ref]$inventoryAvailable))
    $trackedIds = @($rootId) + $descendantIds
    $command = Invoke-NativeTreeTerminationCommand -ProcessId $rootId `
        -TimeoutMilliseconds 5000
    try { [void]$Process.WaitForExit(1000) } catch {}
    if ((Test-NativeProcessIdsExited -ProcessIds $trackedIds) -and
        ($inventoryAvailable -or [string]$command.state -eq "COMMAND_SUCCEEDED")) {
        return [pscustomobject]@{
            state = "TERMINATED"; root_exited = $true
            descendants_exited = $true; command_state = [string]$command.state
        }
    }

    # A non-zero or timed-out taskkill is not evidence of termination. Capture
    # the still-owned tree again, kill descendants before the root, then prove
    # every observed member is gone.
    $fallbackInventoryAvailable = $false
    $descendantIds = @($descendantIds) + @(
        Get-NativeDescendantProcessIds -RootProcessId $rootId `
            -InventoryAvailable ([ref]$fallbackInventoryAvailable)
    ) | Select-Object -Unique
    $trackedIds = @($rootId) + @($descendantIds)
    $fallbackIds = @($descendantIds)
    [array]::Reverse($fallbackIds)
    foreach ($processIdValue in $fallbackIds) {
        try {
            $descendant = [System.Diagnostics.Process]::GetProcessById(
                [int]$processIdValue
            )
            try { $descendant.Kill() } finally { $descendant.Dispose() }
        } catch {}
    }
    try { $Process.Kill() } catch {}
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds(5)
    while ([DateTimeOffset]::UtcNow -lt $deadline -and
        -not (Test-NativeProcessIdsExited -ProcessIds $trackedIds)) {
        Start-Sleep -Milliseconds 50
    }
    $rootExited = -not [bool](Get-Process -Id $rootId -ErrorAction SilentlyContinue)
    $treeInventoryAvailable = [bool]($inventoryAvailable -or
        $fallbackInventoryAvailable)
    $descendantsExited = [bool]($treeInventoryAvailable -and
        (Test-NativeProcessIdsExited -ProcessIds $descendantIds))
    return [pscustomobject]@{
        state = if ($rootExited -and $descendantsExited) {
            "TERMINATED"
        } else { "TERMINATION_FAILED" }
        root_exited = $rootExited
        descendants_exited = $descendantsExited
        command_state = [string]$command.state
    }
}

function Get-ReleaseDeploymentProviderObservation {
    param([switch]$ForceFresh, [switch]$NoRefresh)
    $now = [DateTimeOffset]::UtcNow
    $cached = $script:releaseDeploymentObservationCache
    if ($NoRefresh) {
        if ($cached -and [string]$cached.status -eq "AVAILABLE" -and
            ($now - [DateTimeOffset]::Parse([string]$cached.observed_at)) -le
                $releaseProviderObservationMaximumStaleAge) {
            return $cached
        }
        return [pscustomobject]@{
            status = "UNKNOWN"; value = $null
            observed_at = $now.ToString("o"); reason = "PROVIDER_OBSERVATION_NOT_REFRESHED"
        }
    }
    if (-not $ForceFresh -and $cached -and
        ($now - [DateTimeOffset]::Parse([string]$cached.attempted_at)) -lt
            $releaseProviderObservationInterval) {
        if ([string]$cached.status -eq "AVAILABLE" -and
            ($now - [DateTimeOffset]::Parse([string]$cached.observed_at)) -gt
                $releaseProviderObservationMaximumStaleAge) {
            return [pscustomobject]@{
                status = "UNKNOWN"; value = $null
                observed_at = $now.ToString("o"); reason = "PROVIDER_OBSERVATION_STALE"
            }
        }
        return $cached
    }
    $attemptedAt = [DateTimeOffset]::UtcNow
    try {
        $value = Get-CloudflareDeployment
        $completedAt = [DateTimeOffset]::UtcNow
        $cached = [pscustomobject]@{
            status = "AVAILABLE"; value = $value
            observed_at = $completedAt.ToString("o")
            attempted_at = $attemptedAt.ToString("o")
            reason = "CLOUDFLARE_DEPLOYMENT_OBSERVED"
        }
    } catch {
        if ([string]$_.Exception.Message -eq
            "NATIVE_PROCESS_TERMINATION_UNRESOLVED") { throw }
        $completedAt = [DateTimeOffset]::UtcNow
        $cached = [pscustomobject]@{
            status = "UNKNOWN"; value = $null
            observed_at = $completedAt.ToString("o")
            attempted_at = $attemptedAt.ToString("o")
            reason = "CLOUDFLARE_DEPLOYMENT_OBSERVATION_UNAVAILABLE"
        }
    }
    $script:releaseDeploymentObservationCache = $cached
    return $cached
}

function Test-ReleaseExactVersionProviderEnvelope {
    param(
        [Parameter(Mandatory = $true)][string]$RequestedVersionId,
        [AllowNull()][object]$Value
    )
    if (-not $Value -or $Value -is [System.Array] -or
        -not $Value.PSObject.Properties['id'] -or
        [string]::IsNullOrWhiteSpace([string]$Value.id)) {
        return "UNKNOWN"
    }
    if ([string]$Value.id -cne $RequestedVersionId) { return "MISMATCH" }
    if (-not $Value.PSObject.Properties['metadata'] -or -not $Value.metadata -or
        -not $Value.PSObject.Properties['resources'] -or -not $Value.resources -or
        -not $Value.resources.PSObject.Properties['script'] -or
        -not $Value.resources.script -or
        -not $Value.resources.script.PSObject.Properties['handlers'] -or
        'fetch' -notin @($Value.resources.script.handlers)) {
        return "UNKNOWN"
    }
    return "AVAILABLE"
}

function Get-ReleaseExactVersionProviderObservation {
    param(
        [Parameter(Mandatory = $true)][string]$VersionId,
        [switch]$ForceFresh,
        [switch]$NoRefresh
    )
    $now = [DateTimeOffset]::UtcNow
    $cached = if ($script:releaseExactVersionObservationCache.ContainsKey($VersionId)) {
        $script:releaseExactVersionObservationCache[$VersionId]
    } else { $null }
    if (-not $ForceFresh -and $cached -and [string]$cached.status -eq "AVAILABLE") {
        return $cached
    }
    if ($NoRefresh) {
        if ($cached -and [string]$cached.status -eq "AVAILABLE") { return $cached }
        return [pscustomobject]@{
            status = "UNKNOWN"; value = $null
            observed_at = $now.ToString("o"); reason = "EXACT_VERSION_NOT_REFRESHED"
        }
    }
    if (-not $ForceFresh -and $cached -and
        ($now - [DateTimeOffset]::Parse([string]$cached.attempted_at)) -lt
            $releaseProviderObservationInterval) {
        return $cached
    }
    $attemptedAt = [DateTimeOffset]::UtcNow
    try {
        $value = Get-CloudflareVersionDetails -VersionId $VersionId
        $completedAt = [DateTimeOffset]::UtcNow
        $envelopeStatus = Test-ReleaseExactVersionProviderEnvelope `
            -RequestedVersionId $VersionId -Value $value
        $next = [pscustomobject]@{
            status = $envelopeStatus
            value = if ($envelopeStatus -eq "AVAILABLE") { $value } else { $null }
            observed_at = $completedAt.ToString("o")
            attempted_at = $attemptedAt.ToString("o")
            reason = if ($envelopeStatus -eq "AVAILABLE") {
                "EXACT_WORKER_VERSION_OBSERVED"
            } elseif ($envelopeStatus -eq "MISMATCH") {
                "EXACT_WORKER_VERSION_IDENTITY_MISMATCH"
            } else { "EXACT_WORKER_VERSION_RESPONSE_MALFORMED" }
        }
        if ($envelopeStatus -ne "AVAILABLE" -and $cached -and
            [string]$cached.status -eq "AVAILABLE") {
            return $next
        }
        $cached = $next
    } catch {
        if ([string]$_.Exception.Message -eq
            "NATIVE_PROCESS_TERMINATION_UNRESOLVED") { throw }
        $diagnostic = [string]$_.Exception.Message
        $status = if ($diagnostic -match '(?i)(^|[:\s])(?:CLOUDFLARE_)?VERSION_NOT_FOUND(?:[:\s]|$)' -or
            $diagnostic -eq "CLOUDFLARE_HTTP_404" -or
            $diagnostic -match '(?i)\bHTTP(?:_STATUS| STATUS| STATUS CODE)?[ :=]+404\b') {
            "UNAVAILABLE"
        } else { "UNKNOWN" }
        $completedAt = [DateTimeOffset]::UtcNow
        $failure = [pscustomobject]@{
            status = $status; value = $null
            observed_at = $completedAt.ToString("o")
            attempted_at = $attemptedAt.ToString("o")
            reason = "EXACT_WORKER_VERSION_$status"
        }
        if ($status -eq "UNKNOWN" -and $cached -and
            [string]$cached.status -eq "AVAILABLE") {
            return $failure
        }
        $cached = $failure
    }
    $script:releaseExactVersionObservationCache[$VersionId] = $cached
    return $cached
}

function Get-CloudflareRollbackArtifactObservation {
    param(
        [Parameter(Mandatory = $true)][object]$IdentityResolution,
        [switch]$ForceFresh,
        [switch]$NoRefresh
    )
    $Target = $IdentityResolution.identity
    $targetVersionId = [string]$Target.worker_version_id
    if ([string]$IdentityResolution.status -ne "COMPLETE" -or
        [string]::IsNullOrWhiteSpace($targetVersionId)) {
        return New-ReleaseWorkerArtifactObservation `
            -IdentityResolution $IdentityResolution -VersionDetails $null `
            -ProviderStatus "UNKNOWN"
    }
    $provider = Get-ReleaseExactVersionProviderObservation `
        -VersionId $targetVersionId -ForceFresh:$ForceFresh -NoRefresh:$NoRefresh
    $artifact = New-ReleaseWorkerArtifactObservation `
        -IdentityResolution $IdentityResolution -VersionDetails $provider.value `
        -ProviderStatus ([string]$provider.status) `
        -ProviderScopeVerified ([bool]([string]$provider.status -eq "AVAILABLE"))
    $artifact | Add-Member -NotePropertyName provider_observed_at `
        -NotePropertyValue ([string]$provider.observed_at) -Force
    return $artifact
}

function ConvertFrom-ReleaseTrafficPercentage {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value -or
        [string]::IsNullOrWhiteSpace([string]$Value)) {
        return [pscustomobject]@{ valid = $false; value = $null }
    }
    $parsed = 0.0
    $valid = [double]::TryParse(
        [string]$Value,
        [Globalization.NumberStyles]::Float,
        [Globalization.CultureInfo]::InvariantCulture,
        [ref]$parsed
    )
    $valid = [bool]($valid -and -not [double]::IsNaN($parsed) -and
        -not [double]::IsInfinity($parsed) -and $parsed -ge 0.0 -and
        $parsed -le 100.0)
    return [pscustomobject]@{
        valid = $valid
        value = if ($valid) { [double]$parsed } else { $null }
    }
}

function Get-ReleaseTrafficObservation {
    param(
        [AllowNull()][object]$Deployment,
        [AllowNull()][object]$Previous,
        [string]$Status = "AVAILABLE"
    )
    $previousId = if ($Previous) { [string]$Previous.worker_version_id } else { "" }
    $notObservedMembership = if ($previousId) { "UNKNOWN" } else { "NOT_APPLICABLE" }
    if ($Status -ne "AVAILABLE" -or -not $Deployment -or
        -not $Deployment.PSObject.Properties['versions']) {
        return [pscustomobject]@{
            status = "UNKNOWN"; version_id = $null; git_sha = $null
            traffic_percent = $null; previous_is_member = $false
            previous_membership_status = $notObservedMembership
            previous_traffic_percent = $null
        }
    }
    $versions = @($Deployment.versions | Where-Object { $null -ne $_ })
    $parsedVersions = @()
    foreach ($version in $versions) {
        if (-not $version.PSObject.Properties['version_id'] -or
            [string]::IsNullOrWhiteSpace([string]$version.version_id) -or
            -not $version.PSObject.Properties['percentage']) {
            return [pscustomobject]@{
                status = "MISMATCH"; version_id = $null; git_sha = $null
                traffic_percent = $null; previous_is_member = $false
                previous_membership_status = "MISMATCH"
                previous_traffic_percent = $null
            }
        }
        $percentage = ConvertFrom-ReleaseTrafficPercentage $version.percentage
        if (-not $percentage.valid) {
            return [pscustomobject]@{
                status = "MISMATCH"; version_id = $null; git_sha = $null
                traffic_percent = $null; previous_is_member = $false
                previous_membership_status = "MISMATCH"
                previous_traffic_percent = $null
            }
        }
        $parsedVersions += [pscustomobject]@{
            version_id = [string]$version.version_id
            percentage = [double]$percentage.value
            raw = $version
        }
    }
    $active = @($parsedVersions | Where-Object { $_.percentage -eq 100.0 })
    $otherPositive = @($parsedVersions | Where-Object {
        $_.percentage -gt 0.0 -and $_.percentage -lt 100.0
    })
    if ($active.Count -ne 1 -or $otherPositive.Count -gt 0) {
        return [pscustomobject]@{
            status = "MISMATCH"
            version_id = $null; git_sha = $null
            traffic_percent = $null; previous_is_member = $false
            previous_membership_status = if ($previousId) { "MISMATCH" } else {
                "NOT_APPLICABLE"
            }
            previous_traffic_percent = $null
        }
    }
    $previousRows = @($parsedVersions | Where-Object {
        $previousId -and [string]$_.version_id -ceq $previousId
    })
    $membershipStatus = if (-not $previousId) {
        "NOT_APPLICABLE"
    } elseif ($previousRows.Count -eq 0) {
        "NOT_ASSIGNED"
    } elseif ($previousRows.Count -eq 1) { "ASSIGNED" } else { "MISMATCH" }
    [pscustomobject]@{
        status = "AVAILABLE"
        version_id = [string]$active[0].version_id
        git_sha = if ($active[0].raw.PSObject.Properties['git_sha']) {
            [string]$active[0].raw.git_sha
        } else { $null }
        traffic_percent = [double]$active[0].percentage
        previous_is_member = [bool]($membershipStatus -eq "ASSIGNED")
        previous_membership_status = $membershipStatus
        previous_traffic_percent = if ($membershipStatus -eq "ASSIGNED") {
            [double]$previousRows[0].percentage
        } else { $null }
    }
}

function Get-ReleaseWindowsArtifactObservation {
    param([Parameter(Mandatory = $true)][object]$IdentityResolution)
    $Target = $IdentityResolution.identity
    if ([string]$IdentityResolution.status -ne "COMPLETE" -or -not $Target) {
        return New-ReleaseWindowsArtifactObservation `
            -IdentityResolution $IdentityResolution `
            -Status "NOT_APPLICABLE"
    }
    $revision = [string]$Target.windows_revision
    if ($revision -notmatch '^[0-9a-f]{40}$' -or
        ([string]$Target.git_sha -match '^[0-9a-f]{40}$' -and
            [string]$Target.git_sha -cne $revision)) {
        return New-ReleaseWindowsArtifactObservation `
            -IdentityResolution $IdentityResolution `
            -Status "MISMATCH" -Reason "WINDOWS_REVISION_IDENTITY_MISMATCH"
    }
    try {
        $commit = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
            "-C", $repositoryRoot, "cat-file", "-e", "$revision^{commit}"
        )
        if ($commit.exit_code -ne 0) {
            return New-ReleaseWindowsArtifactObservation `
                -IdentityResolution $IdentityResolution `
                -Status "UNAVAILABLE" -Reason "WINDOWS_REVISION_UNAVAILABLE"
        }
        $manifest = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
            "-C", $repositoryRoot, "show", "${revision}:scripts/windows-service-launch-contract.json"
        )
        if ($manifest.exit_code -eq 0) {
            $contract = ([string]$manifest.stdout) | ConvertFrom-ReleaseControlJson
            $keys = @($contract.services | ForEach-Object { [string]$_.key })
            if ([string]$contract.schema_version -ne "windows-service-launch-contract-v1" -or
                $keys.Count -ne 6 -or @($keys | Sort-Object -Unique).Count -ne 6 -or
                @($keys | Where-Object {
                    $_ -notin @("quote","collector","annotator","api","sync","broadcast")
                }).Count) {
                return New-ReleaseWindowsArtifactObservation `
                    -IdentityResolution $IdentityResolution `
                    -Status "MISMATCH" -Reason "WINDOWS_LAUNCH_CONTRACT_INVALID"
            }
            return New-ReleaseWindowsArtifactObservation `
                -IdentityResolution $IdentityResolution `
                -Status "AVAILABLE" -Reason "REVISION_OWNED_LAUNCH_CONTRACT_AVAILABLE"
        }
        if ([string]$IdentityResolution.identity_kind -eq "NARROW_LEGACY") {
            return New-ReleaseWindowsArtifactObservation `
                -IdentityResolution $IdentityResolution `
                -Status "AVAILABLE" -Reason "KNOWN_NARROW_LEGACY_ADAPTER_AVAILABLE"
        }
        return New-ReleaseWindowsArtifactObservation `
            -IdentityResolution $IdentityResolution `
            -Status "UNAVAILABLE" -Reason "WINDOWS_LAUNCH_CONTRACT_UNAVAILABLE"
    } catch {
        return New-ReleaseWindowsArtifactObservation `
            -IdentityResolution $IdentityResolution `
            -Status "UNKNOWN" -Reason "WINDOWS_ARTIFACT_OBSERVATION_UNAVAILABLE"
    }
}

function Get-ReleaseActiveHealthObservation {
    $ownerStatus = if (Test-SingleProductionOwner) { "SINGLE_OWNER" } else { "INVALID" }
    $healthy = Test-CurrentBusinessRuntimeHealth
    $businessStatus = if ($healthy) { "HEALTHY" } else { "DEGRADED" }
    $businessReason = if ($healthy) {
        "CURRENT_RUNTIME_HEALTHY"
    } else { "LOCAL_API_OR_RUNTIME_HEALTH_FAILED" }
    [pscustomobject]@{
        status = if ($businessStatus -eq "HEALTHY" -and
            $ownerStatus -eq "SINGLE_OWNER") { "HEALTHY" } else { "DEGRADED" }
        reason = if ($ownerStatus -ne "SINGLE_OWNER") {
            "PRODUCTION_OWNER_UNIQUENESS_FAILED"
        } else {
            $businessReason
        }
        business_health_status = $businessStatus
        business_health_reason = $businessReason
        ownership_status = $ownerStatus
    }
}

function Get-ReleaseProviderRuntimeFacts {
    param(
        [Parameter(Mandatory = $true)][object]$PersistedState,
        [switch]$ForceProviderRefresh,
        [switch]$SkipProviderObservation
    )
    $schema = [string]$PersistedState.schema_version
    $previousResolution = Resolve-ReleaseRuntimeIdentity `
        $PersistedState.previous_stable $schema
    $freshRefreshBlocked = [bool]($ForceProviderRefresh -and
        $script:releaseProviderRefreshInFlight)
    $cacheOnly = [bool]($SkipProviderObservation -or
        $script:releaseProviderRefreshInFlight)
    if (-not $cacheOnly) { $script:releaseProviderRefreshInFlight = $true }
    try {
        $deploymentObservation = Get-ReleaseDeploymentProviderObservation `
            -ForceFresh:$ForceProviderRefresh -NoRefresh:$cacheOnly
        if ($freshRefreshBlocked) {
            $deploymentObservation = [pscustomobject]@{
                status = "UNKNOWN"; value = $null
                observed_at = [DateTimeOffset]::UtcNow.ToString("o")
                reason = "FRESH_PROVIDER_OBSERVATION_ALREADY_IN_FLIGHT"
            }
        }
        $deployment = $deploymentObservation.value
        $deploymentStatus = [string]$deploymentObservation.status
        $traffic = Get-ReleaseTrafficObservation -Deployment $deployment `
            -Previous $previousResolution.identity -Status $deploymentStatus
        $traffic | Add-Member -NotePropertyName provider_observed_at `
            -NotePropertyValue ([string]$deploymentObservation.observed_at) -Force
        $activeFact = [pscustomobject]@{
            status = "NOT_APPLICABLE"; requested_version_id = $null
            observed_version_id = $null; git_sha = $null; observed_at = $null
        }
        if ([string]$traffic.status -eq "AVAILABLE") {
            $activeProvider = Get-ReleaseExactVersionProviderObservation `
                -VersionId ([string]$traffic.version_id) `
                -ForceFresh:$ForceProviderRefresh -NoRefresh:$cacheOnly
            $activeVersion = $activeProvider.value
            if ([string]$activeProvider.status -eq "AVAILABLE" -and
                $activeVersion -and $activeVersion -isnot [System.Array] -and
                [string]$activeVersion.id -ceq [string]$traffic.version_id) {
                $traffic.git_sha = Get-ReleaseGitShaFromVersion -Version $activeVersion
            } else { $traffic.git_sha = $null }
            $traffic | Add-Member -NotePropertyName version_observed_at `
                -NotePropertyValue ([string]$activeProvider.observed_at) -Force
            $activeFact = [pscustomobject]@{
                status = [string]$activeProvider.status
                requested_version_id = [string]$traffic.version_id
                observed_version_id = if ($activeVersion -and
                    $activeVersion -isnot [System.Array]) {
                    [string]$activeVersion.id
                } else { $null }
                git_sha = [string]$traffic.git_sha
                observed_at = [string]$activeProvider.observed_at
            }
        }
        $previousWorker = if ($freshRefreshBlocked) {
            New-ReleaseWorkerArtifactObservation `
                -IdentityResolution $previousResolution -VersionDetails $null `
                -ProviderStatus "UNKNOWN"
        } else {
            Get-CloudflareRollbackArtifactObservation `
                -IdentityResolution $previousResolution `
                -ForceFresh:$ForceProviderRefresh -NoRefresh:$cacheOnly
        }
    } finally {
        if (-not $cacheOnly) { $script:releaseProviderRefreshInFlight = $false }
    }
    return [pscustomobject]@{
        schema_version = "control-center-provider-facts-v1"
        status = if ([string]$traffic.status -eq "AVAILABLE" -and
            [string]$activeFact.status -eq "AVAILABLE") { "AVAILABLE" } else { "UNKNOWN" }
        deployment_observation = [pscustomobject]@{
            status = [string]$deploymentObservation.status
            observed_at = [string]$deploymentObservation.observed_at
            reason = [string]$deploymentObservation.reason
        }
        active_worker_observation = $traffic
        active_exact_version_fact = $activeFact
        previous_worker_artifact = $previousWorker
    }
}

function Get-ReleaseLocalRuntimeFacts {
    param(
        [Parameter(Mandatory = $true)][object]$PersistedState,
        [Parameter(Mandatory = $true)][object]$PreviousIdentityResolution,
        [switch]$ReleaseLockOwnedByCaller
    )
    $runtime = Get-RuntimeCodeState
    $windowsObservation = if ($runtime -and $runtime.applied_revision) {
        [pscustomobject]@{
            status = "AVAILABLE"
            revision = [string]$runtime.applied_revision
        }
    } else { $null }
    $health = Get-ReleaseActiveHealthObservation
    $previousWindows = Get-ReleaseWindowsArtifactObservation `
        -IdentityResolution $PreviousIdentityResolution
    $bundle = Get-RuntimeControlBundleIdentity
    $bundleStatus = if ($bundle -and [bool]$bundle.exact_revision) {
        "AVAILABLE"
    } else { "UNAVAILABLE" }
    return [pscustomobject]@{
        schema_version = "control-center-local-runtime-facts-v1"
        active_windows_observation = $windowsObservation
        health_observation = $health
        previous_windows_artifact = $previousWindows
        control_bundle_status = $bundleStatus
        release_lock_active = [bool]((Test-Path -LiteralPath $releaseLockPath) -and
            -not $ReleaseLockOwnedByCaller)
    }
}

function New-UnknownReleaseProviderFacts {
    param([string]$Reason = "PROVIDER_OBSERVATION_UNKNOWN")
    [pscustomobject]@{
        schema_version = "control-center-provider-facts-v1"
        status = "UNKNOWN"
        deployment_observation = [pscustomobject]@{
            status = "UNKNOWN"; value = $null; observed_at = $null; reason = $Reason
        }
        active_worker_observation = [pscustomobject]@{
            status = "UNKNOWN"; version_id = $null; git_sha = $null
            traffic_percent = $null; previous_is_member = $false
            previous_membership_status = "UNKNOWN"; previous_traffic_percent = $null
        }
        active_exact_version_fact = [pscustomobject]@{
            status = "UNKNOWN"; requested_version_id = $null
            observed_version_id = $null; git_sha = $null; observed_at = $null
        }
        previous_worker_artifact = [pscustomobject]@{ status = "UNKNOWN"; reason = $Reason }
    }
}

function Join-ReleaseRuntimeFacts {
    param(
        [Parameter(Mandatory = $true)][object]$PersistedState,
        [Parameter(Mandatory = $true)][object]$ProviderFacts,
        [Parameter(Mandatory = $true)][object]$LocalFacts,
        [DateTimeOffset]$ObservedAt = [DateTimeOffset]::UtcNow
    )
    $schema = [string]$PersistedState.schema_version
    $committedResolution = Resolve-ReleaseRuntimeIdentity $PersistedState.stable $schema
    $previousResolution = Resolve-ReleaseRuntimeIdentity $PersistedState.previous_stable $schema
    $targetRaw = if ($PersistedState.transaction -and $PersistedState.transaction.target) {
        $PersistedState.transaction.target
    } else { $PersistedState.candidate }
    $targetResolution = Resolve-ReleaseRuntimeIdentity $targetRaw $schema
    $traffic = Get-ReleaseRuntimeProperty $ProviderFacts "active_worker_observation"
    if (-not $traffic) {
        $traffic = (New-UnknownReleaseProviderFacts).active_worker_observation
    }
    $previousWorker = Get-ReleaseRuntimeProperty $ProviderFacts "previous_worker_artifact"
    if (-not $previousWorker) {
        $previousWorker = [pscustomobject]@{
            status = "UNKNOWN"; reason = "PREVIOUS_WORKER_ARTIFACT_UNKNOWN"
        }
    }
    $windowsObservation = Get-ReleaseRuntimeProperty $LocalFacts "active_windows_observation"
    $health = Get-ReleaseRuntimeProperty $LocalFacts "health_observation"
    $previousWindows = Get-ReleaseRuntimeProperty $LocalFacts "previous_windows_artifact"
    if (-not $previousWindows) {
        $previousWindows = [pscustomobject]@{
            status = "UNKNOWN"; reason = "PREVIOUS_WINDOWS_ARTIFACT_UNKNOWN"
        }
    }
    $reverse = New-ReleaseReversePrecheck `
        -PreviousIdentity $previousResolution.identity `
        -CommittedIdentityStatus ([string]$committedResolution.status) `
        -PreviousIdentityStatus ([string]$previousResolution.status) `
        -WorkerArtifact $previousWorker -WindowsArtifact $previousWindows `
        -ControlBundleStatus ([string]$LocalFacts.control_bundle_status) `
        -TransactionActive ([bool]$PersistedState.transaction) `
        -ReleaseLockActive ([bool]$LocalFacts.release_lock_active) `
        -OwnershipStatus ([string]$health.ownership_status) `
        -ActiveObservationStatus $(if ([string]$traffic.status -eq "AVAILABLE" -and
            [string]$windowsObservation.status -eq "AVAILABLE") {
            "AVAILABLE"
        } else { "UNKNOWN" }) `
        -ActiveIdentityStatus $(if ($traffic.version_id -match
            '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -and
            $traffic.git_sha -match '^[0-9a-f]{40}$' -and
            $windowsObservation.revision -match '^[0-9a-f]{40}$') { "COMPLETE" } else { "INCOMPLETE" }) `
        -ActiveMatchesCommitted ([bool]($committedResolution.identity -and
            [string]$traffic.version_id -ceq [string]$committedResolution.identity.worker_version_id -and
            [string]$traffic.git_sha -ceq [string]$committedResolution.identity.git_sha -and
            [string]$windowsObservation.revision -ceq [string]$committedResolution.identity.windows_revision))
    $model = New-ReleaseRuntimeReadModel -PersistedState $PersistedState `
        -ActiveWorkerObservation $traffic -ActiveWindowsObservation $windowsObservation `
        -HealthObservation $health -PreviousWorkerArtifact $previousWorker `
        -PreviousWindowsArtifact $previousWindows -ReversePrecheck $reverse `
        -CommittedIdentityResolution $committedResolution `
        -PreviousIdentityResolution $previousResolution `
        -TargetIdentityResolution $targetResolution `
        -ObservedAt $ObservedAt
    $model | Add-Member -NotePropertyName local_facts `
        -NotePropertyValue $LocalFacts -Force
    return $model
}

function Get-CurrentReleaseRuntimeReadModel {
    param(
        [AllowNull()][object]$PersistedState = $null,
        [DateTimeOffset]$ObservedAt = [DateTimeOffset]::UtcNow,
        [switch]$ReleaseLockOwnedByCaller,
        [switch]$ForceProviderRefresh,
        [switch]$SkipProviderObservation
    )
    if (-not $PersistedState) { $PersistedState = Get-ReleaseControlState }
    if (-not $PersistedState) { return $null }
    $schema = [string]$PersistedState.schema_version
    $previousResolution = Resolve-ReleaseRuntimeIdentity `
        $PersistedState.previous_stable $schema
    $providerFacts = Get-ReleaseProviderRuntimeFacts -PersistedState $PersistedState `
        -ForceProviderRefresh:$ForceProviderRefresh `
        -SkipProviderObservation:$SkipProviderObservation
    $localFacts = Get-ReleaseLocalRuntimeFacts -PersistedState $PersistedState `
        -PreviousIdentityResolution $previousResolution `
        -ReleaseLockOwnedByCaller:$ReleaseLockOwnedByCaller
    return Join-ReleaseRuntimeFacts -PersistedState $PersistedState `
        -ProviderFacts $providerFacts -LocalFacts $localFacts -ObservedAt $ObservedAt
}
