# Adapted from PR #321: use the real parser; never execute inspected sources.
param([Parameter(Mandatory = $true)][string]$Root,
      [Parameter(Mandatory = $true)][string]$Selection)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$resolved = [System.IO.Path]::GetFullPath($Root)
$manifest = Get-Content -LiteralPath $Selection -Raw -Encoding UTF8 | ConvertFrom-Json
$paths = @($manifest.views.PSObject.Properties.Value.files | Where-Object { $_ -like '*.ps1' } | Sort-Object -Unique)
$symbols = [System.Collections.Generic.List[object]]::new()
$edges = [System.Collections.Generic.List[object]]::new()
foreach ($path in $paths) {
    $full = [System.IO.Path]::GetFullPath((Join-Path $resolved $path))
    if (-not $full.StartsWith($resolved.TrimEnd('\','/') + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'ARCHITECTURE_PATH_ESCAPE' }
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($full, [ref]$tokens, [ref]$errors)
    if (@($errors).Count) { throw "ARCHITECTURE_PARSE_FAILED:$path" }
    foreach ($node in $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]}, $true)) {
        $symbols.Add(@{id="$path`::$($node.Name)";name=$node.Name;path=$path;line=$node.Extent.StartLineNumber;end_line=$node.Extent.EndLineNumber;language='powershell';syntactic_owner=$path})
    }
    foreach ($node in $ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst]}, $true)) {
        $parent = $node.Parent
        while ($parent -and $parent -isnot [System.Management.Automation.Language.FunctionDefinitionAst]) { $parent = $parent.Parent }
        if (-not $parent) { continue }
        $name = $node.GetCommandName()
        if (-not $name) { $name = '<dynamic-command>' }
        $edges.Add(@{source="$path`::$($parent.Name)";target=$name;kind='calls';line=$node.Extent.StartLineNumber;resolution='UNKNOWN';extractor='powershell-ast';binding='Runtime command resolution is not proven by AST'})
    }
}
@{symbols=@($symbols);edges=@($edges)} | ConvertTo-Json -Depth 8 -Compress
