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
        $owners = @(Get-VerifiedWatchdogOwners)
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

function Invoke-ControlPlaneInstall {
    param(
        [Parameter(Mandatory = $true)][string]$VerifiedSourceRoot,
        [Parameter(Mandatory = $true)][string]$TargetRevision
    )
    if ($TargetRevision -notmatch '^[0-9a-f]{40}$') {
        throw "CONTROL_BUNDLE_EXACT_REVISION_REQUIRED"
    }
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
    $oldOwners = @(Get-VerifiedWatchdogOwners -AllowLegacySingleOwner)
    if ($oldOwners.Count -ne 1) {
        throw "CONTROL_PLANE_EXACTLY_ONE_WATCHDOG_REQUIRED"
    }
    $oldOwner = $oldOwners[0]
    $oldHeartbeat = Assert-CurrentWatchdogHeartbeat -Owner $oldOwner `
        -ExpectedRevision ([string]$currentBundle.source_revision)
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
        $supervisionState = Suspend-ControlPlaneSupervision
        Write-ControlPlaneInstallState @{ supervision_state = $supervisionState }
        Wait-ControlPlaneGuardQuiesced
        # Revalidate the complete stage before the first destructive process action.
        if (-not (Get-RuntimeControlBundleIdentityAtRoot -ControlRoot $stageRoot)) {
            throw "CONTROL_BUNDLE_STAGED_HASH_VERIFICATION_FAILED"
        }
        Write-ControlPlaneInstallState @{ phase = "STOP_OLD_WATCHDOG" }
        Stop-VerifiedWatchdogOwner -Identity $oldOwner
        $oldStopped = $true
        Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (@(Get-VerifiedWatchdogOwners).Count -ne 0) {
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
        $isolationBefore = Get-ControlPlaneIsolationSnapshot
        Assert-ControlPlaneIsolationBaseline -Snapshot $isolationBefore `
            -ReleaseState $release
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
        $null = Start-WatchdogReplacement -PassThru `
            -InstallTransactionId $transactionId
        Write-ControlPlaneInstallState @{ phase = "VERIFY_QUIESCED_HANDOFF" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner -ExpectedMode "QUIESCED" `
            -ExpectedInstallTransactionId $transactionId
        $isolationAfter = Get-ControlPlaneIsolationSnapshot
        Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
            -After $isolationAfter
        Write-ControlPlaneInstallState @{ phase = "ACTIVATE_NEW_WATCHDOG" }
        $newOwner = Wait-VerifiedWatchdogHandoff -ExpectedRevision $TargetRevision `
            -PreviousIdentity $oldOwner -ExpectedMode "ACTIVE"
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
                    $isolationAfter = Get-ControlPlaneIsolationSnapshot
                    Assert-ControlPlaneIsolationSnapshot -Before $isolationBefore `
                        -After $isolationAfter
                }
                # Recovery proves restoration against the captured baseline.
                # It deliberately does not re-run the contextual normal-state
                # owner rule that may have caused the forward handoff failure.
                $null = Start-WatchdogReplacement -PassThru
                $restoredOwner = Wait-VerifiedWatchdogHandoff `
                    -ExpectedRevision ([string]$currentBundle.source_revision) `
                    -PreviousIdentity $oldOwner
                $rollbackResult = "ROLLED_BACK"
                $newOwner = $restoredOwner
            }
        } catch {
            $rollbackResult = "ROLLBACK_FAILED: $($_.Exception.Message)"
        }
        try {
            Restore-ControlPlaneSupervision -State $supervisionState
        } catch {
            $rollbackResult = "ROLLBACK_FAILED: supervision restore: $($_.Exception.Message)"
        }
        Write-ControlPlaneInstallState @{
            phase = if ($rollbackResult -eq "ROLLED_BACK") { "ROLLED_BACK" } else { "FAILED" }
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
            if (Test-Path -LiteralPath $path) {
                Remove-Item -LiteralPath $path -Recurse -Force
            }
        }
    }
}
