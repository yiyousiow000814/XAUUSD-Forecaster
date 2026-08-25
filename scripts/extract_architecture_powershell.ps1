param([Parameter(Mandatory = $true)][string]$Root)

$ErrorActionPreference = "Stop"
$resolvedRoot = [System.IO.Path]::GetFullPath($Root)
$files = @(Get-ChildItem -LiteralPath (Join-Path $resolvedRoot "scripts") -Recurse -File |
    Where-Object { $_.Extension -in @(".ps1", ".psm1") } | Sort-Object FullName)
$facts = [System.Collections.Generic.List[object]]::new()
foreach ($file in $files) {
    $tokens = $null
    $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile(
        $file.FullName, [ref]$tokens, [ref]$errors
    )
    if (@($errors).Count -gt 0) {
        throw "PowerShell parser failed for $($file.FullName): $($errors[0].Message)"
    }
    $prefix = $resolvedRoot.TrimEnd("\") + "\"
    if (-not $file.FullName.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "PowerShell source escaped repository root"
    }
    $path = $file.FullName.Substring($prefix.Length).Replace("\", "/")
    $nodes = @($ast.FindAll({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -or
        $node -is [System.Management.Automation.Language.CommandAst]
    }, $true))
    foreach ($node in $nodes) {
        $type = $null
        $name = $null
        if ($node -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
            $type = "powershell_function"
            $name = [string]$node.Name
        } elseif ($node.InvocationOperator -eq
            [System.Management.Automation.Language.TokenKind]::Dot) {
            $type = "powershell_dot_source"
            $name = [string]$node.CommandElements[0].Value
        } elseif ([string]$node.GetCommandName() -eq "Start-Process") {
            $type = "powershell_process"
            $name = "Start-Process"
        }
        if ($type) {
            $facts.Add([ordered]@{
                id = "$type`:$path`:$($node.Extent.StartLineNumber)`:$name"
                type = $type
                path = $path
                line = [int]$node.Extent.StartLineNumber
                end_line = [int]$node.Extent.EndLineNumber
                name = $name
                extractor = "powershell-ast"
                certainty = "EXACT"
            })
        }
    }
}
@{schema = "architecture-powershell-evidence-v1"; facts = @($facts)} |
    ConvertTo-Json -Depth 8 -Compress
