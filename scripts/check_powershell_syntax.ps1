#requires -Version 5.1
<#
.SYNOPSIS
Parse every repository PowerShell script without executing it.
#>
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-I2Root
$failed = @()
$checked = 0

Get-ChildItem -LiteralPath (Join-Path $root 'scripts') -Recurse -Filter '*.ps1' |
    ForEach-Object {
        $checked++
        $tokens = $null
        $errors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
        if ($errors.Count -gt 0) {
            $failed += [pscustomobject]@{ Path = $_.FullName; Errors = $errors }
        }
    }

if ($failed.Count -gt 0) {
    foreach ($item in $failed) {
        Write-I2Fail "Syntax error: $($item.Path)"
        foreach ($error in $item.Errors) {
            Write-Host ("  line {0}: {1}" -f $error.Extent.StartLineNumber, $error.Message)
        }
    }
    exit 1
}

Write-I2Ok "PowerShell syntax: $checked script(s) parsed"
exit 0
