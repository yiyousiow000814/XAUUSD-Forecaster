# Canonical Control Center owner. Dot-sourced by xauusd_control_center.ps1.
# Do not execute this file directly.
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

function Get-Sha256TextHex {
    param([Parameter(Mandatory = $true)][string]$Value)
    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        return ([System.BitConverter]::ToString(
            $algorithm.ComputeHash($bytes)
        ) -replace "-", "").ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function ConvertTo-RuntimeControlRelativePath {
    param([Parameter(Mandatory = $true)][string]$Name)
    $normalized = $Name.Replace("\", "/").Normalize(
        [System.Text.NormalizationForm]::FormC
    )
    if (-not $normalized -or [IO.Path]::GetFileName($normalized) -ne $normalized -or
        $normalized -match '[/\r\n\t]' -or
        [IO.Path]::IsPathRooted($normalized)) {
        throw "CONTROL_BUNDLE_RELATIVE_PATH_INVALID:$Name"
    }
    return $normalized
}

function Get-RuntimeControlOrdinalNames {
    param([Parameter(Mandatory = $true)][object[]]$Names)
    [string[]]$names = @($Names | ForEach-Object {
        ConvertTo-RuntimeControlRelativePath -Name ([string]$_)
    })
    [Array]::Sort($names, [StringComparer]::Ordinal)
    return $names
}

function Get-RuntimeControlBundleDigest {
    param(
        [Parameter(Mandatory = $true)][int]$SchemaVersion,
        [Parameter(Mandatory = $true)][string]$SourceRevision,
        [Parameter(Mandatory = $true)][hashtable]$Hashes
    )
    if ($SchemaVersion -ne $runtimeControlBundleSchemaVersion -or
        $SourceRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_DIGEST_IDENTITY_INVALID"
    }
    $names = @(Get-RuntimeControlOrdinalNames -Names @($Hashes.Keys))
    $lines = @(
        $runtimeControlBundleDigestAlgorithm
        "schema_version=$SchemaVersion"
        "source_revision=$SourceRevision"
        "file_count=$($names.Count)"
    )
    foreach ($name in $names) {
        $hash = ([string]$Hashes[$name]).ToLowerInvariant()
        if ($hash -notmatch '^[0-9a-f]{64}$') {
            throw "CONTROL_BUNDLE_FILE_HASH_INVALID:$name"
        }
        $lines += "file=$name`thash=$hash"
    }
    return Get-Sha256TextHex -Value ($lines -join "`n")
}

function Get-RuntimeControlLegacyV2Digest {
    param([Parameter(Mandatory = $true)][hashtable]$Hashes)
    $lines = @(Get-RuntimeControlOrdinalNames -Names @($Hashes.Keys) |
        ForEach-Object { "{0}={1}" -f $_, ([string]$Hashes[$_]).ToLowerInvariant() })
    return Get-Sha256TextHex -Value ($lines -join "`n")
}

function Get-RuntimeControlSourceManifestAtRoot {
    param([Parameter(Mandatory = $true)][string]$Root)
    $path = Join-Path $Root $runtimeControlSourceManifestName
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $manifest = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $files = @($manifest.files | ForEach-Object { [string]$_ })
        $entrypoints = @($manifest.entrypoints | ForEach-Object { [string]$_ })
        if ([int]$manifest.schema_version -ne 1 -or $files.Count -eq 0 -or
            @($files | Select-Object -Unique).Count -ne $files.Count -or
            $runtimeControlSourceManifestName -notin $files -or
            @($entrypoints | Where-Object { $_ -notin $files }).Count -ne 0 -or
            @($files | Where-Object {
                try {
                    (ConvertTo-RuntimeControlRelativePath -Name $_) -ne $_
                } catch { $true }
            }).Count -ne 0) {
            return $null
        }
        return [pscustomobject]@{
            path = $path
            files = $files
            entrypoints = $entrypoints
            digest = Get-Sha256Hex -LiteralPath $path
        }
    } catch { return $null }
}

function Get-RuntimeControlPowerShellDependencies {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $LiteralPath, [ref]$tokens, [ref]$errors
    )
    if (@($errors).Count -ne 0) {
        throw "CONTROL_BUNDLE_POWERSHELL_PARSE_FAILED:$([IO.Path]::GetFileName($LiteralPath))"
    }
    $commands = @($ast.FindAll({
        param($node)
        if ($node -isnot [System.Management.Automation.Language.CommandAst]) {
            return $false
        }
        return ($node.InvocationOperator -eq
                [System.Management.Automation.Language.TokenKind]::Dot -or
            [string]::Equals($node.GetCommandName(), "Import-Module",
                [StringComparison]::OrdinalIgnoreCase))
    }, $true))
    $dependencies = @()
    foreach ($command in $commands) {
        $candidates = @($command.FindAll({
            param($node)
            return ($node -is
                    [System.Management.Automation.Language.StringConstantExpressionAst] -and
                [string]$node.Value -match '(?i)\.(ps1|psm1)$')
        }, $true) | ForEach-Object { [IO.Path]::GetFileName([string]$_.Value) } |
            Select-Object -Unique)
        if ($candidates.Count -ne 1) {
            throw "CONTROL_BUNDLE_DYNAMIC_POWERSHELL_DEPENDENCY:$([IO.Path]::GetFileName($LiteralPath))"
        }
        $dependencies += $candidates[0]
    }
    return @($dependencies | Select-Object -Unique)
}

function Assert-RuntimeControlDependencyClosure {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][object]$SourceManifest
    )
    $declared = @($SourceManifest.files)
    $graph = [ordered]@{}
    foreach ($name in $declared) {
        $path = Join-Path $Root $name
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "CONTROL_BUNDLE_DECLARED_FILE_MISSING:$name"
        }
        if ($name -notmatch '(?i)\.(ps1|psm1)$') { continue }
        $dependencies = @(Get-RuntimeControlPowerShellDependencies `
            -LiteralPath $path)
        foreach ($dependency in $dependencies) {
            if ($dependency -notin $declared) {
                throw "CONTROL_BUNDLE_UNDECLARED_DEPENDENCY:$name`:$dependency"
            }
        }
        $graph[$name] = $dependencies
    }
    return [pscustomobject]$graph
}

function Get-RuntimeControlBundleIdentityAtRoot {
    param(
        [Parameter(Mandatory = $true)][string]$ControlRoot,
        [switch]$RequireDependencyClosure
    )
    $path = Join-Path $ControlRoot $runtimeControlManifestName
    if (-not (Test-Path -LiteralPath $path)) { return $null }
    try {
        $identity = Get-Content -LiteralPath $path -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        if (-not [bool]$identity.exact_revision -or
            [string]$identity.source_revision -notmatch '^[0-9a-f]{40}$') {
            return $null
        }
        $manifestFileNames = @($identity.files.PSObject.Properties.Name |
            ForEach-Object { [string]$_ })
        $sourceManifest = Get-RuntimeControlSourceManifestAtRoot `
            -Root $ControlRoot
        $schemaVersion = [int]$identity.schema_version
        $canonicalBundle = ($schemaVersion -eq $runtimeControlBundleSchemaVersion -and
            [string]$identity.bundle_digest_algorithm -eq
                $runtimeControlBundleDigestAlgorithm)
        $legacyV2Bundle = ($schemaVersion -eq 2 -and
            -not [string]$identity.bundle_digest_algorithm)
        if ($schemaVersion -notin @(1, 2, $runtimeControlBundleSchemaVersion) -or
            ($schemaVersion -ge 2 -and
                (-not [bool]$identity.dependency_closed -or -not $sourceManifest)) -or
            ($schemaVersion -eq $runtimeControlBundleSchemaVersion -and
                -not $canonicalBundle)) {
            return $null
        }
        $dependencyClosed = (($canonicalBundle -or $legacyV2Bundle) -and
            [bool]$identity.dependency_closed -and $sourceManifest)
        $expectedNames = if ($dependencyClosed) {
            @($sourceManifest.files)
        } else {
            # Schema 1 remains verifiable only as an independently recoverable
            # previous bundle. It is never accepted as a new closed stage.
            $manifestFileNames
        }
        if ($RequireDependencyClosure -and -not $dependencyClosed) { return $null }
        $observedFileSet = (@(Get-RuntimeControlOrdinalNames `
            -Names $manifestFileNames) -join "`n")
        $expectedFileSet = (@(Get-RuntimeControlOrdinalNames `
            -Names $expectedNames) -join "`n")
        if ($observedFileSet -ne $expectedFileSet) { return $null }
        $hashes = @{}
        foreach ($name in $expectedNames) {
            $file = Join-Path $ControlRoot $name
            $expected = [string]$identity.files.$name
            if (-not (Test-Path -LiteralPath $file) -or
                $expected -notmatch '^[0-9a-f]{64}$') { return $null }
            $actual = Get-Sha256Hex -LiteralPath $file
            if ($actual -ne $expected) { return $null }
            $hashes[$name] = $actual
        }
        if ($dependencyClosed) {
            $expectedBundleDigest = if ($canonicalBundle) {
                Get-RuntimeControlBundleDigest -SchemaVersion $schemaVersion `
                    -SourceRevision ([string]$identity.source_revision) `
                    -Hashes $hashes
            } else {
                # Schema 2 used the same path=hash byte format, but delegated
                # ordering to culture-sensitive Sort-Object. The versioned
                # adapter fixes that intended legacy order to ordinal so the
                # exact existing commitment is reproducible on PS 5 and 7.
                Get-RuntimeControlLegacyV2Digest -Hashes $hashes
            }
            if ([string]$identity.source_manifest_sha256 -ne
                    [string]$sourceManifest.digest -or
                [string]$identity.bundle_digest -ne
                    $expectedBundleDigest) {
                return $null
            }
            $null = Assert-RuntimeControlDependencyClosure `
                -Root $ControlRoot -SourceManifest $sourceManifest
        }
        $identity | Add-Member -NotePropertyName dependency_closed_verified `
            -NotePropertyValue $dependencyClosed -Force
        $identity | Add-Member -NotePropertyName canonical_bundle_digest_verified `
            -NotePropertyValue $canonicalBundle -Force
        $identity | Add-Member -NotePropertyName legacy_v2_digest_verified `
            -NotePropertyValue $legacyV2Bundle -Force
        return $identity
    } catch { return $null }
}

function Assert-ControlPlaneSourceRevision {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$SourceRevision,
        [switch]$RequireImmutableSource
    )
    if ($SourceRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
    $revisionRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
        -Arguments @("-C", $SourceRoot, "rev-parse", "HEAD")
    $observedRevision = ([string]$revisionRead.stdout).Trim()
    if ($revisionRead.exit_code -ne 0 -or $observedRevision -ne $SourceRevision) {
        throw "CONTROL_BUNDLE_SOURCE_REVISION_MISMATCH"
    }
    if ($RequireImmutableSource) {
        $statusRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $SourceRoot, "status", "--porcelain")
        if ($statusRead.exit_code -ne 0 -or @($statusRead.stdout_lines).Count -ne 0) {
            throw "CONTROL_BUNDLE_IMMUTABLE_SOURCE_REQUIRED"
        }
        $symbolicRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
            -Arguments @("-C", $SourceRoot, "symbolic-ref", "-q", "HEAD")
        if ($symbolicRead.exit_code -eq 0) {
            throw "CONTROL_BUNDLE_DETACHED_SOURCE_REQUIRED"
        }
    }
}

function New-VerifiedRuntimeControlBundleStage {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$SourceRevision,
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [switch]$RequireImmutableSource
    )
    Assert-ControlPlaneSourceRevision -SourceRoot $SourceRoot -SourceRevision $SourceRevision `
        -RequireImmutableSource:$RequireImmutableSource
    $sourceManifest = Get-RuntimeControlSourceManifestAtRoot `
        -Root (Join-Path $SourceRoot "scripts")
    if (-not $sourceManifest) { throw "CONTROL_BUNDLE_SOURCE_MANIFEST_INVALID" }
    $null = Assert-RuntimeControlDependencyClosure `
        -Root (Join-Path $SourceRoot "scripts") -SourceManifest $sourceManifest
    New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null
    foreach ($name in $sourceManifest.files) {
        $source = Join-Path $SourceRoot ("scripts\{0}" -f $name)
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Missing runtime control file: $source"
        }
        Copy-Item -LiteralPath $source -Destination (Join-Path $StageRoot $name) -Force
    }
    $hashes = @{}
    foreach ($name in $sourceManifest.files) {
        $hashes[$name] = Get-Sha256Hex -LiteralPath (Join-Path $StageRoot $name)
    }
    [pscustomobject]@{
        schema_version = $runtimeControlBundleSchemaVersion
        source_revision = $SourceRevision
        exact_revision = $true
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
        dependency_closed = $true
        bundle_digest_algorithm = $runtimeControlBundleDigestAlgorithm
        source_manifest_sha256 = [string]$sourceManifest.digest
        bundle_digest = Get-RuntimeControlBundleDigest `
            -SchemaVersion $runtimeControlBundleSchemaVersion `
            -SourceRevision $SourceRevision -Hashes $hashes
        files = $hashes
    } | ConvertTo-Json -Depth 5 | Set-Content `
        -LiteralPath (Join-Path $StageRoot $runtimeControlManifestName) -Encoding UTF8
    $identity = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $StageRoot `
        -RequireDependencyClosure
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
    $stagedIdentity = Get-RuntimeControlBundleIdentityAtRoot `
        -ControlRoot $StageRoot -RequireDependencyClosure
    if (-not $stagedIdentity) { throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED" }
    New-Item -ItemType Directory -Path $ControlRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $payloadNames = @($stagedIdentity.files.PSObject.Properties.Name) +
        @($runtimeControlManifestName)
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
        $installed = Get-RuntimeControlBundleIdentityAtRoot `
            -ControlRoot $ControlRoot -RequireDependencyClosure
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
    $payloadNames = @($backupIdentity.files.PSObject.Properties.Name) +
        @($runtimeControlManifestName)
    foreach ($name in $payloadNames) {
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

function Invoke-RuntimeControlBundleStartupPreflight {
    param(
        [Parameter(Mandatory = $true)][string]$StageRoot,
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][string]$RepositoryRootForPreflight
    )
    $resultPath = Join-Path $StageRoot `
        (".bundle-preflight-{0}.json" -f ([guid]::NewGuid().ToString("N")))
    try {
        $controlScript = Join-Path $StageRoot "xauusd_control_center.ps1"
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $controlScript -Action ControlBundlePreflight `
            -RuntimeRoot $RepositoryRootForPreflight `
            -RepositoryRoot $RepositoryRootForPreflight `
            -OperationResultPath $resultPath
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $resultPath)) {
            throw "CONTROL_BUNDLE_STARTUP_PREFLIGHT_FAILED"
        }
        $receipt = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $observedAt = ConvertTo-ReleaseTimestampUtc -Value $receipt.observed_at
        if ([string]$receipt.supervision_mode -ne "QUIESCED" -or
            [string]$receipt.control_bundle_revision -ne $ExpectedRevision -or
            -not [bool]$receipt.control_bundle_hash_verified -or
            -not [bool]$receipt.dependency_closed -or
            $observedAt -eq [DateTimeOffset]::MinValue -or
            ([DateTimeOffset]::UtcNow - $observedAt).TotalSeconds -gt 30) {
            throw "CONTROL_BUNDLE_STARTUP_PREFLIGHT_INVALID"
        }
        return $receipt
    } finally {
        Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
    }
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
            $revisionRead = Invoke-Utf8NativeProcess -FilePath "git.exe" `
                -Arguments @("-C", $SourceRoot, "rev-parse", "HEAD")
            if ($revisionRead.exit_code -ne 0) {
                throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
            }
            $SourceRevision = ([string]$revisionRead.stdout).Trim()
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

function Get-ControlPlaneInstallState {
    if (-not (Test-Path -LiteralPath $controlPlaneInstallStatePath)) { return $null }
    try {
        Get-Content -LiteralPath $controlPlaneInstallStatePath -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
    } catch { return $null }
}

function Write-ControlPlaneInstallState {
    param([Parameter(Mandatory = $true)][hashtable]$Values)
    $current = @{}
    if (Test-Path -LiteralPath $controlPlaneInstallStatePath) {
        try {
            $prior = Get-Content -LiteralPath $controlPlaneInstallStatePath -Raw -Encoding UTF8 |
                ConvertFrom-ReleaseControlJson
            foreach ($property in $prior.PSObject.Properties) {
                $current[$property.Name] = $property.Value
            }
        } catch {}
    }
    foreach ($key in $Values.Keys) { $current[$key] = $Values[$key] }
    Write-ControlCenterJsonAtomic -Path $controlPlaneInstallStatePath `
        -Value $current -Depth 8
}

function Suspend-ControlPlaneSupervision {
    param([switch]$CollectorClockRecovery)
    $state = @{}
    foreach ($name in @($guardTaskName, $taskName)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) { throw "CONTROL_PLANE_SCHEDULED_TASK_MISSING:$name" }
        $state[$name] = [bool]$task.Settings.Enabled
    }
    try {
        # Keep the main task enabled so a machine restart can launch the exact
        # installed bundle and resume the durable handoff. Only the guard is
        # disabled because it would otherwise race the intentional owner gap.
        Disable-ScheduledTask -TaskName $guardTaskName | Out-Null
        Stop-ScheduledTask -TaskName $guardTaskName -ErrorAction SilentlyContinue
        if ($CollectorClockRecovery) {
            Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            Stop-ScheduledTask -TaskName $taskName -ErrorAction Stop
        }
    } catch {
        Restore-ControlPlaneSupervision -State $state
        throw
    }
    return $state
}

function Restore-ControlPlaneSupervision {
    param([object]$State)
    if (-not $State) { return }
    foreach ($name in @($taskName, $guardTaskName)) {
        $enabled = if ($State -is [System.Collections.IDictionary]) {
            [bool]$State[$name]
        } elseif ($State.PSObject.Properties[$name]) {
            [bool]$State.$name
        } else { $false }
        if ($enabled) { Enable-ScheduledTask -TaskName $name | Out-Null }
    }
}

function Wait-ControlPlaneGuardQuiesced {
    param([TimeSpan]$Timeout = ([TimeSpan]::FromSeconds(15)))
    $guardScript = Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_watchdog_guard.ps1"
    $guardLauncher = Join-Path $repositoryRoot `
        ".local\runtime-control\xauusd_watchdog_guard_launcher.vbs"
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    do {
        $owners = @(
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object {
                    $_.CommandLine -and
                    ($_.CommandLine.Contains($guardScript) -or
                     $_.CommandLine.Contains($guardLauncher))
                }
        )
        if ($owners.Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "CONTROL_PLANE_GUARD_DID_NOT_QUIESCE"
}

function Stop-VerifiedWatchdogOwner {
    param([Parameter(Mandatory = $true)][object]$Identity)
    $current = Get-ControlPlaneProcessIdentity -ProcessId ([int]$Identity.process_id)
    if (-not $current -or
        -not (Test-ControlPlaneStartTokenEqual `
            -Left $current.process_start_token `
            -Right $Identity.process_start_token) -or
        $current.name -ne "powershell.exe" -or
        $current.command_line -notmatch '(?i)-Action\s+Watchdog') {
        throw "CONTROL_PLANE_WATCHDOG_IDENTITY_CHANGED"
    }
    $expectedLauncherIdentity = $Identity.launcher_identity
    $launcher = Get-ControlPlaneProcessIdentity `
        -ProcessId ([int]$expectedLauncherIdentity.process_id)
    if ($launcher -and $launcher.name -eq "wscript.exe") {
        $expectedLauncher = Join-Path $repositoryRoot `
            ".local\runtime-control\xauusd_watchdog_launcher.vbs"
        if (-not (Test-ControlPlaneStartTokenEqual `
                -Left $launcher.process_start_token `
                -Right $expectedLauncherIdentity.process_start_token) -or
            -not $launcher.command_line.Contains($expectedLauncher) -or
            -not $launcher.command_line.Contains($moduleRoot) -or
            -not $launcher.command_line.Contains($repositoryRoot)) {
            throw "CONTROL_PLANE_LAUNCHER_IDENTITY_MISMATCH"
        }
    } elseif ($launcher) {
        throw "CONTROL_PLANE_LAUNCHER_IDENTITY_MISMATCH"
    }
    $allowLegacy = -not $Identity.watchdog_owner_receipt
    $null = Stop-WatchdogControllerOwner -RootIdentity $current `
        -LauncherIdentity $launcher -AllowLegacyReceiptless:$allowLegacy
}

function Start-WatchdogReplacement {
    param(
        [switch]$PassThru,
        [string]$InstallTransactionId = ""
    )
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $controlScript = Join-Path $controlRoot "xauusd_control_center.ps1"
    $launcher = Join-Path $controlRoot "xauusd_watchdog_launcher.vbs"
    if (-not (Test-Path -LiteralPath $controlScript) -or
        -not (Test-Path -LiteralPath $launcher)) {
        throw "Updated watchdog control files are unavailable."
    }
    $wscript = Join-Path $env:SystemRoot "System32\wscript.exe"
    $arguments = if ($InstallTransactionId) {
        '"{0}" "{1}" "{2}" "{3}" "{4}"' -f `
            $launcher, $controlScript, $moduleRoot, $repositoryRoot, $InstallTransactionId
    } else {
        '"{0}" "{1}" "{2}" "{3}"' -f `
            $launcher, $controlScript, $moduleRoot, $repositoryRoot
    }
    $process = Start-Process -FilePath $wscript -ArgumentList $arguments `
        -WindowStyle Hidden -PassThru
    if ($PassThru) { return $process }
}

function Wait-VerifiedWatchdogHandoff {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedRevision,
        [Parameter(Mandatory = $true)][object]$PreviousIdentity,
        [ValidateSet("ACTIVE", "QUIESCED")][string]$ExpectedMode = "ACTIVE",
        [string]$ExpectedInstallTransactionId = "",
        [switch]$RequireCompleteInventory,
        [TimeSpan]$Timeout = ([TimeSpan]::FromSeconds(90))
    )
    $deadline = [DateTimeOffset]::UtcNow.Add($Timeout)
    do {
        Start-Sleep -Milliseconds 250
        $heartbeat = $null
        try {
            if (Test-Path -LiteralPath $watchdogHeartbeatPath) {
                $heartbeat = Get-Content -LiteralPath $watchdogHeartbeatPath -Raw -Encoding UTF8 |
                    ConvertFrom-ReleaseControlJson
            }
        } catch {}
        if (-not $heartbeat -or
            [string]$heartbeat.control_bundle_revision -ne $ExpectedRevision -or
            -not [bool]$heartbeat.control_bundle_exact_revision -or
            -not [bool]$heartbeat.control_bundle_hash_verified -or
            [string]$heartbeat.supervision_mode -ne $ExpectedMode -or
            [string]$heartbeat.install_transaction_id -ne
                $ExpectedInstallTransactionId -or
            [string]$heartbeat.process_start_token -eq "") { continue }
        $owners = @(Get-VerifiedWatchdogOwners -RequireCompleteInventory:$RequireCompleteInventory)
        if ($owners.Count -ne 1) { continue }
        $owner = $owners[0]
        $ownerReceipt = $owner.watchdog_owner_receipt
        $expectedReceiptMode = if ($ExpectedMode -eq 'QUIESCED') {
            'QUIESCED_INSTALL'
        } else { 'ACTIVE' }
        if ([int]$owner.process_id -ne [int]$heartbeat.process_id -or
            -not (Test-ControlPlaneStartTokenEqual `
                -Left $owner.process_start_token `
                -Right $heartbeat.process_start_token) -or
            ([int]$owner.process_id -eq [int]$PreviousIdentity.process_id -and
             (Test-ControlPlaneStartTokenEqual `
                -Left $owner.process_start_token `
                -Right $PreviousIdentity.process_start_token)) -or
            -not $ownerReceipt -or
            [string]$ownerReceipt.mode -ne $expectedReceiptMode -or
            [string]$ownerReceipt.install_transaction_id -ne
                $ExpectedInstallTransactionId -or
            [string]$heartbeat.instance_id -ne [string]$ownerReceipt.instance_id -or
            [string]$heartbeat.owner_receipt_digest -ne
                (Get-WatchdogOwnerReceiptDigest -Receipt $ownerReceipt)) { continue }
        return $owner
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "CONTROL_PLANE_NEW_WATCHDOG_HEARTBEAT_TIMEOUT"
}

function Wait-ControlPlaneInstallActivation {
    param([Parameter(Mandatory = $true)][string]$TransactionId)
    while ($true) {
        $state = Get-ControlPlaneInstallState
        if (-not $state -or [string]$state.transaction_id -ne $TransactionId) {
            throw "CONTROL_PLANE_INSTALL_FENCE_LOST"
        }
        if ([string]$state.phase -in @("FAILED", "ROLLED_BACK", "COMMITTED")) {
            throw "CONTROL_PLANE_INSTALL_ACTIVATION_WITHDRAWN"
        }
        $installerAlive = Get-ControlPlaneInstallOwnerAlive -State $state
        Write-WatchdogHeartbeat -SupervisionMode "QUIESCED" `
            -InstallTransactionId $TransactionId
        if ([string]$state.phase -eq "ACTIVATE_NEW_WATCHDOG") {
            if ($installerAlive) { return "INSTALLER_GRANTED" }
        }
        if (-not $installerAlive -and [string]$state.phase -in @(
            "INSTALL_BUNDLE", "START_NEW_WATCHDOG", "VERIFY_QUIESCED_HANDOFF"
        ) -or (-not $installerAlive -and
            [string]$state.phase -eq "ACTIVATE_NEW_WATCHDOG")) {
            try {
                $verified = Assert-AbandonedControlPlaneInstallActivation `
                    -State $state -TransactionId $TransactionId
                Write-ControlPlaneInstallState @{
                    phase = "ACTIVATE_NEW_WATCHDOG"
                    recovery = "INSTALL_OWNER_EXITED_AFTER_INDEPENDENT_VERIFICATION"
                    new_watchdog_identity = $verified.owner
                    isolation_after = $verified.isolation
                }
                return "RECOVERED"
            } catch {
                $failure = $_.Exception.Message
                $null = Restore-AbandonedControlPlaneInstallForWatchdog `
                    -State $state -Failure $failure
                throw "CONTROL_PLANE_ABANDONED_INSTALL_ROLLED_BACK: $failure"
            }
        }
        Start-Sleep -Milliseconds 250
    }
}

function Assert-CollectorClockRecoveryContext {
    param([Parameter(Mandatory = $true)][object]$Context)
    $descriptor = Get-WatchdogSingletonDescriptor
    if ([string]$Context.incident -cne 'COLLECTOR_CLOCK_EVENT_ATOMICITY' -or
        [string]$Context.state -cne 'DEGRADED_RECOVERY_BASELINE' -or
        [string]$Context.broken_revision -notmatch '^[0-9a-f]{40}$' -or
        [string]$Context.target_revision -notmatch '^[0-9a-f]{40}$' -or
        [string]$Context.broken_revision -ceq [string]$Context.target_revision -or
        [string]$Context.user_sid -cne [string]$descriptor.user_sid -or
        [string]$Context.runtime_root_hash -cne [string]$descriptor.runtime_root_hash -or
        [string]$Context.repository_root_hash -cne [string]$descriptor.repository_root_hash -or
        [string]$Context.snapshot.decision_time -cne '2026-09-04T16:05:00.000000+00:00' -or
        [string]$Context.snapshot.snapshot_hash -cne
            'b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee') {
        throw 'COLLECTOR_RECOVERY_CONTEXT_INVALID'
    }
}

function Get-CollectorClockRecoveryContext {
    if (-not (Test-Path -LiteralPath $controlPlaneInstallStatePath)) { return $null }
    # A corrupt persisted maintenance context must not turn a restart hold off.
    $state = Get-Content -LiteralPath $controlPlaneInstallStatePath -Raw -Encoding UTF8 |
        ConvertFrom-ReleaseControlJson
    if (-not $state.collector_clock_recovery) { return $null }
    Assert-CollectorClockRecoveryContext -Context $state.collector_clock_recovery
    return $state.collector_clock_recovery
}

function Test-CollectorClockRecoveryHold {
    $context = Get-CollectorClockRecoveryContext
    if (-not $context) { return $false }
    $revision = Get-CodeRevision
    if ([string]$revision -ceq [string]$context.broken_revision) { return $true }
    $release = Get-ReleaseControlState
    if ([string]$revision -ceq [string]$context.target_revision -and (
        ([string]$release.stable.windows_revision -ceq [string]$context.target_revision) -or
        ($release.transaction -and [string]$release.transaction.type -ceq 'PROMOTE' -and
         [string]$release.transaction.target.windows_revision -ceq [string]$context.target_revision)
    )) { return $false }
    throw 'COLLECTOR_RECOVERY_RUNTIME_TRANSITION_UNPROVED'
}

function Invoke-CollectorClockRecoveryOperation {
    param([switch]$Apply)
    if (-not $script:releaseTransactionLockHeld) {
        throw 'COLLECTOR_RECOVERY_RELEASE_LOCK_REQUIRED'
    }
    $context = Get-CollectorClockRecoveryContext
    if (-not $context) { throw 'COLLECTOR_RECOVERY_CONTEXT_REQUIRED' }
    $source = Join-Path ([IO.Path]::GetTempPath()) ('xauusd-clock-recovery-' + [guid]::NewGuid().ToString('N'))
    $added = $false
    try {
        $stage = Invoke-Utf8NativeProcess -FilePath 'git.exe' -Arguments @(
            '-C', $repositoryRoot, 'worktree', 'add', '--detach', '--quiet', $source,
            [string]$context.target_revision
        )
        if ($stage.exit_code -ne 0) { throw 'COLLECTOR_RECOVERY_SOURCE_STAGE_FAILED' }
        $added = $true
        $baseline = Get-CollectorClockRecoveryBaseline -VerifiedSourceRoot $source `
            -TargetRevision ([string]$context.target_revision) -SupervisionRecovered
        if (-not $Apply -and -not [bool]$baseline.snapshot.exclusion_recorded) {
            throw 'COLLECTOR_RECOVERY_EXISTING_STATE_NOT_REPAIRED'
        }
        if ($Apply -and -not [bool]$baseline.snapshot.exclusion_recorded) {
            $before = Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory
            Assert-ControlPlaneIsolationBaseline -Snapshot $before `
                -ReleaseState (Get-ReleaseControlState) -CollectorClockRecoveryBaseline $context
            $inspected = [pscustomobject]@{
                business_runtime_revision = $baseline.broken_revision
                services = $baseline.services
                release_state_hash = $before.release_state_hash
                release_history_hash = $before.release_history_hash
            }
            Assert-ControlPlaneIsolationSnapshot -Before $inspected -After $before
            $write = Invoke-Utf8NativeProcess -FilePath 'python' -WorkingDirectory $source `
                -Arguments @((Join-Path $source 'scripts\run_evidence_repair_v2.py'),
                    '--local-root', $runtimeForwardRoot, '--snapshot-only-clock',
                    [string]$context.snapshot.decision_time, '--expected-snapshot-hash',
                    [string]$context.snapshot.snapshot_hash) -TimeoutMilliseconds 15000
            if ($write.exit_code -ne 0) { throw 'COLLECTOR_RECOVERY_EXISTING_STATE_REPAIR_FAILED' }
            $result = ConvertFrom-ReleaseControlJson -Json ([string]$write.stdout)
            if ([string]$result.status -cne 'EXCLUDED_INCOMPLETE' -or
                [string]$result.snapshot_hash -cne [string]$context.snapshot.snapshot_hash -or
                [string]$result.decision_time -cne [string]$context.snapshot.decision_time) {
                throw 'COLLECTOR_RECOVERY_REPAIR_RESULT_CONFLICT'
            }
            $after = Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory
            Assert-ControlPlaneIsolationSnapshot -Before $before -After $after
            $baseline.snapshot.exclusion_recorded = $true
            $baseline | Add-Member -NotePropertyName repair -NotePropertyValue $result
        }
        return $baseline
    } finally {
        if ($added -and -not $script:unresolvedNativeProcess) {
            $cleanup = Invoke-Utf8NativeProcess -FilePath 'git.exe' -Arguments @(
                '-C', $repositoryRoot, 'worktree', 'remove', '--force', $source
            )
            if ($cleanup.exit_code -ne 0) { Write-Warning "Collector inspection checkout retained: $source" }
        }
    }
}

function Get-CollectorClockRecoveryBaseline {
    param(
        [Parameter(Mandatory = $true)][string]$VerifiedSourceRoot,
        [Parameter(Mandatory = $true)][string]$TargetRevision,
        [switch]$SupervisionRecovered
    )
    # Admission for this incident only. This read-only function cannot install,
    # repair a database, release a mutex or start a service.
    Assert-ControlPlaneSourceRevision -SourceRoot $VerifiedSourceRoot `
        -SourceRevision $TargetRevision -RequireImmutableSource
    $release = Get-ReleaseControlState
    $priorInstall = Get-ControlPlaneInstallState
    $ownReleaseLock = $false
    if ($SupervisionRecovered -and $script:releaseTransactionLockHeld) {
        $lock = Get-Content -LiteralPath (Join-Path $releaseLockPath 'owner.json') -Raw -Encoding UTF8 |
            ConvertFrom-ReleaseControlJson
        $self = Get-ControlPlaneProcessIdentity -ProcessId $PID -RequireCompleteInventory
        $ownReleaseLock = $self -and [int]$lock.owner_pid -eq $PID -and
            (Test-ControlPlaneStartTokenEqual -Left $self.process_start_token -Right $lock.owner_process_start_token)
    }
    if (-not $release -or $release.transaction -or
        ((Test-Path -LiteralPath $releaseLockPath) -and -not $ownReleaseLock) -or
        ($priorInstall -and [string]$priorInstall.phase -notin @('COMMITTED', 'ROLLED_BACK', 'FAILED'))) {
        throw 'COLLECTOR_RECOVERY_TRANSACTION_ACTIVE'
    }
    $revision = Get-CodeRevision
    if ([string]$revision -notmatch '^[0-9a-f]{40}$' -or
        [string]$revision -cne [string]$release.stable.windows_revision -or
        $TargetRevision -notmatch '^[0-9a-f]{40}$' -or $TargetRevision -ceq $revision) {
        throw 'COLLECTOR_RECOVERY_REVISION_MISMATCH'
    }
    if ($SupervisionRecovered) {
        $context = Get-CollectorClockRecoveryContext
        $bundle = Assert-ActiveControlBundle
        if (-not $ownReleaseLock -or -not $context -or
            [string]$priorInstall.phase -cne 'COMMITTED' -or
            [string]$context.target_revision -cne $TargetRevision -or
            [string]$context.broken_revision -cne [string]$revision -or
            [string]$context.stable_worker -cne [string]$release.stable.worker_version_id -or
            [string]$bundle.source_revision -cne $TargetRevision) {
            throw 'COLLECTOR_RECOVERY_SUPERVISION_CONTEXT_UNPROVED'
        }
    }
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    if (@($all | Where-Object {
        $_.Name -in @('python.exe', 'powershell.exe', 'pwsh.exe', 'wscript.exe') -and
        -not $_.CommandLine
    }).Count -gt 0) { throw 'COLLECTOR_RECOVERY_PROCESS_INVENTORY_UNKNOWN' }
    $inventory = Get-WatchdogOwnershipInventory -RequireCompleteInventory
    if (@($inventory.authoritative).Count -ne $(if ($SupervisionRecovered) { 1 } else { 0 }) -or
        @($inventory.duplicate_shaped).Count -ne 0 -or
        @($inventory.legacy_orphaned).Count -ne 0 -or
        @($inventory.unknown).Count -ne 0) {
        throw 'COLLECTOR_RECOVERY_WATCHDOG_NOT_ABSENT'
    }
    $permittedProcesses = @($PID)
    if ($SupervisionRecovered) {
        $watchdog = $inventory.authoritative[0]
        $null = Assert-CurrentWatchdogHeartbeat -Owner $watchdog -ExpectedRevision $TargetRevision
        $permittedProcesses += @([int]$watchdog.process_id, [int]$watchdog.launcher_identity.process_id)
    }
    $controlRoot = Join-Path $repositoryRoot '.local\runtime-control'
    if (@($all | Where-Object {
        [int]$_.ProcessId -notin $permittedProcesses -and $_.CommandLine -and
        ($_.CommandLine.Contains($controlRoot) -or $_.CommandLine.Contains($moduleRoot)) -and
        (($_.Name -eq 'wscript.exe' -and $_.CommandLine.Contains($controlRoot)) -or $_.CommandLine -match
            '(?i)-Action\s+(Watchdog|DiscoverCandidate|RetryCandidateValidation|InstallControlPlane|PromoteCandidate)\b')
    }).Count -gt 0) { throw 'COLLECTOR_RECOVERY_CONTROL_HELPER_ACTIVE' }
    $descriptor = Get-WatchdogSingletonDescriptor
    if (-not $inventory.receipt -or
        -not (Test-WatchdogOwnerReceiptShape -Receipt $inventory.receipt -Descriptor $descriptor)) {
        throw 'COLLECTOR_RECOVERY_STALE_RECEIPT_REQUIRED'
    }
    $prior = Get-ControlPlaneProcessIdentity -ProcessId ([int]$inventory.receipt.process_id) `
        -RequireCompleteInventory
    if (-not $SupervisionRecovered -and $prior -and (Test-ControlPlaneStartTokenEqual -Left $prior.process_start_token `
        -Right $inventory.receipt.process_start_token)) {
        throw 'COLLECTOR_RECOVERY_PRIOR_OWNER_ALIVE'
    }
    $owners = [ordered]@{}
    foreach ($service in $services) {
        $matches = @($all | Where-Object {
            Test-ForecasterServiceProcess -Process $_ -Service $service
        })
        $unclassified = @($all | Where-Object {
            $_.CommandLine -and $_.CommandLine.Contains($runtimeForwardRoot) -and
            $_.CommandLine.Contains([string]$service.Match) -and
            $_.Name -in @('python.exe', 'powershell.exe', 'pwsh.exe') -and
            -not (Test-ForecasterServiceProcess -Process $_ -Service $service)
        })
        if ($unclassified.Count -gt 0) {
            throw "COLLECTOR_RECOVERY_SERVICE_IDENTITY_UNKNOWN:$($service.Key)"
        }
        $required = $service.Key -ne 'collector' -and
            (Test-ControlPlaneServiceOwnerRequired -Service $service -ReleaseState $release)
        if ($matches.Count -ne $(if ($required) { 1 } else { 0 })) {
            throw "COLLECTOR_RECOVERY_SERVICE_OWNER_INVALID:$($service.Key)"
        }
        $owners[$service.Key] = @($matches | ForEach-Object {
            $identity = Get-ControlPlaneProcessIdentity -ProcessId ([int]$_.ProcessId) `
                -RequireCompleteInventory
            if (-not $identity -or [string]$identity.owner_sid -cne [string]$descriptor.user_sid) {
                throw "COLLECTOR_RECOVERY_SERVICE_IDENTITY_UNKNOWN:$($service.Key)"
            }
            $identity
        })
        if ($required) {
            $health = Get-ServiceState -Service $service -Processes $matches
            if ($health -notin @('RUNNING', 'LIVE', 'MARKET CLOSED', 'API OK', 'SYNC OK')) {
                throw "COLLECTOR_RECOVERY_SERVICE_UNHEALTHY:$($service.Key):$health"
            }
            if ($service.Key -eq 'quote' -and -not (Get-BrokerMarketSession)) {
                throw 'COLLECTOR_RECOVERY_SESSION_AUTHORITY_UNAVAILABLE'
            }
        }
    }
    $provider = Get-ReleaseProviderRuntimeFacts -PersistedState $release -ForceProviderRefresh
    $traffic = $provider.active_worker_observation
    if (-not $traffic -or [string]$traffic.status -ne 'AVAILABLE' -or
        [double]$traffic.traffic_percent -ne 100 -or
        [string]$traffic.version_id -cne [string]$release.stable.worker_version_id) {
        throw 'COLLECTOR_RECOVERY_STABLE_TRAFFIC_UNPROVED'
    }
    $clock = '2026-09-04T16:05:00.000000+00:00'
    $hash = 'b139c8a9d913c237e8e9e3ebc677a1144cd8ad2f9e0adee6b62ed8cd2a7fa5ee'
    $inspection = Invoke-Utf8NativeProcess -FilePath 'python' -WorkingDirectory $VerifiedSourceRoot `
        -Arguments @((Join-Path $VerifiedSourceRoot 'scripts\run_evidence_repair_v2.py'),
            '--local-root', $runtimeForwardRoot, '--snapshot-only-clock', $clock,
            '--expected-snapshot-hash', $hash, '--inspect-snapshot-only') -TimeoutMilliseconds 15000
    if ($inspection.timed_out -or $inspection.exit_code -ne 0) {
        throw 'COLLECTOR_RECOVERY_SNAPSHOT_INSPECTION_FAILED'
    }
    $evidence = ConvertFrom-ReleaseControlJson -Json ([string]$inspection.stdout)
    if ([string]$evidence.snapshot_hash -cne $hash -or [string]$evidence.decision_time -cne $clock) {
        throw 'COLLECTOR_RECOVERY_SNAPSHOT_INSPECTION_MISMATCH'
    }
    return [pscustomobject]@{
        incident = 'COLLECTOR_CLOCK_EVENT_ATOMICITY'
        state = 'DEGRADED_RECOVERY_BASELINE'
        observed_at = [DateTimeOffset]::UtcNow.ToString('o')
        broken_revision = [string]$revision
        target_revision = $TargetRevision
        stable_worker = [string]$release.stable.worker_version_id
        runtime_root_hash = [string]$descriptor.runtime_root_hash
        repository_root_hash = [string]$descriptor.repository_root_hash
        user_sid = [string]$descriptor.user_sid
        previous_watchdog_receipt = $inventory.receipt
        services = [pscustomobject]$owners
        snapshot = $evidence
    }
}

function Invoke-ControlPlaneInstall {
    param(
        [Parameter(Mandatory = $true)][string]$VerifiedSourceRoot,
        [Parameter(Mandatory = $true)][string]$TargetRevision,
        [switch]$CollectorClockRecovery
    )
    if ($TargetRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
    $bootstrapMutex = $null
    $bootstrapMutexHeld = $false
    try {
    $controlRoot = Join-Path $repositoryRoot ".local\runtime-control"
    $currentBundle = Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $controlRoot
    if (-not $currentBundle) { throw "CONTROL_BUNDLE_CURRENT_VERIFICATION_FAILED" }
    if (@(Get-VerifiedControlCenterGuiOwners).Count -ne 0) {
        throw "CONTROL_CENTER_GUI_MUST_BE_CLOSED"
    }
    $release = Get-ReleaseControlState
    if (($release -and $release.transaction) -or
        (Test-Path -LiteralPath $releaseLockPath)) {
        throw "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"
    }
    # A final source bundle may install over the last pre-singleton Control
    # Plane. Permit exactly one fully shaped legacy owner only at this boundary;
    # the replacement must establish the v2 mutex receipt before handoff passes.
    $incidentBaseline = $null
    $existingIncident = Get-CollectorClockRecoveryContext
    if ($existingIncident -and -not $CollectorClockRecovery -and
        [string]$release.stable.windows_revision -cne [string]$existingIncident.target_revision) {
        throw 'COLLECTOR_RECOVERY_INSTALL_IN_PROGRESS'
    }
    if ($CollectorClockRecovery) {
        $descriptor = Get-WatchdogSingletonDescriptor
        $bootstrapMutex = [Threading.Mutex]::new($false, [string]$descriptor.mutex_name)
        try { $bootstrapMutexHeld = $bootstrapMutex.WaitOne(0) }
        catch [Threading.AbandonedMutexException] { $bootstrapMutexHeld = $true }
        if (-not $bootstrapMutexHeld) { throw 'COLLECTOR_RECOVERY_BOOTSTRAP_OWNER_PRESENT' }
        $incidentBaseline = Get-CollectorClockRecoveryBaseline `
            -VerifiedSourceRoot $VerifiedSourceRoot -TargetRevision $TargetRevision
        $oldOwner = $incidentBaseline.previous_watchdog_receipt
        $oldHeartbeat = $null
    } else {
        $oldOwners = @(Get-VerifiedWatchdogOwners -AllowLegacySingleOwner)
        if ($oldOwners.Count -ne 1) {
            throw "CONTROL_PLANE_EXACTLY_ONE_WATCHDOG_REQUIRED"
        }
        $oldOwner = $oldOwners[0]
        $oldHeartbeat = Assert-CurrentWatchdogHeartbeat -Owner $oldOwner `
            -ExpectedRevision ([string]$currentBundle.source_revision)
    }
    $isolationBefore = $null

    $controlParent = Split-Path -Parent $controlRoot
    $transactionId = [guid]::NewGuid().ToString("N")
    $stageRoot = Join-Path $controlParent (".cps-{0}" -f $transactionId)
    $backupRoot = Join-Path $controlParent (".cpb-{0}" -f $transactionId)
    $supervisionState = $null
    $releaseLockHeld = $false
    $oldStopped = $false
    $bundleInstalled = $false
    $newOwner = $null
    $rollbackResult = 'NOT_REQUIRED'
    $startedAt = [DateTimeOffset]::UtcNow.ToString("o")
    $installOwnerIdentity = Get-ControlPlaneProcessIdentity -ProcessId $PID
    if (-not $installOwnerIdentity) {
        throw "CONTROL_PLANE_INSTALL_OWNER_IDENTITY_REQUIRED"
    }
    Write-ControlPlaneInstallState @{
        transaction_id = $transactionId
        target_revision = $TargetRevision
        previous_revision = [string]$currentBundle.source_revision
        started_at = $startedAt
        completed_at = $null
        phase = "PRECHECK"
        old_watchdog_identity = $oldOwner
        install_owner_identity = $installOwnerIdentity
        stage_root = $stageRoot
        backup_root = $backupRoot
        old_watchdog_heartbeat = $oldHeartbeat
        new_watchdog_identity = $null
        bundle_hash_verified = $false
        rollback_result = $null
        failure = $null
        isolation_before = $null
        isolation_after = $null
        collector_clock_recovery = $incidentBaseline
    }
    try {
        if (-not (Enter-ReleaseTransactionLock)) {
            throw "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"
        }
        $releaseLockHeld = $true
        $staged = New-VerifiedRuntimeControlBundleStage `
            -SourceRoot $VerifiedSourceRoot -SourceRevision $TargetRevision `
            -StageRoot $stageRoot -RequireImmutableSource
        if (-not $staged) { throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED" }
        # Execute the staged entrypoint while the old supervisor still owns
        # production. This proves load-time dependency closure in a clean bundle
        # before any active Control Plane process is disturbed.
        $null = Invoke-RuntimeControlBundleStartupPreflight `
            -StageRoot $stageRoot -ExpectedRevision $TargetRevision `
            -RepositoryRootForPreflight $VerifiedSourceRoot
        Write-ControlPlaneInstallState @{
            phase = "QUIESCE_CONTROL_SUPERVISION"
            bundle_hash_verified = $true
        }
        $supervisionState = Suspend-ControlPlaneSupervision -CollectorClockRecovery:$CollectorClockRecovery
        Write-ControlPlaneInstallState @{ supervision_state = $supervisionState }
        Wait-ControlPlaneGuardQuiesced
        # Revalidate the complete stage before the first destructive process action.
        if (-not (Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $stageRoot)) {
            throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED"
        }
        Write-ControlPlaneInstallState @{ phase = "STOP_OLD_WATCHDOG" }
        if (-not $CollectorClockRecovery) { Stop-VerifiedWatchdogOwner -Identity $oldOwner }
        $oldStopped = $true
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (@(Get-VerifiedWatchdogOwners -RequireCompleteInventory:$CollectorClockRecovery).Count -ne 0) {
            throw "CONTROL_PLANE_OLD_WATCHDOG_STILL_OWNS"
        }
        # The watchdog can recover a service while the immutable bundle stage is
        # being verified. Establish the service baseline only after supervision
        # is quiesced and the old watchdog has stopped, so no owner can mutate it
        # between the snapshot and the handoff.
        # Release state is mutable before the transaction lock is acquired and
        # the old supervisor is fenced. Classify the service baseline only from
        # the fresh state at this quiesced boundary.
        $release = Get-ReleaseControlState
        if ($release -and $release.transaction) {
            throw "CONTROL_PLANE_INSTALL_BLOCKED_BY_RELEASE_TRANSACTION"
        }
        $isolationBefore = Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory:$CollectorClockRecovery
        Assert-ControlPlaneIsolationBaseline -Snapshot $isolationBefore `
            -ReleaseState $release -CollectorClockRecoveryBaseline $incidentBaseline
        Write-ControlPlaneInstallState @{ isolation_before = $isolationBefore }
        Write-ControlPlaneInstallState @{ phase = "INSTALL_BUNDLE" }
        $installed = Install-VerifiedRuntimeControlBundleStage `
            -StageRoot $stageRoot -ControlRoot $controlRoot -BackupRoot $backupRoot
        $bundleInstalled = $true
        if ([string]$installed.source_revision -ne $TargetRevision) {
            throw "CONTROL_BUNDLE_INSTALLED_REVISION_MISMATCH"
        }
        Write-ControlPlaneInstallState @{
            phase = "START_NEW_WATCHDOG"
            handoff_mode = "QUIESCED"
        }
        if ($bootstrapMutexHeld) {
            $bootstrapMutex.ReleaseMutex()
            $bootstrapMutexHeld = $false
        }
        $null = Start-WatchdogReplacement -PassThru `
            -InstallTransactionId $transactionId
        Write-ControlPlaneInstallState @{ phase = "VERIFY_QUIESCED_HANDOFF" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner -ExpectedMode "QUIESCED" `
            -ExpectedInstallTransactionId $transactionId -RequireCompleteInventory:$CollectorClockRecovery
        $isolationAfter = Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory:$CollectorClockRecovery
        Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
            -After $isolationAfter
        Write-ControlPlaneInstallState @{ phase = "ACTIVATE_NEW_WATCHDOG" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner -ExpectedMode "ACTIVE" -RequireCompleteInventory:$CollectorClockRecovery
        Restore-ControlPlaneSupervision -State $supervisionState
        $supervisionState = $null
        Write-ControlPlaneInstallState @{
            phase = "COMMITTED"
            completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            new_watchdog_identity = $newOwner
            rollback_result = "NOT_REQUIRED"
            failure = $null
            isolation_after = $isolationAfter
        }
        return [pscustomobject]@{
            status = "COMMITTED"
            previous_revision = [string]$currentBundle.source_revision
            target_revision = $TargetRevision
            old_watchdog_identity = $oldOwner
            new_watchdog_identity = $newOwner
            business_runtime_revision = [string]$isolationBefore.business_runtime_revision
            bundle_hash_verified = $true
        }
    } catch {
        $failure = $_.Exception.Message
        $rollbackResult = "NOT_REQUIRED"
        try {
            if ($oldStopped) {
                Write-ControlPlaneInstallState @{ phase = "ROLLING_BACK" }
                foreach ($owner in @(Get-VerifiedWatchdogOwners)) {
                    Stop-VerifiedWatchdogOwner -Identity $owner
                }
                if ($bundleInstalled) {
                    $null = Restore-RuntimeControlBundleBackup `
                        -BackupRoot $backupRoot -ControlRoot $controlRoot
                }
                if ($isolationBefore) {
                    $isolationAfter = Get-ControlPlaneIsolationSnapshot -RequireCompleteInventory:$CollectorClockRecovery
                    Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
                        -After $isolationAfter
                }
                # Recovery proves restoration against the captured baseline.
                # It deliberately does not re-run the contextual normal-state
                # owner rule that may have caused the forward handoff failure.
                if ($CollectorClockRecovery) {
                    $rollbackResult = 'ROLLED_BACK_DEGRADED_BASELINE'
                    $newOwner = $null
                } else {
                    $null = Start-WatchdogReplacement -PassThru
                    $restoredOwner = Wait-VerifiedWatchdogHandoff `
                        -ExpectedRevision ([string]$currentBundle.source_revision) `
                        -PreviousIdentity $oldOwner
                    $rollbackResult = "ROLLED_BACK"
                    $newOwner = $restoredOwner
                }
            }
        } catch {
            $rollbackResult = "ROLLBACK_FAILED: $($_.Exception.Message)"
        }
        try {
            if ($CollectorClockRecovery) {
                foreach ($name in @($taskName, $guardTaskName)) {
                    Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
                }
            } else { Restore-ControlPlaneSupervision -State $supervisionState }
        } catch {
            $rollbackResult = "ROLLBACK_FAILED: supervision restore: $($_.Exception.Message)"
        }
        Write-ControlPlaneInstallState @{
            phase = if ($rollbackResult -in @('ROLLED_BACK', 'ROLLED_BACK_DEGRADED_BASELINE')) { "ROLLED_BACK" } else { "FAILED" }
            completed_at = [DateTimeOffset]::UtcNow.ToString("o")
            new_watchdog_identity = $newOwner
            rollback_result = $rollbackResult
            failure = $failure
            isolation_after = if ($oldStopped) { $isolationAfter } else { $null }
        }
        throw "CONTROL_PLANE_INSTALL_FAILED: $failure; $rollbackResult"
    } finally {
        if ($releaseLockHeld) { Exit-ReleaseTransactionLock }
        foreach ($path in @($stageRoot, $backupRoot)) {
            if ($CollectorClockRecovery -and $rollbackResult -like 'ROLLBACK_FAILED*') { continue }
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Recurse -Force
            }
        }
    }
    } finally {
        if ($bootstrapMutexHeld) { $bootstrapMutex.ReleaseMutex() }
        if ($bootstrapMutex) { $bootstrapMutex.Dispose() }
    }
}
