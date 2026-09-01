$releaseEvidenceReceiptSchema = "release-evidence-node-receipt-v1"
$releaseEvidenceDigestAlgorithm = "xauusd.release-evidence-node.sha256.v1"
$releaseEvidenceExecutionModes = @("FRESH", "REUSED", "RENEWED")
$releaseEvidenceTerminalStates = @("PASSED", "FAILED", "INVALIDATED")
$releaseEvidenceReceiptMaximumBytes = 65536

function ConvertTo-ReleaseEvidenceJson {
    param([Parameter(Mandatory = $true)][object]$Value)
    return ($Value | ConvertTo-Json -Depth 12 -Compress)
}

function ConvertFrom-ReleaseEvidenceJson {
    param(
        [Parameter(Mandatory = $true, ValueFromPipeline = $true)]
        [string]$Json
    )
    process {
        if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey("DateKind")) {
            return $Json | ConvertFrom-Json -DateKind String -ErrorAction Stop
        }
        return $Json | ConvertFrom-Json -ErrorAction Stop
    }
}

function Get-ReleaseEvidenceSha256 {
    param([Parameter(Mandatory = $true)][string]$Value)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function ConvertTo-ReleaseEvidenceNativePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($env:OS -ne "Windows_NT" -or $fullPath.StartsWith("\\?\")) {
        return $fullPath
    }
    if ($fullPath.StartsWith("\\")) {
        return "\\?\UNC\$($fullPath.Substring(2))"
    }
    return "\\?\$fullPath"
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

function Get-ReleaseEvidenceContract {
    param([Parameter(Mandatory = $true)][string]$ContractPath)
    if (-not (Test-Path -LiteralPath $ContractPath)) {
        throw "RELEASE_EVIDENCE_CONTRACT_MISSING"
    }
    $contract = Get-Content -LiteralPath $ContractPath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseEvidenceJson
    if ([int]$contract.schema_version -ne 1 -or
        [string]$contract.receipt_schema -ne $releaseEvidenceReceiptSchema) {
        throw "RELEASE_EVIDENCE_CONTRACT_INVALID"
    }
    $ids = @($contract.nodes | ForEach-Object { [string]$_.id })
    if ($ids.Count -ne 15 -or @($ids | Select-Object -Unique).Count -ne 15) {
        throw "RELEASE_EVIDENCE_NODE_SET_INVALID"
    }
    foreach ($node in @($contract.nodes)) {
        if ([string]$node.id -notmatch '^[a-z][a-z0-9_]{0,63}$' -or
            [string]::IsNullOrWhiteSpace([string]$node.owner) -or
            @($node.behavior_inputs).Count -eq 0 -or
            @($node.dependencies | Where-Object { [string]$_ -notin $ids }).Count -gt 0 -or
            [string]$node.id -in @($node.dependencies)) {
            throw "RELEASE_EVIDENCE_DEPENDENCY_INVALID"
        }
    }
    $resolved = @()
    while ($resolved.Count -lt $ids.Count) {
        $priorCount = $resolved.Count
        foreach ($node in @($contract.nodes)) {
            $nodeId = [string]$node.id
            if ($nodeId -in $resolved) { continue }
            if (@($node.dependencies | Where-Object { [string]$_ -notin $resolved }).Count -eq 0) {
                $resolved += $nodeId
            }
        }
        if ($resolved.Count -eq $priorCount) {
            throw "RELEASE_EVIDENCE_DEPENDENCY_CYCLE"
        }
    }
    return $contract
}

function New-ReleaseEvidenceReceiptPreimage {
    param(
        [Parameter(Mandatory = $true)][object]$Contract,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][string]$BehaviorKey,
        [Parameter(Mandatory = $true)][string]$State,
        [Parameter(Mandatory = $true)][object]$SourceIdentity,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$CompletedAt,
        [Parameter(Mandatory = $true)][string]$ExecutionMode,
        [Parameter(Mandatory = $true)][string]$WhyRan,
        [object[]]$Dependencies = @(),
        [string]$ReuseReason = "",
        [string]$PriorReceipt = "",
        [string]$InvalidationReason = ""
    )
    $definition = @($Contract.nodes | Where-Object { [string]$_.id -eq $Node })
    if ($definition.Count -ne 1) { throw "RELEASE_EVIDENCE_NODE_UNKNOWN" }
    if ($State -notin $releaseEvidenceTerminalStates) {
        throw "RELEASE_EVIDENCE_STATE_INVALID"
    }
    if ($ExecutionMode -notin $releaseEvidenceExecutionModes) {
        throw "RELEASE_EVIDENCE_EXECUTION_MODE_INVALID"
    }
    if ([string]::IsNullOrWhiteSpace($BehaviorKey) -or
        $BehaviorKey.Length -gt 512 -or
        [string]::IsNullOrWhiteSpace($WhyRan) -or $WhyRan.Length -gt 512) {
        throw "RELEASE_EVIDENCE_REQUIRED_FIELD_MISSING"
    }
    $started = [DateTimeOffset]::Parse($StartedAt).ToUniversalTime()
    $completed = [DateTimeOffset]::Parse($CompletedAt).ToUniversalTime()
    if ($completed -lt $started) { throw "RELEASE_EVIDENCE_TIME_INVALID" }
    $allowedDependencies = @($definition[0].dependencies | ForEach-Object { [string]$_ })
    $normalizedDependencies = @()
    foreach ($dependency in @($Dependencies)) {
        $dependencyNode = [string]$dependency.node
        $dependencyDigest = [string]$dependency.receipt_digest
        if ($dependencyNode -notin $allowedDependencies -or
            $dependencyDigest -notmatch '^[0-9a-f]{64}$') {
            throw "RELEASE_EVIDENCE_DEPENDENCY_INVALID"
        }
        $normalizedDependencies += [ordered]@{
            node = $dependencyNode
            receipt_digest = $dependencyDigest
        }
    }
    if ($State -eq "PASSED") {
        $actualDependencyNodes = @($normalizedDependencies | ForEach-Object { [string]$_.node })
        if ($actualDependencyNodes.Count -ne $allowedDependencies.Count -or
            @($allowedDependencies | Where-Object { $_ -notin $actualDependencyNodes }).Count -gt 0) {
            throw "RELEASE_EVIDENCE_DEPENDENCY_INCOMPLETE"
        }
    }
    $canonicalDependencies = @()
    foreach ($dependencyNode in $allowedDependencies) {
        $canonicalDependencies += @($normalizedDependencies | Where-Object {
            [string]$_.node -eq $dependencyNode
        })
    }
    $normalizedDependencies = $canonicalDependencies
    if ($ExecutionMode -in @("REUSED", "RENEWED") -and
        ($PriorReceipt -notmatch '^[0-9a-f]{64}$' -or
            [string]::IsNullOrWhiteSpace($ReuseReason))) {
        throw "RELEASE_EVIDENCE_REUSE_LINK_INVALID"
    }
    if ($ExecutionMode -eq "FRESH" -and -not [string]::IsNullOrWhiteSpace($PriorReceipt)) {
        throw "RELEASE_EVIDENCE_REUSE_LINK_INVALID"
    }
    if ($State -eq "INVALIDATED" -and [string]::IsNullOrWhiteSpace($InvalidationReason)) {
        throw "RELEASE_EVIDENCE_INVALIDATION_REASON_MISSING"
    }
    return [ordered]@{
        schema_version = $releaseEvidenceReceiptSchema
        contract_version = [int]$Contract.schema_version
        digest_algorithm = $releaseEvidenceDigestAlgorithm
        node = $Node
        owner = [string]$definition[0].owner
        behavior_key = $BehaviorKey
        dependencies = $normalizedDependencies
        state = $State
        source_identity = $SourceIdentity
        created_at = $completed.ToString("o")
        verified_at = $completed.ToString("o")
        started_at = $started.ToString("o")
        completed_at = $completed.ToString("o")
        elapsed_ms = [long][Math]::Round(($completed - $started).TotalMilliseconds)
        execution_mode = $ExecutionMode
        why_ran = $WhyRan
        reuse_reason = $ReuseReason
        prior_receipt = $PriorReceipt
        invalidation_reason = $InvalidationReason
    }
}

function Write-ReleaseEvidenceNodeReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ContractPath,
        [Parameter(Mandatory = $true)][string]$ValidationKey,
        [Parameter(Mandatory = $true)][string]$Node,
        [Parameter(Mandatory = $true)][string]$BehaviorKey,
        [Parameter(Mandatory = $true)][string]$State,
        [Parameter(Mandatory = $true)][object]$SourceIdentity,
        [Parameter(Mandatory = $true)][string]$StartedAt,
        [Parameter(Mandatory = $true)][string]$CompletedAt,
        [Parameter(Mandatory = $true)][string]$ExecutionMode,
        [Parameter(Mandatory = $true)][string]$WhyRan,
        [object[]]$Dependencies = @(),
        [string]$ReuseReason = "",
        [string]$PriorReceipt = "",
        [string]$InvalidationReason = ""
    )
    if ([string]::IsNullOrWhiteSpace($ValidationKey)) {
        throw "RELEASE_EVIDENCE_VALIDATION_KEY_MISSING"
    }
    $validationKeyDigest = Get-ReleaseEvidenceSha256 -Value $ValidationKey
    $contract = Get-ReleaseEvidenceContract -ContractPath $ContractPath
    $preimage = New-ReleaseEvidenceReceiptPreimage -Contract $contract -Node $Node `
        -BehaviorKey $BehaviorKey -State $State -SourceIdentity $SourceIdentity `
        -StartedAt $StartedAt -CompletedAt $CompletedAt -ExecutionMode $ExecutionMode `
        -WhyRan $WhyRan -Dependencies $Dependencies -ReuseReason $ReuseReason `
        -PriorReceipt $PriorReceipt -InvalidationReason $InvalidationReason
    foreach ($dependency in @($preimage.dependencies)) {
        $dependencyPath = Join-Path $Root `
            "$validationKeyDigest\$([string]$dependency.node)\$([string]$dependency.receipt_digest).json"
        $nativeDependencyPath = ConvertTo-ReleaseEvidenceNativePath -Path $dependencyPath
        if (-not [System.IO.File]::Exists($nativeDependencyPath)) {
            throw "RELEASE_EVIDENCE_DEPENDENCY_RECEIPT_MISSING"
        }
        $dependencyReceipt = [System.IO.File]::ReadAllText(
            $nativeDependencyPath, [System.Text.Encoding]::UTF8) |
                ConvertFrom-ReleaseEvidenceJson
        if (-not (Test-ReleaseEvidenceNodeReceipt -Receipt $dependencyReceipt) -or
            [string]$dependencyReceipt.node -cne [string]$dependency.node -or
            [string]$dependencyReceipt.receipt_digest -cne [string]$dependency.receipt_digest) {
            throw "RELEASE_EVIDENCE_DEPENDENCY_RECEIPT_INVALID"
        }
    }
    $preimageJson = ConvertTo-ReleaseEvidenceJson $preimage
    if ([System.Text.Encoding]::UTF8.GetByteCount($preimageJson) -gt
        $releaseEvidenceReceiptMaximumBytes) {
        throw "RELEASE_EVIDENCE_RECEIPT_TOO_LARGE"
    }
    $digest = Get-ReleaseEvidenceSha256 -Value $preimageJson
    $receipt = [ordered]@{}
    foreach ($entry in $preimage.GetEnumerator()) { $receipt[$entry.Key] = $entry.Value }
    $receipt.receipt_digest = $digest
    $receiptPath = Join-Path $Root "$validationKeyDigest\$Node\$digest.json"
    $nativeReceiptPath = ConvertTo-ReleaseEvidenceNativePath -Path $receiptPath
    $json = ConvertTo-ReleaseEvidenceJson $receipt
    if ([System.IO.File]::Exists($nativeReceiptPath)) {
        $existing = [System.IO.File]::ReadAllText(
            $nativeReceiptPath, [System.Text.Encoding]::UTF8)
        if ($existing -cne $json) { throw "RELEASE_EVIDENCE_IMMUTABLE_COLLISION" }
    } else {
        Write-ReleaseEvidenceUtf8Atomic -Path $receiptPath -Content $json -CreateNew
    }
    $index = [ordered]@{
        schema_version = "release-evidence-current-index-v1"
        validation_key_digest = $validationKeyDigest
        node = $Node
        state = $State
        behavior_key = $BehaviorKey
        receipt_digest = $digest
        receipt_path = "$Node/$digest.json"
        updated_at = ([DateTimeOffset]::Parse($CompletedAt).ToUniversalTime().ToString("o"))
    }
    $indexPath = Join-Path $Root "$validationKeyDigest\current\$Node.json"
    Write-ReleaseEvidenceUtf8Atomic -Path $indexPath `
        -Content (ConvertTo-ReleaseEvidenceJson $index)
    return [pscustomobject]$receipt
}

function Test-ReleaseEvidenceNodeReceipt {
    param([Parameter(Mandatory = $true)][object]$Receipt)
    try {
        $preimage = [ordered]@{}
        foreach ($field in @(
            "schema_version", "contract_version", "digest_algorithm", "node", "owner",
            "behavior_key", "dependencies", "state", "source_identity", "created_at",
            "verified_at", "started_at", "completed_at", "elapsed_ms", "execution_mode",
            "why_ran", "reuse_reason", "prior_receipt", "invalidation_reason"
        )) {
            if (-not $Receipt.PSObject.Properties[$field]) { return $false }
            $preimage[$field] = $Receipt.$field
        }
        return [string]$Receipt.receipt_digest -ceq
            (Get-ReleaseEvidenceSha256 -Value (ConvertTo-ReleaseEvidenceJson $preimage))
    } catch { return $false }
}

function Get-ReleaseEvidenceWaterfall {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$ValidationKey
    )
    $validationKeyDigest = Get-ReleaseEvidenceSha256 -Value $ValidationKey
    $currentRoot = Join-Path $Root "$validationKeyDigest\current"
    $nativeCurrentRoot = ConvertTo-ReleaseEvidenceNativePath -Path $currentRoot
    $entries = @()
    if ([System.IO.Directory]::Exists($nativeCurrentRoot)) {
        foreach ($indexPath in @([System.IO.Directory]::EnumerateFiles(
                $nativeCurrentRoot, "*.json", [System.IO.SearchOption]::TopDirectoryOnly))) {
            $index = [System.IO.File]::ReadAllText(
                $indexPath, [System.Text.Encoding]::UTF8) |
                    ConvertFrom-ReleaseEvidenceJson
            $indexNode = [System.IO.Path]::GetFileNameWithoutExtension($indexPath)
            $indexDigest = [string]$index.receipt_digest
            $expectedReceiptPath = "$indexNode/$indexDigest.json"
            if ([string]$index.schema_version -ne "release-evidence-current-index-v1" -or
                [string]$index.validation_key_digest -cne $validationKeyDigest -or
                [string]$index.node -cne $indexNode -or
                $indexDigest -notmatch '^[0-9a-f]{64}$' -or
                [string]$index.receipt_path -cne $expectedReceiptPath) {
                throw "RELEASE_EVIDENCE_INDEX_INVALID"
            }
            $receiptPath = Join-Path (Split-Path -Parent $currentRoot) `
                ([string]$index.receipt_path -replace '/', '\')
            $nativeReceiptPath = ConvertTo-ReleaseEvidenceNativePath -Path $receiptPath
            if (-not [System.IO.File]::Exists($nativeReceiptPath)) {
                throw "RELEASE_EVIDENCE_RECEIPT_MISSING"
            }
            $receipt = [System.IO.File]::ReadAllText(
                $nativeReceiptPath, [System.Text.Encoding]::UTF8) |
                    ConvertFrom-ReleaseEvidenceJson
            if (-not (Test-ReleaseEvidenceNodeReceipt -Receipt $receipt)) {
                throw "RELEASE_EVIDENCE_RECEIPT_TAMPERED"
            }
            if ([string]$receipt.node -cne $indexNode -or
                [string]$receipt.receipt_digest -cne $indexDigest -or
                [string]$receipt.state -cne [string]$index.state -or
                [string]$receipt.behavior_key -cne [string]$index.behavior_key) {
                throw "RELEASE_EVIDENCE_INDEX_RECEIPT_MISMATCH"
            }
            $entries += [pscustomobject][ordered]@{
                node = [string]$receipt.node
                state = [string]$receipt.state
                behavior_key = [string]$receipt.behavior_key
                receipt_digest = [string]$receipt.receipt_digest
                started_at = [string]$receipt.started_at
                completed_at = [string]$receipt.completed_at
                elapsed_ms = [long]$receipt.elapsed_ms
                execution_mode = [string]$receipt.execution_mode
                why_ran = [string]$receipt.why_ran
                reuse_reason = [string]$receipt.reuse_reason
                prior_receipt = [string]$receipt.prior_receipt
                invalidation_reason = [string]$receipt.invalidation_reason
            }
        }
    }
    $orderedEntries = @($entries | Sort-Object started_at, node)
    $waterfallStarted = $null
    $waterfallCompleted = $null
    foreach ($entry in $orderedEntries) {
        $entryStarted = [DateTimeOffset]::Parse([string]$entry.started_at)
        $entryCompleted = [DateTimeOffset]::Parse([string]$entry.completed_at)
        if ($null -eq $waterfallStarted -or $entryStarted -lt $waterfallStarted) {
            $waterfallStarted = $entryStarted
        }
        if ($null -eq $waterfallCompleted -or $entryCompleted -gt $waterfallCompleted) {
            $waterfallCompleted = $entryCompleted
        }
    }
    return [pscustomobject]@{
        schema_version = "release-evidence-waterfall-v1"
        validation_key_digest = $validationKeyDigest
        node_count = $orderedEntries.Count
        started_at = if ($null -ne $waterfallStarted) { $waterfallStarted.ToUniversalTime().ToString("o") } else { $null }
        completed_at = if ($null -ne $waterfallCompleted) { $waterfallCompleted.ToUniversalTime().ToString("o") } else { $null }
        elapsed_ms = if ($null -ne $waterfallStarted -and $null -ne $waterfallCompleted) {
            [long][Math]::Round(($waterfallCompleted - $waterfallStarted).TotalMilliseconds)
        } else { 0 }
        nodes = $orderedEntries
    }
}
