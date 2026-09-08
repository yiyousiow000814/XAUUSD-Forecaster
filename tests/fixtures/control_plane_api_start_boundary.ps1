# Appended only to the existing isolated boundary. Retains the real service
# launcher while permitting exactly one generated API at its declared endpoint.
function Start-Process {
    param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,
        $RedirectStandardOutput,$RedirectStandardError,[switch]$PassThru)
    if ($FilePath -ne 'python' -or $WorkingDirectory -ne '__SOURCE_ROOT__' -or
        -not $script:fixtureApprovedAPIArguments -or
        (@($ArgumentList) -join ' ') -cne $script:fixtureApprovedAPIArguments) {
        throw 'STAGED_PROCESS_START_DENIED'
    }
    Assert-FixturePath $WorkingDirectory
    Assert-FixturePath $RedirectStandardOutput
    Assert-FixturePath $RedirectStandardError
    $script:fixtureAPIProcess = Microsoft.PowerShell.Management\Start-Process `
        -FilePath '__PYTHON_EXE__' -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput $RedirectStandardOutput `
        -RedirectStandardError $RedirectStandardError -PassThru
    return $script:fixtureAPIProcess
}
function Start-ForecasterService {
    param($Service,[switch]$SkipExistingCheck)
    if ($Service.Key -ne 'api' -or $Service.CodeRoot -ne '__SOURCE_ROOT__' -or
        $Service.ScriptPath -ne '__SOURCE_ROOT__\scripts\runtime\run_dashboard_api.py' -or
        ($Service.Arguments -join '|') -cne '__API_ARGUMENTS__') {
        throw 'STAGED_UNEXPECTED_BUSINESS_START'
    }
    $script:fixtureApprovedAPIArguments = (@(
        @([string]$Service.ScriptPath) + @($Service.Arguments) | ForEach-Object {
            ConvertTo-NativeProcessArgument -Argument ([string]$_)
        }
    ) -join ' ')
    try {
        & $script:fixtureRealBusinessStart -Service $Service -SkipExistingCheck:$SkipExistingCheck
    } finally {
        $script:fixtureApprovedAPIArguments = $null
    }
}
