$workerCpuPolicyVersion = "worker-cpu-policy-v1"
$workerCpuQualificationVersion = "worker-cpu-qualification-v1"
$workerCpuEvidenceRoot = Join-Path $runtimeForwardRoot "worker-cpu-evidence"

function Get-WorkerCpuEvidencePolicy {
    [pscustomobject][ordered]@{
        version = $workerCpuPolicyVersion
        required_observed_acceptance = 10
        reserve_acceptance = 2
        deficit_top_up_acceptance = 4
        headroom_top_up_acceptance = 10
        maximum_deficit_top_ups = 1
        maximum_headroom_top_ups = 1
        active_read_backoff_seconds = @(5, 10, 20, 30, 45, 60)
        maximum_background_reads = 4
        background_read_interval_seconds = 900
        pass_p95_ms = 6
        pass_p99_ms = 8
        hard_max_ms = 10
        omission_max_ms = 8
    }
}

function Get-WorkerCpuCanonicalDigest {
    param([Parameter(Mandatory = $true)][object]$Value)
    $json = if ($null -eq $Value) { "null" } else { $Value | ConvertTo-Json -Compress -Depth 30 }
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
    finally { $algorithm.Dispose() }
}

function Get-WorkerCpuRunRoot {
    param([Parameter(Mandatory = $true)][string]$ValidationRun)
    if ($ValidationRun -notmatch '^[0-9a-fA-F-]{36}$') { throw "WORKER_CPU_RUN_ID_INVALID" }
    return Join-Path $workerCpuEvidenceRoot $ValidationRun.ToLowerInvariant()
}

function Write-WorkerCpuAtomicJson {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$Value
    )
    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Add-WorkerCpuLedgerEvent {
    param(
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][string]$Event,
        [Parameter(Mandatory = $true)][object]$Detail
    )
    $root = Get-WorkerCpuRunRoot -ValidationRun $ValidationRun
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    [pscustomobject][ordered]@{
        occurred_at = [DateTimeOffset]::UtcNow.ToString("o")
        event = $Event
        detail = $Detail
    } | ConvertTo-Json -Compress -Depth 30 |
        Add-Content -LiteralPath (Join-Path $root "directed-ledger.jsonl") -Encoding UTF8
}

function New-WorkerCpuRequestPlan {
    param(
        [Parameter(Mandatory = $true)][object[]]$Routes,
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][string]$CandidateWorkerVersion,
        [Parameter(Mandatory = $true)][string]$QualificationKey,
        [Parameter(Mandatory = $true)][string]$ValidationPlanDigest,
        [Parameter(Mandatory = $true)][object]$FixtureDigestSet
    )
    $policy = Get-WorkerCpuEvidencePolicy
    $requests = @()
    foreach ($route in $Routes) {
        $warmupCount = [int]$route.warmup_samples
        for ($index = 0; $index -lt $warmupCount; $index++) {
            $requests += [pscustomobject][ordered]@{
                request_id = [guid]::NewGuid().ToString()
                family = [string]$route.family
                scenario = [string]$route.scenario
                method = [string]$route.method
                path = [string]$route.path
                request_query = [string]$route.request_query
                fixture = [string]$route.fixture
                phase = "warmup"
                sample_kind = "warmup"
                planned_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
        }
        $required = [int]$policy.required_observed_acceptance
        $reserve = [int]$policy.reserve_acceptance
        for ($index = 0; $index -lt ($required + $reserve); $index++) {
            $requests += [pscustomobject][ordered]@{
                request_id = [guid]::NewGuid().ToString()
                family = [string]$route.family
                scenario = [string]$route.scenario
                method = [string]$route.method
                path = [string]$route.path
                request_query = [string]$route.request_query
                fixture = [string]$route.fixture
                phase = "acceptance"
                sample_kind = if ($index -lt $required) { "required" } else { "reserve" }
                planned_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
        }
    }
    $plan = [pscustomobject][ordered]@{
        schema_version = "worker-directed-ledger-v1"
        validation_run = $ValidationRun
        candidate_worker_version = $CandidateWorkerVersion
        qualification_key = $QualificationKey
        policy_version = [string]$policy.version
        validation_plan_digest = $ValidationPlanDigest
        fixture_digest_set = $FixtureDigestSet
        requests = @($requests)
        request_universe_digest = Get-WorkerCpuCanonicalDigest -Value @($requests)
    }
    $path = Join-Path (Get-WorkerCpuRunRoot -ValidationRun $ValidationRun) "plan.json"
    Write-WorkerCpuAtomicJson -Path $path -Value $plan
    Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun -Event "REQUEST_PLAN_FROZEN" -Detail ([pscustomobject]@{
        request_count = $requests.Count
        request_universe_digest = $plan.request_universe_digest
        qualification_key = $QualificationKey
    })
    return $plan
}

function New-WorkerDirectedCorrectnessPlan {
    param(
        [Parameter(Mandatory = $true)][object[]]$Routes,
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][string]$CandidateWorkerVersion,
        [Parameter(Mandatory = $true)][string]$QualificationKey,
        [Parameter(Mandatory = $true)][string]$ValidationPlanDigest,
        [Parameter(Mandatory = $true)][object]$FixtureDigestSet
    )
    $requests = @($Routes | ForEach-Object {
        [pscustomobject][ordered]@{
            request_id = [guid]::NewGuid().ToString()
            family = [string]$_.family; scenario = [string]$_.scenario
            method = [string]$_.method; path = [string]$_.path
            request_query = [string]$_.request_query; fixture = [string]$_.fixture
            phase = "acceptance"; sample_kind = "directed_correctness"
            planned_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
    })
    $plan = [pscustomobject][ordered]@{
        schema_version = "worker-directed-ledger-v1"
        validation_run = $ValidationRun; candidate_worker_version = $CandidateWorkerVersion
        qualification_key = $QualificationKey; policy_version = $workerCpuPolicyVersion
        validation_plan_digest = $ValidationPlanDigest; fixture_digest_set = $FixtureDigestSet
        requests = $requests; request_universe_digest = Get-WorkerCpuCanonicalDigest -Value $requests
    }
    Write-WorkerCpuAtomicJson -Path (Join-Path (Get-WorkerCpuRunRoot -ValidationRun $ValidationRun) "plan.json") -Value $plan
    Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun -Event "REUSED_QUALIFICATION_DIRECTED_PLAN_FROZEN" `
        -Detail ([pscustomobject]@{ request_count=$requests.Count; qualification_key=$QualificationKey; request_universe_digest=$plan.request_universe_digest })
    return $plan
}

function Add-WorkerCpuPlannedRequests {
    param(
        [Parameter(Mandatory = $true)][object]$Plan,
        [Parameter(Mandatory = $true)][object[]]$Groups,
        [ValidateSet("deficit_top_up", "headroom_top_up")]
        [Parameter(Mandatory = $true)][string]$SampleKind,
        [Parameter(Mandatory = $true)][int]$CountPerGroup
    )
    $newRequests = @()
    foreach ($group in $Groups) {
        for ($index = 0; $index -lt $CountPerGroup; $index++) {
            $newRequests += [pscustomobject][ordered]@{
                request_id = [guid]::NewGuid().ToString()
                family = [string]$group.family
                scenario = [string]$group.scenario
                method = [string]$group.method
                path = [string]$group.path
                request_query = [string]$group.request_query
                fixture = [string]$group.fixture
                phase = "acceptance"
                sample_kind = $SampleKind
                planned_at = [DateTimeOffset]::UtcNow.ToString("o")
            }
        }
    }
    $Plan.requests = @($Plan.requests) + @($newRequests)
    $Plan.request_universe_digest = Get-WorkerCpuCanonicalDigest -Value @($Plan.requests)
    $path = Join-Path (Get-WorkerCpuRunRoot -ValidationRun ([string]$Plan.validation_run)) "plan.json"
    Write-WorkerCpuAtomicJson -Path $path -Value $Plan
    Add-WorkerCpuLedgerEvent -ValidationRun ([string]$Plan.validation_run) `
        -Event "TARGETED_REQUESTS_PLANNED" -Detail ([pscustomobject]@{
            sample_kind = $SampleKind
            groups = @($Groups | ForEach-Object { "$([string]$_.family)|$([string]$_.scenario)" })
            request_ids = @($newRequests | ForEach-Object { [string]$_.request_id })
            request_universe_digest = $Plan.request_universe_digest
        })
    return @($newRequests)
}

function Add-WorkerCpuDirectResponse {
    param(
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][object]$Request,
        [Parameter(Mandatory = $true)][object]$Response
    )
    $bounded = [pscustomobject][ordered]@{
        request_id = [string]$Request.request_id
        family = [string]$Request.family
        scenario = [string]$Request.scenario
        phase = [string]$Request.phase
        sample_kind = [string]$Request.sample_kind
        expected_worker_version = [string]$Response.requested_worker_version
        observed_worker_version = [string]$Response.observed_worker_version
        observed_git_sha = [string]$Response.observed_git_sha
        http_status = [int]$Response.status
        passed = [bool]$Response.passed
        reason = [string]$Response.reason
        route = [string]$Response.route
        resource = [string]$Response.resource
        mutated = if ($Response.PSObject.Properties['mutated']) { [bool]$Response.mutated } else { $false }
        d1_operations = [string]$Response.d1_operations
        request_bytes = [string]$Response.request_bytes
        response_bytes = [string]$Response.response_bytes
        response_content_digest = [string]$Response.response_content_digest
        response_receipt = Get-WorkerCpuCanonicalDigest -Value ([pscustomobject][ordered]@{
            request_id = [string]$Request.request_id
            status = [int]$Response.status
            passed = [bool]$Response.passed
            reason = [string]$Response.reason
            worker = [string]$Response.observed_worker_version
            git = [string]$Response.observed_git_sha
            route = [string]$Response.route
            resource = [string]$Response.resource
            d1_operations = [string]$Response.d1_operations
            request_bytes = [string]$Response.request_bytes
            response_bytes = [string]$Response.response_bytes
            response_content_digest = [string]$Response.response_content_digest
        })
        completed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun -Event "DIRECT_RESPONSE_RECORDED" -Detail $bounded
    return $bounded
}

function Add-WorkerCpuRequestSend {
    param(
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][object]$Request,
        [Parameter(Mandatory = $true)][string]$CandidateWorkerVersion,
        [Parameter(Mandatory = $true)][string]$QualificationKey
    )
    $sentAt = [DateTimeOffset]::UtcNow.ToString("o")
    Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun -Event "REQUEST_SEND_STARTED" `
        -Detail ([pscustomobject][ordered]@{
            request_id=[string]$Request.request_id; family=[string]$Request.family
            scenario=[string]$Request.scenario; phase=[string]$Request.phase
            sample_kind=[string]$Request.sample_kind; send_timestamp=$sentAt
            candidate_worker_version=$CandidateWorkerVersion; qualification_key=$QualificationKey
        })
    return $sentAt
}

function Merge-WorkerCpuProviderEvidence {
    param(
        [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$AcceptedRecords,
        [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$NewRecords,
        [Parameter(Mandatory = $true)][object[]]$ExpectedRequests,
        [Parameter(Mandatory = $true)][string]$CandidateWorkerVersion,
        [Parameter(Mandatory = $true)][string]$ValidationRun
    )
    $expected = @{}
    foreach ($request in $ExpectedRequests) { $expected[[string]$request.request_id] = $request }
    $events = @{}
    $requests = @{}
    foreach ($record in @($AcceptedRecords) + @($NewRecords)) {
        $requestId = [string]$record.request_id
        $eventId = [string]$record.event_id
        if (-not $expected.ContainsKey($requestId) -or -not $eventId -or
            [string]$record.worker_version_id -ne $CandidateWorkerVersion -or
            [string]$record.validation_run -ne $ValidationRun -or
            [string]$record.validation_phase -ne "acceptance") {
            throw "WORKER_CPU_PROVIDER_EVIDENCE_CONTAMINATED"
        }
        $digest = Get-WorkerCpuCanonicalDigest -Value $record
        if ($events.ContainsKey($eventId) -and [string]$events[$eventId].digest -ne $digest) {
            throw "WORKER_CPU_PROVIDER_EVENT_CONFLICT"
        }
        if ($requests.ContainsKey($requestId) -and [string]$requests[$requestId] -ne $eventId) {
            throw "WORKER_CPU_PROVIDER_REQUEST_DUPLICATED"
        }
        if (-not $events.ContainsKey($eventId)) {
            $events[$eventId] = [pscustomobject]@{ digest=$digest; record=$record }
            $requests[$requestId] = $eventId
        }
    }
    return @($events.Keys | Sort-Object | ForEach-Object { $events[$_].record })
}

function Get-WorkerCpuMetricsFromRecords {
    param([Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$Records)
    if ($Records.Count -eq 0) { return $null }
    $cpu = [double[]]@($Records | ForEach-Object { [double]$_.cpu_ms })
    [pscustomobject][ordered]@{
        invocations = $Records.Count
        p95_cpu_ms = Get-ReleaseTelemetryPercentile -Values $cpu -Percentile 0.95
        p99_cpu_ms = Get-ReleaseTelemetryPercentile -Values $cpu -Percentile 0.99
        max_cpu_ms = [double](($cpu | Measure-Object -Maximum).Maximum)
        responses_5xx = @($Records | Where-Object { [int]$_.status -ge 500 }).Count
        responses_1102 = @($Records | Where-Object { [string]$_.outcome -in @("exceededCpu", "exceededMemory") }).Count
        exceeded_cpu = @($Records | Where-Object { [string]$_.outcome -eq "exceededCpu" }).Count
        exceeded_memory = @($Records | Where-Object { [string]$_.outcome -eq "exceededMemory" }).Count
    }
}

function Get-WorkerCpuQualificationDecision {
    param(
        [Parameter(Mandatory = $true)][object[]]$ExpectedRequests,
        [Parameter(Mandatory = $true)][AllowNull()][AllowEmptyCollection()][object[]]$ProviderRecords,
        [bool]$DirectResponsesComplete = $true,
        [object]$AggregateEvidence = $null,
        [bool]$RecoveryBudgetExhausted = $false
    )
    $policy = Get-WorkerCpuEvidencePolicy
    if (-not $DirectResponsesComplete) {
        return [pscustomobject]@{ state="HARD_FAILURE"; reason="DIRECTED_REQUEST_LEDGER_INCOMPLETE" }
    }
    $expectedById = @{}
    foreach ($request in $ExpectedRequests) { $expectedById[[string]$request.request_id] = $request }
    $acceptance = @($ExpectedRequests | Where-Object { [string]$_.phase -eq "acceptance" })
    $observedIds = @($ProviderRecords | ForEach-Object { [string]$_.request_id })
    $missingIds = @($acceptance | Where-Object { [string]$_.request_id -notin $observedIds } |
        ForEach-Object { [string]$_.request_id })
    $global = Get-WorkerCpuMetricsFromRecords -Records $ProviderRecords
    if ($global -and ($global.max_cpu_ms -ge [double]$policy.hard_max_ms -or
        $global.responses_5xx -gt 0 -or $global.responses_1102 -gt 0 -or
        $global.exceeded_cpu -gt 0 -or $global.exceeded_memory -gt 0)) {
        return [pscustomobject]@{ state="HARD_FAILURE"; reason="WORKER_CPU_OR_PLATFORM_HARD_FAILURE"; global=$global; missing_request_ids=$missingIds }
    }
    if ($AggregateEvidence) {
        $aggregateHardError = @("responses_5xx", "responses_1102", "exceeded_cpu", "exceeded_memory") |
            Where-Object { $null -ne $AggregateEvidence.PSObject.Properties[$_] -and [int]$AggregateEvidence.$_ -gt 0 }
        if ([int]$AggregateEvidence.invocations -lt $ProviderRecords.Count -or
            [int]$AggregateEvidence.invocations -gt $acceptance.Count -or
            @($aggregateHardError).Count -gt 0) {
            return [pscustomobject]@{ state="HARD_FAILURE"; reason="PROVIDER_CORROBORATION_CONTRADICTION"; global=$global; aggregate=$AggregateEvidence }
        }
    }
    $groups = @()
    $deficits = @()
    $reviews = @()
    foreach ($group in @($acceptance | Group-Object { "$([string]$_.family)|$([string]$_.scenario)" })) {
        $first = $group.Group[0]
        $records = @($ProviderRecords | Where-Object {
            $request = $expectedById[[string]$_.request_id]
            [string]$request.family -eq [string]$first.family -and
                [string]$request.scenario -eq [string]$first.scenario
        })
        $metrics = Get-WorkerCpuMetricsFromRecords -Records $records
        $groupExpectedIds = @($group.Group | ForEach-Object { [string]$_.request_id })
        $groupObservedIds = @($records | ForEach-Object { [string]$_.request_id })
        $groupMissing = @($groupExpectedIds | Where-Object { $_ -notin $groupObservedIds })
        $row = [pscustomobject][ordered]@{
            family = [string]$first.family
            scenario = [string]$first.scenario
            method = [string]$first.method
            path = [string]$first.path
            request_query = [string]$first.request_query
            fixture = [string]$first.fixture
            sent = $groupExpectedIds.Count
            observed = $records.Count
            required = [int]$policy.required_observed_acceptance
            reserve = [int]$policy.reserve_acceptance
            missing = $groupMissing.Count
            metrics = $metrics
        }
        $groups += $row
        if ($records.Count -lt [int]$policy.required_observed_acceptance) { $deficits += $row; continue }
        if ($metrics.p95_cpu_ms -gt [double]$policy.pass_p95_ms -or
            $metrics.p99_cpu_ms -gt [double]$policy.pass_p99_ms -or
            $metrics.max_cpu_ms -ge [double]$policy.hard_max_ms -or
            ($groupMissing.Count -gt 0 -and $metrics.max_cpu_ms -gt [double]$policy.omission_max_ms)) {
            $reviews += $row
        }
    }
    if ($deficits.Count -gt 0) {
        return [pscustomobject]@{
            state = if ($RecoveryBudgetExhausted) { "PROVIDER_EVIDENCE_INSUFFICIENT" } else { "PROVIDER_EVIDENCE_PENDING" }
            reason = "OBSERVED_FAMILY_QUOTA_DEFICIT"
            groups = $groups; deficient_groups = $deficits; review_groups = $reviews
            global = $global; missing_request_ids = $missingIds
        }
    }
    if ($reviews.Count -gt 0 -or ($global -and ($global.p95_cpu_ms -gt [double]$policy.pass_p95_ms -or
        $global.p99_cpu_ms -gt [double]$policy.pass_p99_ms -or $global.max_cpu_ms -ge [double]$policy.hard_max_ms))) {
        return [pscustomobject]@{ state="HEADROOM_REVIEW"; reason="WORKER_CPU_HEADROOM_REVIEW_REQUIRED"; groups=$groups; review_groups=$reviews; global=$global; missing_request_ids=$missingIds }
    }
    $state = if ($missingIds.Count -gt 0) { "QUALIFIED_WITH_PROVIDER_OMISSION" } else { "QUALIFIED" }
    return [pscustomobject]@{ state=$state; reason=$null; groups=$groups; global=$global; missing_request_ids=$missingIds; aggregate=$AggregateEvidence }
}

function Write-WorkerCpuProviderEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Records,
        [Parameter(Mandatory = $true)][object]$RecoveryState
    )
    $root = Get-WorkerCpuRunRoot -ValidationRun $ValidationRun
    $payload = [pscustomobject][ordered]@{
        schema_version = "worker-provider-evidence-v1"
        validation_run = $ValidationRun
        records = @($Records)
        observed_universe_digest = Get-ReleaseTelemetryDigest -Records @($Records)
        recovery = $RecoveryState
        updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-WorkerCpuAtomicJson -Path (Join-Path $root "provider-evidence.json") -Value $payload
    Add-WorkerCpuLedgerEvent -ValidationRun $ValidationRun -Event "PROVIDER_EVIDENCE_UNIONED" -Detail ([pscustomobject]@{
        observed = $Records.Count
        digest = $payload.observed_universe_digest
        active_reads = [int]$RecoveryState.active_reads
        background_reads = [int]$RecoveryState.background_reads
    })
    return $payload
}

function Read-WorkerCpuRunArtifact {
    param([Parameter(Mandatory = $true)][string]$ValidationRun, [Parameter(Mandatory = $true)][string]$Name)
    $path = Join-Path (Get-WorkerCpuRunRoot -ValidationRun $ValidationRun) $Name
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    return Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
}

function Get-WorkerCpuDirectResponseReceipts {
    param([Parameter(Mandatory = $true)][string]$ValidationRun)
    $path = Join-Path (Get-WorkerCpuRunRoot -ValidationRun $ValidationRun) "directed-ledger.jsonl"
    if (-not (Test-Path -LiteralPath $path)) { return @() }
    $receipts = @()
    foreach ($line in @(Get-Content -LiteralPath $path -Encoding UTF8)) {
        if (-not $line) { continue }
        $entry = $line | ConvertFrom-ReleaseControlJson
        if ([string]$entry.event -eq "DIRECT_RESPONSE_RECORDED") { $receipts += $entry.detail }
    }
    $duplicates = @($receipts | Group-Object request_id | Where-Object { $_.Count -ne 1 })
    if ($duplicates.Count -gt 0) { throw "WORKER_CPU_DIRECT_RESPONSE_LEDGER_DUPLICATED" }
    return @($receipts)
}

function Get-WorkerCpuGitTreeDigest {
    param(
        [Parameter(Mandatory = $true)][string]$Revision,
        [Parameter(Mandatory = $true)][string[]]$Paths
    )
    $arguments = @("-C", $repositoryRoot, "ls-tree", "-r", "--full-tree", $Revision, "--") + $Paths
    $result = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments $arguments
    if ($result.exit_code -ne 0 -or -not $result.stdout) { throw "WORKER_CPU_QUALIFICATION_TREE_UNAVAILABLE" }
    $rows = @([string]$result.stdout -split "`n" | Where-Object { $_ } | Sort-Object)
    return Get-WorkerCpuCanonicalDigest -Value $rows
}

function Get-WorkerVersionQualificationMetadata {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $wrangler = Join-Path $repositoryRoot "web\node_modules\.bin\wrangler.cmd"
    if (-not (Test-Path -LiteralPath $wrangler)) { throw "WORKER_CPU_WRANGLER_UNAVAILABLE" }
    $result = Invoke-Utf8NativeProcess -FilePath $wrangler -Arguments @(
        "versions", "view", [string]$Candidate.worker_version_id,
        "--name", $workerName, "--json"
    ) -WorkingDirectory (Join-Path $repositoryRoot "web")
    if ($result.exit_code -ne 0 -or -not $result.stdout) { throw "WORKER_CPU_VERSION_METADATA_UNAVAILABLE" }
    $version = [string]$result.stdout | ConvertFrom-ReleaseControlJson
    $message = [string]$version.annotations.'workers/message'
    if ([string]$version.id -ne [string]$Candidate.worker_version_id -or
        $message -notmatch ("release:{0}(?:\s|$)" -f [regex]::Escape([string]$Candidate.git_sha)) -or
        -not $version.resources.script.etag) {
        throw "WORKER_CPU_VERSION_METADATA_MISMATCH"
    }
    $bindings = @($version.resources.bindings | ForEach-Object {
        [pscustomobject][ordered]@{
            name = [string]$_.name
            type = [string]$_.type
            resource = if ($_.database_id) { [string]$_.database_id } elseif ($_.index_name) { [string]$_.index_name } else { "" }
        }
    } | Sort-Object name, type, resource)
    return [pscustomobject][ordered]@{
        worker_version_id = [string]$version.id
        executable_bundle_etag = [string]$version.resources.script.etag
        compatibility_date = [string]$version.resources.script_runtime.compatibility_date
        compatibility_flags = @($version.resources.script_runtime.compatibility_flags | Sort-Object)
        assets = $version.resources.script_runtime.assets
        bindings = $bindings
        provenance_message = $message
    }
}

function Get-WorkerCpuQualificationIdentity {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan,
        [Parameter(Mandatory = $true)][AllowNull()][object]$FixtureDigestSet
    )
    $policy = Get-WorkerCpuEvidencePolicy
    $version = Get-WorkerVersionQualificationMetadata -Candidate $Candidate
    $runtimeTree = Get-WorkerCpuGitTreeDigest -Revision ([string]$Candidate.git_sha) -Paths @(
        "web/app/api", "web/app/_lib", "web/worker", "web/vite.config.ts",
        "web/package.json", "web/package-lock.json"
    )
    $configTree = Get-WorkerCpuGitTreeDigest -Revision ([string]$Candidate.git_sha) -Paths @("web/wrangler.jsonc")
    $manifestTree = Get-WorkerCpuGitTreeDigest -Revision ([string]$Candidate.git_sha) -Paths @("web/worker-validation-manifest.json")
    $schemaTree = Get-WorkerCpuGitTreeDigest -Revision ([string]$Candidate.git_sha) -Paths @("web/drizzle")
    $fixtureBuilderTree = Get-WorkerCpuGitTreeDigest -Revision ([string]$Candidate.git_sha) -Paths @(
        "scripts/build_release_validation_fixtures.py", "xauusd_forecaster/dashboard_payloads.py",
        "xauusd_forecaster/news_projection.py", "tests/fixtures/release_validation"
    )
    $shape = @(@($RoutePlan.worker_reads) + @($RoutePlan.worker_writes) | ForEach-Object {
        [pscustomobject][ordered]@{
            family = [string]$_.family; scenario = [string]$_.scenario
            method = [string]$_.method; path = [string]$_.path
            request_query = [string]$_.request_query; strategy = [string]$_.strategy
            fixture = [string]$_.fixture; criticality = [string]$_.criticality
        }
    } | Sort-Object family, scenario, method, path)
    $fields = [pscustomobject][ordered]@{
        version = $workerCpuQualificationVersion
        runtime_configuration_digest = Get-WorkerCpuCanonicalDigest -Value ([pscustomobject][ordered]@{
            compatibility_date = $version.compatibility_date
            compatibility_flags = $version.compatibility_flags
            assets = $version.assets
            bindings = $version.bindings
            checked_config_tree = $configTree
        })
        route_implementation_and_toolchain_digest = $runtimeTree
        worker_validation_manifest_digest = $manifestTree
        fixture_digest_set_digest = Get-WorkerCpuCanonicalDigest -Value $FixtureDigestSet
        fixture_builder_digest = $fixtureBuilderTree
        cpu_policy_digest = Get-WorkerCpuCanonicalDigest -Value $policy
        d1_schema_capability_digest = $schemaTree
        production_data_shape_contract_digest = Get-WorkerCpuCanonicalDigest -Value $shape
    }
    return [pscustomobject][ordered]@{
        version = $workerCpuQualificationVersion
        key = Get-WorkerCpuCanonicalDigest -Value $fields
        fields = $fields
        candidate_worker_version = [string]$Candidate.worker_version_id
        candidate_git_sha = [string]$Candidate.git_sha
        exact_candidate_binding = [pscustomobject][ordered]@{
            worker_version_id = [string]$version.worker_version_id
            executable_bundle_etag = [string]$version.executable_bundle_etag
            provenance_message = [string]$version.provenance_message
        }
    }
}

function Get-WorkerCpuFixtureDigestSet {
    param([Parameter(Mandatory = $true)][string]$FixtureRoot)
    if (-not (Test-Path -LiteralPath $FixtureRoot -PathType Container)) {
        throw "WORKER_CPU_FIXTURE_ROOT_UNAVAILABLE"
    }
    $items = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $FixtureRoot -File | Sort-Object Name)) {
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try {
            $stream = [IO.File]::OpenRead($file.FullName)
            try { $digest = ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace("-", "").ToLowerInvariant() }
            finally { $stream.Dispose() }
        } finally { $algorithm.Dispose() }
        $items += [pscustomobject][ordered]@{ name=$file.Name; bytes=[long]$file.Length; sha256=$digest }
    }
    if ($items.Count -eq 0) { throw "WORKER_CPU_FIXTURE_SET_EMPTY" }
    return @($items)
}

function Get-WorkerCpuValidationPlanDigest {
    param([Parameter(Mandatory = $true)][object]$RoutePlan)
    $routes = @(@($RoutePlan.worker_reads) + @($RoutePlan.worker_writes) | ForEach-Object {
        [pscustomobject][ordered]@{
            family=[string]$_.family; scenario=[string]$_.scenario
            method=[string]$_.method; path=[string]$_.path; request_query=[string]$_.request_query
            boundary=[string]$_.boundary; strategy=[string]$_.strategy; fixture=[string]$_.fixture
            warmup_samples=[int]$_.warmup_samples
        }
    } | Sort-Object family, scenario, method, path)
    return Get-WorkerCpuCanonicalDigest -Value ([pscustomobject][ordered]@{
        policy = Get-WorkerCpuEvidencePolicy
        routes = $routes
    })
}

function Get-WorkerCpuQualificationReceipt {
    param([Parameter(Mandatory = $true)][string]$QualificationKey)
    if ($QualificationKey -notmatch '^[0-9a-f]{64}$') { throw "WORKER_CPU_QUALIFICATION_KEY_INVALID" }
    $path = Join-Path (Join-Path $workerCpuEvidenceRoot "qualifications") "$QualificationKey.json"
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
    $core = [pscustomobject][ordered]@{
        schema_version = [string]$receipt.schema_version
        qualification_key = [string]$receipt.qualification_key
        qualification_fields = $receipt.qualification_fields
        source_worker_version = [string]$receipt.source_worker_version
        source_git_sha = [string]$receipt.source_git_sha
        source_executable_bundle_etag = [string]$receipt.source_executable_bundle_etag
        validation_run = [string]$receipt.validation_run
        outcome = [string]$receipt.outcome
        cpu_evidence = $receipt.cpu_evidence
        qualified_at = [string]$receipt.qualified_at
    }
    if ([string]$receipt.qualification_key -ne $QualificationKey -or
        [string]$receipt.receipt_digest -ne (Get-WorkerCpuCanonicalDigest -Value $core) -or
        [string]$receipt.outcome -notin @("QUALIFIED", "QUALIFIED_WITH_PROVIDER_OMISSION")) {
        throw "WORKER_CPU_QUALIFICATION_RECEIPT_INVALID"
    }
    return $receipt
}

function Write-WorkerCpuQualificationReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Qualification,
        [Parameter(Mandatory = $true)][string]$ValidationRun,
        [Parameter(Mandatory = $true)][object]$Decision
    )
    if ([string]$Decision.state -notin @("QUALIFIED", "QUALIFIED_WITH_PROVIDER_OMISSION")) {
        throw "WORKER_CPU_UNQUALIFIED_RECEIPT_FORBIDDEN"
    }
    $core = [pscustomobject][ordered]@{
        schema_version = "worker-cpu-qualification-receipt-v1"
        qualification_key = [string]$Qualification.key
        qualification_fields = $Qualification.fields
        source_worker_version = [string]$Qualification.candidate_worker_version
        source_git_sha = [string]$Qualification.candidate_git_sha
        source_executable_bundle_etag = [string]$Qualification.exact_candidate_binding.executable_bundle_etag
        validation_run = $ValidationRun
        outcome = [string]$Decision.state
        cpu_evidence = $Decision
        qualified_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $receipt = $core.PSObject.Copy()
    $receipt | Add-Member receipt_digest (Get-WorkerCpuCanonicalDigest -Value $core)
    $path = Join-Path (Join-Path $workerCpuEvidenceRoot "qualifications") "$([string]$Qualification.key).json"
    Write-WorkerCpuAtomicJson -Path $path -Value $receipt
    return $receipt
}

function New-ReusedWorkerCpuEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Receipt,
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Qualification
    )
    $decision = $Receipt.cpu_evidence
    $global = $decision.global
    return [pscustomobject]@{
        source = "IMMUTABLE_WORKER_CPU_QUALIFICATION_RECEIPT"
        evidence_class = "CONTROLLED_EXACT"
        qualification_state = [string]$decision.state
        qualification_mode = "CPU_QUALIFICATION_REUSED"
        qualification_key = [string]$Receipt.qualification_key
        qualification_receipt_digest = [string]$Receipt.receipt_digest
        source_worker_version = [string]$Receipt.source_worker_version
        source_git_sha = [string]$Receipt.source_git_sha
        source_executable_bundle_etag = [string]$Receipt.source_executable_bundle_etag
        current_executable_bundle_etag = [string]$Qualification.exact_candidate_binding.executable_bundle_etag
        worker_version_id = [string]$Candidate.worker_version_id
        candidate_git_sha = [string]$Candidate.git_sha
        validation_run = [string]$Receipt.validation_run
        routes = @($decision.groups | ForEach-Object {
            $metric = $_.metrics
            [pscustomobject]@{
                route_family=$_.family; scenario=$_.scenario; invocations=$_.observed
                sent=$_.sent; required=$_.required; reserve=$_.reserve; missing=$_.missing
                p95_cpu_ms=$metric.p95_cpu_ms; p99_cpu_ms=$metric.p99_cpu_ms; max_cpu_ms=$metric.max_cpu_ms
                responses_5xx=$metric.responses_5xx; responses_1102=$metric.responses_1102
                exceeded_cpu=$metric.exceeded_cpu; exceeded_memory=$metric.exceeded_memory
            }
        })
        provider_corroboration = $decision.aggregate
        global = $global
        invocations = $global.invocations
        max_cpu_ms = $global.max_cpu_ms; p95_cpu_ms = $global.p95_cpu_ms; p99_cpu_ms = $global.p99_cpu_ms
        exceeded_cpu = $global.exceeded_cpu; exceeded_memory = $global.exceeded_memory
        responses_1102 = $global.responses_1102; responses_5xx = $global.responses_5xx
        gate_state = "PASSED"; passed = $true
    }
}
