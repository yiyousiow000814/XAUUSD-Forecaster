# Injected only into a generated, isolated test bundle before any user read.
$fixtureConfigurationPath = [Environment]::GetEnvironmentVariable('XAUUSD_FIXTURE_CONFIGURATION', 'Process')
if (-not $fixtureConfigurationPath -or
    [IO.Path]::GetFullPath($fixtureConfigurationPath) -cne '__CONFIG_PATH__') {
    throw 'FIXTURE_CONFIGURATION_REQUIRED'
}
$fixtureConfigurationBytes = [IO.File]::ReadAllBytes($fixtureConfigurationPath)
if ($fixtureConfigurationBytes.Length -gt 32768) { throw 'FIXTURE_CONFIGURATION_TOO_LARGE' }
$fixtureHasher = [Security.Cryptography.SHA256]::Create()
try {
    $fixtureConfigurationDigest = ([BitConverter]::ToString(
        $fixtureHasher.ComputeHash($fixtureConfigurationBytes))).Replace('-', '').ToLowerInvariant()
} finally { $fixtureHasher.Dispose() }
if ($fixtureConfigurationDigest -cne '__CONFIG_DIGEST__') { throw 'FIXTURE_CONFIGURATION_IDENTITY_MISMATCH' }
$script:fixtureUserConfiguration = [Text.UTF8Encoding]::new($false, $true).GetString(
    $fixtureConfigurationBytes) | ConvertFrom-Json
if ($script:fixtureUserConfiguration.schema_version -ne 1 -or
    $script:fixtureUserConfiguration.fixture_id -cne '__FIXTURE_ID__') {
    throw 'FIXTURE_CONFIGURATION_SCHEMA_INVALID'
}
# One bounded, per-process record; no credential values are recorded.
[IO.File]::WriteAllText(('__ATTESTATION_ROOT__\' + $PID + '.json'),
    (@{pid=$PID;action=$Action;fixture_id='__FIXTURE_ID__';configuration_sha256=$fixtureConfigurationDigest} |
        ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
function Get-FixtureUserEnvironmentValue {
    param([Parameter(Mandatory=$true)][string]$Name)
    $property = $script:fixtureUserConfiguration.values.PSObject.Properties[$Name]
    if (-not $property) { throw "FIXTURE_ENVIRONMENT_KEY_UNDECLARED:$Name" }
    return [string]$property.Value
}
