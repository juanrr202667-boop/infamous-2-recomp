#requires -Version 5.1
<#
.SYNOPSIS
Run the Python and native synthetic test suites.

.DESCRIPTION
By default the native suite excludes GPU-optional and game-local tests. Those
tests are reported as excluded, not as passed. Use -IncludeGpu for the D3D12
WARP fixtures and -IncludeGameLocal only when the proprietary local inputs are
present.

.EXAMPLE
./scripts/test.ps1 -Suite all
./scripts/test.ps1 -Suite native -IncludeGpu
./scripts/test.ps1 -Regression
#>
param(
    [ValidateSet('python', 'native', 'all')]
    [string]$Suite = 'all',
    [string]$BuildDir,
    [int]$Jobs = 0,
    [switch]$IncludeGpu,
    [switch]$IncludeGameLocal,
    [switch]$Regression,
    [switch]$UpdateBaseline
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')

$root = Get-I2Root
$envInfo = Set-I2BuildEnvironment -Quiet
if ($Jobs -le 0) { $Jobs = [Math]::Max(1, [Environment]::ProcessorCount - 1) }

if (-not $BuildDir) { $BuildDir = Join-Path $root 'build\windows-tests' }
$BuildDir = [IO.Path]::GetFullPath($BuildDir)

$python = Get-I2Python
if (-not $python) { throw 'Python was not found. Run scripts/setup.ps1 first.' }

if ($envInfo.ClangCl) {
    # The Python fixtures that compile small C/C++ programs look for this first.
    $env:I2_CLANG_CL = $envInfo.ClangCl
}
if ($envInfo.Rc) { $env:I2_RC = $envInfo.Rc }

function Invoke-PythonSuite {
    Write-I2Step 'Python tests (pytest)'
    Push-Location $root
    $previous = $ErrorActionPreference
    # pytest writes its asyncio deprecation warning to stderr. With
    # ErrorActionPreference=Stop PowerShell turns that into a terminating
    # error before the summary is printed, so native output must be allowed.
    $ErrorActionPreference = 'Continue'
    try {
        & $python -m pytest tests -q -ra
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        Pop-Location
    }
    if ($code -ne 0) { throw "pytest failed with exit code $code." }
}

function Invoke-NativeSuite {
    if (-not (Test-Path -LiteralPath (Join-Path $BuildDir 'CTestTestfile.cmake'))) {
        throw "No configured native build at $BuildDir. Run ./scripts/build.ps1 -Target tests first."
    }
    Write-I2Step 'Native tests (CTest)'
    $ctestArgs = @('--test-dir', $BuildDir, '--output-on-failure', '--parallel', "$Jobs")
    if (-not $IncludeGameLocal) {
        $exclude = if ($IncludeGpu) { 'game-local' } else { 'gpu-optional|game-local' }
        $ctestArgs += @('-LE', $exclude)
        Write-Host "  excluded labels: $exclude (not run, not counted as pass)"
    }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & ctest @ctestArgs
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw "CTest failed with exit code $code." }
}

if ($Regression) {
    Write-I2Step 'Regression gate'
    $gateArgs = @(
        (Join-Path $root 'scripts\regression_gate.py'),
        '--build-dir', $BuildDir,
        '--ctest',
        '--pytest'
    )
    if ($IncludeGpu) { $gateArgs += '--include-gpu' }
    if ($UpdateBaseline) { $gateArgs += '--update-baseline' }
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $python @gateArgs
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) { throw "Regression gate failed with exit code $code." }
    exit 0
}

switch ($Suite) {
    'python' { Invoke-PythonSuite }
    'native' { Invoke-NativeSuite }
    'all' {
        Invoke-NativeSuite
        Invoke-PythonSuite
    }
}

Write-I2Step 'Test run complete'
Write-Host 'A skip or an excluded label is not a pass. See the counts above.'
exit 0
