# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
function Get-CandidateChangedFiles {
    param([string]$StableRevision, [string]$CandidateRevision)
    $read = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "diff", "--name-only", $StableRevision, $CandidateRevision
    )
    if ($read.exit_code -ne 0) { throw "Candidate boundary classification failed." }
    @($read.stdout_lines | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
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
        "web/drizzle/0025_seed_legacy_news_reverse_projection.sql",
        "web/drizzle/0026_reconcile_legacy_news_current_identity.sql",
        "web/drizzle/0027_materialize_news_projection_counts.sql",
        "web/drizzle/0028_fence_legacy_news_current_identity.sql",
        "web/drizzle/0029_news_projection_receipt_index.sql",
        "web/drizzle/0030_news_evidence_cleanup_budget.sql",
        "web/drizzle/0031_bounded_learning_history_reads.sql"
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
        $isLegacyNewsReconciliation =
            $file -eq "web/drizzle/0026_reconcile_legacy_news_current_identity.sql" -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_details`' -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_index`' -and
            $sql -match '(?im)UPDATE\s+`news_index`' -and
            $sql -match '(?im)SUPERSEDED_CONTRACT' -and
            $sql -match '(?im)NOT\s+EXISTS\s*\(' -and
            $sql -match '(?im)s\.`projection_state`\s*=\s*''CURRENT''' -and
            $sql -match '(?im)s\.`receipt_digest`\s*=\s*g\.`expected_receipt_digest`' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        $isNewsFreePlanMaterialization =
            $file -eq "web/drizzle/0027_materialize_news_projection_counts.sql" -and
            $sql -match '(?im)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`news_projection_receipts_v2`' -and
            $sql -match '(?im)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`news_projection_counts`' -and
            $sql -match '(?im)CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+`news_projection_index_review_page_idx`' -and
            $sql -match '(?im)CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+`news_projection_index_review_category_page_idx`' -and
            $sql -match '(?im)`candidate_expiries`\s+text\s+NOT\s+NULL' -and
            $sql -match '(?im)INSERT\s+INTO\s+`news_projection_counts`' -and
            $sql -match '(?im)JOIN\s+`news_projection_state`' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|REPLACE|TRUNCATE|VACUUM)\b'
        $isLegacyNewsWriteFence =
            $file -eq "web/drizzle/0028_fence_legacy_news_current_identity.sql" -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`legacy_news_current_index_delete_fence`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`legacy_news_current_detail_delete_fence`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`legacy_news_noncurrent_index_insert_fence`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`legacy_news_current_index_update_fence`' -and
            $sql -match '(?im)SELECT\s+RAISE\s*\(\s*IGNORE\s*\)' -and
            $sql -match '(?im)SUPERSEDED_CONTRACT' -and
            $sql -match '(?im)s\.`projection_state`\s*=\s*''CURRENT''' -and
            $sql -notmatch '(?im)\b(DROP|REPLACE|TRUNCATE|VACUUM)\b'
        $isNewsReceiptIndex =
            $file -eq "web/drizzle/0029_news_projection_receipt_index.sql" -and
            $sql -match '(?im)ALTER\s+TABLE\s+`news_projection_receipts_v2`' -and
            $sql -match '(?im)`identity_keys_json`\s+text\s+NOT\s+NULL' -and
            $sql -match '(?im)`items_json`\s+text\s+NOT\s+NULL' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`legacy_news_v4_current_index_delete_fence`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`legacy_news_v4_current_detail_delete_fence`' -and
            $sql -match '(?im)news-projection-generation-v4' -and
            $sql -notmatch '(?im)\b(DROP|UPDATE|REPLACE|TRUNCATE|VACUUM)\b'
        $isNewsEvidenceCleanupBudget =
            $file -eq "web/drizzle/0030_news_evidence_cleanup_budget.sql" -and
            $sql -match '(?im)CREATE\s+TABLE\s+`news_evidence_cleanup_budget`' -and
            $sql -match '(?im)`reserved_rows_written`\s+integer\s+NOT\s+NULL' -and
            $sql -match '(?im)CHECK\s*\(\s*`id`\s*=\s*1\s*\)' -and
            $sql -match '(?im)CHECK\s*\(\s*`reserved_rows_written`\s*>=\s*0\s*\)' -and
            $sql -notmatch '(?im)\b(DROP|DELETE|UPDATE|REPLACE|TRUNCATE|VACUUM)\b'
        $isBoundedLearningHistoryReads =
            $file -eq "web/drizzle/0031_bounded_learning_history_reads.sql" -and
            $sql -match '(?im)CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+`learning_records_resource_identity_time_idx`' -and
            $sql -match '(?im)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`learning_record_counts`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`learning_record_count_insert`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`learning_record_count_delete`' -and
            $sql -match '(?im)CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+`learning_record_count_identity_update`' -and
            $sql -notmatch '(?im)\b(DROP|REPLACE|TRUNCATE|VACUUM)\b'
        if ($file -eq "web/drizzle/0030_news_evidence_cleanup_budget.sql" -and
            -not $isNewsEvidenceCleanupBudget) {
            throw "MIGRATION_CAPABILITY_CONTRACT_MISSING:$file"
        }
        if (($sql -match '(?im)\b(DROP|DELETE|UPDATE|REPLACE|TRUNCATE|VACUUM)\b') -and
            -not $isBoundedAuditHandover -and -not $isLegacyNewsHandover -and
            -not $isLegacyNewsReconciliation -and -not $isNewsFreePlanMaterialization -and
            -not $isLegacyNewsWriteFence -and -not $isNewsReceiptIndex -and
            -not $isNewsEvidenceCleanupBudget -and -not $isBoundedLearningHistoryReads) {
            throw "MIGRATION_REVERSE_INCOMPATIBLE:$file"
        }
    }
}

function Invoke-CoordinatedMigrationD1Query {
    param([Parameter(Mandatory = $true)][string]$Sql)
    # Keep the SQL in one bounded argument so Wrangler returns SELECT rows;
    # Invoke-WranglerJson bypasses npx.cmd's lower Windows transport limit.
    $command = ($Sql -replace "`r`n|`n|`r", " ").Trim()
    $blocks = @(Invoke-WranglerJson -Arguments @(
        "d1", "execute", "DB", "--remote", "--command", $command
    ))
    if ($blocks.Count -eq 0 -or @($blocks | Where-Object {
        -not [bool]$_.success
    }).Count -gt 0) {
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
    $candidatePayload = $candidateStatus.Content | ConvertFrom-ReleaseControlJson
    $healthPayload = $candidateHealth.Content | ConvertFrom-ReleaseControlJson
    $stablePayload = $stableStatus.Content | ConvertFrom-ReleaseControlJson
    $stableNewsPayload = $stableNewsHealth.Content | ConvertFrom-ReleaseControlJson
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
        stable_worker_version = [string]$Stable.worker_version_id
        stable_git_sha = [string]$Stable.git_sha
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
    if ([string]$candidateVersion.id -cne [string]$Candidate.worker_version_id -or
        (Get-ReleaseGitShaFromVersion -Version $candidateVersion) -cne
            [string]$Candidate.git_sha) {
        throw "MIGRATION_CANDIDATE_VERSION_IDENTITY_MISMATCH"
    }
    $stableVersionGit = Get-ReleaseGitShaFromVersion -Version $stableVersion
    if ([string]$stableVersion.id -cne [string]$Stable.worker_version_id -or
        ($stableVersionGit -and
            [string]$stableVersionGit -cne [string]$Stable.git_sha)) {
        throw "MIGRATION_STABLE_VERSION_IDENTITY_MISMATCH"
    }
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
WITH current_projection AS MATERIALIZED (
 SELECT json_extract(j.value,'$.detail_key') AS detail_key,
        json_extract(j.value,'$.category') AS category,
        json_extract(j.value,'$.cluster_id') AS cluster_id,
        coalesce(json_extract(j.value,'$.source_published_time'),
                 json_extract(j.value,'$.collector_first_seen_time')) AS published_time,
        json_extract(j.value,'$.collector_first_seen_time') AS collector_first_seen_time,
        CASE WHEN json_type(j.value,'$.parsed_at')='text' THEN 1 ELSE 0 END AS parsed,
        CASE WHEN json_extract(j.value,'$.model_visibility')='MODEL_VISIBLE'
             THEN 1 ELSE 0 END AS model_candidate,
        json_extract(j.value,'$.impact_expires_at') AS impact_expires_at,
        json_extract(j.value,'$.mirror_contract') AS mirror_contract,
        j.value AS payload
   FROM news_projection_receipts_v2 r,json_each(r.items_json) j
   JOIN news_projection_state active
     ON active.id=1 AND active.active_generation_id=r.generation_id
  WHERE r.batch_kind='index'
)
SELECT
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('news_projection_generations','news_projection_index','news_projection_details',
   'news_projection_batches','news_projection_receipts_v2','news_projection_state',
   'news_projection_counts')) AS projection_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='index' AND name IN
  ('news_projection_generations_state_idx','news_projection_index_ordinal_idx',
   'news_projection_index_page_idx','news_projection_index_category_idx',
   'news_projection_index_review_page_idx',
   'news_projection_index_review_category_page_idx')) AS projection_indexes,
 (SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN
   ('legacy_news_current_index_delete_fence','legacy_news_current_detail_delete_fence',
    'legacy_news_noncurrent_index_insert_fence','legacy_news_current_index_update_fence',
    'legacy_news_v4_current_index_delete_fence',
    'legacy_news_v4_current_detail_delete_fence'))
   AS projection_triggers,
 (SELECT count(*) FROM pragma_table_info('news_projection_counts') WHERE name IN
  ('generation_id','review_state','category','item_count','parsed_count','candidate_expiries')) AS projection_count_columns,
  (SELECT count(*) FROM pragma_table_info('news_projection_receipts_v2') WHERE name IN
   ('generation_id','batch_kind','batch_offset','item_count','payload_hash',
    'receipt_digest','identity_digest','identity_keys_json','items_json','updated_at')) AS projection_receipt_columns,
 (SELECT count(*) FROM pragma_table_info('operator_retry_sync_state') WHERE name IN
   ('id','payload_digest','item_count','synced_at')) AS retry_columns,
 (SELECT count(*) FROM sqlite_master WHERE type='table'
   AND name='news_evidence_cleanup_budget') AS evidence_cleanup_budget_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='table'
   AND name='learning_record_counts') AS learning_count_tables,
 (SELECT count(*) FROM sqlite_master WHERE type='index'
   AND name='learning_records_resource_identity_time_idx') AS learning_identity_indexes,
 (SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name IN
   ('learning_record_count_insert','learning_record_count_delete',
    'learning_record_count_identity_update')) AS learning_count_triggers,
 (SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN
  ('dashboard_snapshots','news_index','news_details','news_evidence_records')) AS legacy_tables,
  coalesce((SELECT json_array_length(json_extract(payload,'$.recent_decisions'))
    FROM dashboard_snapshots WHERE id=4 AND json_valid(payload)),0) AS legacy_decisions,
  (SELECT count(*) FROM current_projection pi
    WHERE EXISTS(SELECT 1 FROM news_index li WHERE li.detail_key=pi.detail_key))
    AS legacy_current_index_count,
  (SELECT count(*) FROM current_projection pi
    WHERE EXISTS(SELECT 1 FROM news_details ld WHERE ld.detail_key=pi.detail_key))
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
  (SELECT count(*) FROM news_index li
    WHERE li.detail_key IN (
      SELECT detail_key FROM news_index
       WHERE COALESCE(json_extract(payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
      EXCEPT SELECT detail_key FROM current_projection))
    AS legacy_extra_current_index_count,
  (SELECT count(*) FROM (
    SELECT detail_key,category,cluster_id,published_time,collector_first_seen_time,
           parsed,model_candidate,impact_expires_at,mirror_contract,payload
      FROM news_index
     WHERE COALESCE(json_extract(payload,'$.annotation_status'),'') <> 'SUPERSEDED_CONTRACT'
    EXCEPT
    SELECT detail_key,category,cluster_id,published_time,collector_first_seen_time,
           parsed,model_candidate,impact_expires_at,mirror_contract,payload
      FROM current_projection))
    AS legacy_current_row_mismatch_count,
  coalesce((SELECT item_count FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_all_count,
  coalesce((SELECT sum(item_count) FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state<>'ALL' AND c.category=''),-1) AS summary_review_count,
  coalesce((SELECT sum(item_count) FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id AND c.category<>''),-1) AS summary_category_count,
  coalesce((SELECT parsed_count FROM news_projection_counts c
    WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_parsed_count,
  coalesce((SELECT CASE WHEN candidate_expiries='' THEN 0 ELSE
      1 + length(candidate_expiries) - length(replace(candidate_expiries,char(10),'')) END
    FROM news_projection_counts c WHERE c.generation_id=s.active_generation_id
      AND c.review_state='ALL' AND c.category=''),-1) AS summary_candidate_count,
  (SELECT coalesce(sum(parsed),0) FROM current_projection) AS current_parsed_count,
  (SELECT count(*) FROM current_projection i
    WHERE i.model_candidate=1) AS current_candidate_count,
  (SELECT count(*) FROM current_projection i
    WHERE i.model_candidate=1
      AND (i.impact_expires_at IS NULL OR length(i.impact_expires_at)<>32
        OR substr(i.impact_expires_at,27)<>'+00:00')) AS invalid_candidate_expiry_count,
  s.projection_state,s.active_generation_id,s.snapshot_id,s.source_digest,s.receipt_digest,
 s.index_count,s.detail_count,s.missing_detail_count,s.invariant_violation_count,
 g.state AS generation_state,g.contract_version AS generation_contract_version,
 g.watermark AS generation_watermark,g.activated_at AS generation_activated_at,
 g.expected_receipt_digest,g.staged_index_count,g.staged_detail_count
FROM news_projection_state s JOIN news_projection_generations g
 ON g.generation_id=s.active_generation_id WHERE s.id=1
"@
    $capabilities = @(Invoke-CoordinatedMigrationD1Query -Sql $capabilitySql)
    if ($capabilities.Count -ne 1 -or
        [int]$capabilities[0].projection_tables -ne 7 -or
        [int]$capabilities[0].projection_indexes -ne 6 -or
        [int]$capabilities[0].projection_triggers -ne 6 -or
        [int]$capabilities[0].projection_count_columns -ne 6 -or
        [int]$capabilities[0].projection_receipt_columns -ne 10 -or
        [int]$capabilities[0].retry_columns -ne 4 -or
        [int]$capabilities[0].evidence_cleanup_budget_tables -ne 1 -or
        [int]$capabilities[0].learning_count_tables -ne 1 -or
        [int]$capabilities[0].learning_identity_indexes -ne 1 -or
        [int]$capabilities[0].learning_count_triggers -ne 3) {
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
        [int]$state.legacy_duplicate_cluster_count -ne 0 -or
        [int]$state.legacy_extra_current_index_count -ne 0 -or
        [int]$state.legacy_current_row_mismatch_count -ne 0) {
        throw "MIGRATION_LEGACY_NEWS_COMPATIBILITY_FAILED"
    }
    if ([int]$state.summary_all_count -ne [int]$state.index_count -or
        [int]$state.summary_review_count -ne [int]$state.index_count -or
        [int]$state.summary_category_count -ne [int]$state.index_count -or
        [int]$state.summary_parsed_count -ne [int]$state.current_parsed_count -or
        [int]$state.summary_candidate_count -ne [int]$state.current_candidate_count -or
        [int]$state.invalid_candidate_expiry_count -ne 0) {
        throw "MIGRATION_NEWS_SUMMARY_INVALID"
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
    $runtimeIdentity = Get-CoordinatedMigrationRuntimeRootIdentity
    return [ordered]@{
        validation_key = [string]$Candidate.validation_key
        candidate_git_sha = [string]$Candidate.git_sha
        candidate_worker_version = [string]$Candidate.worker_version_id
        candidate_windows_revision = [string]$Candidate.windows_revision
        stable_git_sha = [string]$Stable.git_sha
        stable_worker_version = [string]$Stable.worker_version_id
        stable_windows_revision = [string]$Stable.windows_revision
        runtime_root = [string]$runtimeIdentity.path
        runtime_root_identity = [string]$runtimeIdentity.identity
        database_id = [string]$database.uuid
        database_name = [string]$database.name
        migration_files = $migrationHashes
        applied_migrations = @($ledgerNames)
        pending_migrations = @()
        projection_tables = [int]$state.projection_tables
        projection_indexes = [int]$state.projection_indexes
        projection_triggers = [int]$state.projection_triggers
        projection_count_columns = [int]$state.projection_count_columns
        projection_receipt_columns = [int]$state.projection_receipt_columns
        operator_retry_columns = [int]$state.retry_columns
        evidence_cleanup_budget_tables = [int]$state.evidence_cleanup_budget_tables
        learning_count_tables = [int]$state.learning_count_tables
        learning_identity_indexes = [int]$state.learning_identity_indexes
        learning_count_triggers = [int]$state.learning_count_triggers
        legacy_tables = [int]$state.legacy_tables
        legacy_decisions = [int]$state.legacy_decisions
        legacy_news_index_count = [int]$state.legacy_current_index_count
        legacy_news_detail_count = [int]$state.legacy_current_detail_count
        legacy_news_missing_detail_count = [int]$state.legacy_missing_detail_count
        legacy_news_invariant_violation_count = [int]$state.legacy_review_violation_count
        legacy_news_parsed_flag_mismatch_count = [int]$state.legacy_parsed_flag_mismatch_count
        legacy_news_candidate_flag_mismatch_count = [int]$state.legacy_candidate_flag_mismatch_count
        legacy_news_duplicate_cluster_count = [int]$state.legacy_duplicate_cluster_count
        legacy_news_extra_current_index_count = [int]$state.legacy_extra_current_index_count
        legacy_news_current_row_mismatch_count = [int]$state.legacy_current_row_mismatch_count
        stable_news_status = [string]$endpoints.stable_news_status
        news_generation_id = [string]$state.active_generation_id
        news_contract_version = [string]$state.generation_contract_version
        news_watermark = [string]$state.generation_watermark
        news_activated_at = [string]$state.generation_activated_at
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

function Get-CoordinatedMigrationReceiptCore {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    return [ordered]@{
        schema_version = [string]$Receipt.schema_version
        checked_at = [string]$Receipt.checked_at
        expires_at = [string]$Receipt.expires_at
        evidence = $Receipt.evidence
    }
}

function Get-CoordinatedMigrationRootReceiptPath {
    param([Parameter(Mandatory = $true)][string]$Digest)
    if ($Digest -notmatch '^[0-9a-f]{64}$') { throw "MIGRATION_RECEIPT_TAMPERED" }
    return Join-Path $coordinatedMigrationRootReceiptRoot "$Digest.json"
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

function Write-CoordinatedMigrationRootReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $core = Get-CoordinatedMigrationReceiptCore -Receipt $Receipt
    if ([string]$Receipt.schema_version -ne "coordinated-storage-migration-receipt-v1" -or
        [string]$Receipt.receipt_digest -cne
            (Get-CoordinatedMigrationReceiptDigest -Core $core)) {
        throw "MIGRATION_RECEIPT_TAMPERED"
    }
    New-Item -ItemType Directory -Path $coordinatedMigrationRootReceiptRoot -Force |
        Out-Null
    $rootPath = Get-CoordinatedMigrationRootReceiptPath `
        -Digest ([string]$Receipt.receipt_digest)
    if (Test-Path -LiteralPath $rootPath) {
        $existing = Get-Content -LiteralPath $rootPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $existingCore = Get-CoordinatedMigrationReceiptCore -Receipt $existing
        if ([string]$existing.receipt_digest -cne [string]$Receipt.receipt_digest -or
            [string]$existing.receipt_digest -cne
                (Get-CoordinatedMigrationReceiptDigest -Core $existingCore)) {
            throw "MIGRATION_ROOT_RECEIPT_IMMUTABLE_CONFLICT"
        }
    } else {
        Write-ControlCenterJsonAtomic -Path $rootPath -Value $Receipt `
            -Depth 12 -Immutable
    }
}

function Write-CoordinatedMigrationReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    Write-CoordinatedMigrationRootReceipt -Receipt $Receipt
    Write-ControlCenterJsonAtomic -Path $coordinatedMigrationReceiptPath `
        -Value $Receipt -Depth 12
}

function Get-CoordinatedMigrationRootReceiptByDigest {
    param([Parameter(Mandatory = $true)][string]$Digest)
    $path = Get-CoordinatedMigrationRootReceiptPath -Digest $Digest
    if (Test-Path -LiteralPath $path) {
        return Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
    }
    if (-not (Test-Path -LiteralPath $coordinatedMigrationReceiptPath)) {
        throw "MIGRATION_RECEIPT_MISSING"
    }
    $legacy = Get-Content -LiteralPath $coordinatedMigrationReceiptPath `
        -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
    if ([string]$legacy.receipt_digest -cne $Digest) {
        throw "MIGRATION_RECEIPT_MISSING"
    }
    # Import the legacy single-file receipt into immutable digest-addressed
    # storage. The original file remains untouched and remains root evidence.
    Write-CoordinatedMigrationRootReceipt -Receipt $legacy
    return $legacy
}

function Assert-CoordinatedMigrationRootReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [Parameter(Mandatory = $true)][string]$Digest,
        [switch]$AllowStale
    )
    $receipt = Get-CoordinatedMigrationRootReceiptByDigest -Digest $Digest
    $core = Get-CoordinatedMigrationReceiptCore -Receipt $receipt
    if ([string]$receipt.schema_version -ne "coordinated-storage-migration-receipt-v1" -or
        [string]$receipt.receipt_digest -cne $Digest -or
        [string]$receipt.receipt_digest -cne
            (Get-CoordinatedMigrationReceiptDigest -Core $core)) {
        throw "MIGRATION_RECEIPT_TAMPERED"
    }
    $expires = ConvertTo-ReleaseTimestampUtc -Value $receipt.expires_at
    if ($expires -eq [DateTimeOffset]::MinValue) { throw "MIGRATION_RECEIPT_STALE" }
    if (-not $AllowStale -and $expires -le [DateTimeOffset]::UtcNow) {
        throw "MIGRATION_RECEIPT_STALE"
    }
    if ([string]$receipt.evidence.validation_key -ne [string]$Candidate.validation_key -or
        [string]$receipt.evidence.candidate_git_sha -ne [string]$Candidate.git_sha -or
        [string]$receipt.evidence.candidate_worker_version -ne
            [string]$Candidate.worker_version_id) {
        throw "MIGRATION_RECEIPT_CANDIDATE_MISMATCH"
    }
    if ([string]$receipt.evidence.stable_git_sha -ne [string]$Stable.git_sha -or
        [string]$receipt.evidence.stable_worker_version -ne
            [string]$Stable.worker_version_id) {
        throw "MIGRATION_RECEIPT_STABLE_MISMATCH"
    }
    if ($receipt.evidence.PSObject.Properties['candidate_windows_revision'] -and
        [string]$receipt.evidence.candidate_windows_revision -cne
            [string]$Candidate.windows_revision) {
        throw "MIGRATION_RECEIPT_CANDIDATE_MISMATCH"
    }
    if ($receipt.evidence.PSObject.Properties['stable_windows_revision'] -and
        [string]$receipt.evidence.stable_windows_revision -cne
            [string]$Stable.windows_revision) {
        throw "MIGRATION_RECEIPT_STABLE_MISMATCH"
    }
    if ($receipt.evidence.PSObject.Properties['runtime_root_identity']) {
        $runtimeIdentity = Get-CoordinatedMigrationRuntimeRootIdentity
        if ([string]$receipt.evidence.runtime_root -cne [string]$runtimeIdentity.path -or
            [string]$receipt.evidence.runtime_root_identity -cne
                [string]$runtimeIdentity.identity) {
            throw "MIGRATION_RECEIPT_RUNTIME_ROOT_MISMATCH"
        }
    }
    $recordedPaths = @($receipt.evidence.migration_files | ForEach-Object {
        [string]$_.path
    })
    if (($recordedPaths -join "`n") -cne (@($MigrationFiles) -join "`n")) {
        throw "MIGRATION_RECEIPT_FILE_SET_MISMATCH"
    }
    return $receipt
}

function Assert-CoordinatedMigrationLiveEvidenceMatchesRoot {
    param(
        [Parameter(Mandatory = $true)][object]$Receipt,
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [switch]$RequireExactGeneration
    )
    $live = Get-CoordinatedMigrationLiveEvidence -Candidate $Candidate `
        -Stable $Stable -MigrationFiles $MigrationFiles
    $immutableFields = @(
        "validation_key", "candidate_git_sha", "candidate_worker_version",
        "stable_git_sha", "stable_worker_version", "database_id", "database_name",
        "migration_files", "applied_migrations", "pending_migrations",
        "projection_tables", "projection_indexes", "projection_triggers", "projection_count_columns",
        "projection_receipt_columns", "operator_retry_columns",
        "evidence_cleanup_budget_tables", "learning_count_tables",
        "learning_identity_indexes", "learning_count_triggers",
        "legacy_tables", "stable_read", "candidate_read", "reverse_safe"
    )
    foreach ($field in $immutableFields) {
        $recordedValue = $Receipt.evidence.$field | ConvertTo-Json -Compress -Depth 12
        $liveValue = $live.$field | ConvertTo-Json -Compress -Depth 12
        if ($recordedValue -cne $liveValue) {
            throw "MIGRATION_RECEIPT_LIVE_EVIDENCE_MISMATCH:$field"
        }
    }
    $recordedActivation = ConvertTo-RequiredReleaseTime `
        $Receipt.evidence.news_activated_at
    $liveActivation = ConvertTo-RequiredReleaseTime $live.news_activated_at
    if ($liveActivation -lt $recordedActivation) {
        throw "MIGRATION_RECEIPT_GENERATION_REGRESSION"
    }
    if ($RequireExactGeneration -and
        [string]$live.news_generation_id -cne
            [string]$Receipt.evidence.news_generation_id) {
        throw "MIGRATION_RECEIPT_GENERATION_CHANGED"
    }
    if ([string]$live.news_generation_id -eq
            [string]$Receipt.evidence.news_generation_id) {
        foreach ($field in @(
            "news_contract_version", "news_watermark", "news_activated_at",
            "news_snapshot_id", "news_source_digest", "news_receipt_digest",
            "news_index_count", "news_detail_count"
        )) {
            if ([string]$live.$field -cne [string]$Receipt.evidence.$field) {
                throw "MIGRATION_RECEIPT_GENERATION_MUTATED:$field"
            }
        }
    }
    return $live
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
    $pointer = Get-Content -LiteralPath $coordinatedMigrationReceiptPath `
        -Raw -Encoding UTF8 | ConvertFrom-ReleaseControlJson
    $pointerCore = Get-CoordinatedMigrationReceiptCore -Receipt $pointer
    if ([string]$pointer.receipt_digest -cne
            (Get-CoordinatedMigrationReceiptDigest -Core $pointerCore)) {
        throw "MIGRATION_RECEIPT_TAMPERED"
    }
    $receipt = Assert-CoordinatedMigrationRootReceipt -Candidate $Candidate `
        -Stable $Stable -MigrationFiles $MigrationFiles `
        -Digest ([string]$pointer.receipt_digest)
    $null = Assert-CoordinatedMigrationLiveEvidenceMatchesRoot -Receipt $receipt `
        -Candidate $Candidate -Stable $Stable -MigrationFiles $MigrationFiles
    return $receipt
}

function Get-CoordinatedMigrationRuntimeRootIdentity {
    $path = [System.IO.Path]::GetFullPath($moduleRoot).TrimEnd('\')
    $authority = $path.ToUpperInvariant()
    return [pscustomobject]@{
        path = $path
        identity = Get-Sha256BytesHex `
            -Bytes ([System.Text.Encoding]::UTF8.GetBytes($authority))
    }
}

function Get-CoordinatedMigrationLiveEvidenceDigest {
    param([Parameter(Mandatory = $true)][object]$Evidence)
    $json = $Evidence | ConvertTo-Json -Compress -Depth 16
    return Get-Sha256BytesHex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

function Get-CoordinatedMigrationRenewalCore {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    return [ordered]@{
        schema_version = [string]$Receipt.schema_version
        state = [string]$Receipt.state
        root_migration_receipt_digest = [string]$Receipt.root_migration_receipt_digest
        previous_migration_renewal_digest = [string]$Receipt.previous_migration_renewal_digest
        validation_key = [string]$Receipt.validation_key
        candidate_git_sha = [string]$Receipt.candidate_git_sha
        candidate_worker_version_id = [string]$Receipt.candidate_worker_version_id
        candidate_windows_revision = [string]$Receipt.candidate_windows_revision
        stable_git_sha = [string]$Receipt.stable_git_sha
        stable_worker_version_id = [string]$Receipt.stable_worker_version_id
        stable_windows_revision = [string]$Receipt.stable_windows_revision
        database_id = [string]$Receipt.database_id
        database_name = [string]$Receipt.database_name
        runtime_root = [string]$Receipt.runtime_root
        runtime_root_identity = [string]$Receipt.runtime_root_identity
        migration_files = @($Receipt.migration_files)
        news_generation_id = [string]$Receipt.news_generation_id
        news_snapshot_id = [string]$Receipt.news_snapshot_id
        news_source_digest = [string]$Receipt.news_source_digest
        news_receipt_digest = [string]$Receipt.news_receipt_digest
        reverse_safe = [bool]$Receipt.reverse_safe
        checked_at = [string]$Receipt.checked_at
        expires_at = [string]$Receipt.expires_at
        live_evidence_digest = [string]$Receipt.live_evidence_digest
    }
}

function Get-CoordinatedMigrationRenewalReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 16
    return Get-Sha256BytesHex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

function Get-CoordinatedMigrationRenewalReceiptPath {
    param([Parameter(Mandatory = $true)][string]$Digest)
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
    }
    return Join-Path $coordinatedMigrationRenewalReceiptRoot "$Digest.json"
}

function Write-CoordinatedMigrationRenewalReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $core = Get-CoordinatedMigrationRenewalCore -Receipt $Receipt
    if ([string]$Receipt.receipt_digest -cne
            (Get-CoordinatedMigrationRenewalReceiptDigest -Core $core)) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
    }
    $path = Get-CoordinatedMigrationRenewalReceiptPath `
        -Digest ([string]$Receipt.receipt_digest)
    New-Item -ItemType Directory -Path $coordinatedMigrationRenewalReceiptRoot -Force |
        Out-Null
    if (Test-Path -LiteralPath $path) {
        $existing = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $existingCore = Get-CoordinatedMigrationRenewalCore -Receipt $existing
        if ([string]$existing.receipt_digest -ceq [string]$Receipt.receipt_digest -and
            [string]$existing.receipt_digest -ceq
                (Get-CoordinatedMigrationRenewalReceiptDigest -Core $existingCore)) {
            return
        }
        throw "MIGRATION_QUALIFICATION_RENEWAL_IMMUTABLE_CONFLICT"
    }
    Write-ControlCenterJsonAtomic -Path $path -Value $Receipt `
        -Depth 16 -Immutable
}

function Assert-CoordinatedMigrationRenewalReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [Parameter(Mandatory = $true)][string]$Digest,
        [Parameter(Mandatory = $true)][string]$RootDigest,
        [switch]$AllowStale,
        [hashtable]$Visited = $null
    )
    if ($null -eq $Visited) { $Visited = @{} }
    if ($Visited.Count -ge $coordinatedMigrationRenewalMaximumDepth) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_CHAIN_BOUND_EXCEEDED"
    }
    if ($Visited.ContainsKey($Digest)) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
    }
    $Visited[$Digest] = $true
    $path = Get-CoordinatedMigrationRenewalReceiptPath -Digest $Digest
    if (-not (Test-Path -LiteralPath $path)) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_MISSING"
    }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    $core = Get-CoordinatedMigrationRenewalCore -Receipt $receipt
    if ([string]$receipt.schema_version -ne "migration-qualification-renewal-v1" -or
        [string]$receipt.state -ne "MIGRATION_QUALIFICATION_RENEWED" -or
        [string]$receipt.receipt_digest -cne $Digest -or
        [string]$receipt.receipt_digest -cne
            (Get-CoordinatedMigrationRenewalReceiptDigest -Core $core)) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
    }
    if ([string]$receipt.root_migration_receipt_digest -cne $RootDigest -or
        [string]$Candidate.migration_acceptance.receipt_digest -cne $RootDigest) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_ROOT_MISMATCH"
    }
    $null = Assert-CoordinatedMigrationRootReceipt -Candidate $Candidate `
        -Stable $Stable -MigrationFiles $MigrationFiles -Digest $RootDigest -AllowStale
    if ([string]$receipt.validation_key -cne [string]$Candidate.validation_key -or
        [string]$receipt.candidate_git_sha -cne [string]$Candidate.git_sha -or
        [string]$receipt.candidate_worker_version_id -cne
            [string]$Candidate.worker_version_id -or
        [string]$receipt.candidate_windows_revision -cne
            [string]$Candidate.windows_revision) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_CANDIDATE_MISMATCH"
    }
    if ([string]$receipt.stable_git_sha -cne [string]$Stable.git_sha -or
        [string]$receipt.stable_worker_version_id -cne [string]$Stable.worker_version_id -or
        [string]$receipt.stable_windows_revision -cne [string]$Stable.windows_revision) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_STABLE_MISMATCH"
    }
    $runtimeIdentity = Get-CoordinatedMigrationRuntimeRootIdentity
    if ([string]$receipt.runtime_root -cne [string]$runtimeIdentity.path -or
        [string]$receipt.runtime_root_identity -cne [string]$runtimeIdentity.identity) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_RUNTIME_ROOT_MISMATCH"
    }
    $expires = ConvertTo-ReleaseTimestampUtc -Value $receipt.expires_at
    if ($expires -eq [DateTimeOffset]::MinValue) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_STALE"
    }
    if (-not $AllowStale -and $expires -le [DateTimeOffset]::UtcNow) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_STALE"
    }
    $previousDigest = [string]$receipt.previous_migration_renewal_digest
    if ($previousDigest) {
        $previous = Assert-CoordinatedMigrationRenewalReceipt -Candidate $Candidate `
            -Stable $Stable -MigrationFiles $MigrationFiles -Digest $previousDigest `
            -RootDigest $RootDigest -AllowStale -Visited $Visited
        $previousChecked = ConvertTo-ReleaseTimestampUtc -Value $previous.checked_at
        $checked = ConvertTo-ReleaseTimestampUtc -Value $receipt.checked_at
        if ($checked -le $previousChecked) {
            throw "MIGRATION_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
        }
    }
    return $receipt
}

function Get-LatestCoordinatedMigrationRenewalReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [Parameter(Mandatory = $true)][string]$RootDigest
    )
    if (-not (Test-Path -LiteralPath $coordinatedMigrationRenewalReceiptRoot)) {
        return $null
    }
    $files = @(Get-ChildItem -LiteralPath $coordinatedMigrationRenewalReceiptRoot `
        -File -Filter "*.json")
    if ($files.Count -gt $coordinatedMigrationRenewalStoreMaximumReceipts) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_STORE_BOUND_EXCEEDED"
    }
    $candidates = @()
    foreach ($file in $files) {
        if ($file.Length -gt 131072 -or
            $file.BaseName -notmatch '^[0-9a-f]{64}$') {
            throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
        }
        try {
            $receipt = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
        } catch { throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED" }
        $core = Get-CoordinatedMigrationRenewalCore -Receipt $receipt
        if ([string]$receipt.receipt_digest -cne [string]$file.BaseName -or
            [string]$receipt.receipt_digest -cne
                (Get-CoordinatedMigrationRenewalReceiptDigest -Core $core)) {
            throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
        }
        if ([string]$receipt.root_migration_receipt_digest -cne $RootDigest -or
            [string]$receipt.validation_key -cne [string]$Candidate.validation_key) {
            continue
        }
        $valid = Assert-CoordinatedMigrationRenewalReceipt -Candidate $Candidate `
            -Stable $Stable -MigrationFiles $MigrationFiles `
            -Digest ([string]$receipt.receipt_digest) -RootDigest $RootDigest -AllowStale
        $checked = ConvertTo-ReleaseTimestampUtc -Value $valid.checked_at
        if ($checked -eq [DateTimeOffset]::MinValue) {
            throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
        }
        $candidates += @([pscustomobject]@{ receipt = $valid; checked_at = $checked })
    }
    if ($candidates.Count -eq 0) { return $null }
    return @($candidates | Sort-Object checked_at -Descending)[0].receipt
}

function Set-CandidateMigrationQualificationFromReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Receipt
    )
    $Candidate | Add-Member -Force -NotePropertyName migration_qualification `
        -NotePropertyValue ([pscustomobject]@{
            state = "MIGRATION_QUALIFICATION_RENEWED"
            validation_key = [string]$Candidate.validation_key
            root_receipt_digest = [string]$Receipt.root_migration_receipt_digest
            previous_migration_renewal_digest =
                [string]$Receipt.previous_migration_renewal_digest
            receipt_digest = [string]$Receipt.receipt_digest
            checked_at = [string]$Receipt.checked_at
            expires_at = [string]$Receipt.expires_at
        })
}

function Assert-CoordinatedMigrationRenewalSafety {
    param([Parameter(Mandatory = $true)][object]$Stable)
    $state = Get-ReleaseControlState
    if (-not $state -or $state.transaction) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_TRANSACTION_ACTIVE"
    }
    if (Test-Path -LiteralPath $runtimeStateMigrationLockPath) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_LOCK_ACTIVE"
    }
    $runtimeState = Get-RuntimeCodeState
    if (-not $runtimeState -or
        [string]$runtimeState.applied_revision -cne [string]$Stable.windows_revision) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_WINDOWS_IDENTITY_UNSAFE"
    }
    $deployment = Get-CloudflareDeployment
    $owners = @($deployment.versions | Where-Object { [double]$_.percentage -gt 0 })
    if ($owners.Count -ne 1 -or
        [string]$owners[0].version_id -cne [string]$Stable.worker_version_id -or
        [double]$owners[0].percentage -ne 100) {
        throw "MIGRATION_QUALIFICATION_RENEWAL_PRODUCTION_OWNERSHIP_UNSAFE"
    }
}

function New-CoordinatedMigrationRenewalReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][object]$RootReceipt,
        [Parameter(Mandatory = $true)][object]$LiveEvidence,
        [string]$PreviousRenewalDigest = ""
    )
    $runtimeIdentity = Get-CoordinatedMigrationRuntimeRootIdentity
    $checkedAt = [DateTimeOffset]::UtcNow
    $core = [ordered]@{
        schema_version = "migration-qualification-renewal-v1"
        state = "MIGRATION_QUALIFICATION_RENEWED"
        root_migration_receipt_digest = [string]$RootReceipt.receipt_digest
        previous_migration_renewal_digest = $PreviousRenewalDigest
        validation_key = [string]$Candidate.validation_key
        candidate_git_sha = [string]$Candidate.git_sha
        candidate_worker_version_id = [string]$Candidate.worker_version_id
        candidate_windows_revision = [string]$Candidate.windows_revision
        stable_git_sha = [string]$Stable.git_sha
        stable_worker_version_id = [string]$Stable.worker_version_id
        stable_windows_revision = [string]$Stable.windows_revision
        database_id = [string]$LiveEvidence.database_id
        database_name = [string]$LiveEvidence.database_name
        runtime_root = [string]$runtimeIdentity.path
        runtime_root_identity = [string]$runtimeIdentity.identity
        migration_files = @($LiveEvidence.migration_files)
        news_generation_id = [string]$LiveEvidence.news_generation_id
        news_snapshot_id = [string]$LiveEvidence.news_snapshot_id
        news_source_digest = [string]$LiveEvidence.news_source_digest
        news_receipt_digest = [string]$LiveEvidence.news_receipt_digest
        reverse_safe = [bool]$LiveEvidence.reverse_safe
        checked_at = $checkedAt.ToString("o")
        expires_at = $checkedAt.Add($coordinatedMigrationReceiptMaxAge).ToString("o")
        live_evidence_digest = Get-CoordinatedMigrationLiveEvidenceDigest `
            -Evidence $LiveEvidence
    }
    $receipt = [pscustomobject]$core
    $receipt | Add-Member -NotePropertyName receipt_digest `
        -NotePropertyValue (Get-CoordinatedMigrationRenewalReceiptDigest -Core $core)
    return $receipt
}

function Ensure-CoordinatedMigrationQualification {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][string[]]$MigrationFiles,
        [TimeSpan]$MinimumRemaining = [TimeSpan]::Zero
    )
    if ([string]$Candidate.windows_revision -cne [string]$Candidate.git_sha) {
        throw "MIGRATION_QUALIFICATION_CANDIDATE_IDENTITY_INVALID"
    }
    if ([string]$Stable.windows_revision -cne [string]$Stable.git_sha) {
        throw "MIGRATION_QUALIFICATION_STABLE_IDENTITY_INVALID"
    }
    if (-not $Candidate.migration_acceptance -or
        [string]$Candidate.migration_acceptance.validation_key -cne
            [string]$Candidate.validation_key) {
        throw "MIGRATION_ACCEPTANCE_MISSING"
    }
    $rootDigest = [string]$Candidate.migration_acceptance.receipt_digest
    $previousRenewalDigest = ""
    $stale = $false
    $candidateRenewal = $null
    if ($Candidate.migration_qualification) {
        if ([string]$Candidate.migration_qualification.state -ne
                "MIGRATION_QUALIFICATION_RENEWED") {
            throw "MIGRATION_QUALIFICATION_RENEWAL_TAMPERED"
        }
        $candidateRenewal = Assert-CoordinatedMigrationRenewalReceipt `
            -Candidate $Candidate -Stable $Stable -MigrationFiles $MigrationFiles `
            -Digest ([string]$Candidate.migration_qualification.receipt_digest) `
            -RootDigest $rootDigest -AllowStale
    }
    $storedRenewal = Get-LatestCoordinatedMigrationRenewalReceipt `
        -Candidate $Candidate -Stable $Stable -MigrationFiles $MigrationFiles `
        -RootDigest $rootDigest
    $currentRenewal = if ($storedRenewal) { $storedRenewal } else { $candidateRenewal }
    if ($currentRenewal) {
        $previousRenewalDigest = [string]$currentRenewal.receipt_digest
        try {
            $current = Assert-CoordinatedMigrationRenewalReceipt -Candidate $Candidate `
                -Stable $Stable -MigrationFiles $MigrationFiles `
                -Digest $previousRenewalDigest -RootDigest $rootDigest
            $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $current.expires_at
            if ($MinimumRemaining -le [TimeSpan]::Zero -or
                $expiresAt -gt [DateTimeOffset]::UtcNow.Add($MinimumRemaining)) {
                Set-CandidateMigrationQualificationFromReceipt `
                    -Candidate $Candidate -Receipt $current
                return [pscustomobject]@{
                    state = "MIGRATION_QUALIFICATION_RENEWED"
                    root_receipt_digest = $rootDigest
                    receipt = $current
                }
            }
            $stale = $true
            $null = Assert-CoordinatedMigrationRenewalReceipt -Candidate $Candidate `
                -Stable $Stable -MigrationFiles $MigrationFiles `
                -Digest $previousRenewalDigest -RootDigest $rootDigest -AllowStale
        } catch {
            if ($_.Exception.Message -ne "MIGRATION_QUALIFICATION_RENEWAL_STALE") { throw }
            $stale = $true
            $null = Assert-CoordinatedMigrationRenewalReceipt -Candidate $Candidate `
                -Stable $Stable -MigrationFiles $MigrationFiles `
                -Digest $previousRenewalDigest -RootDigest $rootDigest -AllowStale
        }
    } else {
        try {
            $root = Assert-CoordinatedMigrationRootReceipt -Candidate $Candidate `
                -Stable $Stable -MigrationFiles $MigrationFiles -Digest $rootDigest
            $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $root.expires_at
            if ($MinimumRemaining -le [TimeSpan]::Zero -or
                $expiresAt -gt [DateTimeOffset]::UtcNow.Add($MinimumRemaining)) {
                return [pscustomobject]@{
                    state = "MIGRATION_ACCEPTED"
                    root_receipt_digest = $rootDigest
                    receipt = $root
                }
            }
            $stale = $true
        } catch {
            if ($_.Exception.Message -ne "MIGRATION_RECEIPT_STALE") { throw }
            $stale = $true
        }
    }
    if (-not $stale) { throw "MIGRATION_QUALIFICATION_RENEWAL_NOT_APPLICABLE" }
    $root = Assert-CoordinatedMigrationRootReceipt -Candidate $Candidate `
        -Stable $Stable -MigrationFiles $MigrationFiles -Digest $rootDigest -AllowStale
    Assert-CoordinatedMigrationRenewalSafety -Stable $Stable
    $live = Assert-CoordinatedMigrationLiveEvidenceMatchesRoot -Receipt $root `
        -Candidate $Candidate -Stable $Stable -MigrationFiles $MigrationFiles `
        -RequireExactGeneration
    Assert-CoordinatedMigrationRenewalSafety -Stable $Stable
    $receipt = New-CoordinatedMigrationRenewalReceipt -Candidate $Candidate `
        -Stable $Stable -RootReceipt $root -LiveEvidence $live `
        -PreviousRenewalDigest $previousRenewalDigest
    Write-CoordinatedMigrationRenewalReceipt -Receipt $receipt
    Set-CandidateMigrationQualificationFromReceipt `
        -Candidate $Candidate -Receipt $receipt
    Write-ReleaseHistory -Event "MIGRATION_QUALIFICATION_RENEWED" `
        -Release $Candidate -Detail @{
            validation_key = [string]$Candidate.validation_key
            root_migration_receipt_digest = $rootDigest
            previous_migration_renewal_digest = $previousRenewalDigest
            renewal_receipt_digest = [string]$receipt.receipt_digest
            live_evidence_digest = [string]$receipt.live_evidence_digest
        }
    return [pscustomobject]@{
        state = "MIGRATION_QUALIFICATION_RENEWED"
        root_receipt_digest = $rootDigest
        receipt = $receipt
    }
}

function Test-WatchdogRecoverySuppressed {
    param(
        [Parameter(Mandatory = $true)][string]$ServiceKey,
        [Parameter(Mandatory = $true)][string]$ServiceState,
        [object]$ReleaseState
    )
    if ($ServiceKey -eq 'collector' -and (Test-CollectorClockRecoveryHold)) { return $true }
    if ($ServiceKey -ne "sync" -or $ServiceState -ne "STOPPED") { return $false }
    return [bool]($ReleaseState -and $ReleaseState.transaction -and (
        ([string]$ReleaseState.transaction.type -eq "PROMOTE" -and
         [string]$ReleaseState.transaction.phase -in @("PRECHECK", "CUTOVER")) -or
        ([string]$ReleaseState.transaction.type -eq "REVERSE" -and
         [string]$ReleaseState.transaction.phase -eq "REVERSING")
    ))
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
    $null = Finalize-CandidateQualificationEvidence `
        -WhyRan "MIGRATION_COMPATIBILITY_COMPLETED"
    return (Get-ReleaseControlState).candidate
}

function Test-PreservedCandidateEvidenceAvailable {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    if ([string]$Candidate.validation_key -ne
            "$([string]$Candidate.worker_version_id):$([string]$Candidate.git_sha)" -or
        [string]$Candidate.artifact_kind -ne $productionCandidateArtifactKind -or
        -not $Candidate.validation -or
        [string]$Candidate.validation.key -ne [string]$Candidate.validation_key) {
        return $false
    }
    if ($Candidate.migration_acceptance -and
        [string]$Candidate.migration_acceptance.validation_key -ne
            [string]$Candidate.validation_key) {
        return $false
    }
    $validationRun = [string]$Candidate.validation.validation_run
    if ($validationRun) {
        $plan = Read-WorkerCpuRunArtifact -ValidationRun $validationRun -Name "plan.json"
        $provider = Read-WorkerCpuRunArtifact `
            -ValidationRun $validationRun -Name "provider-evidence.json"
        if (-not $plan -or -not $provider -or
            [string]$plan.validation_run -ne $validationRun -or
            [string]$provider.validation_run -ne $validationRun -or
            [string]$Candidate.validation.worker_qualification.candidate_worker_version -ne
                [string]$Candidate.worker_version_id -or
            [string]$Candidate.validation.worker_qualification.candidate_git_sha -ne
                [string]$Candidate.git_sha) {
            return $false
        }
    }
    try {
        $version = Get-CloudflareVersionDetails `
            -VersionId ([string]$Candidate.worker_version_id)
        if ([string]$version.id -ne [string]$Candidate.worker_version_id -or
            (Get-ReleaseGitShaFromVersion -Version $version) -ne
                [string]$Candidate.git_sha -or
            (Get-ReleaseArtifactKindFromVersion -Version $version) -ne
                $productionCandidateArtifactKind) {
            return $false
        }
    } catch { return $false }
    return $true
}

function Assert-CandidateCpuQualificationReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Validation
    )
    $qualificationKey = [string]$Validation.worker_qualification.key
    if ($qualificationKey -notmatch '^[0-9a-f]{64}$' -or
        [string]$Validation.cpu_evidence.qualification_key -ne $qualificationKey) {
        throw "CANDIDATE_SUPERSESSION_CPU_QUALIFICATION_KEY_INVALID"
    }
    $receipt = Get-WorkerCpuQualificationReceipt `
        -QualificationKey $qualificationKey
    if (-not $receipt -or
        [string]$receipt.receipt_digest -ne
            [string]$Validation.cpu_evidence.qualification_receipt_digest) {
        throw "CANDIDATE_SUPERSESSION_CPU_RECEIPT_INVALID"
    }
    if ([string]$Validation.cpu_evidence.qualification_mode -eq
            "CPU_QUALIFICATION_REUSED") {
        if ([string]$Validation.cpu_evidence.source_worker_version -ne
                [string]$receipt.source_worker_version -or
            [string]$Validation.cpu_evidence.source_git_sha -ne
                [string]$receipt.source_git_sha -or
            [string]$Validation.cpu_evidence.worker_version_id -ne
                [string]$Candidate.worker_version_id -or
            [string]$Validation.cpu_evidence.candidate_git_sha -ne
                [string]$Candidate.git_sha) {
            throw "CANDIDATE_SUPERSESSION_CPU_REUSE_LINEAGE_INVALID"
        }
    } elseif ([string]$receipt.source_worker_version -ne
            [string]$Candidate.worker_version_id -or
        [string]$receipt.source_git_sha -ne [string]$Candidate.git_sha) {
        throw "CANDIDATE_SUPERSESSION_CPU_RECEIPT_INVALID"
    }
    return $receipt
}

function Test-CandidateSupersessionIdentity {
    param([object]$Candidate)
    return [bool](
        $Candidate -and
        [string]$Candidate.worker_version_id -match
            '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' -and
        [string]$Candidate.git_sha -match '^[0-9a-f]{40}$' -and
        [string]$Candidate.windows_revision -eq [string]$Candidate.git_sha -and
        [string]$Candidate.artifact_kind -eq $productionCandidateArtifactKind -and
        [string]$Candidate.validation_key -eq
            "$([string]$Candidate.worker_version_id):$([string]$Candidate.git_sha)"
    )
}

function Test-UnqualifiedSupersessionIntermediate {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][array]$History
    )
    if (-not (Test-CandidateSupersessionIdentity -Candidate $Candidate) -or
        [string]$Candidate.compatibility_state -notin @(
            "PENDING", "REVIEW_REQUIRED", "COORDINATED_STORAGE_MIGRATION_PASSED"
        ) -or
        [string]$Candidate.validation_state -notin @(
            "NEW", "CHECKS_PENDING", "CHECKS_BLOCKED", "TESTING", "REVIEW_REQUIRED",
            "PLATFORM_PENDING"
        )) {
        return $false
    }
    if ($Candidate.migration_acceptance -and (
            [string]$Candidate.migration_acceptance.validation_key -ne
                [string]$Candidate.validation_key -or
            [string]$Candidate.migration_acceptance.receipt_digest -notmatch
                '^[0-9a-f]{64}$')) {
        return $false
    }
    if ($Candidate.validation -and
        [string]$Candidate.validation.key -ne [string]$Candidate.validation_key) {
        return $false
    }
    $accepted = @($History | Where-Object {
        [string]$_.release.validation_key -eq [string]$Candidate.validation_key -and
        [string]$_.event -in @(
            "CANDIDATE_PASSED", "CANDIDATE_ACCESS_BOUNDARY_ACCEPTED",
            "PROMOTION_STARTED", "STABLE_COMMITTED"
        )
    })
    return [bool]($accepted.Count -eq 0)
}

function Test-QualifiedSupersessionCandidateShape {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    if (-not (Test-CandidateSupersessionIdentity -Candidate $Candidate) -or
        [string]$Candidate.validation_state -ne "PASSED" -or
        [string]$Candidate.compatibility_state -notin @(
            "PASSED", "COORDINATED_STORAGE_MIGRATION_PASSED"
        ) -or -not $Candidate.migration_acceptance -or
        [string]$Candidate.migration_acceptance.validation_key -ne
            [string]$Candidate.validation_key -or -not $Candidate.validation -or
        [string]$Candidate.validation.key -ne [string]$Candidate.validation_key -or
        [string]$Candidate.validation.repository -ne "PASSED" -or
        [string]$Candidate.validation.windows -ne "PASSED" -or
        [string]$Candidate.validation.cloudflare -ne "PASSED" -or
        [string]$Candidate.validation.data_parity.state -notin @(
            "PASSED", "PASSED_WITH_DEFERRED_OBLIGATIONS"
        ) -or -not $Candidate.validation.worker_qualification -or
        [string]$Candidate.validation.worker_qualification.key -notmatch '^[0-9a-f]{64}$' -or
        [string]$Candidate.validation.worker_qualification.candidate_worker_version -ne
            [string]$Candidate.worker_version_id -or
        [string]$Candidate.validation.worker_qualification.candidate_git_sha -ne
            [string]$Candidate.git_sha -or -not $Candidate.validation.cpu_evidence -or
        [string]$Candidate.validation.cpu_evidence.qualification_key -ne
            [string]$Candidate.validation.worker_qualification.key -or
        [string]$Candidate.validation.cpu_evidence.qualification_receipt_digest -notmatch
            '^[0-9a-f]{64}$' -or
        [string]$Candidate.migration_acceptance.receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$Candidate.validation.auth_inspection.state -notin @(
            "HUMAN_ACCESS_BOUNDARY_ACCEPTED", "ACCESS_QUALIFICATION_REUSED",
            "ACCESS_QUALIFICATION_RENEWED"
        )) {
        return $false
    }
    return (Test-PreservedCandidateEvidenceAvailable -Candidate $Candidate)
}

function Test-ObservationFailedSupersessionCandidateShape {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    return [bool](
        (Test-CandidateSupersessionIdentity -Candidate $Candidate) -and
        [string]$Candidate.validation_state -eq "FAILED" -and
        [string]$Candidate.compatibility_state -eq "PASSED" -and
        $Candidate.validation -and
        [string]$Candidate.validation.key -eq [string]$Candidate.validation_key -and
        [string]$Candidate.validation.error -eq "OBSERVATION_FAILED" -and
        [string]$Candidate.validation.reason -eq
            "DEFERRED_PROJECTION_OBSERVATION_TIMEOUT" -and
        $Candidate.validation.prior_validation
    )
}

function Get-BoundedReleaseHistoryTail {
    param(
        [Parameter(Mandatory = $true)][int]$MaximumLines,
        [Parameter(Mandatory = $true)][long]$MaximumBytes
    )
    $stream = [System.IO.FileStream]::new(
        $releaseHistoryPath, [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    )
    try {
        $readLength = [int][Math]::Min($stream.Length, $MaximumBytes)
        $start = $stream.Length - $readLength
        $null = $stream.Seek($start, [System.IO.SeekOrigin]::Begin)
        $buffer = [byte[]]::new($readLength)
        $total = 0
        while ($total -lt $readLength) {
            $count = $stream.Read($buffer, $total, $readLength - $total)
            if ($count -le 0) { break }
            $total += $count
        }
    } finally { $stream.Dispose() }
    $offset = 0
    if ($start -gt 0) {
        while ($offset -lt $total -and $buffer[$offset] -ne 10) { $offset++ }
        if ($offset -ge $total) {
            throw "CANDIDATE_SUPERSESSION_HISTORY_BYTE_BOUND_EXCEEDED"
        }
        $offset++
    }
    $text = [System.Text.Encoding]::UTF8.GetString($buffer, $offset, $total - $offset)
    $lines = @($text -split "`n" | ForEach-Object {
        $_.TrimEnd("`r").TrimStart([char]0xFEFF)
    } |
        Where-Object { $_ })
    if ($start -gt 0 -and $lines.Count -lt $MaximumLines) {
        throw "CANDIDATE_SUPERSESSION_HISTORY_BYTE_BOUND_EXCEEDED"
    }
    return @($lines | Select-Object -Last $MaximumLines)
}

function Test-CandidateSupersessionAncestry {
    param(
        [Parameter(Mandatory = $true)][object]$Predecessor,
        [Parameter(Mandatory = $true)][object]$Successor,
        [Parameter(Mandatory = $true)][string]$MainRevision
    )
    foreach ($revision in @(
        [string]$Predecessor.git_sha, [string]$Successor.git_sha, $MainRevision
    )) {
        $exists = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
            "-C", $repositoryRoot, "cat-file", "-e", "$revision`^{commit}"
        )
        if ([int]$exists.exit_code -ne 0) { return $false }
    }
    $edge = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "merge-base", "--is-ancestor",
        [string]$Predecessor.git_sha, [string]$Successor.git_sha
    )
    $main = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "merge-base", "--is-ancestor",
        [string]$Successor.git_sha, $MainRevision
    )
    return [bool]([int]$edge.exit_code -eq 0 -and [int]$main.exit_code -eq 0)
}

function Get-CandidateSupersessionRecoveryPlan {
    param(
        [Parameter(Mandatory = $true)][object]$Head,
        [Parameter(Mandatory = $true)][string]$MainRevision
    )
    if (-not (Test-Path -LiteralPath $releaseHistoryPath)) {
        return [pscustomobject]@{ state = "NOT_APPLICABLE"; reason = "HISTORY_MISSING" }
    }
    $history = @()
    try {
        $historyLines = @(Get-BoundedReleaseHistoryTail `
            -MaximumLines $candidateSupersessionHistoryLimit `
            -MaximumBytes $candidateSupersessionHistoryByteLimit)
    } catch {
        return [pscustomobject]@{
            state = "FAILED"; reason = $_.Exception.Message
        }
    }
    foreach ($line in $historyLines) {
        try { $history += @($line | ConvertFrom-ReleaseControlJson) }
        catch {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_HISTORY_INVALID"
            }
        }
    }
    $headKey = [string]$Head.validation_key
    $current = $Head
    $visited = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $visitedWorkers = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    $null = $visited.Add($headKey)
    $null = $visitedWorkers.Add([string]$Head.worker_version_id)
    $traversed = @()
    for ($depth = 0; $depth -lt $candidateSupersessionMaxDepth; $depth++) {
        if (-not (Test-UnqualifiedSupersessionIntermediate `
                -Candidate $current -History $history)) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_INTERMEDIATE_UNSAFE"
                chain_head = $headKey; traversed = $traversed
            }
        }
        $edges = @($history | Where-Object {
            [string]$_.event -eq "CANDIDATE_SUPERSEDED" -and
            [string]$_.detail.replacement_key -eq [string]$current.validation_key
        })
        if ($edges.Count -eq 0) {
            return [pscustomobject]@{
                state = if ($depth -eq 0) { "NOT_APPLICABLE" } else {
                    "REUSE_UNAVAILABLE"
                }
                reason = if ($depth -eq 0) {
                    "CANDIDATE_SUPERSESSION_CHAIN_NOT_FOUND"
                } else {
                    "CANDIDATE_SUPERSESSION_REUSE_PREDECESSOR_UNAVAILABLE"
                }
                chain_head = $headKey; traversed = $traversed
                diagnostic_recorded = [bool](@($history | Where-Object {
                    [string]$_.event -eq
                        "CANDIDATE_SUPERSESSION_REUSE_UNAVAILABLE" -and
                    [string]$_.detail.chain_head -eq $headKey -and
                    [string]$_.detail.reason -eq
                        "CANDIDATE_SUPERSESSION_REUSE_PREDECESSOR_UNAVAILABLE" -and
                    [string]$_.detail.current_main -eq $MainRevision
                }).Count -gt 0)
            }
        }
        if ($edges.Count -ne 1) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_EDGE_AMBIGUOUS"
                chain_head = $headKey; traversed = $traversed
            }
        }
        $predecessor = $edges[0].release
        if (-not (Test-CandidateSupersessionIdentity -Candidate $predecessor)) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_EDGE_IDENTITY_INVALID"
                chain_head = $headKey; traversed = $traversed
            }
        }
        $predecessorKey = [string]$predecessor.validation_key
        if ($predecessorKey -eq [string]$current.validation_key) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_SELF_LOOP"
                chain_head = $headKey; traversed = $traversed
            }
        }
        if (-not $visited.Add($predecessorKey)) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_CYCLE"
                chain_head = $headKey; traversed = $traversed
            }
        }
        if (-not $visitedWorkers.Add([string]$predecessor.worker_version_id)) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_WORKER_REUSED"
                chain_head = $headKey; traversed = $traversed
            }
        }
        $qualified = Test-QualifiedSupersessionCandidateShape -Candidate $predecessor
        $observationFailed = Test-ObservationFailedSupersessionCandidateShape `
            -Candidate $predecessor
        if ($qualified -or $observationFailed) {
            $provenance = Get-ProductionCandidateProvenanceResult -Candidate $predecessor `
                -VerifiedOriginMainRevision $MainRevision
            if ([string]$provenance.state -ne "PASSED" -or
                [string]$provenance.mode -ne "CONTROL_PLANE_ONLY_MAIN_ADVANCE" -or
                [string]$provenance.current_main_git_sha -ne $MainRevision) {
                return [pscustomobject]@{
                    state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_PROVENANCE_INVALID"
                    chain_head = $headKey; traversed = $traversed
                }
            }
        } elseif (-not (Test-CandidateSupersessionAncestry `
                -Predecessor $predecessor -Successor $current `
                -MainRevision $MainRevision)) {
            return [pscustomobject]@{
                state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_ANCESTRY_INVALID"
                chain_head = $headKey; traversed = $traversed
            }
        }
        $traversed += @([pscustomobject]@{
            successor_key = [string]$current.validation_key
            predecessor_key = $predecessorKey
            predecessor_worker_version_id = [string]$predecessor.worker_version_id
            predecessor_git_sha = [string]$predecessor.git_sha
            predecessor_ineligible_reason = if ($qualified -or $observationFailed) {
                $null
            } else {
                "QUALIFICATION_OR_REUSABLE_EVIDENCE_UNAVAILABLE"
            }
        })
        if ($qualified) {
            return [pscustomobject]@{
                state = "FOUND"; reason = "QUALIFIED_PREDECESSOR"
                chain_head = $headKey; candidate = $predecessor
                recovery_mode = "QUALIFIED"; depth = $depth + 1
                traversed = $traversed
            }
        }
        if ($observationFailed) {
            return [pscustomobject]@{
                state = "FOUND"; reason = "OBSERVATION_FAILED_PREDECESSOR"
                chain_head = $headKey; candidate = $predecessor
                recovery_mode = "OBSERVATION_FAILED"; depth = $depth + 1
                traversed = $traversed
            }
        }
        $current = $predecessor
    }
    return [pscustomobject]@{
        state = "FAILED"; reason = "CANDIDATE_SUPERSESSION_MAX_DEPTH_EXCEEDED"
        chain_head = $headKey; traversed = $traversed
    }
}

function Restore-ControlPlaneOnlySupersededCandidate {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][string]$MainRevision
    )
    $replacement = $State.candidate
    if (-not $replacement -or $State.transaction -or
        [string]$replacement.compatibility_state -notin @(
            "PENDING", "REVIEW_REQUIRED", "COORDINATED_STORAGE_MIGRATION_PASSED"
        ) -or
        [string]$replacement.validation_state -notin @(
            "NEW", "CHECKS_PENDING", "CHECKS_BLOCKED", "TESTING", "REVIEW_REQUIRED",
            "PLATFORM_PENDING"
        ) -or
        ($replacement.migration_acceptance -and
            [string]$replacement.migration_acceptance.validation_key -ne
                [string]$replacement.validation_key) -or
        ($replacement.validation -and [string]$replacement.validation.key -ne
            [string]$replacement.validation_key)) {
        return $null
    }
    $replacementProvenance = Get-ProductionCandidateProvenanceResult -Candidate $replacement `
        -VerifiedOriginMainRevision $MainRevision
    $replacementTracksMain = [bool](
        [string]$replacementProvenance.state -eq "PASSED" -and (
            [string]$replacementProvenance.mode -eq "EXACT_MAIN" -or (
                [string]$replacementProvenance.mode -eq
                    "CONTROL_PLANE_ONLY_MAIN_ADVANCE" -and
                [string]$replacementProvenance.current_main_git_sha -eq
                    $MainRevision
            )
        )
    )
    if (-not $replacementTracksMain) { return $null }
    $deployment = Get-CloudflareDeployment
    $productionOwners = @($deployment.versions | Where-Object {
        [double]$_.percentage -gt 0
    })
    if ($productionOwners.Count -ne 1 -or
        [string]$productionOwners[0].version_id -ne [string]$State.stable.worker_version_id -or
        [double]$productionOwners[0].percentage -ne 100) {
        throw "CANDIDATE_SUPERSESSION_CHAIN_PRODUCTION_OWNERSHIP_UNSAFE"
    }
    $plan = Get-CandidateSupersessionRecoveryPlan `
        -Head $replacement -MainRevision $MainRevision
    if ([string]$plan.state -eq "NOT_APPLICABLE") { return $null }
    if ([string]$plan.state -eq "REUSE_UNAVAILABLE") {
        if (-not [bool]$plan.diagnostic_recorded) {
            Write-ReleaseHistory -Event "CANDIDATE_SUPERSESSION_REUSE_UNAVAILABLE" `
                -Release $replacement -Detail @{
                    reason = [string]$plan.reason
                    chain_head = [string]$plan.chain_head
                    current_main = $MainRevision
                    traversed = @($plan.traversed)
                }
        }
        return $null
    }
    if ([string]$plan.state -ne "FOUND") {
        Write-ReleaseHistory -Event "CANDIDATE_SUPERSESSION_CHAIN_RECOVERY_REJECTED" `
            -Release $replacement -Detail @{
                reason = [string]$plan.reason
                chain_head = [string]$plan.chain_head
                traversed = @($plan.traversed)
            }
        throw [string]$plan.reason
    }
    $prior = $plan.candidate
    try {
        $changed = @(Get-CandidateChangedFiles `
            -StableRevision ([string]$State.stable.git_sha) `
            -CandidateRevision ([string]$prior.git_sha))
        $compatibility = Get-CandidateCompatibilityRequirement -ChangedFiles $changed
        if ([string]$compatibility.state -ne "COORDINATED_STORAGE_MIGRATION_REQUIRED") {
            throw "CANDIDATE_SUPERSESSION_MIGRATION_CONTRACT_MOVED"
        }
        $migrationQualification = Ensure-CoordinatedMigrationQualification `
            -Candidate $prior -Stable $State.stable `
            -MigrationFiles @($compatibility.files)
        if ([string]$migrationQualification.root_receipt_digest -ne
            [string]$prior.migration_acceptance.receipt_digest) {
            throw "MIGRATION_RECEIPT_AUTHORITY_MISMATCH"
        }
    } catch {
        throw "CANDIDATE_SUPERSESSION_MIGRATION_RECEIPT_INVALID:$($_.Exception.Message)"
    }
    $reuseEvidence = $null
    if ([string]$plan.recovery_mode -eq "OBSERVATION_FAILED") {
        $trialState = $State.PSObject.Copy()
        $trialState.candidate = $prior
        $restored = Restore-ControlPlaneObservationFailedCandidate `
            -State $trialState -MainRevision $MainRevision
        if (-not $restored) {
            throw "CANDIDATE_SUPERSESSION_QUALIFICATION_REUSE_INVALID"
        }
        $prior = $restored
        $reuseEvidence = [pscustomobject]@{
            qualification_key = [string]$prior.validation.worker_qualification.key
            cpu_receipt_digest = [string]$prior.validation.cpu_evidence.qualification_receipt_digest
            access_receipt_digest = [string]$prior.access_qualification.receipt_digest
        }
    } else {
        try {
            $cpuReceipt = Assert-CandidateCpuQualificationReceipt `
                -Candidate $prior -Validation $prior.validation
            if ([string]$prior.validation.auth_inspection.state -eq
                    "HUMAN_ACCESS_BOUNDARY_ACCEPTED") {
                $accessReceipt = Assert-AccessBoundaryAcceptanceReceipt `
                    -Candidate $prior -Stable $State.stable
            } else {
                $accessReceipt = Ensure-AccessQualificationMachineReceipt -Candidate $prior
            }
            Set-CloudflareCandidatePointer -Stable $State.stable -Candidate $prior
            $placement = Wait-CandidatePlacementPropagation -Candidate $prior
            if (-not $placement.passed) {
                throw "CANDIDATE_SUPERSESSION_PLACEMENT_UNAVAILABLE"
            }
            $reuseEvidence = [pscustomobject]@{
                qualification_key = [string]$prior.validation.worker_qualification.key
                cpu_receipt_digest = [string]$cpuReceipt.receipt_digest
                access_receipt_digest = [string]$accessReceipt.receipt_digest
            }
        } catch {
            throw "CANDIDATE_SUPERSESSION_QUALIFICATION_REUSE_INVALID:$($_.Exception.Message)"
        }
        $State.candidate = $prior
        Set-CandidateMaterializationState -State $State -Revision $MainRevision `
            -Status "PRESERVED" -WorkerVersionId ([string]$prior.worker_version_id)
        $State.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $State
    }
    Write-ReleaseHistory -Event "CANDIDATE_RECOVERED_THROUGH_SUPERSESSION_CHAIN" `
        -Release $prior -Detail @{
            chain_head = [string]$plan.chain_head
            traversed = @($plan.traversed)
            recovered_candidate_key = [string]$prior.validation_key
            recovered_worker_version_id = [string]$prior.worker_version_id
            depth = [int]$plan.depth
            recovery_mode = [string]$plan.recovery_mode
            current_main_git_sha = $MainRevision
            qualification_key = [string]$reuseEvidence.qualification_key
            cpu_receipt_digest = [string]$reuseEvidence.cpu_receipt_digest
            migration_receipt_digest =
                [string]$prior.migration_acceptance.receipt_digest
            migration_renewal_receipt_digest = if (
                $prior.migration_qualification -and
                [string]$prior.migration_qualification.state -eq
                    "MIGRATION_QUALIFICATION_RENEWED"
            ) { [string]$prior.migration_qualification.receipt_digest } else { "" }
            access_receipt_digest = [string]$reuseEvidence.access_receipt_digest
            accepted_evidence_replayed = $false
        }
    return $prior
}

function Restore-ControlPlaneObservationFailedCandidate {
    param(
        [Parameter(Mandatory = $true)][object]$State,
        [Parameter(Mandatory = $true)][string]$MainRevision
    )
    $candidate = $State.candidate
    if (-not $candidate -or $State.transaction -or
        [string]$State.deployment_status -ne "READY" -or
        [string]$candidate.validation_state -ne "FAILED" -or
        [string]$candidate.compatibility_state -ne "PASSED" -or
        -not $candidate.validation -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key -or
        [string]$candidate.validation.error -ne "OBSERVATION_FAILED" -or
        [string]$candidate.validation.reason -ne
            "DEFERRED_PROJECTION_OBSERVATION_TIMEOUT" -or
        -not $candidate.validation.prior_validation) {
        return $null
    }
    $failedAttempt = $candidate.validation
    $priorValidation = $failedAttempt.prior_validation
    if ([string]$priorValidation.key -ne [string]$candidate.validation_key -or
        [string]$priorValidation.repository -ne "PASSED" -or
        [string]$priorValidation.windows -ne "PASSED" -or
        [string]$priorValidation.cloudflare -ne "PASSED" -or
        [string]$priorValidation.data_parity.state -notin @(
            "PASSED", "PASSED_WITH_DEFERRED_OBLIGATIONS"
        ) -or -not $priorValidation.worker_qualification -or
        [string]$priorValidation.worker_qualification.key -notmatch '^[0-9a-f]{64}$' -or
        [string]$priorValidation.worker_qualification.candidate_worker_version -ne
            [string]$candidate.worker_version_id -or
        [string]$priorValidation.worker_qualification.candidate_git_sha -ne
            [string]$candidate.git_sha -or -not $priorValidation.cpu_evidence -or
        [string]$priorValidation.cpu_evidence.qualification_key -ne
            [string]$priorValidation.worker_qualification.key -or
        [string]$priorValidation.cpu_evidence.qualification_receipt_digest -notmatch
            '^[0-9a-f]{64}$' -or -not $candidate.migration_acceptance -or
        [string]$candidate.migration_acceptance.validation_key -ne
            [string]$candidate.validation_key) {
        return $null
    }
    $provenance = Get-ProductionCandidateProvenanceResult -Candidate $candidate
    if ([string]$provenance.state -ne "PASSED" -or
        [string]$provenance.mode -ne "CONTROL_PLANE_ONLY_MAIN_ADVANCE" -or
        [string]$provenance.current_main_git_sha -ne $MainRevision) {
        return $null
    }
    $qualifiedCandidate = $candidate.PSObject.Copy()
    $qualifiedCandidate.validation_state = "PASSED"
    $qualifiedCandidate.validation = $priorValidation
    if (-not (Test-PreservedCandidateEvidenceAvailable `
            -Candidate $qualifiedCandidate)) {
        return $null
    }
    try {
        $cpuReceipt = Assert-CandidateCpuQualificationReceipt `
            -Candidate $candidate -Validation $priorValidation
        if ([string]$priorValidation.auth_inspection.state -eq
                "HUMAN_ACCESS_BOUNDARY_ACCEPTED") {
            $null = Assert-AccessBoundaryAcceptanceReceipt `
                -Candidate $qualifiedCandidate -Stable $State.stable
        } elseif ([string]$priorValidation.auth_inspection.state -in @(
                "ACCESS_QUALIFICATION_REUSED", "ACCESS_QUALIFICATION_RENEWED"
            )) {
            $null = Ensure-AccessQualificationMachineReceipt `
                -Candidate $qualifiedCandidate
        } else { return $null }
        Set-CloudflareCandidatePointer -Stable $State.stable `
            -Candidate $qualifiedCandidate
        $placement = Wait-CandidatePlacementPropagation -Candidate $qualifiedCandidate
        if (-not $placement.passed) { return $null }
    } catch { return $null }

    Write-ReleaseHistory -Event "CANDIDATE_RELEASE_ATTEMPT_FAILURE_PRESERVED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            error = [string]$failedAttempt.error
            reason = [string]$failedAttempt.reason
            failed_at = [string]$failedAttempt.tested_at
            qualification_restored = $false
        }
    $candidate | Add-Member -Force -NotePropertyName last_release_attempt `
        -NotePropertyValue ([pscustomobject]@{
            state = "FAILED"
            error = [string]$failedAttempt.error
            reason = [string]$failedAttempt.reason
            tested_at = [string]$failedAttempt.tested_at
            deferred_projection_evidence = $failedAttempt.deferred_projection_evidence
        })
    $candidate.validation = $priorValidation
    $candidate | Add-Member -Force -NotePropertyName access_qualification `
        -NotePropertyValue $qualifiedCandidate.access_qualification
    $candidate.validation_state = "EVIDENCE_PENDING"
    Set-CandidateMaterializationState -State $State -Revision $MainRevision `
        -Status "PRESERVED" -WorkerVersionId ([string]$candidate.worker_version_id)
    $State.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $State
    Write-ReleaseHistory -Event "CANDIDATE_RELEASE_ATTEMPT_RECOVERED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            prior_error = [string]$failedAttempt.error
            prior_reason = [string]$failedAttempt.reason
            current_main_git_sha = $MainRevision
            preservation_mode = [string]$provenance.mode
            accepted_evidence_replayed = $false
            candidate_repositioned_at_zero_percent = $true
        }
    $null = Finalize-CandidateQualificationEvidence `
        -WhyRan "AUTOMATIC_PROVIDER_RECOVERY_COMPLETED"
    return (Get-ReleaseControlState).candidate
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

function Get-WorkerValidationManifest {
    param([string]$Revision = "")
    if ($Revision) {
        $object = "{0}:web/worker-validation-manifest.json" -f $Revision
        $read = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $repositoryRoot, "show", $object)
        $raw = [string]$read.stdout
        if ($read.exit_code -ne 0 -or -not $raw) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_UNAVAILABLE"
        }
    } else {
        $path = Join-Path $repositoryRoot "web\worker-validation-manifest.json"
        if (-not (Test-Path -LiteralPath $path)) {
            throw "WORKER_ROUTE_VALIDATION_MANIFEST_UNAVAILABLE"
        }
        $raw = Get-Content -LiteralPath $path -Raw -Encoding UTF8
    }
    $manifest = $raw | ConvertFrom-ReleaseControlJson
    $cpuPolicy = Get-WorkerCpuEvidencePolicy
    if ([int]$manifest.schema_version -ne 4 -or @($manifest.routes).Count -eq 0 -or
        -not $manifest.fixture_builder -or -not $manifest.cpu_evidence_policy -or
        (Get-WorkerCpuCanonicalDigest -Value $manifest.cpu_evidence_policy) -ne
            (Get-WorkerCpuCanonicalDigest -Value $cpuPolicy)) {
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
    param([string[]]$ChangedFiles, [string]$Revision = "", [switch]$AllCpuRoutes)
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
        [bool]$_.cpu_required -and ($AllCpuRoutes -or (
            $manifestChanged -or $fixtureBuilderChanged -or
            (Test-ValidationRouteOwnedByChange -Route $_ -ChangedFiles $ChangedFiles) -or
            ($workerCodeChanged -and [bool]$_.baseline)
        ))
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
        cpu_evidence_policy = $manifest.cpu_evidence_policy
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
    $worktree = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "worktree", "add", "--detach", "--quiet",
        $stageRoot, [string]$Candidate.git_sha
    )
    if ($worktree.exit_code -ne 0) { throw "Candidate fixture worktree is unavailable." }
    try {
        $python = (Get-Command python.exe -ErrorAction Stop).Source
        $build = Invoke-Utf8NativeProcess -FilePath $python -Arguments @(
            (Join-Path $stageRoot "scripts\build_release_validation_fixtures.py"),
            "--output", $fixtureRoot
        ) -WorkingDirectory $stageRoot -Environment @{ PYTHONUTF8 = "1" }
        if ($build.exit_code -ne 0 -or -not (Test-Path -LiteralPath $fixtureRoot)) {
            throw "Production-shaped fixture generation failed."
        }
        return [pscustomobject]@{ stage_root=$stageRoot; fixture_root=$fixtureRoot }
    } catch {
        $null = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $repositoryRoot, "worktree", "remove", "--force", $stageRoot)
        $null = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $repositoryRoot, "worktree", "prune")
        throw
    }
}

function Remove-CandidateValidationFixtureWorkspace {
    param([object]$Workspace)
    if (-not $Workspace -or -not $Workspace.stage_root) { return }
    $null = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
        "-C", $repositoryRoot, "worktree", "remove", "--force", [string]$Workspace.stage_root
    )
    $null = Invoke-Utf8NativeProcess -FilePath "git.exe" `
        -Arguments @("-C", $repositoryRoot, "worktree", "prune")
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
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$FixtureRoot,
        [string]$IngestToken = "",
        [ValidateSet("warmup", "acceptance")][string]$ValidationPhase = "acceptance",
        [string]$RequestId = ""
    )
    $requestId = if ($RequestId) { $RequestId } else { [guid]::NewGuid().ToString() }
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
        try { $payload = $response.Content | ConvertFrom-ReleaseControlJson } catch {}
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
            response_content_digest=Get-WorkerCpuCanonicalDigest -Value ([string]$response.Content)
            mutated=if ($payload -and $null -ne $payload.PSObject.Properties['mutated']) {
                [bool]$payload.mutated
            } else { $false }
        }
    } catch {
        $errorResponse = $_.Exception.Response
        $status = if ($errorResponse) {
            [int]$errorResponse.StatusCode
        } else { 0 }
        $payload = $null
        try { $payload = $_.ErrorDetails.Message | ConvertFrom-ReleaseControlJson } catch {}
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
            response_content_digest=if ($_.ErrorDetails.Message) {
                Get-WorkerCpuCanonicalDigest -Value ([string]$_.ErrorDetails.Message)
            } else { "" }
        }
    }
}

function Invoke-CandidatePlannedCpuSamples {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan,
        [Parameter(Mandatory = $true)][object]$RequestPlan,
        [Parameter(Mandatory = $true)][object[]]$PlannedRequests,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$FixtureRoot,
        [string]$IngestToken = ""
    )
    $routes = @($RoutePlan.worker_reads) + @($RoutePlan.worker_writes)
    $headers = @{
        "Cloudflare-Workers-Version-Overrides" =
            "$workerName=`"$([string]$Candidate.worker_version_id)`""
    }
    $responses = @()
    foreach ($request in @($PlannedRequests)) {
        $route = @($routes | Where-Object {
            [string]$_.family -eq [string]$request.family -and
            [string]$_.scenario -eq [string]$request.scenario
        }) | Select-Object -First 1
        if (-not $route) { throw "WORKER_CPU_TARGETED_ROUTE_UNAVAILABLE" }
        $route | Add-Member -NotePropertyName expected_worker_version `
            -NotePropertyValue ([string]$Candidate.worker_version_id) -Force
        $route | Add-Member -NotePropertyName expected_git_sha `
            -NotePropertyValue ([string]$Candidate.git_sha) -Force
        $null = Add-WorkerCpuRequestSend -ValidationRun ([string]$RequestPlan.validation_run) `
            -Request $request -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
            -QualificationKey ([string]$RequestPlan.qualification_key)
        $sample = Invoke-CandidateRouteSample -Route $route -VersionHeaders $headers `
            -ValidationRun ([string]$RequestPlan.validation_run) -FixtureRoot $FixtureRoot `
            -IngestToken $IngestToken -ValidationPhase "acceptance" `
            -RequestId ([string]$request.request_id)
        $receipt = Add-WorkerCpuDirectResponse -ValidationRun ([string]$RequestPlan.validation_run) `
            -Request $request -Response $sample
        $responses += $receipt
        if (-not $sample.passed) { throw "TARGETED_DIRECTED_WORKER_VALIDATION_FAILED" }
    }
    return [pscustomobject]@{
        requests=@($PlannedRequests); responses=$responses; completed_at=[DateTimeOffset]::UtcNow
    }
}

function Invoke-CandidateTargetedCpuSamples {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan,
        [Parameter(Mandatory = $true)][object]$RequestPlan,
        [Parameter(Mandatory = $true)][object[]]$Groups,
        [ValidateSet("deficit_top_up", "headroom_top_up", "outlier_confirmation")]
        [Parameter(Mandatory = $true)][string]$SampleKind,
        [Parameter(Mandatory = $true)][int]$CountPerGroup,
        [Parameter(Mandatory = $true)][string]$FixtureRoot,
        [string]$IngestToken = ""
    )
    $planned = @(Add-WorkerCpuPlannedRequests -Plan $RequestPlan -Groups $Groups `
        -SampleKind $SampleKind -CountPerGroup $CountPerGroup)
    return Invoke-CandidatePlannedCpuSamples -Candidate $Candidate -RoutePlan $RoutePlan `
        -RequestPlan $RequestPlan -PlannedRequests $planned -FixtureRoot $FixtureRoot `
        -IngestToken $IngestToken
}

function Invoke-CandidateCpuOutlierConfirmation {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$RoutePlan,
        [Parameter(Mandatory = $true)][object]$RequestPlan,
        [Parameter(Mandatory = $true)][object]$Decision,
        [Parameter(Mandatory = $true)][object]$ProviderEvidence,
        [Parameter(Mandatory = $true)][string]$QualificationKey,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$FixtureRoot,
        [string]$IngestToken = ""
    )
    $policy = Get-WorkerCpuEvidencePolicy
    if ([string]$Decision.state -ne "CPU_OUTLIER_REVIEW_REQUIRED" -or
        @($Decision.outlier_groups).Count -ne 1 -or
        [int]$ProviderEvidence.recovery.outlier_confirmations -ge
            [int]$policy.maximum_outlier_confirmations -or
        (Read-WorkerCpuOutlierConfirmationPlan `
            -ValidationRun ([string]$RequestPlan.validation_run))) {
        throw "WORKER_CPU_OUTLIER_CONFIRMATION_NOT_ELIGIBLE"
    }
    $confirmationPlan = New-WorkerCpuOutlierConfirmationPlan `
        -RequestPlan $RequestPlan -OutlierGroup (@($Decision.outlier_groups)[0]) `
        -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
        -QualificationKey $QualificationKey `
        -PriorProviderDigest ([string]$ProviderEvidence.observed_universe_digest)
    $planned = @(Apply-WorkerCpuOutlierConfirmationPlan `
        -RequestPlan $RequestPlan -ConfirmationPlan $confirmationPlan)
    $result = Invoke-CandidatePlannedCpuSamples -Candidate $Candidate `
        -RoutePlan $RoutePlan -RequestPlan $RequestPlan `
        -PlannedRequests $planned -FixtureRoot $FixtureRoot `
        -IngestToken $IngestToken
    $ProviderEvidence.recovery | Add-Member `
        -NotePropertyName outlier_confirmations -NotePropertyValue 1 -Force
    $ProviderEvidence.recovery.active_reads = 0
    $null = Write-WorkerCpuProviderEvidence `
        -ValidationRun ([string]$RequestPlan.validation_run) `
        -Records @($ProviderEvidence.records) `
        -RecoveryState $ProviderEvidence.recovery
    return $result
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
    $expectedRequests = @()
    $workerStartedAt = $null
    $workerEndedAt = $null
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
    $qualification = $null
    $reusedQualificationReceipt = $null
    $requestPlan = $null
    $directResponses = @()
    $ingestToken = [Environment]::GetEnvironmentVariable("CLOUDFLARE_INGEST_TOKEN", "User")
    try {
        if (@($RoutePlan.worker_writes).Count -gt 0) {
            $workspace = New-CandidateValidationFixtureWorkspace -Candidate $Candidate
        }
        $fixtureRoot = if ($workspace) { [string]$workspace.fixture_root } else { "" }
        $fixtureDigestSet = if ($fixtureRoot) {
            @(Get-WorkerCpuFixtureDigestSet -FixtureRoot $fixtureRoot)
        } else { @() }
        $qualification = Get-WorkerCpuQualificationIdentity -Candidate $Candidate `
            -RoutePlan $RoutePlan -FixtureDigestSet $fixtureDigestSet
        $reusedQualificationReceipt = Get-WorkerCpuQualificationReceipt `
            -QualificationKey ([string]$qualification.key)
        $planArguments = @{
            Routes=$workerRoutes; ValidationRun=$validationRun
            CandidateWorkerVersion=[string]$Candidate.worker_version_id
            QualificationKey=[string]$qualification.key
            ValidationPlanDigest=(Get-WorkerCpuValidationPlanDigest -RoutePlan $RoutePlan)
            FixtureDigestSet=$fixtureDigestSet
        }
        $requestPlan = if ($reusedQualificationReceipt) {
            New-WorkerDirectedCorrectnessPlan @planArguments
        } else {
            New-WorkerCpuRequestPlan @planArguments
        }
        $workerStartedAt = [DateTimeOffset]::UtcNow
        Write-CandidateCpuInFlightState -Candidate $Candidate -RoutePlan $RoutePlan `
            -RequestPlan $requestPlan -Qualification $qualification -WindowFrom $workerStartedAt
        foreach ($route in $workerRoutes) {
            $warmups = @()
            $plannedWarmups = @($requestPlan.requests | Where-Object {
                [string]$_.family -eq [string]$route.family -and
                [string]$_.scenario -eq [string]$route.scenario -and
                [string]$_.phase -eq "warmup"
            })
            foreach ($planned in $plannedWarmups) {
                $null = Add-WorkerCpuRequestSend -ValidationRun $validationRun -Request $planned `
                    -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                    -QualificationKey ([string]$qualification.key)
                $sample = Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken `
                    -ValidationPhase "warmup" -RequestId ([string]$planned.request_id)
                $warmups += $sample
                $directResponses += Add-WorkerCpuDirectResponse -ValidationRun $validationRun `
                    -Request $planned -Response $sample
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
        foreach ($route in $workerRoutes) {
            $samples = @()
            $plannedAcceptance = @($requestPlan.requests | Where-Object {
                [string]$_.family -eq [string]$route.family -and
                [string]$_.scenario -eq [string]$route.scenario -and
                [string]$_.phase -eq "acceptance"
            })
            foreach ($planned in $plannedAcceptance) {
                $null = Add-WorkerCpuRequestSend -ValidationRun $validationRun -Request $planned `
                    -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                    -QualificationKey ([string]$qualification.key)
                $sample = Invoke-CandidateRouteSample -Route $route `
                    -VersionHeaders $header -ValidationRun $validationRun `
                    -FixtureRoot $fixtureRoot -IngestToken $ingestToken `
                    -ValidationPhase "acceptance" -RequestId ([string]$planned.request_id)
                $samples += $sample
                $directResponses += Add-WorkerCpuDirectResponse -ValidationRun $validationRun `
                    -Request $planned -Response $sample
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
            $expectedRequests = @($requestPlan.requests | Where-Object { [string]$_.phase -eq "acceptance" })
            $expectedInvocations = $expectedRequests.Count
            if ($reusedQualificationReceipt) {
                $platform = New-ReusedWorkerCpuEvidence -Receipt $reusedQualificationReceipt `
                    -Candidate $Candidate -Qualification $qualification
                Add-WorkerCpuLedgerEvent -ValidationRun $validationRun `
                    -Event "CPU_QUALIFICATION_REUSED" -Detail ([pscustomobject]@{
                        qualification_key=[string]$qualification.key
                        receipt_digest=[string]$reusedQualificationReceipt.receipt_digest
                        current_worker_version=[string]$Candidate.worker_version_id
                        current_git_sha=[string]$Candidate.git_sha
                    })
            } else {
                Start-Sleep -Seconds 8
                $platform = Get-CandidateFrozenPlatformEvidence -Candidate $Candidate `
                    -From $workerStartedAt -To $workerEndedAt `
                    -ExpectedRequests $expectedRequests -ValidationRun $validationRun
            }
            if (-not $reusedQualificationReceipt -and $platform -and
                [string]$platform.gate_state -eq "REVIEW_REQUIRED") {
                $storedEvidence = Read-WorkerCpuRunArtifact -ValidationRun $validationRun `
                    -Name "provider-evidence.json"
                $reviewDecision = Get-WorkerCpuQualificationDecision `
                    -ExpectedRequests $expectedRequests -ProviderRecords @($storedEvidence.records) `
                    -DirectResponsesComplete $true -AggregateEvidence $platform.provider_corroboration
                if ([string]$reviewDecision.state -eq
                    "CPU_OUTLIER_REVIEW_REQUIRED" -and
                    [int]$storedEvidence.recovery.outlier_confirmations -lt
                        [int]$((Get-WorkerCpuEvidencePolicy).maximum_outlier_confirmations) -and
                    -not (Read-WorkerCpuOutlierConfirmationPlan `
                        -ValidationRun $validationRun)) {
                    $topUp = Invoke-CandidateCpuOutlierConfirmation `
                        -Candidate $Candidate -RoutePlan $RoutePlan `
                        -RequestPlan $requestPlan -Decision $reviewDecision `
                        -ProviderEvidence $storedEvidence `
                        -QualificationKey ([string]$qualification.key) `
                        -FixtureRoot $fixtureRoot -IngestToken $ingestToken
                    $expectedRequests = @($requestPlan.requests | Where-Object {
                        [string]$_.phase -eq "acceptance"
                    })
                    $directResponses += @($topUp.responses)
                    $workerEndedAt = $topUp.completed_at
                    $platform = Get-CandidateFrozenPlatformEvidence `
                        -Candidate $Candidate -From $workerStartedAt `
                        -To $workerEndedAt -ExpectedRequests $expectedRequests `
                        -ValidationRun $validationRun
                } elseif ([string]$reviewDecision.state -eq "HEADROOM_REVIEW" -and
                    @($reviewDecision.review_groups).Count -eq 1 -and
                    [int]$storedEvidence.recovery.headroom_top_ups -lt 1) {
                    $topUp = Invoke-CandidateTargetedCpuSamples -Candidate $Candidate `
                        -RoutePlan $RoutePlan -RequestPlan $requestPlan `
                        -Groups @($reviewDecision.review_groups) -SampleKind "headroom_top_up" `
                        -CountPerGroup 10 -FixtureRoot $fixtureRoot -IngestToken $ingestToken
                    $expectedRequests = @($requestPlan.requests | Where-Object { [string]$_.phase -eq "acceptance" })
                    $directResponses += @($topUp.responses)
                    $workerEndedAt = $topUp.completed_at
                    $storedEvidence.recovery.active_reads = 0
                    $storedEvidence.recovery.headroom_top_ups = [int]$storedEvidence.recovery.headroom_top_ups + 1
                    $null = Write-WorkerCpuProviderEvidence -ValidationRun $validationRun `
                        -Records @($storedEvidence.records) -RecoveryState $storedEvidence.recovery
                    $platform = Get-CandidateFrozenPlatformEvidence -Candidate $Candidate `
                        -From $workerStartedAt -To $workerEndedAt -ExpectedRequests $expectedRequests `
                        -ValidationRun $validationRun
                }
            }
            if ($platform -and $platform.passed -and -not $reusedQualificationReceipt) {
                $decision = Get-WorkerCpuQualificationDecision -ExpectedRequests $expectedRequests `
                    -ProviderRecords @((Read-WorkerCpuRunArtifact -ValidationRun $validationRun `
                        -Name "provider-evidence.json").records) -DirectResponsesComplete $true `
                    -AggregateEvidence $platform.provider_corroboration
                $receipt = Write-WorkerCpuQualificationReceipt -Qualification $qualification `
                    -ValidationRun $validationRun -Decision $decision
                $platform | Add-Member -NotePropertyName qualification_receipt_digest `
                    -NotePropertyValue ([string]$receipt.receipt_digest) -Force
                $qualificationMode = if ([string]$decision.state -eq
                    "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER") {
                    "CPU_QUALIFICATION_WITH_ISOLATED_CPU_OUTLIER"
                } else { "CPU_QUALIFICATION_FRESH" }
                $platform | Add-Member -NotePropertyName qualification_mode `
                    -NotePropertyValue $qualificationMode -Force
                $platform | Add-Member -NotePropertyName qualification_key `
                    -NotePropertyValue ([string]$qualification.key) -Force
            }
        } else {
            $platform = "NOT_RUN"
        }
    } finally {
        Remove-CandidateValidationFixtureWorkspace -Workspace $workspace
    }
    $expectedInvocations = @($expectedRequests).Count
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
        telemetry_window_from = if ($workerStartedAt) { $workerStartedAt.ToString('o') } else { $null }
        telemetry_window_to = if ($workerEndedAt) { $workerEndedAt.ToString('o') } else { $null }
        expected_requests = @($expectedRequests)
        directed_request_ledger = if ($requestPlan) {
            [pscustomobject]@{
                evidence_class="CONTROLLED_EXACT"
                request_universe_digest=[string]$requestPlan.request_universe_digest
                planned=@($requestPlan.requests).Count
                completed=@($directResponses).Count
                passed=@($directResponses | Where-Object { $_.passed }).Count
            }
        } else { $null }
        worker_qualification = $qualification
        cpu_qualification_mode = if ($platform -and $platform -ne "NOT_RUN" -and $platform.passed) {
            if ($reusedQualificationReceipt) {
                "CPU_QUALIFICATION_REUSED"
            } elseif ($platform.qualification_mode) {
                [string]$platform.qualification_mode
            } else { "CPU_QUALIFICATION_FRESH" }
        } else { $null }
    }
}

function Resume-CandidateWorkerPlatformEvidence {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Validation
    )
    if ([string]$Validation.key -ne [string]$Candidate.validation_key -or
        -not $Validation.validation_run -or
        @($Validation.expected_requests).Count -eq 0 -or -not $Validation.cpu_route_plan -or
        -not $Validation.telemetry_window_from -or
        @($Validation.routes).Count -eq 0) {
        throw "CANDIDATE_PLATFORM_RESUME_RECEIPT_INVALID"
    }
    $from = ConvertTo-ReleaseTimestampUtc -Value $Validation.telemetry_window_from
    $to = ConvertTo-ReleaseTimestampUtc -Value $Validation.telemetry_window_to
    if ($from -eq [DateTimeOffset]::MinValue -or $to -eq [DateTimeOffset]::MinValue -or $to -lt $from) {
        throw "CANDIDATE_PLATFORM_RESUME_RECEIPT_INVALID"
    }
    $script:lastWorkersObservabilityDiagnostic = $null
    $skipPlatformRead = $false
    $expectedRequests = @($Validation.expected_requests)
    $plan = Read-WorkerCpuRunArtifact -ValidationRun ([string]$Validation.validation_run) -Name "plan.json"
    $storedEvidence = Read-WorkerCpuRunArtifact -ValidationRun ([string]$Validation.validation_run) `
        -Name "provider-evidence.json"
    if (-not $plan -or -not $storedEvidence -or -not $Validation.worker_qualification -or
        [string]$plan.candidate_worker_version -ne [string]$Candidate.worker_version_id -or
        [string]$plan.qualification_key -ne [string]$Validation.worker_qualification.key) {
        throw "CANDIDATE_PLATFORM_RESUME_LEDGER_UNAVAILABLE"
    }
    $existingRepairPlan = Read-WorkerCpuDeficitRepairPlan `
        -ValidationRun ([string]$Validation.validation_run)
    if ($existingRepairPlan) {
        $null = Apply-WorkerCpuDeficitRepairPlan -RequestPlan $plan `
            -RepairPlan $existingRepairPlan
        if ([int]$storedEvidence.recovery.deficit_top_ups -lt 1) {
            $storedEvidence.recovery.deficit_top_ups = 1
            $null = Write-WorkerCpuProviderEvidence `
                -ValidationRun ([string]$Validation.validation_run) `
                -Records @($storedEvidence.records) -RecoveryState $storedEvidence.recovery
        }
    }
    $receipts = @(Get-WorkerCpuDirectResponseReceipts -ValidationRun ([string]$Validation.validation_run))
    $resumeRoutes = @($Validation.cpu_route_plan.worker_reads) +
        @($Validation.cpu_route_plan.worker_writes)
    foreach ($failed in @($receipts | Where-Object { -not $_.passed })) {
        $request = @($plan.requests | Where-Object {
            [string]$_.request_id -eq [string]$failed.request_id
        }) | Select-Object -First 1
        $route = @($resumeRoutes | Where-Object {
            [string]$_.family -eq [string]$request.family -and
            [string]$_.scenario -eq [string]$request.scenario
        }) | Select-Object -First 1
        if ($request -and $route -and [string]$route.strategy -eq "DIRECT_REQUEST") {
            $null = Repair-WorkerCpuDirectResponseIdentityExpectation `
                -ValidationRun ([string]$Validation.validation_run) -Request $request `
                -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                -CandidateGitSha ([string]$Candidate.git_sha) `
                -QualificationKey ([string]$plan.qualification_key)
        }
    }
    $receipts = @(Get-WorkerCpuDirectResponseReceipts `
        -ValidationRun ([string]$Validation.validation_run))
    if (@($receipts | Where-Object { -not $_.passed }).Count -gt 0) {
        throw "CANDIDATE_PLATFORM_RESUME_DIRECT_FAILURE"
    }
    $completedIds = @($receipts | ForEach-Object { [string]$_.request_id })
    $unsent = @($plan.requests | Where-Object { [string]$_.request_id -notin $completedIds })
    $restartProviderRecovery = $false
    if ($unsent.Count -gt 0) {
        $workspace = $null
        try {
            if (@($Validation.cpu_route_plan.worker_writes).Count -gt 0) {
                $workspace = New-CandidateValidationFixtureWorkspace -Candidate $Candidate
            }
            $fixtureRoot = if ($workspace) { [string]$workspace.fixture_root } else { "" }
            $routes = @($Validation.cpu_route_plan.worker_reads) + @($Validation.cpu_route_plan.worker_writes)
            $headers = @{ "Cloudflare-Workers-Version-Overrides" = "$workerName=`"$([string]$Candidate.worker_version_id)`"" }
            $ingestToken = [Environment]::GetEnvironmentVariable("CLOUDFLARE_INGEST_TOKEN", "User")
            foreach ($request in $unsent) {
                $route = @($routes | Where-Object {
                    [string]$_.family -eq [string]$request.family -and
                    [string]$_.scenario -eq [string]$request.scenario
                }) | Select-Object -First 1
                if (-not $route) { throw "CANDIDATE_PLATFORM_RESUME_ROUTE_UNAVAILABLE" }
                $route | Add-Member expected_worker_version ([string]$Candidate.worker_version_id) -Force
                $route | Add-Member expected_git_sha ([string]$Candidate.git_sha) -Force
                $null = Add-WorkerCpuRequestSend -ValidationRun ([string]$Validation.validation_run) `
                    -Request $request -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                    -QualificationKey ([string]$plan.qualification_key)
                $sample = Invoke-CandidateRouteSample -Route $route -VersionHeaders $headers `
                    -ValidationRun ([string]$Validation.validation_run) -FixtureRoot $fixtureRoot `
                    -IngestToken $ingestToken -ValidationPhase ([string]$request.phase) `
                    -RequestId ([string]$request.request_id)
                $receipt = Add-WorkerCpuDirectResponse -ValidationRun ([string]$Validation.validation_run) `
                    -Request $request -Response $sample
                if (-not $receipt.passed) { throw "CANDIDATE_PLATFORM_RESUME_DIRECT_FAILURE" }
            }
            $to = [DateTimeOffset]::UtcNow
            $restartProviderRecovery = $true
        } finally { Remove-CandidateValidationFixtureWorkspace -Workspace $workspace }
        $receipts = @(Get-WorkerCpuDirectResponseReceipts -ValidationRun ([string]$Validation.validation_run))
    }
    if ($existingRepairPlan) {
        $repairRequestIds = @($existingRepairPlan.payload.requests | ForEach-Object {
            [string]$_.request_id
        })
        $repairReceipts = @($receipts | Where-Object {
            [string]$_.request_id -in $repairRequestIds
        })
        if ($repairRequestIds.Count -gt 0 -and
            $repairReceipts.Count -eq $repairRequestIds.Count) {
            $latestRepairResponse = @($repairReceipts | ForEach-Object {
                ConvertTo-ReleaseTimestampUtc -Value ([string]$_.completed_at)
            } | Where-Object { $_ -ne [DateTimeOffset]::MinValue } |
                Sort-Object -Descending) | Select-Object -First 1
            if ($latestRepairResponse -and $latestRepairResponse -gt $to) {
                $to = $latestRepairResponse
            }
            $lastProviderRead = ConvertTo-ReleaseTimestampUtc `
                -Value ([string]$storedEvidence.recovery.last_read_at)
            if ($latestRepairResponse -and
                ($lastProviderRead -eq [DateTimeOffset]::MinValue -or
                 $latestRepairResponse -gt $lastProviderRead)) {
                $restartProviderRecovery = $true
            }
        }
    }
    if ($restartProviderRecovery) {
        $storedEvidence.recovery.active_reads = 0
        $storedEvidence.recovery.background_reads = 0
        $storedEvidence.recovery | Add-Member -NotePropertyName last_read_at `
            -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
        $null = Write-WorkerCpuProviderEvidence `
            -ValidationRun ([string]$Validation.validation_run) `
            -Records @($storedEvidence.records) `
            -RecoveryState $storedEvidence.recovery
    }
    $expectedRequests = @($plan.requests | Where-Object { [string]$_.phase -eq "acceptance" })
    if ($receipts.Count -ne @($plan.requests).Count) { throw "CANDIDATE_PLATFORM_RESUME_LEDGER_INCOMPLETE" }
    if ($plan -and $storedEvidence) {
        $policy = Get-WorkerCpuEvidencePolicy
        $recoveryExhausted = [bool]([int]$storedEvidence.recovery.background_reads -ge
            [int]$policy.maximum_background_reads)
        $pendingDecision = Get-WorkerCpuQualificationDecision -ExpectedRequests $expectedRequests `
            -ProviderRecords @($storedEvidence.records) -DirectResponsesComplete $true `
            -RecoveryBudgetExhausted $recoveryExhausted
        if ([string]$pendingDecision.state -eq
            "CPU_OUTLIER_REVIEW_REQUIRED" -and
            [int]$storedEvidence.recovery.outlier_confirmations -lt
                [int]$policy.maximum_outlier_confirmations -and
            -not (Read-WorkerCpuOutlierConfirmationPlan `
                -ValidationRun ([string]$Validation.validation_run))) {
            $workspace = $null
            try {
                if (@($Validation.cpu_route_plan.worker_writes).Count -gt 0) {
                    $workspace = New-CandidateValidationFixtureWorkspace `
                        -Candidate $Candidate
                }
                $topUpFixtureRoot = if ($workspace) {
                    [string]$workspace.fixture_root
                } else { "" }
                $topUp = Invoke-CandidateCpuOutlierConfirmation `
                    -Candidate $Candidate `
                    -RoutePlan $Validation.cpu_route_plan `
                    -RequestPlan $plan -Decision $pendingDecision `
                    -ProviderEvidence $storedEvidence `
                    -QualificationKey ([string]$Validation.worker_qualification.key) `
                    -FixtureRoot $topUpFixtureRoot `
                    -IngestToken ([Environment]::GetEnvironmentVariable(
                        "CLOUDFLARE_INGEST_TOKEN", "User"))
                $expectedRequests = @($plan.requests | Where-Object {
                    [string]$_.phase -eq "acceptance"
                })
                $receipts = @(Get-WorkerCpuDirectResponseReceipts `
                    -ValidationRun ([string]$Validation.validation_run))
                $to = $topUp.completed_at
                $pendingDecision = [pscustomobject]@{
                    state="PROVIDER_EVIDENCE_PENDING"
                }
            } finally {
                Remove-CandidateValidationFixtureWorkspace -Workspace $workspace
            }
        } elseif ([string]$pendingDecision.state -eq "HEADROOM_REVIEW" -and
            @($pendingDecision.review_groups).Count -eq 1 -and
            [int]$storedEvidence.recovery.headroom_top_ups -lt [int]$policy.maximum_headroom_top_ups) {
            $workspace = $null
            try {
                if (@($Validation.cpu_route_plan.worker_writes).Count -gt 0) {
                    $workspace = New-CandidateValidationFixtureWorkspace -Candidate $Candidate
                }
                $topUpFixtureRoot = if ($workspace) { [string]$workspace.fixture_root } else { "" }
                $topUp = Invoke-CandidateTargetedCpuSamples -Candidate $Candidate `
                    -RoutePlan $Validation.cpu_route_plan -RequestPlan $plan `
                    -Groups @($pendingDecision.review_groups) -SampleKind "headroom_top_up" `
                    -CountPerGroup ([int]$policy.headroom_top_up_acceptance) `
                    -FixtureRoot $topUpFixtureRoot `
                    -IngestToken ([Environment]::GetEnvironmentVariable("CLOUDFLARE_INGEST_TOKEN", "User"))
                $expectedRequests = @($plan.requests | Where-Object { [string]$_.phase -eq "acceptance" })
                $to = $topUp.completed_at
                $storedEvidence.recovery.active_reads = 0
                $storedEvidence.recovery.headroom_top_ups = [int]$storedEvidence.recovery.headroom_top_ups + 1
                $null = Write-WorkerCpuProviderEvidence -ValidationRun ([string]$Validation.validation_run) `
                    -Records @($storedEvidence.records) -RecoveryState $storedEvidence.recovery
                $pendingDecision = [pscustomobject]@{ state="PROVIDER_EVIDENCE_PENDING" }
            } finally { Remove-CandidateValidationFixtureWorkspace -Workspace $workspace }
        }
        if ([string]$pendingDecision.state -eq "PROVIDER_EVIDENCE_INSUFFICIENT" -and
            -not $existingRepairPlan) {
            $workspace = $null
            try {
                $preflight = Get-CandidateDeficitRepairProviderPreflight `
                    -Candidate $Candidate -From $from -To $to `
                    -ExpectedRequests $expectedRequests `
                    -ValidationRun ([string]$Validation.validation_run)
                $storedEvidence = $preflight.provider_evidence
                if (-not $preflight.available) {
                    $script:lastWorkersObservabilityDiagnostic =
                        if ($preflight.diagnostic) {
                            [string]$preflight.diagnostic
                        } else { "OBSERVABILITY_TRANSIENT_API_FAILURE" }
                    $skipPlatformRead = $true
                } elseif ($preflight.digest_changed) {
                    $storedEvidence.recovery.background_reads = 0
                    $storedEvidence.recovery | Add-Member -NotePropertyName last_read_at `
                        -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
                    $null = Write-WorkerCpuProviderEvidence `
                        -ValidationRun ([string]$Validation.validation_run) `
                        -Records @($storedEvidence.records) -RecoveryState $storedEvidence.recovery
                    $script:lastWorkersObservabilityDiagnostic = "PROVIDER_EVIDENCE_PENDING"
                } else {
                    $pendingDecision = $preflight.decision
                    $eligibility = Get-WorkerCpuDeficitRepairEligibility `
                        -Plan $plan -ProviderEvidence $storedEvidence -Decision $pendingDecision `
                        -DirectResponses $receipts `
                        -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                        -QualificationKey ([string]$Validation.worker_qualification.key) `
                        -ProviderAvailable ([bool]$preflight.available) `
                        -PlateauStable ([bool]$preflight.plateau_stable)
                    if ([string]$eligibility.reason -eq
                        "DEFICIT_REPAIR_GLOBAL_BUDGET_EXCEEDED") {
                        $script:lastWorkersObservabilityDiagnostic =
                            "PROVIDER_EVIDENCE_INSUFFICIENT"
                    } elseif ($eligibility.eligible) {
                        $existingRepairPlan = New-WorkerCpuDeficitRepairPlan `
                            -RequestPlan $plan `
                            -DeficientGroups @($eligibility.deficient_groups) `
                            -CandidateWorkerVersion ([string]$Candidate.worker_version_id) `
                            -QualificationKey ([string]$Validation.worker_qualification.key) `
                            -PriorProviderDigest ([string]$storedEvidence.observed_universe_digest) `
                            -PriorObservedTotal @($storedEvidence.records).Count
                        $plannedRepairRequests = @(Apply-WorkerCpuDeficitRepairPlan `
                            -RequestPlan $plan -RepairPlan $existingRepairPlan)
                        if (@($Validation.cpu_route_plan.worker_writes).Count -gt 0) {
                            $workspace = New-CandidateValidationFixtureWorkspace `
                                -Candidate $Candidate
                        }
                        $topUpFixtureRoot = if ($workspace) {
                            [string]$workspace.fixture_root
                        } else { "" }
                        $topUp = Invoke-CandidatePlannedCpuSamples `
                            -Candidate $Candidate -RoutePlan $Validation.cpu_route_plan `
                            -RequestPlan $plan -PlannedRequests $plannedRepairRequests `
                            -FixtureRoot $topUpFixtureRoot `
                            -IngestToken ([Environment]::GetEnvironmentVariable(
                                "CLOUDFLARE_INGEST_TOKEN", "User"))
                        $expectedRequests = @($plan.requests | Where-Object {
                            [string]$_.phase -eq "acceptance"
                        })
                        $receipts = @(Get-WorkerCpuDirectResponseReceipts `
                            -ValidationRun ([string]$Validation.validation_run))
                        $to = $topUp.completed_at
                        $storedEvidence.recovery.active_reads = 0
                        $storedEvidence.recovery.background_reads = 0
                        $storedEvidence.recovery.deficit_top_ups = 1
                        $storedEvidence.recovery | Add-Member `
                            -NotePropertyName last_read_at `
                            -NotePropertyValue ([DateTimeOffset]::UtcNow.ToString("o")) -Force
                        $null = Write-WorkerCpuProviderEvidence `
                            -ValidationRun ([string]$Validation.validation_run) `
                            -Records @($storedEvidence.records) `
                            -RecoveryState $storedEvidence.recovery
                        $pendingDecision = [pscustomobject]@{
                            state="PROVIDER_EVIDENCE_PENDING"
                        }
                    }
                }
            } finally { Remove-CandidateValidationFixtureWorkspace -Workspace $workspace }
        }
    }
    $platform = if ($skipPlatformRead) { $null } else {
        Get-CandidateFrozenPlatformEvidence -Candidate $Candidate `
            -From $from -To $to `
            -ExpectedRequests $expectedRequests `
            -ValidationRun ([string]$Validation.validation_run)
    }
    $qualificationMode = [string]$Validation.cpu_qualification_mode
    if ($platform -and $platform.passed -and -not $qualificationMode) {
        if (-not $Validation.worker_qualification) {
            throw "CANDIDATE_PLATFORM_RESUME_QUALIFICATION_UNAVAILABLE"
        }
        $storedEvidence = Read-WorkerCpuRunArtifact -ValidationRun ([string]$Validation.validation_run) `
            -Name "provider-evidence.json"
        if (-not $storedEvidence) { throw "CANDIDATE_PLATFORM_RESUME_EVIDENCE_UNAVAILABLE" }
        $decision = Get-WorkerCpuQualificationDecision -ExpectedRequests $expectedRequests `
            -ProviderRecords @($storedEvidence.records) -DirectResponsesComplete $true `
            -AggregateEvidence $platform.provider_corroboration
        $receipt = Write-WorkerCpuQualificationReceipt -Qualification $Validation.worker_qualification `
            -ValidationRun ([string]$Validation.validation_run) -Decision $decision
        $qualificationMode = if ([string]$decision.state -eq
            "QUALIFIED_WITH_ISOLATED_CPU_OUTLIER") {
            "CPU_QUALIFICATION_WITH_ISOLATED_CPU_OUTLIER"
        } else { "CPU_QUALIFICATION_FRESH" }
        $platform | Add-Member -NotePropertyName qualification_receipt_digest `
            -NotePropertyValue ([string]$receipt.receipt_digest) -Force
        $platform | Add-Member -NotePropertyName qualification_mode `
            -NotePropertyValue $qualificationMode -Force
        $platform | Add-Member -NotePropertyName qualification_key `
            -NotePropertyValue ([string]$Validation.worker_qualification.key) -Force
    }
    return [pscustomobject]@{
        channel = "VERSION_HOST_RESULT"
        passed = $true
        resumed_platform_only = $true
        validation_run = [string]$Validation.validation_run
        expected_worker_invocations = @($expectedRequests).Count
        observed_worker_invocations = if ($platform) { $platform.invocations } else { $null }
        static_worker_invocations = [int]$Validation.static_worker_invocations
        static_observability_state = [string]$Validation.static_observability_state
        observability_credential_source = [string]$script:lastWorkersObservabilityCredentialSource
        observability_diagnostic = [string]$script:lastWorkersObservabilityDiagnostic
        routes = @($Validation.routes)
        cpu_evidence = $platform
        telemetry_window_from = [string]$Validation.telemetry_window_from
        telemetry_window_to = $to.ToString('o')
        expected_requests = @($expectedRequests)
        cpu_route_plan = $Validation.cpu_route_plan
        worker_qualification = $Validation.worker_qualification
        directed_request_ledger = [pscustomobject]@{
            evidence_class="CONTROLLED_EXACT"; request_universe_digest=[string]$plan.request_universe_digest
            planned=@($plan.requests).Count; completed=$receipts.Count
            passed=@($receipts | Where-Object { $_.passed }).Count
        }
        cpu_qualification_mode = $qualificationMode
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
    if ($Value -is [DateTime] -or $Value -is [DateTimeOffset]) {
        $typed = ConvertTo-ReleaseTimestampUtc -Value $Value
        if ($typed -eq [DateTimeOffset]::MinValue) {
            throw "CANDIDATE_STATUS_SCHEMA_MISMATCH"
        }
        return $typed
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
          [Parameter(Mandatory = $true)][object]$Candidate,
          [object]$RoutePlan = ([pscustomobject]@{ contract_routes = @() }))
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
        $acceptanceClass = Get-CandidateParityClass -Path $path `
            -RoutePlan $RoutePlan
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
                    acceptance_class = $acceptanceClass
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
                    route = $path; acceptance_class = $acceptanceClass
                    state = "FAILED"; passed = $false; reason = $reason
                    error = Protect-PreflightDiagnosticText $_.Exception.Message
                    stable_version_id = [string]$Stable.worker_version_id
                    candidate_version_id = [string]$Candidate.worker_version_id
                }
            }
            continue
        }
        $stableRead = Get-ExactVersionJsonObservation `
            -VersionId ([string]$Stable.worker_version_id) `
            -GitSha ([string]$Stable.git_sha) -Path $path `
            -AllowLegacyIdentity:$legacyMode
        $candidateRead = Get-ExactVersionJsonObservation `
            -VersionId ([string]$Candidate.worker_version_id) `
            -GitSha ([string]$Candidate.git_sha) -Path $path
        if (-not $candidateRead.identity_passed -or
            (-not $legacyMode -and -not $stableRead.identity_passed)) {
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = "FAILED"; passed = $false; blocking = $true
                reason = "EXACT_VERSION_IDENTITY_MISMATCH"
                stable_failure = [string]$stableRead.failure_class
                candidate_failure = [string]$candidateRead.failure_class
            }
            continue
        }
        if (-not $stableRead.passed -or -not $candidateRead.passed) {
            $equivalentDebt = [bool](
                $acceptanceClass -eq "C" -and -not $stableRead.passed -and
                -not $candidateRead.passed -and
                -not [bool]$candidateRead.hard_safety_failure -and
                [bool]$stableRead.failure_fingerprint_available -and
                [bool]$candidateRead.failure_fingerprint_available -and
                [string]$stableRead.failure_fingerprint -ceq
                    [string]$candidateRead.failure_fingerprint
            )
            $matchingDebt = [bool](
                $acceptanceClass -eq "C" -and -not $stableRead.passed -and (
                    $candidateRead.passed -or
                    $equivalentDebt
                )
            )
            $failureReason = if ($matchingDebt) {
                if ($candidateRead.passed) { "CANDIDATE_IMPROVES_STABLE_DEBT" }
                else { "UNCHANGED_EXISTING_STABLE_DEBT" }
            } elseif ([bool]$candidateRead.hard_safety_failure) {
                "CANDIDATE_HARD_SAFETY_FAILURE"
            } elseif ($acceptanceClass -eq "B") {
                "CHANGED_BOUNDARY_FAILURE"
            } elseif ($acceptanceClass -eq "C" -and $stableRead.passed) {
                "CANDIDATE_REGRESSION"
            } elseif ($acceptanceClass -eq "C" -and -not $stableRead.passed -and
                -not $candidateRead.passed) {
                "CANDIDATE_DEBT_EQUIVALENCE_UNPROVEN"
            } else { "EXACT_VERSION_READ_FAILED" }
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = if ($matchingDebt) {
                    if ($candidateRead.passed) { "STABLE_DEBT_IMPROVED" }
                    else { "EXISTING_STABLE_DEBT" }
                } else { "FAILED" }
                passed = $matchingDebt
                blocking = -not $matchingDebt
                reason = $failureReason
                stable_failure = [string]$stableRead.failure_class
                candidate_failure = [string]$candidateRead.failure_class
                stable_failure_fingerprint = [string]$stableRead.failure_fingerprint
                candidate_failure_fingerprint = [string]$candidateRead.failure_fingerprint
                stable_failure_reason_code = [string]$stableRead.failure_reason_code
                candidate_failure_reason_code = [string]$candidateRead.failure_reason_code
                stable_diagnostic = [string]$stableRead.diagnostic
                candidate_diagnostic = [string]$candidateRead.diagnostic
            }
            continue
        }
        try {
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
                route = $path; acceptance_class = $acceptanceClass
                state = if ($passed) { "PASSED" } else { "FAILED" }
                passed = $passed; blocking = -not $passed; reason = $reason
                stable_version_id = if ($legacyMode) { [string]$Stable.worker_version_id } else { [string]$stableRead.observed_version_id }
                candidate_version_id = [string]$candidateRead.observed_version_id
            }
        } catch {
            $results += [pscustomobject]@{
                route = $path; acceptance_class = $acceptanceClass
                state = "FAILED"; passed = $false; blocking = $true
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
        [bool]$_.blocking -or (-not $_.passed -and [string]$_.state -ne
            "DEFERRED_TO_POST_CUTOVER_OBSERVATION")
    })
    $stableDebt = @($results | Where-Object {
        [string]$_.state -in @("EXISTING_STABLE_DEBT", "STABLE_DEBT_IMPROVED")
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
        stable_debt = $stableDebt
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
            -Uri "$protectedDashboardUrl/admin/api/session" -Headers $headers `
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

function Get-ProtectedAccessBoundaryIdentity {
    $uri = [Uri]$protectedDashboardUrl
    $production = [Uri]$workerUrl
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne "https" -or
        -not $uri.DnsSafeHost -or
        $uri.DnsSafeHost -ine $production.DnsSafeHost -or
        $uri.Port -ne $production.Port -or $uri.AbsolutePath -ne "/" -or
        $uri.Query -or $uri.Fragment) {
        throw "ACCESS_PROTECTED_HOST_INVALID"
    }
    return [ordered]@{
        origin = $production.GetLeftPart([UriPartial]::Authority).TrimEnd("/")
        host = $production.DnsSafeHost.ToLowerInvariant()
        owner_resource = "/admin/api/session"
    }
}

function Get-AccessQualificationContract {
    if (-not (Test-Path -LiteralPath $accessQualificationContractPath)) {
        throw "ACCESS_QUALIFICATION_CONTRACT_MISSING"
    }
    $contract = Get-Content -LiteralPath $accessQualificationContractPath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    if ([int]$contract.schema_version -ne 1 -or
        [string]$contract.key_contract_version -ne "access-qualification-key-v1" -or
        @($contract.repository_artifacts).Count -eq 0) {
        throw "ACCESS_QUALIFICATION_CONTRACT_INVALID"
    }
    return $contract
}

function Get-AccessProviderBehaviorCore {
    param([Parameter(Mandatory = $true)][object]$Inspection)
    $destinations = @($Inspection.destinations | ForEach-Object { [string]$_ } |
        Sort-Object -Unique)
    $identityProviders = @($Inspection.identity_providers | ForEach-Object {
        ([string]$_).Trim().ToLowerInvariant()
    } | Sort-Object -Unique)
    return [ordered]@{
        application_id = [string]$Inspection.application_id
        application_audience = [string]$Inspection.application_audience
        application_name = [string]$Inspection.application_name
        application_type = ([string]$Inspection.application_type).ToLowerInvariant()
        application_session_duration = ([string]$Inspection.application_session_duration).ToLowerInvariant()
        destinations = $destinations
        policy_id = [string]$Inspection.policy_id
        policy_name = [string]$Inspection.policy_name
        policy_action = ([string]$Inspection.policy_action).ToLowerInvariant()
        policy_order = [int]$Inspection.policy_order
        policy_rule_count = [int]$Inspection.policy_rule_count
        policy_session_duration = ([string]$Inspection.policy_session_duration).ToLowerInvariant()
        owner_rule_sha256 = ([string]$Inspection.owner_rule_sha256).ToLowerInvariant()
        identity_providers = $identityProviders
        mfa_required = [bool]$Inspection.mfa_required
        browser_isolation = [bool]$Inspection.browser_isolation
        purpose_justification = [bool]$Inspection.purpose_justification
        temporary_authentication = [bool]$Inspection.temporary_authentication
    }
}

function Get-AccessProviderBehaviorFingerprint {
    param([Parameter(Mandatory = $true)][object]$Inspection)
    $core = Get-AccessProviderBehaviorCore -Inspection $Inspection
    $json = $core | ConvertTo-Json -Compress -Depth 12
    return Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
}

function Assert-AccessProviderInspectionMatchesContract {
    param([Parameter(Mandatory = $true)][object]$Inspection)
    $required = @(
        "application_id", "application_audience", "application_name",
        "application_type", "application_session_duration", "destinations",
        "policy_id", "policy_name", "policy_action", "policy_order",
        "policy_rule_count", "policy_session_duration", "owner_rule_sha256",
        "identity_providers", "mfa_required", "browser_isolation",
        "purpose_justification", "temporary_authentication"
    )
    foreach ($name in $required) {
        if (-not $Inspection.PSObject.Properties[$name]) {
            throw "ACCESS_PROVIDER_INSPECTION_INVALID:$name"
        }
    }
    foreach ($name in @(
        "mfa_required", "browser_isolation", "purpose_justification",
        "temporary_authentication"
    )) {
        if ($Inspection.PSObject.Properties[$name].Value -isnot [bool]) {
            throw "ACCESS_PROVIDER_INSPECTION_INVALID:$name"
        }
    }
    if ([string]$Inspection.owner_rule_sha256 -notmatch '^[0-9a-f]{64}$' -or
        @($Inspection.destinations).Count -eq 0 -or
        @($Inspection.identity_providers).Count -eq 0) {
        throw "ACCESS_PROVIDER_INSPECTION_INVALID"
    }
    $contract = Get-AccessQualificationContract
    $actual = Get-AccessProviderBehaviorCore -Inspection $Inspection
    $expectedSource = $contract.provider_boundary
    $expected = Get-AccessProviderBehaviorCore -Inspection ([pscustomobject]@{
        application_id = $expectedSource.application_id
        application_audience = $expectedSource.application_audience
        application_name = $expectedSource.application_name
        application_type = $expectedSource.application_type
        application_session_duration = $expectedSource.application_session_duration
        destinations = $contract.protected_boundary.destinations
        policy_id = $expectedSource.policy_id
        policy_name = $expectedSource.policy_name
        policy_action = $expectedSource.policy_action
        policy_order = $expectedSource.policy_order
        policy_rule_count = $expectedSource.policy_rule_count
        policy_session_duration = $expectedSource.policy_session_duration
        owner_rule_sha256 = $expectedSource.owner_rule_sha256
        identity_providers = $expectedSource.identity_providers
        mfa_required = $expectedSource.mfa_required
        browser_isolation = $expectedSource.browser_isolation
        purpose_justification = $expectedSource.purpose_justification
        temporary_authentication = $expectedSource.temporary_authentication
    })
    if (($actual | ConvertTo-Json -Compress -Depth 12) -cne
        ($expected | ConvertTo-Json -Compress -Depth 12)) {
        throw "ACCESS_PROVIDER_CONFIGURATION_CHANGED"
    }
    return $actual
}

function Get-AccessProviderInspectionReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 12
    return Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
}

function Get-AccessEvidenceUtcNow {
    return [DateTimeOffset]::UtcNow
}

function Assert-AccessProviderInspectionReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $core = [ordered]@{
        schema_version = [string]$Receipt.schema_version
        observed_at = [string]$Receipt.observed_at
        inspection_method = [string]$Receipt.inspection_method
        audit_window_start = [string]$Receipt.audit_window_start
        audit_window_end = [string]$Receipt.audit_window_end
        application_change_count = [int]$Receipt.application_change_count
        policy_change_count = [int]$Receipt.policy_change_count
        policy_last_updated_at = [string]$Receipt.policy_last_updated_at
        behavior = $Receipt.behavior
        provider_fingerprint = [string]$Receipt.provider_fingerprint
    }
    $isApi = [string]$Receipt.schema_version -eq "access-provider-inspection-v2"
    if ($isApi) {
        $core.audit_history_complete = [bool]$Receipt.audit_history_complete
        $core.audit_page_count = [int]$Receipt.audit_page_count
        $core.audit_event_count = [int]$Receipt.audit_event_count
        $core.access_failure_count = [int]$Receipt.access_failure_count
    }
    if ([string]$Receipt.schema_version -notin @(
            "access-provider-inspection-v1", "access-provider-inspection-v2"
        ) -or
        [string]$Receipt.receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$Receipt.receipt_digest -cne
            (Get-AccessProviderInspectionReceiptDigest -Core $core) -or
        [string]$Receipt.provider_fingerprint -cne
            (Get-AccessProviderBehaviorFingerprint -Inspection $Receipt.behavior)) {
        throw "ACCESS_PROVIDER_INSPECTION_TAMPERED"
    }
    $null = Assert-AccessProviderInspectionMatchesContract -Inspection $Receipt.behavior
    $observedAt = ConvertTo-ReleaseTimestampUtc -Value $Receipt.observed_at
    $windowStart = ConvertTo-ReleaseTimestampUtc -Value $Receipt.audit_window_start
    $windowEnd = ConvertTo-ReleaseTimestampUtc -Value $Receipt.audit_window_end
    $policyUpdated = ConvertTo-ReleaseTimestampUtc -Value $Receipt.policy_last_updated_at
    if ([string]$Receipt.inspection_method -notin @(
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
        [int]$Receipt.application_change_count -ne 0 -or
        [int]$Receipt.policy_change_count -ne 0 -or
        ($isApi -and (-not [bool]$Receipt.audit_history_complete -or
            [int]$Receipt.audit_page_count -lt 1 -or
            [int]$Receipt.access_failure_count -ne 0))) {
        throw "ACCESS_PROVIDER_INSPECTION_INVALID"
    }
    return $Receipt
}

function Get-LatestAccessProviderInspectionReceipt {
    if (-not (Test-Path -LiteralPath $accessProviderInspectionRoot)) {
        throw "ACCESS_PROVIDER_INSPECTION_UNAVAILABLE"
    }
    $valid = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $accessProviderInspectionRoot -Filter '*.json' -File)) {
        try {
            $receipt = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            $valid += Assert-AccessProviderInspectionReceipt -Receipt $receipt
        } catch {}
    }
    if ($valid.Count -eq 0) { throw "ACCESS_PROVIDER_INSPECTION_UNAVAILABLE" }
    return $valid | Sort-Object {
        ConvertTo-ReleaseTimestampUtc -Value $_.observed_at
    } | Select-Object -Last 1
}

function Get-AccessRepositoryArtifactIdentity {
    param([Parameter(Mandatory = $true)][string]$GitSha)
    if ($GitSha -notmatch '^[0-9a-f]{40}$') { throw "ACCESS_GIT_IDENTITY_INVALID" }
    $contract = Get-AccessQualificationContract
    $result = [ordered]@{}
    foreach ($path in @($contract.repository_artifacts | Sort-Object -Unique)) {
        $read = Invoke-Utf8NativeProcess -FilePath "git.exe" -Arguments @(
            "-C", $repositoryRoot, "rev-parse", "$GitSha`:$([string]$path)"
        )
        $blob = ([string]$read.stdout).Trim()
        if ([int]$read.exit_code -ne 0 -or $blob -notmatch '^[0-9a-f]{40,64}$') {
            throw "ACCESS_ARTIFACT_UNAVAILABLE:$([string]$path)"
        }
        $result[[string]$path] = $blob
    }
    return $result
}

function Get-AccessQualificationIdentity {
    param(
        [Parameter(Mandatory = $true)][string]$GitSha,
        [Parameter(Mandatory = $true)][object]$ProviderInspection
    )
    $contract = Get-AccessQualificationContract
    $boundary = Get-ProtectedAccessBoundaryIdentity
    $artifacts = Get-AccessRepositoryArtifactIdentity -GitSha $GitSha
    $core = [ordered]@{
        contract_version = [string]$contract.key_contract_version
        protected_boundary = [ordered]@{
            origin = [string]$boundary.origin
            host = [string]$boundary.host
            owner_resource = [string]$boundary.owner_resource
            destinations = @($contract.protected_boundary.destinations | Sort-Object -Unique)
        }
        provider_fingerprint = [string]$ProviderInspection.provider_fingerprint
        repository_artifacts = $artifacts
    }
    $json = $core | ConvertTo-Json -Compress -Depth 12
    return [pscustomobject]@{
        access_qualification_key = Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
        core = $core
    }
}

function Get-AccessBoundaryOperatorChecklistText {
    param([Parameter(Mandatory = $true)][object]$Candidate)
    $boundary = Get-ProtectedAccessBoundaryIdentity
    return @"
Protected URL: $([string]$boundary.origin)$([string]$boundary.owner_resource)
Git SHA: $([string]$Candidate.git_sha)
Worker Version: $([string]$Candidate.worker_version_id)
Validation key: $([string]$Candidate.validation_key)

Confirm every real protected-host check is complete:
[1] Owner login succeeds.
[2] The owner-only resource is accessible after owner login.
[3] Non-owner or unauthorized access is denied.
[4] Logout/session termination succeeds.
[5] Access is denied after logout.
[6] Reauthentication succeeds.

This records human evidence only. It does not perform authentication.
"@.Trim()
}

function Get-AccessBoundaryReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 12
    Get-Sha256BytesHex -Bytes ([System.Text.Encoding]::UTF8.GetBytes($json))
}

function Get-AccessBoundaryReceiptPath {
    param([Parameter(Mandatory = $true)][string]$ValidationKey)
    $keyDigest = Get-Sha256BytesHex -Bytes `
        ([System.Text.Encoding]::UTF8.GetBytes($ValidationKey))
    return Join-Path $accessBoundaryReceiptRoot "$keyDigest.json"
}

function New-AccessBoundaryAcceptanceReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [Parameter(Mandatory = $true)][object]$Checklist
    )
    $acceptedAt = [DateTimeOffset]::UtcNow
    $boundary = Get-ProtectedAccessBoundaryIdentity
    $core = [ordered]@{
        schema_version = "access-boundary-acceptance-receipt-v1"
        accepted_at = $acceptedAt.ToString("o")
        expires_at = $acceptedAt.Add($accessBoundaryReceiptMaxAge).ToString("o")
        accepted_by = [Environment]::UserName
        validation_key = [string]$Candidate.validation_key
        candidate = [ordered]@{
            git_sha = [string]$Candidate.git_sha
            worker_version_id = [string]$Candidate.worker_version_id
            windows_revision = [string]$Candidate.windows_revision
            artifact_kind = [string]$Candidate.artifact_kind
            branch = [string]$Candidate.branch
        }
        stable = [ordered]@{
            validation_key = [string]$Stable.validation_key
            git_sha = [string]$Stable.git_sha
            worker_version_id = [string]$Stable.worker_version_id
            windows_revision = [string]$Stable.windows_revision
        }
        access_boundary = $boundary
        checklist = $Checklist
    }
    return [pscustomobject]@{
        schema_version = $core.schema_version
        accepted_at = $core.accepted_at
        expires_at = $core.expires_at
        accepted_by = $core.accepted_by
        validation_key = $core.validation_key
        candidate = $core.candidate
        stable = $core.stable
        access_boundary = $core.access_boundary
        checklist = $core.checklist
        receipt_digest = Get-AccessBoundaryReceiptDigest -Core $core
    }
}

function Write-AccessBoundaryAcceptanceReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $path = Get-AccessBoundaryReceiptPath -ValidationKey ([string]$Receipt.validation_key)
    New-Item -ItemType Directory -Path $accessBoundaryReceiptRoot -Force | Out-Null
    if (Test-Path -LiteralPath $path) {
        $existing = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if ([string]$existing.receipt_digest -eq [string]$Receipt.receipt_digest) {
            return
        }
        throw "ACCESS_RECEIPT_IMMUTABLE_CONFLICT"
    }
    Write-ControlCenterJsonAtomic -Path $path -Value $Receipt `
        -Depth 12 -Immutable
}

function Assert-AccessBoundaryAcceptanceReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$Stable,
        [switch]$SkipCandidateStateBinding
    )
    $path = Get-AccessBoundaryReceiptPath -ValidationKey ([string]$Candidate.validation_key)
    if (-not (Test-Path -LiteralPath $path)) {
        throw "ACCESS_RECEIPT_MISSING"
    }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    $core = [ordered]@{
        schema_version = [string]$receipt.schema_version
        accepted_at = [string]$receipt.accepted_at
        expires_at = [string]$receipt.expires_at
        accepted_by = [string]$receipt.accepted_by
        validation_key = [string]$receipt.validation_key
        candidate = $receipt.candidate
        stable = $receipt.stable
        access_boundary = $receipt.access_boundary
        checklist = $receipt.checklist
    }
    if ([string]$receipt.schema_version -ne "access-boundary-acceptance-receipt-v1" -or
        [string]$receipt.receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$receipt.receipt_digest -cne (Get-AccessBoundaryReceiptDigest -Core $core)) {
        throw "ACCESS_RECEIPT_TAMPERED"
    }
    $acceptedAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.accepted_at
    $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.expires_at
    if ($acceptedAt -eq [DateTimeOffset]::MinValue -or
        $expiresAt -eq [DateTimeOffset]::MinValue -or
        $acceptedAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5) -or
        $expiresAt -ne $acceptedAt.Add($accessBoundaryReceiptMaxAge) -or
        $expiresAt -le [DateTimeOffset]::UtcNow) {
        throw "ACCESS_RECEIPT_STALE"
    }
    $requiredChecks = @(
        "owner_login_succeeds",
        "owner_resource_accessible",
        "unauthorized_access_denied",
        "logout_succeeds",
        "access_denied_after_logout",
        "reauthentication_succeeds"
    )
    foreach ($name in $requiredChecks) {
        $property = $receipt.checklist.PSObject.Properties[$name]
        if (-not $property -or $property.Value -isnot [bool] -or
            -not [bool]$property.Value) {
            throw "ACCESS_RECEIPT_CHECKLIST_INCOMPLETE:$name"
        }
    }
    if (@($receipt.checklist.PSObject.Properties).Count -ne $requiredChecks.Count) {
        throw "ACCESS_RECEIPT_CHECKLIST_INVALID"
    }
    if ([string]$receipt.validation_key -ne [string]$Candidate.validation_key -or
        [string]$receipt.candidate.git_sha -ne [string]$Candidate.git_sha -or
        [string]$receipt.candidate.worker_version_id -ne [string]$Candidate.worker_version_id -or
        [string]$receipt.candidate.windows_revision -ne [string]$Candidate.windows_revision -or
        [string]$receipt.candidate.artifact_kind -ne [string]$Candidate.artifact_kind -or
        [string]$receipt.candidate.branch -ne [string]$Candidate.branch) {
        throw "ACCESS_RECEIPT_CANDIDATE_MISMATCH"
    }
    if ([string]$receipt.stable.validation_key -ne [string]$Stable.validation_key -or
        [string]$receipt.stable.git_sha -ne [string]$Stable.git_sha -or
        [string]$receipt.stable.worker_version_id -ne [string]$Stable.worker_version_id -or
        [string]$receipt.stable.windows_revision -ne [string]$Stable.windows_revision) {
        throw "ACCESS_RECEIPT_STABLE_MISMATCH"
    }
    $boundary = Get-ProtectedAccessBoundaryIdentity
    if ([string]$receipt.access_boundary.origin -cne [string]$boundary.origin -or
        [string]$receipt.access_boundary.host -cne [string]$boundary.host -or
        [string]$receipt.access_boundary.owner_resource -cne [string]$boundary.owner_resource) {
        throw "ACCESS_RECEIPT_HOST_MISMATCH"
    }
    if (-not $SkipCandidateStateBinding) {
        if (-not $Candidate.access_acceptance -or
            [string]$Candidate.access_acceptance.validation_key -ne [string]$Candidate.validation_key -or
            [string]$Candidate.access_acceptance.receipt_digest -cne [string]$receipt.receipt_digest -or
            [string]$Candidate.access_acceptance.protected_host -cne [string]$boundary.host -or
            [string]$Candidate.access_acceptance.accepted_at -cne [string]$receipt.accepted_at -or
            [string]$Candidate.access_acceptance.expires_at -cne [string]$receipt.expires_at) {
            throw "ACCESS_RECEIPT_STATE_MISMATCH"
        }
    }
    return $receipt
}

function Assert-HistoricalAccessBoundaryReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $core = [ordered]@{
        schema_version = [string]$Receipt.schema_version
        accepted_at = [string]$Receipt.accepted_at
        expires_at = [string]$Receipt.expires_at
        accepted_by = [string]$Receipt.accepted_by
        validation_key = [string]$Receipt.validation_key
        candidate = $Receipt.candidate
        stable = $Receipt.stable
        access_boundary = $Receipt.access_boundary
        checklist = $Receipt.checklist
    }
    if ([string]$Receipt.schema_version -ne "access-boundary-acceptance-receipt-v1" -or
        [string]$Receipt.receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$Receipt.receipt_digest -cne (Get-AccessBoundaryReceiptDigest -Core $core)) {
        throw "ACCESS_RECEIPT_TAMPERED"
    }
    $acceptedAt = ConvertTo-ReleaseTimestampUtc -Value $Receipt.accepted_at
    $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $Receipt.expires_at
    if ($acceptedAt -eq [DateTimeOffset]::MinValue -or
        $expiresAt -ne $acceptedAt.Add($accessBoundaryReceiptMaxAge) -or
        $acceptedAt -gt [DateTimeOffset]::UtcNow.AddMinutes(5)) {
        throw "ACCESS_RECEIPT_INVALID"
    }
    foreach ($name in @(
        "owner_login_succeeds", "owner_resource_accessible",
        "unauthorized_access_denied", "logout_succeeds",
        "access_denied_after_logout", "reauthentication_succeeds"
    )) {
        $property = $Receipt.checklist.PSObject.Properties[$name]
        if (-not $property -or $property.Value -isnot [bool] -or -not [bool]$property.Value) {
            throw "ACCESS_RECEIPT_CHECKLIST_INCOMPLETE:$name"
        }
    }
    if (@($Receipt.checklist.PSObject.Properties).Count -ne 6) {
        throw "ACCESS_RECEIPT_CHECKLIST_INVALID"
    }
    $boundary = Get-ProtectedAccessBoundaryIdentity
    if ([string]$Receipt.access_boundary.origin -cne [string]$boundary.origin -or
        [string]$Receipt.access_boundary.host -cne [string]$boundary.host -or
        [string]$Receipt.access_boundary.owner_resource -cne [string]$boundary.owner_resource -or
        [string]$Receipt.candidate.git_sha -notmatch '^[0-9a-f]{40}$') {
        throw "ACCESS_RECEIPT_HOST_MISMATCH"
    }
    return $Receipt
}

function Get-LatestHistoricalAccessBoundaryReceipt {
    if (-not (Test-Path -LiteralPath $accessBoundaryReceiptRoot)) {
        throw "ACCESS_HISTORICAL_RECEIPT_MISSING"
    }
    $valid = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $accessBoundaryReceiptRoot -Filter '*.json' -File)) {
        try {
            $receipt = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            $valid += Assert-HistoricalAccessBoundaryReceipt -Receipt $receipt
        } catch {}
    }
    if ($valid.Count -eq 0) { throw "ACCESS_HISTORICAL_RECEIPT_MISSING" }
    return $valid | Sort-Object {
        ConvertTo-ReleaseTimestampUtc -Value $_.accepted_at
    } | Select-Object -Last 1
}

function Test-AccessQualificationHistoryIsClean {
    param([Parameter(Mandatory = $true)][object]$PriorReceipt)
    if (-not (Test-Path -LiteralPath $releaseHistoryPath)) { return $false }
    $acceptedAt = ConvertTo-ReleaseTimestampUtc -Value $PriorReceipt.accepted_at
    $acceptedEventFound = $false
    foreach ($line in [IO.File]::ReadLines($releaseHistoryPath, [Text.Encoding]::UTF8)) {
        try { $entry = $line | ConvertFrom-ReleaseControlJson } catch { continue }
        $occurredAt = ConvertTo-ReleaseTimestampUtc -Value $entry.occurred_at
        if ($occurredAt -lt $acceptedAt) { continue }
        if ([string]$entry.event -eq "CANDIDATE_ACCESS_BOUNDARY_ACCEPTED" -and
            [string]$entry.detail.receipt_digest -ceq [string]$PriorReceipt.receipt_digest -and
            [string]$entry.release.validation_key -eq [string]$PriorReceipt.validation_key) {
            $acceptedEventFound = $true
            continue
        }
        $failureText = "$([string]$entry.event)|$([string]$entry.detail.reason)|$([string]$entry.detail.state)"
        if ($failureText -match '(?i)(ACCESS|AUTH).*(FAIL|REJECT|INVALID|TAMPER|MISMATCH|ERROR)') {
            return $false
        }
    }
    return $acceptedEventFound
}

function Get-AccessQualificationReuseReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 16
    return Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
}

function Get-AccessQualificationReuseReceiptPath {
    param([Parameter(Mandatory = $true)][string]$ValidationKey)
    $digest = Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($ValidationKey))
    return Join-Path $accessQualificationReuseReceiptRoot "$digest.json"
}

function New-AccessQualificationReuseReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][object]$PriorReceipt,
        [Parameter(Mandatory = $true)][object]$ProviderInspection,
        [Parameter(Mandatory = $true)][object]$PriorIdentity,
        [Parameter(Mandatory = $true)][object]$CurrentIdentity,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][string[]]$ChangedAccessArtifacts
    )
    $verifiedAt = Get-AccessEvidenceUtcNow
    $core = [ordered]@{
        schema_version = "access-qualification-reuse-receipt-v1"
        state = "ACCESS_QUALIFICATION_REUSED"
        verified_at = $verifiedAt.ToString("o")
        expires_at = $verifiedAt.Add($accessMachineReceiptMaxAge).ToString("o")
        validation_key = [string]$Candidate.validation_key
        candidate_git_sha = [string]$Candidate.git_sha
        candidate_worker_version_id = [string]$Candidate.worker_version_id
        access_key = [string]$CurrentIdentity.access_qualification_key
        prior_access_receipt_digest = [string]$PriorReceipt.receipt_digest
        prior_access_key = [string]$PriorIdentity.access_qualification_key
        current_access_key = [string]$CurrentIdentity.access_qualification_key
        protected_origin = [string]$CurrentIdentity.core.protected_boundary.origin
        provider_fingerprint = [string]$ProviderInspection.provider_fingerprint
        provider_inspection_receipt_digest = [string]$ProviderInspection.receipt_digest
        changed_access_artifacts = @($ChangedAccessArtifacts | Sort-Object -Unique)
    }
    return [pscustomobject]@{
        schema_version = $core.schema_version
        state = $core.state
        verified_at = $core.verified_at
        expires_at = $core.expires_at
        validation_key = $core.validation_key
        candidate_git_sha = $core.candidate_git_sha
        candidate_worker_version_id = $core.candidate_worker_version_id
        access_key = $core.access_key
        prior_access_receipt_digest = $core.prior_access_receipt_digest
        prior_access_key = $core.prior_access_key
        current_access_key = $core.current_access_key
        protected_origin = $core.protected_origin
        provider_fingerprint = $core.provider_fingerprint
        provider_inspection_receipt_digest = $core.provider_inspection_receipt_digest
        changed_access_artifacts = $core.changed_access_artifacts
        receipt_digest = Get-AccessQualificationReuseReceiptDigest -Core $core
    }
}

function Write-AccessQualificationReuseReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $path = Get-AccessQualificationReuseReceiptPath -ValidationKey $Receipt.validation_key
    New-Item -ItemType Directory -Path $accessQualificationReuseReceiptRoot -Force | Out-Null
    if (Test-Path -LiteralPath $path) {
        $existing = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if ([string]$existing.receipt_digest -ceq [string]$Receipt.receipt_digest) { return }
        throw "ACCESS_QUALIFICATION_REUSE_IMMUTABLE_CONFLICT"
    }
    Write-ControlCenterJsonAtomic -Path $path -Value $Receipt `
        -Depth 16 -Immutable
}

function Assert-AccessQualificationReuseReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [switch]$AllowStale,
        [switch]$SkipCandidateStateBinding
    )
    $path = Get-AccessQualificationReuseReceiptPath -ValidationKey $Candidate.validation_key
    if (-not (Test-Path -LiteralPath $path)) { throw "ACCESS_QUALIFICATION_REUSE_MISSING" }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    $core = [ordered]@{
        schema_version = [string]$receipt.schema_version
        state = [string]$receipt.state
        verified_at = [string]$receipt.verified_at
        expires_at = [string]$receipt.expires_at
        validation_key = [string]$receipt.validation_key
        candidate_git_sha = [string]$receipt.candidate_git_sha
        candidate_worker_version_id = [string]$receipt.candidate_worker_version_id
        access_key = [string]$receipt.access_key
        prior_access_receipt_digest = [string]$receipt.prior_access_receipt_digest
        prior_access_key = [string]$receipt.prior_access_key
        current_access_key = [string]$receipt.current_access_key
        protected_origin = [string]$receipt.protected_origin
        provider_fingerprint = [string]$receipt.provider_fingerprint
        provider_inspection_receipt_digest = [string]$receipt.provider_inspection_receipt_digest
        changed_access_artifacts = @($receipt.changed_access_artifacts)
    }
    if ([string]$receipt.schema_version -ne "access-qualification-reuse-receipt-v1" -or
        [string]$receipt.state -ne "ACCESS_QUALIFICATION_REUSED" -or
        [string]$receipt.receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$receipt.receipt_digest -cne
            (Get-AccessQualificationReuseReceiptDigest -Core $core) -or
        [string]$receipt.validation_key -ne [string]$Candidate.validation_key -or
        [string]$receipt.candidate_git_sha -ne [string]$Candidate.git_sha -or
        [string]$receipt.candidate_worker_version_id -ne [string]$Candidate.worker_version_id -or
        [string]$receipt.prior_access_key -cne [string]$receipt.current_access_key -or
        @($receipt.changed_access_artifacts).Count -ne 0) {
        throw "ACCESS_QUALIFICATION_REUSE_TAMPERED"
    }
    $verifiedAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.verified_at
    $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.expires_at
    if ($verifiedAt -eq [DateTimeOffset]::MinValue -or
        $expiresAt -ne $verifiedAt.Add($accessMachineReceiptMaxAge) -or
        (-not $AllowStale -and $expiresAt -le (Get-AccessEvidenceUtcNow))) {
        throw "ACCESS_QUALIFICATION_REUSE_STALE"
    }
    if (-not $SkipCandidateStateBinding -and (-not $Candidate.access_qualification -or
        [string]$Candidate.access_qualification.receipt_digest -cne
            [string]$receipt.receipt_digest -or
        [string]$Candidate.access_qualification.access_key -cne
            [string]$receipt.access_key)) {
        throw "ACCESS_QUALIFICATION_REUSE_STATE_MISMATCH"
    }
    return $receipt
}

function Get-AccessProviderInspectionReceiptByDigest {
    param([Parameter(Mandatory = $true)][string]$Digest)
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "ACCESS_PROVIDER_INSPECTION_TAMPERED"
    }
    $path = Join-Path $accessProviderInspectionRoot "$Digest.json"
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "ACCESS_PROVIDER_INSPECTION_UNAVAILABLE"
    }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    return Assert-AccessProviderInspectionReceipt -Receipt $receipt
}

function Get-HistoricalAccessBoundaryReceiptByDigest {
    param([Parameter(Mandatory = $true)][string]$Digest)
    if ($Digest -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-Path -LiteralPath $accessBoundaryReceiptRoot)) {
        throw "ACCESS_HISTORICAL_RECEIPT_MISSING"
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $accessBoundaryReceiptRoot `
            -Filter '*.json' -File)) {
        try {
            $receipt = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            if ([string]$receipt.receipt_digest -ceq $Digest) {
                return Assert-HistoricalAccessBoundaryReceipt -Receipt $receipt
            }
        } catch {}
    }
    throw "ACCESS_HISTORICAL_RECEIPT_MISSING"
}

function Get-AccessQualificationRenewalReceiptDigest {
    param([Parameter(Mandatory = $true)][object]$Core)
    $json = $Core | ConvertTo-Json -Compress -Depth 16
    return Get-Sha256BytesHex -Bytes ([Text.Encoding]::UTF8.GetBytes($json))
}

function Get-AccessQualificationRenewalReceiptPath {
    param([Parameter(Mandatory = $true)][string]$Digest)
    if ($Digest -notmatch '^[0-9a-f]{64}$') {
        throw "ACCESS_QUALIFICATION_RENEWAL_TAMPERED"
    }
    return Join-Path $accessQualificationRenewalReceiptRoot "$Digest.json"
}

function Get-AccessQualificationRenewalCore {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    return [ordered]@{
        schema_version = [string]$Receipt.schema_version
        state = [string]$Receipt.state
        issued_at = [string]$Receipt.issued_at
        expires_at = [string]$Receipt.expires_at
        validation_key = [string]$Receipt.validation_key
        candidate_git_sha = [string]$Receipt.candidate_git_sha
        candidate_worker_version_id = [string]$Receipt.candidate_worker_version_id
        root_human_receipt_digest = [string]$Receipt.root_human_receipt_digest
        previous_machine_receipt_digest = [string]$Receipt.previous_machine_receipt_digest
        access_qualification_key = [string]$Receipt.access_qualification_key
        provider_fingerprint = [string]$Receipt.provider_fingerprint
        protected_origin = [string]$Receipt.protected_origin
        provider_inspection_receipt_digest = [string]$Receipt.provider_inspection_receipt_digest
        inspection_window_start = [string]$Receipt.inspection_window_start
        inspection_window_end = [string]$Receipt.inspection_window_end
        provider_changes_observed = [int]$Receipt.provider_changes_observed
        access_failures_observed = [int]$Receipt.access_failures_observed
    }
}

function New-HistoricalAccessMachineCandidate {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $state = [string]$Receipt.state
    if ($state -eq "ACCESS_QUALIFICATION_RENEWED") {
        $accessKey = [string]$Receipt.access_qualification_key
    } elseif ($state -eq "ACCESS_QUALIFICATION_REUSED") {
        $accessKey = [string]$Receipt.access_key
    } else {
        throw "ACCESS_QUALIFICATION_MACHINE_RECEIPT_INVALID"
    }
    return [pscustomobject]@{
        validation_key = [string]$Receipt.validation_key
        git_sha = [string]$Receipt.candidate_git_sha
        worker_version_id = [string]$Receipt.candidate_worker_version_id
        access_qualification = [pscustomobject]@{
            state = $state
            access_key = $accessKey
            receipt_digest = [string]$Receipt.receipt_digest
        }
    }
}

function Write-AccessQualificationRenewalReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    $path = Get-AccessQualificationRenewalReceiptPath -Digest $Receipt.receipt_digest
    New-Item -ItemType Directory -Path $accessQualificationRenewalReceiptRoot -Force |
        Out-Null
    if (Test-Path -LiteralPath $path) {
        $existing = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $existingCore = Get-AccessQualificationRenewalCore -Receipt $existing
        if ([string]$existing.receipt_digest -ceq [string]$Receipt.receipt_digest -and
            [string]$existing.receipt_digest -ceq
                (Get-AccessQualificationRenewalReceiptDigest -Core $existingCore)) {
            return
        }
        throw "ACCESS_QUALIFICATION_RENEWAL_IMMUTABLE_CONFLICT"
    }
    Write-ControlCenterJsonAtomic -Path $path -Value $Receipt `
        -Depth 16 -Immutable
}

function Assert-AccessQualificationRenewalReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [Parameter(Mandatory = $true)][string]$Digest,
        [switch]$AllowStale,
        [switch]$SkipCandidateStateBinding,
        [hashtable]$Visited = $null
    )
    if ($null -eq $Visited) { $Visited = @{} }
    if ($Visited.ContainsKey($Digest)) {
        throw "ACCESS_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
    }
    $Visited[$Digest] = $true
    $path = Get-AccessQualificationRenewalReceiptPath -Digest $Digest
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "ACCESS_QUALIFICATION_RENEWAL_MISSING"
    }
    $receipt = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    $core = Get-AccessQualificationRenewalCore -Receipt $receipt
    if ([string]$receipt.schema_version -ne
            "access-qualification-renewal-receipt-v1" -or
        [string]$receipt.state -ne "ACCESS_QUALIFICATION_RENEWED" -or
        [string]$receipt.receipt_digest -cne $Digest -or
        [string]$receipt.receipt_digest -cne
            (Get-AccessQualificationRenewalReceiptDigest -Core $core) -or
        [string]$receipt.validation_key -ne [string]$Candidate.validation_key -or
        [string]$receipt.candidate_git_sha -ne [string]$Candidate.git_sha -or
        [string]$receipt.candidate_worker_version_id -ne
            [string]$Candidate.worker_version_id -or
        [string]$receipt.root_human_receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$receipt.previous_machine_receipt_digest -notmatch '^[0-9a-f]{64}$' -or
        [string]$receipt.access_qualification_key -notmatch '^[0-9a-f]{64}$' -or
        [string]$receipt.provider_fingerprint -notmatch '^[0-9a-f]{64}$' -or
        [int]$receipt.provider_changes_observed -ne 0 -or
        [int]$receipt.access_failures_observed -ne 0) {
        throw "ACCESS_QUALIFICATION_RENEWAL_TAMPERED"
    }
    $issuedAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.issued_at
    $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.expires_at
    $windowStart = ConvertTo-ReleaseTimestampUtc -Value $receipt.inspection_window_start
    $windowEnd = ConvertTo-ReleaseTimestampUtc -Value $receipt.inspection_window_end
    if ($issuedAt -eq [DateTimeOffset]::MinValue -or $issuedAt -ne $windowEnd -or
        $expiresAt -ne $issuedAt.Add($accessMachineReceiptMaxAge) -or
        (-not $AllowStale -and $expiresAt -le (Get-AccessEvidenceUtcNow)) -or
        $windowStart -ge $windowEnd) {
        throw "ACCESS_QUALIFICATION_RENEWAL_STALE"
    }
    $provider = Get-AccessProviderInspectionReceiptByDigest `
        -Digest ([string]$receipt.provider_inspection_receipt_digest)
    if ([string]$provider.schema_version -ne "access-provider-inspection-v2" -or
        [string]$provider.provider_fingerprint -cne
            [string]$receipt.provider_fingerprint -or
        (ConvertTo-ReleaseTimestampUtc -Value $provider.audit_window_start) -ne $windowStart -or
        (ConvertTo-ReleaseTimestampUtc -Value $provider.audit_window_end) -ne $windowEnd) {
        throw "ACCESS_QUALIFICATION_RENEWAL_PROVIDER_MISMATCH"
    }
    $previousPath = Get-AccessQualificationRenewalReceiptPath `
        -Digest ([string]$receipt.previous_machine_receipt_digest)
    if (Test-Path -LiteralPath $previousPath -PathType Leaf) {
        $previousRaw = Get-Content -LiteralPath $previousPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $previousCandidate = New-HistoricalAccessMachineCandidate `
            -Receipt $previousRaw
        $previous = Assert-AccessQualificationRenewalReceipt `
            -Candidate $previousCandidate `
            -Digest ([string]$receipt.previous_machine_receipt_digest) -AllowStale `
            -SkipCandidateStateBinding -Visited $Visited
        $previousRoot = [string]$previous.root_human_receipt_digest
        $previousProviderDigest = [string]$previous.provider_inspection_receipt_digest
        $previousAccessKey = [string]$previous.access_qualification_key
    } else {
        $reuseFiles = @(Get-ChildItem -LiteralPath $accessQualificationReuseReceiptRoot `
            -Filter '*.json' -File -ErrorAction SilentlyContinue)
        $previousRaw = $null
        foreach ($file in $reuseFiles) {
            try {
                $candidateReceipt = Get-Content -LiteralPath $file.FullName -Raw `
                    -Encoding UTF8 | ConvertFrom-ReleaseControlJson
                if ([string]$candidateReceipt.receipt_digest -ceq
                        [string]$receipt.previous_machine_receipt_digest) {
                    $previousRaw = $candidateReceipt
                    break
                }
            } catch {
                throw "ACCESS_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
            }
        }
        if (-not $previousRaw) {
            throw "ACCESS_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
        }
        $previousCandidate = New-HistoricalAccessMachineCandidate `
            -Receipt $previousRaw
        $previous = Assert-AccessQualificationReuseReceipt `
            -Candidate $previousCandidate `
            -AllowStale -SkipCandidateStateBinding
        if ([string]$previous.receipt_digest -cne
            [string]$receipt.previous_machine_receipt_digest) {
            throw "ACCESS_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
        }
        $previousRoot = [string]$previous.prior_access_receipt_digest
        $previousProviderDigest = [string]$previous.provider_inspection_receipt_digest
        $previousAccessKey = [string]$previous.access_key
    }
    $previousProvider = Get-AccessProviderInspectionReceiptByDigest `
        -Digest $previousProviderDigest
    if ($previousRoot -cne [string]$receipt.root_human_receipt_digest -or
        $previousAccessKey -cne [string]$receipt.access_qualification_key -or
        [string]$previousProvider.provider_fingerprint -cne
            [string]$previous.provider_fingerprint -or
        (ConvertTo-ReleaseTimestampUtc -Value $previousProvider.audit_window_end) -ne
            $windowStart -or
        [string]$previous.provider_fingerprint -cne
            [string]$receipt.provider_fingerprint) {
        throw "ACCESS_QUALIFICATION_RENEWAL_CHAIN_BROKEN"
    }
    $root = Get-HistoricalAccessBoundaryReceiptByDigest `
        -Digest ([string]$receipt.root_human_receipt_digest)
    if (-not (Test-AccessQualificationHistoryIsClean -PriorReceipt $root)) {
        throw "ACCESS_QUALIFICATION_HISTORY_INVALID"
    }
    if (-not $SkipCandidateStateBinding -and (-not $Candidate.access_qualification -or
        [string]$Candidate.access_qualification.receipt_digest -cne $Digest -or
        [string]$Candidate.access_qualification.access_key -cne
            [string]$receipt.access_qualification_key)) {
        throw "ACCESS_QUALIFICATION_RENEWAL_STATE_MISMATCH"
    }
    return $receipt
}

function Get-LatestHistoricalAccessMachineAuthority {
    $valid = @()
    foreach ($file in @(Get-ChildItem -LiteralPath $accessQualificationRenewalReceiptRoot `
            -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try {
            $raw = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            $historicalCandidate = New-HistoricalAccessMachineCandidate -Receipt $raw
            $receipt = Assert-AccessQualificationRenewalReceipt `
                -Candidate $historicalCandidate -Digest ([string]$raw.receipt_digest) `
                -AllowStale -SkipCandidateStateBinding
            $valid += [pscustomobject]@{
                candidate = $historicalCandidate
                receipt = $receipt
                observed_at = ConvertTo-ReleaseTimestampUtc -Value $receipt.issued_at
            }
        } catch { throw }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $accessQualificationReuseReceiptRoot `
            -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        try {
            $raw = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            $historicalCandidate = New-HistoricalAccessMachineCandidate -Receipt $raw
            $receipt = Assert-AccessQualificationReuseReceipt `
                -Candidate $historicalCandidate -AllowStale -SkipCandidateStateBinding
            $valid += [pscustomobject]@{
                candidate = $historicalCandidate
                receipt = $receipt
                observed_at = ConvertTo-ReleaseTimestampUtc -Value $receipt.verified_at
            }
        } catch { throw }
    }
    if ($valid.Count -eq 0) { return $null }
    return $valid | Sort-Object observed_at | Select-Object -Last 1
}

function Assert-AccessQualificationMachineReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [switch]$AllowStale,
        [switch]$SkipCandidateStateBinding
    )
    if ([string]$Candidate.access_qualification.state -eq
            "ACCESS_QUALIFICATION_RENEWED") {
        return Assert-AccessQualificationRenewalReceipt -Candidate $Candidate `
            -Digest ([string]$Candidate.access_qualification.receipt_digest) `
            -AllowStale:$AllowStale `
            -SkipCandidateStateBinding:$SkipCandidateStateBinding
    }
    return Assert-AccessQualificationReuseReceipt -Candidate $Candidate `
        -AllowStale:$AllowStale `
        -SkipCandidateStateBinding:$SkipCandidateStateBinding
}

function Invoke-CandidateAccessQualificationRenewal {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [object]$PriorCandidate = $null
    )
    $authorityCandidate = if ($PriorCandidate) { $PriorCandidate } else { $Candidate }
    if (-not $authorityCandidate.access_qualification) {
        throw "ACCESS_QUALIFICATION_RENEWAL_NOT_APPLICABLE"
    }
    $priorDigest = [string]$authorityCandidate.access_qualification.receipt_digest
    $priorState = [string]$authorityCandidate.access_qualification.state
    if ($priorState -eq "ACCESS_QUALIFICATION_RENEWED") {
        $prior = Assert-AccessQualificationRenewalReceipt `
            -Candidate $authorityCandidate `
            -Digest $priorDigest -AllowStale
        $rootDigest = [string]$prior.root_human_receipt_digest
        $previousProviderDigest = [string]$prior.provider_inspection_receipt_digest
        $accessKey = [string]$prior.access_qualification_key
    } elseif ($priorState -eq "ACCESS_QUALIFICATION_REUSED") {
        $prior = Assert-AccessQualificationReuseReceipt `
            -Candidate $authorityCandidate -AllowStale
        $rootDigest = [string]$prior.prior_access_receipt_digest
        $previousProviderDigest = [string]$prior.provider_inspection_receipt_digest
        $accessKey = [string]$prior.access_key
    } else { throw "ACCESS_QUALIFICATION_RENEWAL_NOT_APPLICABLE" }
    $root = Get-HistoricalAccessBoundaryReceiptByDigest -Digest $rootDigest
    if (-not (Test-AccessQualificationHistoryIsClean -PriorReceipt $root)) {
        throw "ACCESS_QUALIFICATION_HISTORY_INVALID"
    }
    $previousProvider = Get-AccessProviderInspectionReceiptByDigest `
        -Digest $previousProviderDigest
    $provider = Invoke-AccessProviderContinuousInspection `
        -PreviousInspection $previousProvider
    $identity = Get-AccessQualificationIdentity -GitSha ([string]$Candidate.git_sha) `
        -ProviderInspection $provider
    if ([string]$identity.access_qualification_key -cne $accessKey -or
        [string]$provider.provider_fingerprint -cne [string]$prior.provider_fingerprint) {
        throw "ACCESS_QUALIFICATION_KEY_CHANGED"
    }
    $issuedAt = ConvertTo-ReleaseTimestampUtc -Value $provider.audit_window_end
    $core = [ordered]@{
        schema_version = "access-qualification-renewal-receipt-v1"
        state = "ACCESS_QUALIFICATION_RENEWED"
        issued_at = $issuedAt.ToString("o")
        expires_at = $issuedAt.Add($accessMachineReceiptMaxAge).ToString("o")
        validation_key = [string]$Candidate.validation_key
        candidate_git_sha = [string]$Candidate.git_sha
        candidate_worker_version_id = [string]$Candidate.worker_version_id
        root_human_receipt_digest = $rootDigest
        previous_machine_receipt_digest = $priorDigest
        access_qualification_key = $accessKey
        provider_fingerprint = [string]$provider.provider_fingerprint
        protected_origin = [string]$prior.protected_origin
        provider_inspection_receipt_digest = [string]$provider.receipt_digest
        inspection_window_start = [string]$provider.audit_window_start
        inspection_window_end = [string]$provider.audit_window_end
        provider_changes_observed = [int]$provider.application_change_count +
            [int]$provider.policy_change_count
        access_failures_observed = [int]$provider.access_failure_count
    }
    $receipt = [pscustomobject]$core
    $receipt | Add-Member -NotePropertyName receipt_digest `
        -NotePropertyValue (Get-AccessQualificationRenewalReceiptDigest -Core $core)
    Write-AccessQualificationRenewalReceipt -Receipt $receipt
    $Candidate | Add-Member -Force -NotePropertyName access_qualification `
        -NotePropertyValue ([pscustomobject]@{
            state = "ACCESS_QUALIFICATION_RENEWED"
            access_key = $accessKey
            receipt_digest = [string]$receipt.receipt_digest
            root_human_receipt_digest = $rootDigest
            previous_machine_receipt_digest = $priorDigest
            provider_fingerprint = [string]$provider.provider_fingerprint
            verified_at = [string]$receipt.issued_at
            expires_at = [string]$receipt.expires_at
        })
    $Candidate.validation.auth_inspection = [pscustomobject]@{
        state = "ACCESS_QUALIFICATION_RENEWED"
        protected_host = ([Uri]$receipt.protected_origin).DnsSafeHost
        protected_origin = [string]$receipt.protected_origin
        access_key = $accessKey
        receipt_digest = [string]$receipt.receipt_digest
        root_human_receipt_digest = $rootDigest
        provider_inspection_receipt_digest = [string]$provider.receipt_digest
    }
    $Candidate.validation.reason = "ACCESS_QUALIFICATION_RENEWED"
    return Assert-AccessQualificationRenewalReceipt -Candidate $Candidate `
        -Digest ([string]$receipt.receipt_digest)
}

function Ensure-AccessQualificationMachineReceipt {
    param(
        [Parameter(Mandatory = $true)][object]$Candidate,
        [TimeSpan]$MinimumRemaining = [TimeSpan]::Zero
    )
    try {
        $current = Assert-AccessQualificationMachineReceipt -Candidate $Candidate
        $expiresAt = ConvertTo-ReleaseTimestampUtc -Value $current.expires_at
        if ($MinimumRemaining -le [TimeSpan]::Zero -or
            $expiresAt -gt (Get-AccessEvidenceUtcNow).Add($MinimumRemaining)) {
            return $current
        }
    }
    catch {
        if ($_.Exception.Message -notin @(
                "ACCESS_QUALIFICATION_REUSE_STALE",
                "ACCESS_QUALIFICATION_RENEWAL_STALE"
            )) { throw }
    }
    try {
        $receipt = Invoke-CandidateAccessQualificationRenewal -Candidate $Candidate
    } catch {
        if ($_.Exception.Message -match '^ACCESS_PROVIDER_(CONFIGURATION_CHANGED|AUDIT_INTERVAL_UNCOVERED|INSPECTION_INVALID|READ_)' -or
            $_.Exception.Message -eq "ACCESS_QUALIFICATION_KEY_CHANGED") {
            throw "ACCESS_HUMAN_REVIEW_REQUIRED:$($_.Exception.Message)"
        }
        throw
    }
    Write-ReleaseHistory -Event "CANDIDATE_ACCESS_QUALIFICATION_RENEWED" `
        -Release $Candidate -Detail @{
            validation_key = [string]$Candidate.validation_key
            access_key = [string]$receipt.access_qualification_key
            receipt_digest = [string]$receipt.receipt_digest
            root_human_receipt_digest = [string]$receipt.root_human_receipt_digest
            previous_machine_receipt_digest = [string]$receipt.previous_machine_receipt_digest
            provider_fingerprint = [string]$receipt.provider_fingerprint
            provider_inspection_receipt_digest = [string]$receipt.provider_inspection_receipt_digest
        }
    $state = Get-ReleaseControlState
    if ($state -and -not $state.transaction -and
        (Test-ReleaseIdentity $state.candidate $Candidate) -and
        [string]$state.candidate.validation_key -ceq [string]$Candidate.validation_key) {
        $state.candidate.access_qualification = $Candidate.access_qualification
        $state.candidate.validation.auth_inspection = $Candidate.validation.auth_inspection
        $state.candidate.validation_state = "EVIDENCE_PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        $null = Finalize-CandidateQualificationEvidence `
            -WhyRan "ACCESS_MACHINE_RENEWAL_COMPLETED"
    }
    return $receipt
}

function Invoke-CandidateAccessQualificationReuse {
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate -or
        [string]$state.candidate.validation_state -ne "REVIEW_REQUIRED" -or
        [string]$state.candidate.validation.reason -ne "ACCESS_BOUNDARY_REVIEW_REQUIRED") {
        throw "ACCESS_QUALIFICATION_REVIEW_STATE_REQUIRED"
    }
    $candidate = $state.candidate
    $priorReceipt = Get-LatestHistoricalAccessBoundaryReceipt
    if (-not (Test-AccessQualificationHistoryIsClean -PriorReceipt $priorReceipt)) {
        throw "ACCESS_QUALIFICATION_HISTORY_INVALID"
    }
    $machineAuthority = Get-LatestHistoricalAccessMachineAuthority
    if ($machineAuthority) {
        $receipt = Invoke-CandidateAccessQualificationRenewal -Candidate $candidate `
            -PriorCandidate $machineAuthority.candidate
        $candidate.validation.tested_at = [DateTimeOffset]::UtcNow.ToString("o")
        $candidate.validation_state = "EVIDENCE_PENDING"
        $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
        Write-ReleaseControlState -State $state
        Write-ReleaseHistory -Event "CANDIDATE_ACCESS_QUALIFICATION_RENEWED" `
            -Release $candidate -Detail @{
                validation_key = [string]$candidate.validation_key
                access_key = [string]$receipt.access_qualification_key
                receipt_digest = [string]$receipt.receipt_digest
                root_human_receipt_digest = [string]$receipt.root_human_receipt_digest
                previous_machine_receipt_digest = [string]$receipt.previous_machine_receipt_digest
                provider_fingerprint = [string]$receipt.provider_fingerprint
                provider_inspection_receipt_digest = [string]$receipt.provider_inspection_receipt_digest
            }
        $null = Finalize-CandidateQualificationEvidence `
            -WhyRan "ACCESS_MACHINE_RENEWAL_COMPLETED"
        return (Get-ReleaseControlState).candidate
    }
    $provider = Get-LatestAccessProviderInspectionReceipt
    $acceptedAt = ConvertTo-ReleaseTimestampUtc -Value $priorReceipt.accepted_at
    $windowStart = ConvertTo-ReleaseTimestampUtc -Value $provider.audit_window_start
    $windowEnd = ConvertTo-ReleaseTimestampUtc -Value $provider.audit_window_end
    $policyUpdated = ConvertTo-ReleaseTimestampUtc -Value $provider.policy_last_updated_at
    $observedAt = ConvertTo-ReleaseTimestampUtc -Value $provider.observed_at
    if ($windowStart -gt $acceptedAt -or $windowEnd -lt $acceptedAt -or
        $policyUpdated -gt $acceptedAt -or
        $observedAt -lt (Get-AccessEvidenceUtcNow).Subtract($accessMachineReceiptMaxAge)) {
        throw "ACCESS_PROVIDER_HISTORY_CANNOT_BE_MAPPED"
    }
    $priorIdentity = Get-AccessQualificationIdentity `
        -GitSha ([string]$priorReceipt.candidate.git_sha) -ProviderInspection $provider
    $currentIdentity = Get-AccessQualificationIdentity `
        -GitSha ([string]$candidate.git_sha) -ProviderInspection $provider
    $changed = @()
    foreach ($name in @($currentIdentity.core.repository_artifacts.Keys)) {
        if ([string]$currentIdentity.core.repository_artifacts[$name] -cne
            [string]$priorIdentity.core.repository_artifacts[$name]) { $changed += $name }
    }
    if ($changed.Count -gt 0 -or
        [string]$priorIdentity.access_qualification_key -cne
            [string]$currentIdentity.access_qualification_key) {
        throw "ACCESS_QUALIFICATION_KEY_CHANGED"
    }
    $receipt = New-AccessQualificationReuseReceipt -Candidate $candidate `
        -PriorReceipt $priorReceipt -ProviderInspection $provider `
        -PriorIdentity $priorIdentity -CurrentIdentity $currentIdentity `
        -ChangedAccessArtifacts $changed
    Write-AccessQualificationReuseReceipt -Receipt $receipt
    $candidate | Add-Member -Force -NotePropertyName access_qualification `
        -NotePropertyValue ([pscustomobject]@{
            state = "ACCESS_QUALIFICATION_REUSED"
            access_key = [string]$receipt.access_key
            receipt_digest = [string]$receipt.receipt_digest
            prior_access_receipt_digest = [string]$receipt.prior_access_receipt_digest
            provider_fingerprint = [string]$receipt.provider_fingerprint
            verified_at = [string]$receipt.verified_at
            expires_at = [string]$receipt.expires_at
        })
    $priorAuthInspection = $candidate.validation.auth_inspection
    $candidate.validation | Add-Member -Force -NotePropertyName auth_inspection `
        -NotePropertyValue ([pscustomobject]@{
            state = "ACCESS_QUALIFICATION_REUSED"
            protected_host = ([Uri]$receipt.protected_origin).DnsSafeHost
            protected_origin = [string]$receipt.protected_origin
            access_key = [string]$receipt.access_key
            receipt_digest = [string]$receipt.receipt_digest
            provider_inspection = $priorAuthInspection
        })
    $candidate.validation.reason = "ACCESS_QUALIFICATION_REUSED"
    $candidate.validation.tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    $candidate.validation_state = "EVIDENCE_PENDING"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_ACCESS_QUALIFICATION_REUSED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            access_key = [string]$receipt.access_key
            receipt_digest = [string]$receipt.receipt_digest
            prior_access_receipt_digest = [string]$receipt.prior_access_receipt_digest
            provider_fingerprint = [string]$receipt.provider_fingerprint
            changed_access_artifacts = @()
        }
    $null = Finalize-CandidateQualificationEvidence `
        -WhyRan "ACCESS_QUALIFICATION_REUSE_COMPLETED"
    return (Get-ReleaseControlState).candidate
}

function Approve-CandidateAccessBoundary {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$ChecklistConfirmation
    )
    if ($ChecklistConfirmation -cne $accessChecklistConfirmationValue) {
        throw "ACCESS_CHECKLIST_EXPLICIT_CONFIRMATION_REQUIRED"
    }
    $state = Get-ReleaseControlState
    if (-not $state -or -not $state.candidate -or -not $state.stable) {
        throw "ACCESS_CANDIDATE_UNAVAILABLE"
    }
    $candidate = $state.candidate
    if ([string]$candidate.validation_state -eq "PASSED" -and
        $candidate.access_acceptance) {
        $null = Assert-AccessBoundaryAcceptanceReceipt -Candidate $candidate -Stable $state.stable
        return $candidate
    }
    if ([string]$candidate.validation_state -ne "REVIEW_REQUIRED" -or
        [string]$candidate.validation.reason -ne "ACCESS_BOUNDARY_REVIEW_REQUIRED" -or
        [string]$candidate.validation.key -ne [string]$candidate.validation_key -or
        [string]$candidate.validation.repository -ne "PASSED" -or
        [string]$candidate.validation.windows -ne "PASSED" -or
        [string]$candidate.validation.cloudflare -ne "PASSED" -or
        -not [bool]$candidate.validation.data_parity.passed -or
        [string]$candidate.validation.auth_inspection.state -ne "AUTH_BOUNDARY_NOT_TESTABLE" -or
        -not (Test-CandidateAuthBoundaryChanged -RoutePlan $candidate.validation.route_plan)) {
        throw "ACCESS_EXACT_REVIEW_EVIDENCE_REQUIRED"
    }
    $approvalGate = Get-CandidateCompatibilityApprovalGate -Candidate $candidate
    if ([string]$approvalGate.state -ne "PASSED") {
        throw "ACCESS_APPROVAL_REJECTED:$([string]$approvalGate.reason)"
    }
    $checklist = [ordered]@{
        owner_login_succeeds = $true
        owner_resource_accessible = $true
        unauthorized_access_denied = $true
        logout_succeeds = $true
        access_denied_after_logout = $true
        reauthentication_succeeds = $true
    }
    $receipt = New-AccessBoundaryAcceptanceReceipt -Candidate $candidate `
        -Stable $state.stable -Checklist $checklist
    Write-AccessBoundaryAcceptanceReceipt -Receipt $receipt
    $verified = Assert-AccessBoundaryAcceptanceReceipt -Candidate $candidate `
        -Stable $state.stable -SkipCandidateStateBinding
    $candidate | Add-Member -Force -NotePropertyName access_acceptance `
        -NotePropertyValue ([pscustomobject]@{
            validation_key = [string]$candidate.validation_key
            receipt_digest = [string]$verified.receipt_digest
            protected_host = [string]$verified.access_boundary.host
            accepted_at = [string]$verified.accepted_at
            expires_at = [string]$verified.expires_at
        })
    $priorAuthInspection = $candidate.validation.auth_inspection
    $candidate.validation | Add-Member -Force -NotePropertyName auth_inspection `
        -NotePropertyValue ([pscustomobject]@{
            state = "HUMAN_ACCESS_BOUNDARY_ACCEPTED"
            protected_host = [string]$verified.access_boundary.host
            protected_origin = [string]$verified.access_boundary.origin
            receipt_digest = [string]$verified.receipt_digest
            accepted_at = [string]$verified.accepted_at
            provider_inspection = $priorAuthInspection
        })
    $candidate.validation | Add-Member -Force -NotePropertyName access_receipt_digest `
        -NotePropertyValue ([string]$verified.receipt_digest)
    $candidate.validation.reason = "ACCESS_BOUNDARY_ACCEPTED"
    $candidate.validation.tested_at = [DateTimeOffset]::UtcNow.ToString("o")
    $candidate.validation_state = "EVIDENCE_PENDING"
    $state.updated_at = [DateTimeOffset]::UtcNow.ToString("o")
    Write-ReleaseControlState -State $state
    Write-ReleaseHistory -Event "CANDIDATE_ACCESS_BOUNDARY_ACCEPTED" `
        -Release $candidate -Detail @{
            validation_key = [string]$candidate.validation_key
            receipt_digest = [string]$verified.receipt_digest
            protected_host = [string]$verified.access_boundary.host
            checklist = $verified.checklist
        }
    $null = Finalize-CandidateQualificationEvidence `
        -WhyRan "HUMAN_ACCESS_ROOT_COMPLETED"
    return (Get-ReleaseControlState).candidate
}
