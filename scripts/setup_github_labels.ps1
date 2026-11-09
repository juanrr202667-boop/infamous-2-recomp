#requires -Version 5.1
<#
.SYNOPSIS
Create or update the labels declared in .github/labels.yml.

.DESCRIPTION
Requires an authenticated GitHub CLI with write access to the repository. This
script is intentionally not run by CI; label changes are repository
administration, not a build step. Without gh or permission, it prints the manual
command and exits non-zero.

.EXAMPLE
./scripts/setup_github_labels.ps1 -Repository juanrr202667-boop/infamous-2-recomp
#>
param(
[string]$Repository = 'juanrr202667-boop/infamous-2-recomp'
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')
$root = Get-I2Root
$labelsFile = Join-Path $root '.github\labels.yml'

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-I2Warn 'gh (GitHub CLI) is not installed. Install it and authenticate, or create the labels in the web UI.'
    Write-Host "Declarations: $labelsFile"
    exit 2
}
& gh auth status 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-I2Warn 'gh is not authenticated. Run: gh auth login'
    exit 2
}

$entries = @()
$current = $null
foreach ($line in Get-Content -LiteralPath $labelsFile) {
    if ($line -match '^\s*-\s+name:\s*(.+?)\s*$') {
        if ($current) { $entries += $current }
        $current = [ordered]@{ name = $matches[1] }
    } elseif ($current -and $line -match '^\s+color:\s*[''"]?([0-9a-fA-F]{6})[''"]?\s*$') {
        $current.color = $matches[1]
    } elseif ($current -and $line -match '^\s+description:\s*(.+?)\s*$') {
        $current.description = $matches[1]
    }
}
if ($current) { $entries += $current }

foreach ($entry in $entries) {
    Write-I2Step "Label: $($entry.name)"
    & gh label create $entry.name --repo $Repository --color $entry.color `
        --description $entry.description --force
    if ($LASTEXITCODE -ne 0) {
        Write-I2Warn "Could not create/update label '$($entry.name)'. Check repository write permission."
        exit 1
    }
}

Write-I2Ok "Processed $($entries.Count) labels."
Write-Host 'Issue templates are files under .github/ISSUE_TEMPLATE/; GitHub reads them from the default branch.'
Write-Host 'Discussions, if enabled, still need categories configured in repository settings.'
exit 0
