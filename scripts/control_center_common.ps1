# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
function ConvertFrom-ReleaseControlJson {
    param(
        [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
        [AllowEmptyString()][string]$Json
    )
    process {
        # PowerShell 7.5+ otherwise turns ISO JSON strings into DateTime values,
        # while Windows PowerShell preserves the source strings. Immutable
        # release evidence must have identical values under both runtimes.
        if ($convertFromJsonSupportsDateKind) {
            return $Json | ConvertFrom-Json -DateKind String -ErrorAction Stop
        }
        return $Json | ConvertFrom-Json -ErrorAction Stop
    }
}

function Get-UserEnvironmentValue {
    param([Parameter(Mandatory = $true)][string]$Name)
    [Environment]::GetEnvironmentVariable($Name, "User")
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
                ConvertFrom-ReleaseControlJson
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

function Get-CollectorSecret {
    param([Parameter(Mandatory = $true)][string]$Name)
    $userValue = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($userValue) { return $userValue.Trim() }
    if (-not (Test-Path -LiteralPath $collectorSecretsPath)) { return "" }
    try {
        $secrets = Get-Content -LiteralPath $collectorSecretsPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $property = $secrets.PSObject.Properties[$Name]
        if ($property -and $property.Value) { return ([string]$property.Value).Trim() }
    } catch {
        return ""
    }
    return ""
}

function Get-BusinessRuntimeRevision {
    param([string]$CodeRoot = $moduleRoot)
    try {
        $revision = (& git.exe -C $CodeRoot rev-parse HEAD 2>$null).Trim()
        if ($LASTEXITCODE -eq 0 -and $revision -match '^[0-9a-f]{40}$') {
            return $revision
        }
    } catch {}
    throw "BUSINESS_RUNTIME_REVISION_UNAVAILABLE"
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
        lifecycle_phase = if ($Candidate) { "PREPARE" } else { "STABLE" }
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

function ConvertTo-NativeProcessArgument {
    param([AllowEmptyString()][string]$Argument)
    if ($Argument -and $Argument -notmatch '[\s"]') { return $Argument }
    $escaped = [regex]::Replace([string]$Argument, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function ConvertFrom-NativeProcessText {
    param([AllowEmptyString()][string]$Text)
    if (-not $Text) { return @() }
    $lines = @([regex]::Split($Text, '\r?\n'))
    if ($lines.Count -gt 0 -and $lines[-1] -eq '') {
        if ($lines.Count -eq 1) { return @() }
        $lines = @($lines[0..($lines.Count - 2)])
    }
    return $lines
}

function Get-NativeProcessIdentity {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    try {
        [pscustomobject]@{
            pid = [int]$Process.Id
            start_token = ([DateTimeOffset]$Process.StartTime.ToUniversalTime()).Ticks.ToString()
        }
    } catch { return $null }
}

function Write-NativeProcessOwnershipReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$RootProcess,
        [int[]]$DescendantIds = @()
    )
    $root = Get-NativeProcessIdentity $RootProcess
    if (-not $root) { throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED" }
    $descendants = @($DescendantIds | ForEach-Object {
        try {
            $owned = [System.Diagnostics.Process]::GetProcessById([int]$_)
            try { Get-NativeProcessIdentity $owned } finally { $owned.Dispose() }
        } catch { $null }
    } | Where-Object { $_ })
    $receipt = [pscustomobject]@{
        schema_version = "native-process-ownership-v1"
        root = $root
        descendants = $descendants
        observed_at = [DateTimeOffset]::UtcNow.ToString("o")
    }
    $temporary = Join-Path (Split-Path -Parent $Path) (
        "{0}.{1}.tmp" -f (Split-Path -Leaf $Path), [guid]::NewGuid().ToString("N")
    )
    try {
        $json = $receipt | ConvertTo-Json -Compress -Depth 5
        $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
        $bytes = $utf8.GetBytes($json)
        if ($bytes.Length -gt 65536) {
            throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_BOUND_EXCEEDED"
        }
        [System.IO.File]::WriteAllBytes($temporary, $bytes)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [System.IO.File]::Replace($temporary, $Path, $null, $true)
        } else {
            [System.IO.File]::Move($temporary, $Path)
        }
        return Confirm-NativeProcessOwnershipReceipt -Path $Path -ExpectedRoot $root
    } catch {
        throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Confirm-NativeProcessOwnershipReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][object]$ExpectedRoot
    )
    try {
        $receipt = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if (-not $receipt -or
            [string]$receipt.schema_version -cne "native-process-ownership-v1" -or
            -not $receipt.root -or
            [int]$receipt.root.pid -ne [int]$ExpectedRoot.pid -or
            [string]::IsNullOrWhiteSpace([string]$receipt.root.start_token) -or
            [string]$receipt.root.start_token -cne [string]$ExpectedRoot.start_token) {
            throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_MISMATCH"
        }
        return [pscustomobject]@{ state = "VERIFIED"; receipt = $receipt }
    } catch {
        throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"
    }
}

function Test-NativeProcessIdentityAlive {
    param([AllowNull()][object]$Identity)
    if (-not $Identity -or [int]$Identity.pid -le 0 -or
        [string]::IsNullOrWhiteSpace([string]$Identity.start_token)) { return $false }
    try {
        $process = [System.Diagnostics.Process]::GetProcessById([int]$Identity.pid)
        try {
            return [string](([DateTimeOffset]$process.StartTime.ToUniversalTime()).Ticks) -ceq
                [string]$Identity.start_token
        } finally { $process.Dispose() }
    } catch { return $false }
}

function Get-NativeOwnershipReceiptState {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return [pscustomobject]@{ state = "CLEAR"; alive = @() }
    }
    try {
        $receipt = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if ([string]$receipt.schema_version -ne "native-process-ownership-v1") {
            return [pscustomobject]@{ state = "UNRESOLVED"; alive = @() }
        }
        $alive = @(@($receipt.root) + @($receipt.descendants) | Where-Object {
            Test-NativeProcessIdentityAlive $_
        })
        [pscustomobject]@{
            state = if ($alive.Count -eq 0) { "CLEAR" } else { "ACTIVE" }
            alive = $alive
        }
    } catch { return [pscustomobject]@{ state = "UNRESOLVED"; alive = @() } }
}

function Wait-NativeProcessContainment {
    param([Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process)
    while ($true) {
        try {
            if ($Process.HasExited) { return }
        } catch { return }
        Start-Sleep -Milliseconds 100
    }
}

function Invoke-Utf8NativeProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [string]$WorkingDirectory = "",
        [hashtable]$Environment = @{},
        [ValidateRange(1, 300000)][int]$TimeoutMilliseconds = 30000,
        [string]$OwnershipReceiptPath = $script:nativeProcessOwnershipReceiptPath
    )
    $command = Get-Command $FilePath -ErrorAction Stop
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = [string]$command.Source
    $start.Arguments = @($Arguments | ForEach-Object {
        ConvertTo-NativeProcessArgument -Argument ([string]$_)
    }) -join " "
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    if ($WorkingDirectory) { $start.WorkingDirectory = $WorkingDirectory }
    foreach ($name in $Environment.Keys) {
        $start.EnvironmentVariables[[string]$name] = [string]$Environment[$name]
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $start.StandardOutputEncoding = $strictUtf8
    $start.StandardErrorEncoding = $strictUtf8
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    $preserveOwnership = $false
    try {
        [void]$process.Start()
        if ($OwnershipReceiptPath) {
            try {
                $ownership = Write-NativeProcessOwnershipReceipt `
                    -Path $OwnershipReceiptPath -RootProcess $process
                if ([string]$ownership.state -cne "VERIFIED") {
                    throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"
                }
            } catch {
                $termination = Stop-NativeProcessTree -Process $process
                if ([string]$termination.state -notin @("TERMINATED", "ALREADY_EXITED")) {
                    $preserveOwnership = $true
                    $script:unresolvedNativeProcess = $process
                    throw "NATIVE_PROCESS_TERMINATION_UNRESOLVED"
                }
                Remove-Item -LiteralPath $OwnershipReceiptPath -Force `
                    -ErrorAction SilentlyContinue
                throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"
            }
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            $receiptUpdateFailed = $false
            if ($OwnershipReceiptPath) {
                $inventoryAvailable = $false
                $descendants = @(Get-NativeDescendantProcessIds `
                    -RootProcessId $process.Id `
                    -InventoryAvailable ([ref]$inventoryAvailable))
                try {
                    $ownership = Write-NativeProcessOwnershipReceipt `
                        -Path $OwnershipReceiptPath -RootProcess $process `
                        -DescendantIds $descendants
                    if ([string]$ownership.state -cne "VERIFIED") {
                        throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"
                    }
                } catch { $receiptUpdateFailed = $true }
            }
            $termination = Stop-NativeProcessTree -Process $process
            if ([string]$termination.state -notin @("TERMINATED", "ALREADY_EXITED")) {
                $preserveOwnership = $true
                $script:unresolvedNativeProcess = $process
                throw "NATIVE_PROCESS_TERMINATION_UNRESOLVED"
            }
            if ($OwnershipReceiptPath) {
                Remove-Item -LiteralPath $OwnershipReceiptPath -Force `
                    -ErrorAction SilentlyContinue
            }
            if ($receiptUpdateFailed) {
                throw "NATIVE_PROCESS_OWNERSHIP_RECEIPT_FAILED"
            }
            throw "NATIVE_PROCESS_TIMEOUT"
        }
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        return [pscustomobject]@{
            exit_code = [int]$process.ExitCode
            stdout = [string]$stdout
            stderr = [string]$stderr
            stdout_lines = @(ConvertFrom-NativeProcessText -Text ([string]$stdout))
            stderr_lines = @(ConvertFrom-NativeProcessText -Text ([string]$stderr))
        }
    } catch [System.Text.DecoderFallbackException] {
        throw "NATIVE_PROCESS_UTF8_INVALID"
    } finally {
        if (-not $preserveOwnership) {
            if ($OwnershipReceiptPath) {
                Remove-Item -LiteralPath $OwnershipReceiptPath -Force `
                    -ErrorAction SilentlyContinue
            }
            $process.Dispose()
        }
    }
}
