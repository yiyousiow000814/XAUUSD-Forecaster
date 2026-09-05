function Get-ReleaseEvidenceFileDigest {
    param([Parameter(Mandatory = $true)][string]$Path)
    $bytes = [System.IO.File]::ReadAllBytes([System.IO.Path]::GetFullPath($Path))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function New-ReleaseEvidenceAdapterArguments {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$BehaviorInputs,
        [Parameter(Mandatory = $true)][object]$SourceIdentity,
        [Parameter(Mandatory = $true)][DateTimeOffset]$StartedAt,
        [Parameter(Mandatory = $true)][DateTimeOffset]$CompletedAt,
        [Parameter(Mandatory = $true)][string]$WhyRan,
        [ValidateSet("FRESH", "REUSED", "RENEWED")][string]$ExecutionMode = "FRESH",
        [string]$ReuseReason = "",
        [string]$PriorReceipt = ""
    )
    return @{
        Root = $releaseEvidenceRoot
        ContractPath = $releaseEvidenceContractPath
        ValidationKey = [string]$Candidate.validation_key
        BehaviorInputs = $BehaviorInputs
        SourceIdentity = $SourceIdentity
        StartedAt = $StartedAt.ToString("o")
        CompletedAt = $CompletedAt.ToString("o")
        ExecutionMode = $ExecutionMode
        WhyRan = $WhyRan
        ReuseReason = $ReuseReason
        PriorReceipt = $PriorReceipt
    }
}

function Register-CandidateFreePlanEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][string]$ProofPath
    )
    $fullPath = [System.IO.Path]::GetFullPath($ProofPath)
    if (-not (Test-Path -LiteralPath $fullPath) -or
        (Get-Item -LiteralPath $fullPath).Length -gt 262144) {
        throw "FREE_PLAN_PROOF_INPUT_INVALID"
    }
    $proof = Get-Content -LiteralPath $fullPath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    if ([string]$proof.validation_key -cne [string]$Candidate.validation_key -or
        [string]$proof.candidate.worker_version_id -cne
            [string]$Candidate.worker_version_id -or
        [string]$proof.candidate.git_sha -cne [string]$Candidate.git_sha -or
        [string]$proof.candidate.windows_revision -cne
            [string]$Candidate.windows_revision) {
        throw "FREE_PLAN_PROOF_CANDIDATE_MISMATCH"
    }
    $behaviorInputs = [pscustomobject][ordered]@{
        worker_bundle_config = $proof.worker_bundle_config
        sql_behavior = $proof.sql_behavior
        workload_manifest = $proof.workload_manifest
        data_shape_contract = $proof.data_shape_contract
        cadence = $proof.cadence
        migration_plan = $proof.migration_plan
        production_calibration = $proof.production_calibration
        proof_input_digests = $proof.proof_input_digests
        provider_limits_version = [string]$proof.provider_limits_version
    }
    $now = [DateTimeOffset]::UtcNow
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $behaviorInputs -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"
            candidate = [pscustomobject]@{
                validation_key = [string]$Candidate.validation_key
                worker_version_id = [string]$Candidate.worker_version_id
                git_sha = [string]$Candidate.git_sha
                windows_revision = [string]$Candidate.windows_revision
            }
            input_digest = Get-ReleaseEvidenceFileDigest -Path $fullPath
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan "BOUNDED_FREE_PLAN_PROOF_REGISTERED"
    return Publish-FreePlanEvidence -Arguments $arguments `
        -LimitsPath $releaseFreePlanContractPath -Proof $proof
}

function Resolve-ReleaseAccessEvidenceAuthority {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$AuthInspection
    )
    $state = [string]$AuthInspection.state
    if ($state -notin @(
            "HUMAN_ACCESS_BOUNDARY_ACCEPTED",
            "ACCESS_QUALIFICATION_REUSED",
            "ACCESS_QUALIFICATION_RENEWED")) {
        return [pscustomobject][ordered]@{
            required = $false; state = "NOT_REQUIRED"
            root_receipt_digest = "NOT_REQUIRED"
            provider_fingerprint = "NOT_REQUIRED"
        }
    }
    if ($state -eq "HUMAN_ACCESS_BOUNDARY_ACCEPTED") {
        $rootDigest = [string]$Candidate.access_acceptance.receipt_digest
        $provider = Get-LatestAccessProviderInspectionReceipt
        $providerFingerprint = if ($provider) {
            [string]$provider.provider_fingerprint
        } else { "" }
    } elseif ($state -eq "ACCESS_QUALIFICATION_REUSED") {
        $rootDigest = [string]$Candidate.access_qualification.prior_access_receipt_digest
        $providerFingerprint = [string]$Candidate.access_qualification.provider_fingerprint
    } else {
        $rootDigest = [string]$Candidate.access_qualification.root_human_receipt_digest
        $providerFingerprint = [string]$Candidate.access_qualification.provider_fingerprint
    }
    if ($rootDigest -notmatch '^[0-9a-f]{64}$' -or
        $providerFingerprint -notmatch '^[0-9a-f]{64}$') {
        throw "ACCESS_EVIDENCE_AUTHORITY_INVALID"
    }
    return [pscustomobject][ordered]@{
        required = $true; state = $state
        root_receipt_digest = $rootDigest
        provider_fingerprint = $providerFingerprint
    }
}

function Publish-CandidateQualificationEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][object]$Compatibility,
        [Parameter(Mandatory = $true)][object]$RoutePlan,
        [Parameter(Mandatory = $true)][object]$Cloudflare,
        [Parameter(Mandatory = $true)][object]$DataParity,
        [Parameter(Mandatory = $true)][object]$AuthInspection
    )
    $now = [DateTimeOffset]::UtcNow
    $artifact = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
        -ValidationKey ([string]$Candidate.validation_key) -Node "artifact_provenance"
    if (-not $artifact) { throw "RELEASE_EVIDENCE_PRODUCER_MISSING:artifact_provenance" }

    $checksInput = [pscustomobject][ordered]@{
        git_sha = [string]$Candidate.git_sha
        required_check_contract = [pscustomobject][ordered]@{
            schema_version = "required-github-checks-v1"
            checks = @($requiredGitHubChecks)
        }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $checksInput -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"; exact_git_sha = [string]$Candidate.git_sha
            required_checks = @($requiredGitHubChecks)
        }) -StartedAt $now -CompletedAt $now -WhyRan "EXACT_HEAD_REQUIRED_CHECKS_PASSED"
    $null = Publish-ExactHeadCiEvidence -Arguments $arguments

    $runtimeManifestDigest = Get-ReleaseEvidenceFileDigest `
        -Path $runtimeControlSourceManifestPath
    $windowsInput = [pscustomobject][ordered]@{
        windows_revision = [string]$Candidate.windows_revision
        runtime_contract = [pscustomobject][ordered]@{
            production_shape = $runtimePreflightContractVersion
            control_bundle_manifest_digest = $runtimeManifestDigest
        }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $windowsInput -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"
            windows_revision = [string]$Candidate.windows_revision
            preflight_contract = $runtimePreflightContractVersion
        }) -StartedAt $now -CompletedAt $now -WhyRan "PRODUCTION_SHAPED_WINDOWS_PREFLIGHT_PASSED"
    $null = Publish-WindowsRuntimeEvidence -Arguments $arguments

    $migrationRequired = [string]$Compatibility.state -eq
        "COORDINATED_STORAGE_MIGRATION_REQUIRED"
    $migrationRootDigest = if ($migrationRequired) {
        [string]$Candidate.migration_acceptance.receipt_digest
    } else { "NOT_REQUIRED" }
    $migrationInput = [pscustomobject][ordered]@{
        candidate = [string]$Candidate.validation_key
        stable = "$([string]$Stable.worker_version_id):$([string]$Stable.git_sha)"
        database = if ($migrationRequired) {
            [string]$Candidate.migration_acceptance.database_id
        } else { "NOT_REQUIRED" }
        migration_files = @($Compatibility.files | ForEach-Object { [string]$_ })
        current_generation = if ($migrationRequired) {
            [string]$Candidate.migration_acceptance.current_generation
        } else { "NOT_REQUIRED" }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $migrationInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($migrationRequired) { "PASSED" } else { "NOT_REQUIRED" }
            root_receipt_digest = $migrationRootDigest
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan $(if ($migrationRequired) { "MIGRATION_ROOT_ACCEPTED" } else { "MIGRATION_NOT_REQUIRED" })
    $null = Publish-MigrationAcceptanceEvidence -Arguments $arguments

    $migrationLeaseInput = [pscustomobject][ordered]@{
        migration_root_receipt = $migrationRootDigest
        live_owner = if ($migrationRequired) {
            [string]$Candidate.migration_qualification.live_owner
        } else { "NOT_REQUIRED" }
        current_generation = [string]$migrationInput.current_generation
    }
    $migrationExpiry = $now.Add($promotionFreshnessMinimumLifetime)
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $migrationLeaseInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($migrationRequired) { "PASSED" } else { "NOT_REQUIRED" }
            expires_at = $migrationExpiry.ToString("o")
            root_receipt_digest = $migrationRootDigest
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan $(if ($migrationRequired) { "MIGRATION_LIVE_LEASE_VERIFIED" } else { "MIGRATION_LEASE_NOT_REQUIRED" })
    $null = Publish-MigrationLiveLeaseEvidence -Arguments $arguments

    $placementInput = [pscustomobject][ordered]@{
        candidate_worker = [string]$Candidate.worker_version_id
        stable_worker = [string]$Stable.worker_version_id
        traffic_assignment = [pscustomobject][ordered]@{ stable = 100; candidate = 0 }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $placementInput -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"; expires_at = $now.AddMinutes(5).ToString("o")
            candidate_percentage = 0; stable_percentage = 100
        }) -StartedAt $now -CompletedAt $now -WhyRan "EXACT_ZERO_PERCENT_CANDIDATE_PLACEMENT"
    $null = Publish-CandidatePlacementEvidence -Arguments $arguments

    $manifestPath = Join-Path $repositoryRoot "web\worker-validation-manifest.json"
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    $manifestDigest = Get-ReleaseEvidenceFileDigest -Path $manifestPath
    $fixtureDigests = if ($Cloudflare.worker_qualification -and
        $Cloudflare.worker_qualification.fixture_digests) {
        $Cloudflare.worker_qualification.fixture_digests
    } else { @() }
    $workerQualificationKey = if ($Cloudflare.worker_qualification -and
        $Cloudflare.worker_qualification.qualification_key) {
        [string]$Cloudflare.worker_qualification.qualification_key
    } else { [string]$Candidate.validation_key }
    $directedInput = [pscustomobject][ordered]@{
        worker_qualification_key = $workerQualificationKey
        route_manifest = $manifestDigest
        fixture_digests = @($fixtureDigests)
    }
    $directedRequired = -not [string]::IsNullOrWhiteSpace([string]$Cloudflare.validation_run)
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $directedInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($directedRequired) { "PASSED" } else { "NOT_REQUIRED" }
            validation_run = [string]$Cloudflare.validation_run
            directed_ledger_digest = Get-WorkerCpuCanonicalDigest -Value $Cloudflare.directed_request_ledger
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan $(if ($directedRequired) { "DIRECTED_WORKER_LEDGER_PASSED" } else { "DIRECTED_WORKER_NOT_REQUIRED" })
    $null = Publish-DirectedWorkerEvidence -Arguments $arguments

    $requiredFamilyQuotas = @($manifest.routes | Where-Object { [bool]$_.cpu_required } |
        ForEach-Object { [pscustomobject][ordered]@{
            family = [string]$_.family; samples = [int]$_.acceptance_samples
        } })
    $cpuInput = [pscustomobject][ordered]@{
        worker_qualification_key = $workerQualificationKey
        cpu_policy = $manifest.cpu_evidence_policy
        required_family_quotas = $requiredFamilyQuotas
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $cpuInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($directedRequired) { "PASSED" } else { "NOT_REQUIRED" }
            evidence_digest = Get-WorkerCpuCanonicalDigest -Value $Cloudflare.cpu_evidence
            gate_state = [string]$Cloudflare.cpu_evidence.gate_state
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan $(if ($directedRequired) { "WORKER_CPU_EVIDENCE_QUALIFIED" } else { "WORKER_CPU_NOT_REQUIRED" })
    $null = Publish-WorkerCpuEvidence -Arguments $arguments

    $semanticInput = [pscustomobject][ordered]@{
        worker_behavior_key = [string]$Candidate.validation_key
        semantic_contract_version = if ($DataParity.schema_version) {
            [string]$DataParity.schema_version
        } else { "candidate-semantic-parity-v1" }
        data_shape_contract = Get-WorkerCpuCanonicalDigest -Value $DataParity
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $semanticInput -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"; parity_state = [string]$DataParity.state
            deferred_obligations = @($DataParity.deferred_obligations |
                Where-Object { $null -ne $_ })
        }) -StartedAt $now -CompletedAt $now -WhyRan "SEMANTIC_DATA_PARITY_PASSED"
    $null = Publish-SemanticContractEvidence -Arguments $arguments

    $accessContractDigest = Get-ReleaseEvidenceFileDigest -Path $accessQualificationContractPath
    $accessAuthority = Resolve-ReleaseAccessEvidenceAuthority `
        -Candidate $Candidate -AuthInspection $AuthInspection
    $accessRequired = [bool]$accessAuthority.required
    $accessRootDigest = [string]$accessAuthority.root_receipt_digest
    $accessRootInput = [pscustomobject][ordered]@{
        protected_origin = $protectedDashboardUrl
        provider_application_policy = if ($accessRequired) {
            [string]$accessAuthority.provider_fingerprint
        } else { "UNCHANGED_NOT_REQUIRED" }
        access_artifacts = $accessRootDigest
        acceptance_contract = $accessContractDigest
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $accessRootInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($accessRequired) { "PASSED" } else { "NOT_REQUIRED" }
            root_receipt_digest = $accessRootDigest
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan $(if ($accessRequired) { "HUMAN_ACCESS_ROOT_ACCEPTED" } else { "ACCESS_ROOT_NOT_REQUIRED" })
    $null = Publish-HumanAccessRootEvidence -Arguments $arguments

    $accessLeaseInput = [pscustomobject][ordered]@{
        access_root_receipt = $accessRootDigest
        provider_fingerprint = if ($accessRequired) {
            [string]$accessAuthority.provider_fingerprint
        } else { "NOT_REQUIRED" }
        audit_interval = if ($accessRequired) {
            [int]$accessProviderAuditMaximumLookback.TotalSeconds
        } else { 0 }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $Candidate `
        -BehaviorInputs $accessLeaseInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($accessRequired) { "PASSED" } else { "NOT_REQUIRED" }
            expires_at = $now.Add($promotionFreshnessMinimumLifetime).ToString("o")
            root_receipt_digest = $accessRootDigest
        }) -StartedAt $now -CompletedAt $now `
        -WhyRan $(if ($accessRequired) { "ACCESS_PROVIDER_LEASE_VERIFIED" } else { "ACCESS_LEASE_NOT_REQUIRED" })
    $null = Publish-AccessProviderLeaseEvidence -Arguments $arguments

    $freePlan = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
        -ValidationKey ([string]$Candidate.validation_key) -Node "free_plan"
    if (-not $freePlan -or [string]$freePlan.state -cne "PASSED" -or
        [string]$freePlan.source_identity.subject.candidate.validation_key -cne
            [string]$Candidate.validation_key) {
        throw "FREE_PLAN_EVIDENCE_REQUIRED"
    }
    return Assert-ReleaseEvidenceQualification -Root $releaseEvidenceRoot `
        -ContractPath $releaseEvidenceContractPath `
        -ValidationKey ([string]$Candidate.validation_key) `
        -RequiredNodes @($releaseEvidencePrerequisiteNodes | Where-Object {
            [string]$_ -ne "rollback_precheck"
        })
}

function Finalize-CandidateQualificationEvidence {
    param([string]$WhyRan = "CANDIDATE_QUALIFICATION_PRODUCER_COMPLETED")

    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate -or -not $state.stable) {
        return [pscustomobject][ordered]@{
            schema_version = "candidate-qualification-finalizer-v1"
            state = "INCOMPLETE"; reason = "CANDIDATE_AUTHORITY_UNAVAILABLE"
            validation_key = ""; node_count = 0
        }
    }
    $candidate = $state.candidate
    $validationKey = [string]$candidate.validation_key
    if ($state.transaction) {
        return [pscustomobject][ordered]@{
            schema_version = "candidate-qualification-finalizer-v1"
            state = "BLOCKED"; reason = "RELEASE_TRANSACTION_ACTIVE"
            validation_key = $validationKey; node_count = 0
        }
    }

    $validation = $candidate.validation
    if (-not $validation -or [string]$validation.key -cne $validationKey) {
        if ([string]$candidate.validation_state -eq "PASSED") {
            $candidate.validation_state = "REVIEW_REQUIRED"
            if ($validation) {
                $validation | Add-Member -Force -NotePropertyName reason `
                    -NotePropertyValue "RELEASE_EVIDENCE_VALIDATION_KEY_MISMATCH"
            }
            $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $state
        }
        return [pscustomobject][ordered]@{
            schema_version = "candidate-qualification-finalizer-v1"
            state = "INCOMPLETE"; reason = "PERSISTED_VALIDATION_KEY_MISMATCH"
            validation_key = $validationKey; node_count = 0
        }
    }

    $qualification = $null
    try {
        $qualification = Assert-ReleaseEvidenceQualification `
            -Root $releaseEvidenceRoot -ContractPath $releaseEvidenceContractPath `
            -ValidationKey $validationKey -RequiredNodes $releaseEvidencePreActionNodes
    } catch {
        if ($_.Exception.Message -notmatch '^RELEASE_EVIDENCE_PRODUCER_MISSING:') {
            $candidate.validation_state = "REVIEW_REQUIRED"
            if ($candidate.validation) {
                $candidate.validation | Add-Member -Force -NotePropertyName reason `
                    -NotePropertyValue "RELEASE_EVIDENCE_FINALIZATION_BLOCKED"
            }
            $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
            Write-ReleaseControlState -State $state
            return [pscustomobject][ordered]@{
                schema_version = "candidate-qualification-finalizer-v1"
                state = "BLOCKED"; reason = [string]$_.Exception.Message
                validation_key = $validationKey; node_count = 0
            }
        }
    }

    if (-not $qualification) {
        $validationReady = [bool]($validation -and
            [string]$validation.key -ceq $validationKey -and
            [string]$validation.repository -eq "PASSED" -and
            [string]$validation.windows -eq "PASSED" -and
            [string]$validation.cloudflare -eq "PASSED" -and
            $validation.route_plan -and $validation.data_parity -and
            [bool]$validation.data_parity.passed -and $validation.auth_inspection)
        if (-not $validationReady) {
            if ([string]$candidate.validation_state -eq "PASSED") {
                $candidate.validation_state = "REVIEW_REQUIRED"
                $candidate.validation | Add-Member -Force -NotePropertyName reason `
                    -NotePropertyValue "RELEASE_EVIDENCE_INCOMPLETE"
                $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
                Write-ReleaseControlState -State $state
            }
            return [pscustomobject][ordered]@{
                schema_version = "candidate-qualification-finalizer-v1"
                state = "INCOMPLETE"; reason = "PERSISTED_VALIDATION_FACTS_INCOMPLETE"
                validation_key = $validationKey; node_count = 0
            }
        }
        if ((Test-CandidateAuthBoundaryChanged -RoutePlan $validation.route_plan) -and
            [string]$validation.auth_inspection.state -notin @(
                "HUMAN_ACCESS_BOUNDARY_ACCEPTED", "ACCESS_QUALIFICATION_REUSED",
                "ACCESS_QUALIFICATION_RENEWED")) {
            return [pscustomobject][ordered]@{
                schema_version = "candidate-qualification-finalizer-v1"
                state = "INCOMPLETE"; reason = "ACCESS_EVIDENCE_INCOMPLETE"
                validation_key = $validationKey; node_count = 0
            }
        }
        $freePlan = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
            -ValidationKey $validationKey -Node "free_plan"
        if (-not $freePlan) {
            return [pscustomobject][ordered]@{
                schema_version = "candidate-qualification-finalizer-v1"
                state = "INCOMPLETE"; reason = "FREE_PLAN_EVIDENCE_REQUIRED"
                validation_key = $validationKey; node_count = 0
            }
        }
        if ([string]$freePlan.state -cne "PASSED" -or
            [string]$freePlan.source_identity.subject.candidate.validation_key -cne
                $validationKey) {
            throw "FREE_PLAN_EVIDENCE_INVALID"
        }

        $changed = @(Get-CandidateChangedFiles `
            -StableRevision ([string]$state.stable.git_sha) `
            -CandidateRevision ([string]$candidate.git_sha))
        $compatibility = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
        if ([string]$compatibility.state -eq "COORDINATED_STORAGE_MIGRATION_REQUIRED" -and
            (-not $candidate.migration_acceptance -or
             [string]$candidate.migration_acceptance.validation_key -cne $validationKey)) {
            return [pscustomobject][ordered]@{
                schema_version = "candidate-qualification-finalizer-v1"
                state = "INCOMPLETE"; reason = "MIGRATION_EVIDENCE_INCOMPLETE"
                validation_key = $validationKey; node_count = 0
            }
        }
        $cloudflare = [pscustomobject]@{
            routes = $validation.routes
            cpu_evidence = $validation.cpu_evidence
            worker_qualification = $validation.worker_qualification
            cpu_qualification_mode = $validation.cpu_qualification_mode
            directed_request_ledger = $validation.directed_request_ledger
            validation_run = [string]$validation.validation_run
        }
        $qualification = Publish-CandidateQualificationEvidence `
            -Candidate $candidate -Stable $state.stable -Compatibility $compatibility `
            -RoutePlan $validation.route_plan -Cloudflare $cloudflare `
            -DataParity $validation.data_parity -AuthInspection $validation.auth_inspection
    }

    $current = Get-ReleaseControlState
    if (-not $current -or $current.transaction -or
        -not (Test-ReleaseIdentity $current.candidate $candidate) -or
        [string]$current.candidate.validation_key -cne $validationKey) {
        return [pscustomobject][ordered]@{
            schema_version = "candidate-qualification-finalizer-v1"
            state = "BLOCKED"; reason = "CANDIDATE_AUTHORITY_MOVED"
            validation_key = $validationKey; node_count = 0
        }
    }
    $wasPassed = [string]$current.candidate.validation_state -eq "PASSED"
    $current.candidate.validation_state = "PASSED"
    $current.candidate.compatibility_state = "PASSED"
    $current.candidate.validation | Add-Member -Force -NotePropertyName reason `
        -NotePropertyValue "RELEASE_EVIDENCE_DAG_PASSED"
    $current.candidate.validation | Add-Member -Force -NotePropertyName tested_at `
        -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o"))
    $current.candidate | Add-Member -Force -NotePropertyName evidence_authority `
        -NotePropertyValue ([pscustomobject]@{
            schema_version = "release-evidence-compatibility-projection-v1"
            state = [string]$qualification.state
            validation_key = $validationKey
            node_count = @($qualification.receipts.PSObject.Properties).Count
            projected_at = [DateTimeOffset]::UtcNow.ToString("o")
        })
    $current.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $current
    if (-not $wasPassed) {
        Write-ReleaseHistory -Event "CANDIDATE_PASSED" -Release $current.candidate `
            -Detail @{ validation_key = $validationKey; finalizer = $WhyRan }
    }
    return [pscustomobject][ordered]@{
        schema_version = "candidate-qualification-finalizer-v1"
        state = "PASSED"; reason = "RELEASE_EVIDENCE_DAG_PASSED"
        validation_key = $validationKey
        node_count = @($qualification.receipts.PSObject.Properties).Count
    }
}

function Write-CandidateArtifactEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ContractPath
    )
    $completedAt = [DateTimeOffset]::Parse(
        [string]$Candidate.discovered_at).ToUniversalTime()
    $startedAt = $completedAt
    if (-not [string]::IsNullOrWhiteSpace([string]$Candidate.version_created_at)) {
        $created = [DateTimeOffset]::MinValue
        if ([DateTimeOffset]::TryParse(
            [string]$Candidate.version_created_at,
            [ref]$created) -and $created -le $completedAt) {
            $startedAt = $created.ToUniversalTime()
        }
    }
    $sourceIdentity = [ordered]@{
        validation_key = [string]$Candidate.validation_key
        worker_version_id = [string]$Candidate.worker_version_id
        git_sha = [string]$Candidate.git_sha
        windows_revision = [string]$Candidate.windows_revision
        artifact_kind = [string]$Candidate.artifact_kind
    }
    $behaviorInputs = [pscustomobject][ordered]@{
        worker_version_id = [string]$Candidate.worker_version_id
        git_sha = [string]$Candidate.git_sha
        windows_revision = [string]$Candidate.windows_revision
        artifact_kind = [string]$Candidate.artifact_kind
    }
    $behaviorKey = Get-ReleaseEvidenceBehaviorKey `
        -ContractPath $ContractPath `
        -Node "artifact_provenance" -Inputs $behaviorInputs
    $recordedSourceIdentity = [ordered]@{
        producer_adapter = "Write-CandidateArtifactEvidence"
        qualification_state = "PASSED"
        subject = $sourceIdentity
        behavior_inputs = $behaviorInputs
    }
    $receipt = Write-ReleaseEvidenceNodeReceipt -Root $Root `
        -ContractPath $ContractPath `
        -ValidationKey ([string]$Candidate.validation_key) `
        -Node "artifact_provenance" -BehaviorKey $behaviorKey -State "PASSED" `
        -SourceIdentity $recordedSourceIdentity -StartedAt $startedAt.ToString("o") `
        -CompletedAt $completedAt.ToString("o") -ExecutionMode "FRESH" `
        -WhyRan "IMMUTABLE_PRODUCTION_CANDIDATE_DISCOVERED"
    Register-ReleaseEvidenceBehaviorReceipt -Root $Root `
        -ValidationKey ([string]$Candidate.validation_key) -Receipt $receipt
    return $receipt
}

function Publish-PromotionFreshnessEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [switch]$AllowDegradedActive,
        [switch]$CollectorClockRecovery,
        [ValidateSet("", "RESTORE_LKG")][string]$RecoveryAction = "",
        [object]$RuntimeReadModel = $null
    )
    $restoreLkg = $RecoveryAction -eq "RESTORE_LKG"
    if ($restoreLkg -and -not $RuntimeReadModel) {
        throw "RECOVERY_LKG_RUNTIME_READ_MODEL_REQUIRED"
    }
    $candidate = if ($restoreLkg) { $State.stable } else { $State.candidate }
    $stable = $State.stable
    $now = [DateTimeOffset]::UtcNow
    $expires = $now.Add($promotionFreshnessMinimumLifetime)

    $migrationRoot = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
        -ValidationKey ([string]$candidate.validation_key) -Node "migration_acceptance"
    if (-not $migrationRoot) { throw "RELEASE_EVIDENCE_PRODUCER_MISSING:migration_acceptance" }
    $migrationRequired = [string]$migrationRoot.source_identity.qualification_state -ne
        "NOT_REQUIRED"
    $migrationRootDigest = if ($migrationRequired) {
        [string]$migrationRoot.source_identity.subject.root_receipt_digest
    } else { "NOT_REQUIRED" }
    $migrationLeaseInput = [pscustomobject][ordered]@{
        migration_root_receipt = $migrationRootDigest
        live_owner = if ($migrationRequired) {
            [string]$candidate.migration_qualification.live_owner
        } else { "NOT_REQUIRED" }
        current_generation = [string]$migrationRoot.source_identity.behavior_inputs.current_generation
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $candidate `
        -BehaviorInputs $migrationLeaseInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($migrationRequired) { "PASSED" } else { "NOT_REQUIRED" }
            expires_at = $expires.ToString("o"); root_receipt_digest = $migrationRootDigest
        }) -StartedAt $now -CompletedAt $now -WhyRan "PROMOTE_TIME_MIGRATION_LEASE"
    $null = Publish-MigrationLiveLeaseEvidence -Arguments $arguments

    $placementInput = if ($restoreLkg) {
        [pscustomobject][ordered]@{
            candidate_worker = [string]$candidate.worker_version_id
            stable_worker = [string]$RuntimeReadModel.active.worker_version_id
            traffic_assignment = [pscustomobject][ordered]@{
                active = 100
                target = if ([string]$RuntimeReadModel.active.worker_version_id -ceq
                    [string]$candidate.worker_version_id) { 100 } else { 0 }
                recovery_action = "RESTORE_LKG"
            }
        }
    } else {
        [pscustomobject][ordered]@{
            candidate_worker = [string]$candidate.worker_version_id
            stable_worker = [string]$stable.worker_version_id
            traffic_assignment = [pscustomobject][ordered]@{ stable = 100; candidate = 0 }
        }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $candidate `
        -BehaviorInputs $placementInput -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"; expires_at = $expires.ToString("o")
            candidate_percentage = if ($restoreLkg) {
                [int]$placementInput.traffic_assignment.target
            } else { 0 }
            stable_percentage = 100
            recovery_action = if ($restoreLkg) { "RESTORE_LKG" } else { $null }
        }) -StartedAt $now -CompletedAt $now -WhyRan "PROMOTE_TIME_PLACEMENT_RECONCILED"
    $null = Publish-CandidatePlacementEvidence -Arguments $arguments

    $accessRoot = Get-ReleaseEvidenceCurrentReceipt -Root $releaseEvidenceRoot `
        -ValidationKey ([string]$candidate.validation_key) -Node "human_access_root"
    if (-not $accessRoot) { throw "RELEASE_EVIDENCE_PRODUCER_MISSING:human_access_root" }
    $accessRequired = [string]$accessRoot.source_identity.qualification_state -ne "NOT_REQUIRED"
    $accessRootDigest = if ($accessRequired) {
        [string]$accessRoot.source_identity.subject.root_receipt_digest
    } else { "NOT_REQUIRED" }
    $providerFingerprint = "NOT_REQUIRED"
    if ($accessRequired) {
        if ($candidate.access_qualification) {
            $machineReceipt = Ensure-AccessQualificationMachineReceipt `
                -Candidate $candidate -MinimumRemaining $promotionFreshnessMinimumLifetime
            $providerFingerprint = [string]$machineReceipt.provider_fingerprint
            $observedRootDigest = if ([string]$machineReceipt.state -eq
                    "ACCESS_QUALIFICATION_RENEWED") {
                [string]$machineReceipt.root_human_receipt_digest
            } else { [string]$machineReceipt.prior_access_receipt_digest }
        } else {
            $humanReceipt = Assert-AccessBoundaryAcceptanceReceipt `
                -Candidate $candidate -Stable $stable
            $provider = Get-LatestAccessProviderInspectionReceipt
            $providerFingerprint = if ($provider) {
                [string]$provider.provider_fingerprint
            } else { "" }
            $observedRootDigest = [string]$humanReceipt.receipt_digest
        }
        if ($observedRootDigest -cne $accessRootDigest -or
            $providerFingerprint -notmatch '^[0-9a-f]{64}$') {
            throw "ACCESS_EVIDENCE_AUTHORITY_CHANGED"
        }
    }
    $accessLeaseInput = [pscustomobject][ordered]@{
        access_root_receipt = $accessRootDigest
        provider_fingerprint = $providerFingerprint
        audit_interval = if ($accessRequired) {
            [int]$accessProviderAuditMaximumLookback.TotalSeconds
        } else { 0 }
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $candidate `
        -BehaviorInputs $accessLeaseInput -SourceIdentity ([pscustomobject]@{
            qualification_state = if ($accessRequired) { "PASSED" } else { "NOT_REQUIRED" }
            expires_at = $expires.ToString("o"); root_receipt_digest = $accessRootDigest
        }) -StartedAt $now -CompletedAt $now -WhyRan "PROMOTE_TIME_ACCESS_LEASE"
    $null = Publish-AccessProviderLeaseEvidence -Arguments $arguments

    $runtimeReadModel = if ($RuntimeReadModel) { $RuntimeReadModel } else {
        Get-CurrentReleaseRuntimeReadModel -PersistedState $State `
            -ReleaseLockOwnedByCaller -ForceProviderRefresh
    }
    $incidentBaseline = $null
    if ($CollectorClockRecovery) {
        $context = Get-CollectorClockRecoveryContext
        if ($restoreLkg -or $AllowDegradedActive -or -not $context -or
            [string]$candidate.windows_revision -cne [string]$context.target_revision -or
            [string]$stable.windows_revision -cne [string]$context.broken_revision) {
            throw 'COLLECTOR_RECOVERY_EXACT_NORMAL_TARGET_REQUIRED'
        }
        $incidentBaseline = Invoke-CollectorClockRecoveryOperation
    }
    $runtimeAuthorityValid = [bool]($runtimeReadModel -and
        -not $runtimeReadModel.transaction_active -and
        [string]$runtimeReadModel.active.observation_status -eq "AVAILABLE" -and
        [string]$runtimeReadModel.active.identity_status -eq "COMPLETE" -and
        [string]$runtimeReadModel.active.health -in $(if ($AllowDegradedActive -or $incidentBaseline) {
            @("HEALTHY", "DEGRADED")
        } else { @("HEALTHY") }) -and
        ([string]$runtimeReadModel.active.ownership_status -eq "SINGLE_OWNER" -or
            ($incidentBaseline -and [string]$runtimeReadModel.active.ownership_status -eq 'INVALID')) -and
        [string]$runtimeReadModel.committed_stable.worker_version_id -ceq
            [string]$stable.worker_version_id)
    if (-not $runtimeAuthorityValid -or
        [string]$runtimeReadModel.committed_stable.windows_revision -cne
            [string]$stable.windows_revision -or
        (-not $restoreLkg -and -not [bool]$runtimeReadModel.active_matches_committed)) {
        throw "ROLLBACK_RUNTIME_READ_MODEL_INVALID"
    }
    $rollbackInput = [pscustomobject][ordered]@{
        rollback_worker = [string]$stable.worker_version_id
        stable_windows_revision = [string]$stable.windows_revision
        live_owner_health = [pscustomobject][ordered]@{
            active_matches_committed = [bool]$runtimeReadModel.active_matches_committed
            health = [string]$runtimeReadModel.active.health
            ownership = [string]$runtimeReadModel.active.ownership_status
            control_bundle = "VERIFIED"
            migration_authority = if ($migrationRequired) { $migrationRootDigest } else { "NOT_REQUIRED" }
            recovery_action = if ($restoreLkg) { "RESTORE_LKG" } else { "NORMAL" }
        }
    }
    if ($incidentBaseline) {
        $rollbackInput.live_owner_health.recovery_action = 'COLLECTOR_CLOCK_RECOVERY'
        $rollbackInput.live_owner_health | Add-Member -NotePropertyName incident_baseline -NotePropertyValue ([pscustomobject]@{
            state = 'DEGRADED_RECOVERY_BASELINE'
            expected_absent = @('collector')
            observed_at = [string]$incidentBaseline.observed_at
            snapshot = $incidentBaseline.snapshot
            services = $incidentBaseline.services
            watchdog = $incidentBaseline.previous_watchdog_receipt
            target_revision = [string]$incidentBaseline.target_revision
        })
    }
    $arguments = New-ReleaseEvidenceAdapterArguments -Candidate $candidate `
        -BehaviorInputs $rollbackInput -SourceIdentity ([pscustomobject]@{
            qualification_state = "PASSED"; expires_at = $expires.ToString("o")
            read_model_observed_at = [string]$runtimeReadModel.observed_at
        }) -StartedAt $now -CompletedAt $now -WhyRan "EXACT_ROLLBACK_TARGET_VERIFIED"
    $null = Publish-RollbackPrecheckEvidence -Arguments $arguments

    return Assert-ReleaseEvidenceQualification -Root $releaseEvidenceRoot `
        -ContractPath $releaseEvidenceContractPath `
        -ValidationKey ([string]$candidate.validation_key)
}

$releaseEvidencePrerequisiteNodes = @(
    "artifact_provenance", "exact_head_ci", "windows_runtime",
    "migration_acceptance", "migration_live_lease", "candidate_placement",
    "directed_worker", "worker_cpu", "semantic_contract", "free_plan",
    "human_access_root", "access_provider_lease", "rollback_precheck"
)
$releaseEvidencePreActionNodes = @($releaseEvidencePrerequisiteNodes | Where-Object {
    [string]$_ -ne "rollback_precheck"
})
$releaseEvidencePromotionDependencyNodes = @(
    "exact_head_ci", "windows_runtime", "migration_live_lease",
    "directed_worker", "worker_cpu", "semantic_contract", "free_plan",
    "access_provider_lease", "candidate_placement", "rollback_precheck"
)

function ConvertTo-ReleaseEvidenceCanonicalObject {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [string] -or $Value -is [char] -or
        $Value -is [bool] -or $Value -is [ValueType]) { return $Value }
    if ($Value -is [System.Collections.IDictionary]) {
        $ordered = [ordered]@{}
        foreach ($key in @($Value.Keys | ForEach-Object { [string]$_ } | Sort-Object)) {
            $ordered[$key] = ConvertTo-ReleaseEvidenceCanonicalObject -Value $Value[$key]
        }
        return $ordered
    }
    if ($Value -is [System.Collections.IEnumerable]) {
        $items = @()
        foreach ($item in $Value) {
            $items += ,(ConvertTo-ReleaseEvidenceCanonicalObject -Value $item)
        }
        return $items
    }
    $properties = @($Value.PSObject.Properties | Where-Object {
        $_.MemberType -in @("NoteProperty", "Property", "AliasProperty")
    } | Sort-Object Name)
    $object = [ordered]@{}
    foreach ($property in $properties) {
        $object[$property.Name] = ConvertTo-ReleaseEvidenceCanonicalObject -Value $property.Value
    }
    return $object
}

function Get-ReleaseEvidenceProducerRegistry {
    param([Parameter(Mandatory = $true)][string]$ContractPath)
    $contract = Get-ReleaseEvidenceContract -ContractPath $ContractPath
    $registry = [ordered]@{}
    foreach ($node in @($contract.nodes)) {
        $adapter = [string]$node.producer_adapter
        if ($registry.Contains([string]$node.id)) {
            throw "RELEASE_EVIDENCE_PRODUCER_DUPLICATE"
        }
        $registry[[string]$node.id] = $adapter
    }
    if ($registry.Count -ne 15 -or
        @($registry.Values | Select-Object -Unique).Count -ne 15) {
        throw "RELEASE_EVIDENCE_PRODUCER_REGISTRY_INVALID"
    }
    return $registry
}

function Get-ReleaseEvidenceBehaviorKey {
    param(
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][object]$Inputs
    )
    $contract = Get-ReleaseEvidenceContract -ContractPath $ContractPath
    $definitions = @($contract.nodes | Where-Object { [string]$_.id -ceq $Node })
    if ($definitions.Count -ne 1) { throw "RELEASE_EVIDENCE_NODE_UNKNOWN" }
    $expected = @($definitions[0].behavior_inputs | ForEach-Object { [string]$_ })
    $actual = @($Inputs.PSObject.Properties | ForEach-Object { [string]$_.Name })
    if ($Inputs -is [System.Collections.IDictionary]) {
        $actual = @($Inputs.Keys | ForEach-Object { [string]$_ })
    }
    if ($actual.Count -ne $expected.Count -or
        @($actual | Where-Object { $_ -notin $expected }).Count -gt 0) {
        throw "RELEASE_EVIDENCE_BEHAVIOR_INPUTS_INVALID"
    }
    $canonicalInputs = [ordered]@{}
    foreach ($name in $expected) {
        $value = if ($Inputs -is [System.Collections.IDictionary]) {
            $Inputs[$name]
        } else { $Inputs.$name }
        $canonicalInputs[$name] = ConvertTo-ReleaseEvidenceCanonicalObject -Value $value
    }
    $preimage = [ordered]@{
        schema_version = "release-evidence-behavior-key-v1"
        contract_version = [int]$contract.schema_version
        node = $Node
        inputs = $canonicalInputs
    }
    return "release-behavior-key-v1:" +
        (Get-ReleaseEvidenceSha256 -Value (ConvertTo-ReleaseEvidenceJson $preimage))
}

function Get-ReleaseEvidenceCurrentReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [Parameter(Mandatory = $true)][string]$Node
    )
    $waterfall = Get-ReleaseEvidenceWaterfall -Root $Root -ValidationKey $ValidationKey
    $entry = @($waterfall.nodes | Where-Object { [string]$_.node -ceq $Node })
    if ($entry.Count -ne 1) { return $null }
    $keyDigest = Get-ReleaseEvidenceSha256 -Value $ValidationKey
    $path = Join-Path $Root "$keyDigest\$Node\$([string]$entry[0].receipt_digest).json"
    $nativePath = ConvertTo-ReleaseEvidenceNativePath -Path $path
    if (-not [System.IO.File]::Exists($nativePath)) { throw "RELEASE_EVIDENCE_RECEIPT_MISSING" }
    $receipt = [System.IO.File]::ReadAllText(
        $nativePath, [System.Text.Encoding]::UTF8) | ConvertFrom-ReleaseEvidenceJson
    if (-not (Test-ReleaseEvidenceNodeReceipt -Receipt $receipt)) {
        throw "RELEASE_EVIDENCE_RECEIPT_TAMPERED"
    }
    return $receipt
}

function Get-ReleaseEvidenceDependencyLinks {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [Parameter(Mandatory = $true)][string]$Node
    )
    $contract = Get-ReleaseEvidenceContract -ContractPath $ContractPath
    $definition = @($contract.nodes | Where-Object { [string]$_.id -ceq $Node })
    if ($definition.Count -ne 1) { throw "RELEASE_EVIDENCE_NODE_UNKNOWN" }
    $links = @()
    foreach ($dependencyNode in @($definition[0].dependencies)) {
        $receipt = Get-ReleaseEvidenceCurrentReceipt -Root $Root `
            -ValidationKey $ValidationKey -Node ([string]$dependencyNode)
        if (-not $receipt -or [string]$receipt.state -cne "PASSED") {
            throw "RELEASE_EVIDENCE_DEPENDENCY_NOT_PASSED:$dependencyNode"
        }
        $links += [pscustomobject][ordered]@{
            node = [string]$dependencyNode
            receipt_digest = [string]$receipt.receipt_digest
        }
    }
    return $links
}

function Register-ReleaseEvidenceBehaviorReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [Parameter(Mandatory = $true)][object]$Receipt
    )
    if (-not (Test-ReleaseEvidenceNodeReceipt -Receipt $Receipt) -or
        [string]$Receipt.state -cne "PASSED" -or
        [string]$Receipt.behavior_key -notmatch '^release-behavior-key-v1:[0-9a-f]{64}$') {
        throw "RELEASE_EVIDENCE_BEHAVIOR_RECEIPT_INVALID"
    }
    $validationKeyDigest = Get-ReleaseEvidenceSha256 -Value $ValidationKey
    $behaviorKeyDigest = Get-ReleaseEvidenceSha256 -Value ([string]$Receipt.behavior_key)
    $index = [ordered]@{
        schema_version = "release-evidence-behavior-index-v1"
        node = [string]$Receipt.node
        behavior_key = [string]$Receipt.behavior_key
        validation_key_digest = $validationKeyDigest
        receipt_digest = [string]$Receipt.receipt_digest
        updated_at = [string]$Receipt.completed_at
    }
    $path = Join-Path $Root "_behavior\$([string]$Receipt.node)\$behaviorKeyDigest.json"
    Write-ReleaseEvidenceUtf8Atomic -Path $path `
        -Content (ConvertTo-ReleaseEvidenceJson $index)
}

function Find-ReleaseEvidenceBehaviorReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][string]$BehaviorKey
    )
    if ($BehaviorKey -notmatch '^release-behavior-key-v1:[0-9a-f]{64}$') {
        throw "RELEASE_EVIDENCE_BEHAVIOR_KEY_INVALID"
    }
    $behaviorKeyDigest = Get-ReleaseEvidenceSha256 -Value $BehaviorKey
    $indexPath = Join-Path $Root "_behavior\$Node\$behaviorKeyDigest.json"
    $nativeIndexPath = ConvertTo-ReleaseEvidenceNativePath -Path $indexPath
    if (-not [System.IO.File]::Exists($nativeIndexPath)) { return $null }
    $index = [System.IO.File]::ReadAllText(
        $nativeIndexPath, [System.Text.Encoding]::UTF8) | ConvertFrom-ReleaseEvidenceJson
    if ([string]$index.schema_version -cne "release-evidence-behavior-index-v1" -or
        [string]$index.node -cne $Node -or
        [string]$index.behavior_key -cne $BehaviorKey -or
        [string]$index.validation_key_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$index.receipt_digest -notmatch '^[0-9a-f]{64}$') {
        throw "RELEASE_EVIDENCE_BEHAVIOR_INDEX_INVALID"
    }
    $receiptPath = Join-Path $Root `
        "$([string]$index.validation_key_digest)\$Node\$([string]$index.receipt_digest).json"
    $nativeReceiptPath = ConvertTo-ReleaseEvidenceNativePath -Path $receiptPath
    if (-not [System.IO.File]::Exists($nativeReceiptPath)) {
        throw "RELEASE_EVIDENCE_BEHAVIOR_RECEIPT_MISSING"
    }
    $receipt = [System.IO.File]::ReadAllText(
        $nativeReceiptPath, [System.Text.Encoding]::UTF8) | ConvertFrom-ReleaseEvidenceJson
    if (-not (Test-ReleaseEvidenceNodeReceipt -Receipt $receipt) -or
        [string]$receipt.node -cne $Node -or
        [string]$receipt.behavior_key -cne $BehaviorKey -or
        [string]$receipt.receipt_digest -cne [string]$index.receipt_digest -or
        [string]$receipt.state -cne "PASSED") {
        throw "RELEASE_EVIDENCE_BEHAVIOR_RECEIPT_INVALID"
    }
    return $receipt
}

function Publish-ReleaseEvidenceAuthorityNode {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][string]$Adapter,
        [Parameter(Mandatory = $true)][object]$BehaviorInputs,
        [Parameter(Mandatory = $true)][object]$SourceIdentity,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$CompletedAt,
        [ValidateSet("FRESH", "REUSED", "RENEWED")][string]$ExecutionMode = "FRESH",
        [ValidateSet("PASSED", "FAILED", "INVALIDATED")][string]$State = "PASSED",
        [Parameter(Mandatory = $true)][string]$WhyRan,
        [string]$ReuseReason = "",
        [string]$PriorReceipt = "",
        [switch]$PreserveCurrentIndex
    )
    $registry = Get-ReleaseEvidenceProducerRegistry -ContractPath $ContractPath
    if (-not $registry.Contains($Node) -or [string]$registry[$Node] -cne $Adapter) {
        throw "RELEASE_EVIDENCE_PRODUCER_AUTHORITY_MISMATCH"
    }
    $behaviorKey = Get-ReleaseEvidenceBehaviorKey -ContractPath $ContractPath `
        -Node $Node -Inputs $BehaviorInputs
    $dependencies = @(Get-ReleaseEvidenceDependencyLinks -Root $Root `
        -ContractPath $ContractPath -ValidationKey $ValidationKey -Node $Node)
    $recordedSourceIdentity = [ordered]@{
        producer_adapter = $Adapter
        qualification_state = if ($SourceIdentity.PSObject.Properties["qualification_state"]) {
            [string]$SourceIdentity.qualification_state
        } else { $State }
        subject = ConvertTo-ReleaseEvidenceCanonicalObject -Value $SourceIdentity
        behavior_inputs = ConvertTo-ReleaseEvidenceCanonicalObject -Value $BehaviorInputs
    }
    $receipt = Write-ReleaseEvidenceNodeReceipt -Root $Root -ContractPath $ContractPath `
        -ValidationKey $ValidationKey -Node $Node -BehaviorKey $behaviorKey `
        -State $State -SourceIdentity $recordedSourceIdentity -StartedAt $StartedAt `
        -CompletedAt $CompletedAt -ExecutionMode $ExecutionMode -WhyRan $WhyRan `
        -Dependencies $dependencies -ReuseReason $ReuseReason -PriorReceipt $PriorReceipt `
        -PreserveCurrentIndex:$PreserveCurrentIndex
    if ([string]$receipt.state -eq "PASSED") {
        Register-ReleaseEvidenceBehaviorReceipt -Root $Root -ValidationKey $ValidationKey `
            -Receipt $receipt
    }
    return $receipt
}

function Publish-ReleaseEvidenceReuse {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][string]$Adapter,
        [Parameter(Mandatory = $true)][object]$BehaviorInputs,
        [Parameter(Mandatory = $true)][object]$SourceIdentity,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$CompletedAt,
        [Parameter(Mandatory = $true)][string]$ReuseReason
    )
    $behaviorKey = Get-ReleaseEvidenceBehaviorKey -ContractPath $ContractPath `
        -Node $Node -Inputs $BehaviorInputs
    $prior = Find-ReleaseEvidenceBehaviorReceipt -Root $Root -Node $Node `
        -BehaviorKey $behaviorKey
    if (-not $prior) { throw "RELEASE_EVIDENCE_REUSE_SOURCE_MISSING" }
    return Publish-ReleaseEvidenceAuthorityNode -Root $Root -ContractPath $ContractPath `
        -ValidationKey $ValidationKey -Node $Node -Adapter $Adapter `
        -BehaviorInputs $BehaviorInputs -SourceIdentity $SourceIdentity `
        -StartedAt $StartedAt -CompletedAt $CompletedAt -ExecutionMode "REUSED" `
        -WhyRan "UNCHANGED_BEHAVIOR_REUSED" -ReuseReason $ReuseReason `
        -PriorReceipt ([string]$prior.receipt_digest)
}

function Get-ReleaseEvidenceChangePlan {
    param(
        [Parameter(Mandatory = $true)][string]$OwnershipPath,
        [Parameter(Mandatory = $true)][string[]]$ChangedFiles
    )
    $ownership = Get-Content -LiteralPath $OwnershipPath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseEvidenceJson
    if ([string]$ownership.schema_version -ne "release-evidence-change-ownership-v1") {
        throw "RELEASE_EVIDENCE_CHANGE_OWNERSHIP_INVALID"
    }
    $matchedFamilies = @()
    $affectedNodes = @()
    $unmatched = @()
    foreach ($file in @($ChangedFiles | Sort-Object -Unique)) {
        $matches = @($ownership.families | Where-Object {
            $family = $_
            @($family.patterns | Where-Object { $file -match [string]$_ }).Count -gt 0
        })
        if ($matches.Count -eq 0) { $unmatched += $file; continue }
        foreach ($match in $matches) {
            $matchedFamilies += [string]$match.id
            $affectedNodes += @($match.nodes | ForEach-Object { [string]$_ })
        }
    }
    if ($unmatched.Count -gt 0 -or @($matchedFamilies | Select-Object -Unique).Count -gt 1) {
        return [pscustomobject][ordered]@{
            schema_version = "release-evidence-change-plan-v1"
            family = [string]$ownership.unknown_family
            changed_files = @($ChangedFiles | Sort-Object -Unique)
            affected_nodes = @($ownership.unknown_nodes)
            fail_closed = $true
        }
    }
    $family = if ($matchedFamilies.Count -eq 0) { "docs-only" } else {
        [string]@($matchedFamilies | Select-Object -Unique)[0]
    }
    return [pscustomobject][ordered]@{
        schema_version = "release-evidence-change-plan-v1"
        family = $family
        changed_files = @($ChangedFiles | Sort-Object -Unique)
        affected_nodes = @($affectedNodes | Select-Object -Unique)
        fail_closed = $false
    }
}

function Test-ReleaseFreePlanBoundedProof {
    param(
        [Parameter(Mandatory = $true)][string]$LimitsPath,
        [Parameter(Mandatory = $true)][object]$Proof
    )
    try {
        $contract = Get-Content -LiteralPath $LimitsPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseEvidenceJson
        if ([string]$contract.schema_version -ne "release-free-plan-proof-v2" -or
            [string]$Proof.provider_limits_version -cne
                [string]$contract.provider_limits_version) {
            throw "FREE_PLAN_LIMITS_VERSION_MISMATCH"
        }
        foreach ($field in @(
            "worker_bundle_config", "sql_behavior", "workload_manifest",
            "data_shape_contract", "cadence", "migration_plan",
            "production_calibration", "proof_input_digests", "provider_limits_version"
        )) {
            if (-not $Proof.PSObject.Properties[$field]) {
                throw "FREE_PLAN_INPUT_MISSING:$field"
            }
        }
        if ([string]$Proof.cadence.schema_version -ne "bounded-cadence-v1" -or
            -not [bool]$Proof.cadence.bounded -or
            [string]$Proof.workload_manifest.schema_version -ne
                "release-workload-manifest-v2" -or
            [string]$Proof.worker_bundle_config.schema_version -ne
                "worker-bundle-config-v1" -or
            [string]$Proof.sql_behavior.schema_version -ne "sql-behavior-v2" -or
            -not [bool]$Proof.sql_behavior.no_routine_accumulated_scan -or
            [string]$Proof.data_shape_contract.schema_version -ne
                "d1-data-shape-v2" -or
            [string]$Proof.migration_plan.schema_version -ne
                "release-free-migration-plan-v1" -or
            [string]$Proof.production_calibration.schema_version -ne
                "production-usage-calibration-v1") {
            throw "FREE_PLAN_WORKLOAD_UNBOUNDED"
        }
        $readBound = {
            param([object]$Owner, [string]$Name)
            if (-not $Owner -or -not $Owner.PSObject.Properties[$Name]) {
                throw "FREE_PLAN_INPUT_MISSING:$Name"
            }
            $value = [decimal]0
            if (-not [decimal]::TryParse(
                    [string]$Owner.$Name,
                    [Globalization.NumberStyles]::Integer,
                    [Globalization.CultureInfo]::InvariantCulture,
                    [ref]$value) -or
                $value -lt 0 -or $value -gt [long]::MaxValue) {
                throw "FREE_PLAN_INPUT_INVALID:$Name"
            }
            return [long]$value
        }
        $safeProduct = {
            param([long]$Left, [long]$Right)
            $value = [decimal]$Left * [decimal]$Right
            if ($value -gt [long]::MaxValue) { throw "FREE_PLAN_INTEGER_OVERFLOW" }
            return [long]$value
        }
        $safeAdd = {
            param([long]$Left, [long]$Right)
            $value = [decimal]$Left + [decimal]$Right
            if ($value -gt [long]::MaxValue) { throw "FREE_PLAN_INTEGER_OVERFLOW" }
            return [long]$value
        }
        foreach ($name in @(
            "worker_bundle_config", "sql_behavior", "workload_manifest",
            "data_shape_contract", "cadence", "migration_plan", "production_calibration"
        )) {
            $digest = [string]$Proof.proof_input_digests.$name
            if ($digest -notmatch '^[0-9a-f]{64}$') {
                throw "FREE_PLAN_INPUT_DIGEST_INVALID:$name"
            }
            $canonical = ConvertTo-ReleaseEvidenceCanonicalObject -Value $Proof.$name
            $actualDigest = Get-ReleaseEvidenceSha256 `
                (ConvertTo-ReleaseEvidenceJson -Value $canonical)
            if ($digest -cne $actualDigest) {
                throw "FREE_PLAN_INPUT_DIGEST_MISMATCH:$name"
            }
        }
        $requests = [long]0; $rowsRead = [long]0; $rowsWritten = [long]0
        $maximumQueries = [long]0; $maximumSubrequests = [long]0
        $producers = @($Proof.workload_manifest.producers)
        if ($producers.Count -eq 0 -or $producers.Count -gt 128) {
            throw "FREE_PLAN_PRODUCER_SET_INVALID"
        }
        $producerIds = @()
        foreach ($producer in $producers) {
            if ([string]::IsNullOrWhiteSpace([string]$producer.id) -or
                [string]$producer.id -notmatch '^[a-z][a-z0-9_-]{0,63}$' -or
                [string]$producer.id -in $producerIds -or
                [string]::IsNullOrWhiteSpace([string]$producer.execution_owner)) {
                throw "FREE_PLAN_PRODUCER_ID_INVALID"
            }
            $producerIds += [string]$producer.id
            $values = @{}
            foreach ($field in @(
                "executions_per_day", "worker_requests_per_execution",
                "d1_rows_read_per_execution", "d1_rows_written_per_execution",
                "d1_queries_per_invocation", "subrequests_per_invocation"
            )) {
                $values[$field] = & $readBound $producer $field
            }
            $requests = & $safeAdd $requests (& $safeProduct `
                $values.executions_per_day $values.worker_requests_per_execution)
            $rowsRead = & $safeAdd $rowsRead (& $safeProduct `
                $values.executions_per_day $values.d1_rows_read_per_execution)
            $rowsWritten = & $safeAdd $rowsWritten (& $safeProduct `
                $values.executions_per_day $values.d1_rows_written_per_execution)
            $maximumQueries = [Math]::Max($maximumQueries, $values.d1_queries_per_invocation)
            $maximumSubrequests = [Math]::Max(
                $maximumSubrequests, $values.subrequests_per_invocation)
        }
        $cadences = @($Proof.cadence.producers)
        if ($cadences.Count -ne $producers.Count) { throw "FREE_PLAN_CADENCE_MISMATCH" }
        foreach ($producer in $producers) {
            $cadence = @($cadences | Where-Object {
                [string]$_.id -ceq [string]$producer.id
            })
            if ($cadence.Count -ne 1 -or
                (& $readBound $cadence[0] "executions_per_day") -ne
                    (& $readBound $producer "executions_per_day") -or
                (& $readBound $cadence[0] "interval_seconds") -le 0) {
                throw "FREE_PLAN_CADENCE_MISMATCH"
            }
        }
        $bundle = $Proof.worker_bundle_config
        $shape = $Proof.data_shape_contract
        $migration = $Proof.migration_plan
        $sqlMaximum = & $readBound $Proof.sql_behavior "max_d1_queries_per_invocation"
        if ($sqlMaximum -ne $maximumQueries) { throw "FREE_PLAN_SQL_WORKLOAD_MISMATCH" }
        if (-not [bool]$shape.retention_plateau_bounded -or
            -not [bool]$shape.no_local_authority_deletion -or
            [string]$shape.storage_profile -notin @("D1_ONLY", "R2_STANDARD")) {
            throw "FREE_PLAN_STORAGE_UNBOUNDED"
        }
        if ([bool]$migration.destructive -or -not [bool]$migration.stable_compatible) {
            throw "FREE_PLAN_MIGRATION_UNSAFE"
        }
        $measurements = [ordered]@{
            worker_requests_per_day = $requests
            worker_compressed_bytes = & $readBound $bundle "compressed_bytes"
            worker_subrequests_per_invocation = $maximumSubrequests
            worker_environment_variables = & $readBound $bundle "environment_variables"
            static_assets_per_version = & $readBound $bundle "static_assets"
            d1_database_bytes = & $readBound $shape "current_database_bytes"
            d1_account_storage_bytes = & $readBound $shape "account_storage_bytes"
            d1_queries_per_invocation = $sqlMaximum
            d1_rows_read_per_day = $rowsRead
            d1_rows_written_per_day = $rowsWritten
            d1_pre_cutover_peak_bytes = & $readBound $shape "pre_cutover_peak_bytes"
            d1_steady_state_bytes = & $readBound $shape "steady_state_bytes"
            d1_projected_30_day_bytes = & $readBound $shape "projected_30_day_bytes"
            migration_rows_read_per_day = & $readBound $migration "rows_read_per_day"
            migration_rows_written_per_day = & $readBound $migration "rows_written_per_day"
        }
        $measurements.migration_day_rows_read = & $safeAdd `
            $measurements.d1_rows_read_per_day $measurements.migration_rows_read_per_day
        $measurements.migration_day_rows_written = & $safeAdd `
            $measurements.d1_rows_written_per_day $measurements.migration_rows_written_per_day
        foreach ($name in @("d1_rows_read_per_day", "d1_rows_written_per_day")) {
            $null = & $readBound $Proof.production_calibration $name
        }
        $hardBreaches = @()
        foreach ($name in @(
            "worker_requests_per_day", "worker_compressed_bytes",
            "worker_subrequests_per_invocation", "worker_environment_variables",
            "static_assets_per_version", "d1_database_bytes", "d1_account_storage_bytes",
            "d1_queries_per_invocation", "d1_rows_read_per_day", "d1_rows_written_per_day"
        )) {
            $limit = & $readBound $contract.limits $name
            if ([long]$measurements[$name] -gt $limit) { $hardBreaches += $name }
        }
        if ($measurements.d1_projected_30_day_bytes -gt
            (& $readBound $contract.limits "d1_database_bytes")) {
            $hardBreaches += "d1_projected_30_day_bytes"
        }
        if ($measurements.migration_day_rows_read -gt
            (& $readBound $contract.limits "d1_rows_read_per_day")) {
            $hardBreaches += "migration_day_rows_read"
        }
        if ($measurements.migration_day_rows_written -gt
            (& $readBound $contract.limits "d1_rows_written_per_day")) {
            $hardBreaches += "migration_day_rows_written"
        }
        $targetBreaches = @()
        foreach ($name in @(
            "worker_requests_per_day", "worker_subrequests_per_invocation",
            "d1_rows_read_per_day", "d1_rows_written_per_day"
        )) {
            if ([long]$measurements[$name] -gt (& $readBound $contract.internal_targets $name)) {
                $targetBreaches += $name
            }
        }
        if ($measurements.d1_pre_cutover_peak_bytes -gt
            (& $readBound $contract.internal_targets "d1_pre_cutover_peak_bytes")) {
            $targetBreaches += "d1_pre_cutover_peak_bytes"
        }
        if ($measurements.d1_steady_state_bytes -gt
            (& $readBound $contract.internal_targets "d1_steady_state_bytes")) {
            $targetBreaches += "d1_steady_state_bytes"
        }
        $breaches = @($hardBreaches + $targetBreaches | Select-Object -Unique)
        return [pscustomobject][ordered]@{
            schema_version = "release-free-plan-qualification-v2"
            state = if ($breaches.Count -eq 0) { "PASSED" } else { "BLOCKED" }
            reason = if ($breaches.Count -eq 0) { "CANDIDATE_TARGET_PROOF_PASSED" } else {
                "FREE_PLAN_LIMIT_EXCEEDED:" + ($breaches -join ",")
            }
            measurements = [pscustomobject]$measurements
            limits = $contract.limits
            internal_targets = $contract.internal_targets
            producer_count = $producers.Count
            production_calibration_over_limit = (
                (& $readBound $Proof.production_calibration "d1_rows_read_per_day") -gt
                    (& $readBound $contract.limits "d1_rows_read_per_day") -or
                (& $readBound $Proof.production_calibration "d1_rows_written_per_day") -gt
                    (& $readBound $contract.limits "d1_rows_written_per_day"))
        }
    } catch {
        return [pscustomobject][ordered]@{
            schema_version = "release-free-plan-qualification-v2"
            state = "BLOCKED"
            reason = [string]$_.Exception.Message
            measurements = $null
            limits = $null
            internal_targets = $null
            producer_count = 0
            production_calibration_over_limit = $false
        }
    }
}

function Assert-ReleaseEvidenceQualification {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [object]$ExpectedBehaviorKeys = $null,
        [string[]]$RequiredNodes = $releaseEvidencePrerequisiteNodes,
        [DateTimeOffset]$Now = [DateTimeOffset]::UtcNow
    )
    $contract = Get-ReleaseEvidenceContract -ContractPath $ContractPath
    $receipts = [ordered]@{}
    $digests = [ordered]@{}
    foreach ($node in $RequiredNodes) {
        $receipt = Get-ReleaseEvidenceCurrentReceipt -Root $Root `
            -ValidationKey $ValidationKey -Node $node
        if (-not $receipt) { throw "RELEASE_EVIDENCE_PRODUCER_MISSING:$node" }
        if ([int]$receipt.contract_version -ne [int]$contract.schema_version) {
            throw "RELEASE_EVIDENCE_CONTRACT_VERSION_MISMATCH:$node"
        }
        if ([string]$receipt.state -cne "PASSED") {
            throw "RELEASE_EVIDENCE_NODE_NOT_PASSED:$node"
        }
        $expected = if ($null -eq $ExpectedBehaviorKeys) {
            Get-ReleaseEvidenceBehaviorKey -ContractPath $ContractPath -Node $node `
                -Inputs $receipt.source_identity.behavior_inputs
        } elseif ($ExpectedBehaviorKeys -is [System.Collections.IDictionary]) {
            [string]$ExpectedBehaviorKeys[$node]
        } else { [string]$ExpectedBehaviorKeys.$node }
        if ([string]::IsNullOrWhiteSpace($expected) -or
            [string]$receipt.behavior_key -cne $expected) {
            throw "RELEASE_EVIDENCE_BEHAVIOR_KEY_MISMATCH:$node"
        }
        $definition = @($contract.nodes | Where-Object { [string]$_.id -ceq $node })[0]
        if ([string]$definition.qualification_kind -eq "LEASE" -and
            [string]$receipt.source_identity.qualification_state -cne "NOT_REQUIRED") {
            try { $expires = [DateTimeOffset]::Parse(
                [string]$receipt.source_identity.subject.expires_at) }
            catch { throw "RELEASE_EVIDENCE_LEASE_INVALID:$node" }
            if ($expires -le $Now) { throw "RELEASE_EVIDENCE_LEASE_STALE:$node" }
        }
        foreach ($dependency in @($receipt.dependencies)) {
            $current = Get-ReleaseEvidenceCurrentReceipt -Root $Root `
                -ValidationKey $ValidationKey -Node ([string]$dependency.node)
            if (-not $current -or [string]$current.receipt_digest -cne
                [string]$dependency.receipt_digest) {
                throw "RELEASE_EVIDENCE_DEPENDENCY_DIGEST_MOVED:$node"
            }
        }
        $receipts[$node] = $receipt
        $digests[$node] = [string]$receipt.receipt_digest
    }
    return [pscustomobject][ordered]@{
        schema_version = "release-evidence-qualification-v1"
        state = "PASSED"
        validation_key = $ValidationKey
        receipt_digests = [pscustomobject]$digests
        receipts = [pscustomobject]$receipts
    }
}

function Invoke-ReleaseEvidenceAdapter {
    param(
        [Parameter(Mandatory = $true)][string]$Adapter,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][hashtable]$Arguments
    )
    return Publish-ReleaseEvidenceAuthorityNode @Arguments -Node $Node -Adapter $Adapter
}

function Publish-ExactHeadCiEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "exact_head_ci" -Arguments $Arguments }
function Publish-WindowsRuntimeEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "windows_runtime" -Arguments $Arguments }
function Publish-MigrationAcceptanceEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "migration_acceptance" -Arguments $Arguments }
function Publish-MigrationLiveLeaseEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "migration_live_lease" -Arguments $Arguments }
function Publish-CandidatePlacementEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "candidate_placement" -Arguments $Arguments }
function Publish-DirectedWorkerEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "directed_worker" -Arguments $Arguments }
function Publish-WorkerCpuEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "worker_cpu" -Arguments $Arguments }
function Publish-SemanticContractEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "semantic_contract" -Arguments $Arguments }
function Publish-HumanAccessRootEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "human_access_root" -Arguments $Arguments }
function Publish-AccessProviderLeaseEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "access_provider_lease" -Arguments $Arguments }
function Publish-RollbackPrecheckEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "rollback_precheck" -Arguments $Arguments }
function Publish-PromoteAttemptEvidence { param([Parameter(Mandatory=$true)][hashtable]$Arguments) Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name -Node "promote_attempt" -Arguments $Arguments }

function Publish-FreePlanEvidence {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Arguments,
        [Parameter(Mandatory = $true)][string]$LimitsPath,
        [Parameter(Mandatory = $true)][object]$Proof
    )
    $qualification = Test-ReleaseFreePlanBoundedProof -LimitsPath $LimitsPath -Proof $Proof
    if ([string]$qualification.state -cne "PASSED") {
        throw "FREE_PLAN_QUALIFICATION_BLOCKED:$([string]$qualification.reason)"
    }
    $registeredSource = $Arguments.SourceIdentity
    $Arguments.SourceIdentity = [pscustomobject][ordered]@{
        qualification_state = "PASSED"
        candidate = $registeredSource.candidate
        input_digest = [string]$registeredSource.input_digest
        qualification = $qualification
    }
    return Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name `
        -Node "free_plan" -Arguments $Arguments
}

function Publish-ObserveAttemptEvidence {
    param([Parameter(Mandatory = $true)][hashtable]$Arguments)
    $prior = Get-ReleaseEvidenceCurrentReceipt -Root ([string]$Arguments.Root) `
        -ValidationKey ([string]$Arguments.ValidationKey) -Node "observe_attempt"
    if ($prior) { $Arguments.PreserveCurrentIndex = $true }
    return Invoke-ReleaseEvidenceAdapter -Adapter $MyInvocation.MyCommand.Name `
        -Node "observe_attempt" -Arguments $Arguments
}
