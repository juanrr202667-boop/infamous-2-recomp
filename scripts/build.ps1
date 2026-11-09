#requires -Version 5.1
<#
.SYNOPSIS
Configure and build the synthetic tests or the full native port.

.DESCRIPTION
`-Target tests` never reads game files and never needs an EBOOT/ELF. It builds
the pinned PS3Recomp runtime plus the fixtures in tests/native against the
synthetic PPU header. `-Target port` requires a real local lift and is not
usable from a clean public checkout.

.EXAMPLE
./scripts/build.ps1 -Target tests
./scripts/build.ps1 -Target tests -Clean -Jobs 8
./scripts/build.ps1 -Target port
#>
param(
    [ValidateSet('tests', 'port')]
    [string]$Target = 'tests',
    [string]$BuildDir,
    [string]$RecompDir,
    [string]$SdkDir,
    [int]$Jobs = 0,
    [switch]$Clean,
    [switch]$ConfigureOnly,
    [switch]$NoVsEnvironment
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')

$root = Get-I2Root
$envInfo = Set-I2BuildEnvironment -NoVsEnvironment:$NoVsEnvironment
if ($Jobs -le 0) { $Jobs = [Math]::Max(1, [Environment]::ProcessorCount - 1) }

$cmake = Get-I2CommandPath 'cmake.exe'
if (-not $cmake) { throw 'cmake.exe was not found. Run scripts/setup.ps1 first.' }

if (-not $SdkDir) { $SdkDir = Join-Path $root 'tools\ps3recomp' }
if (-not (Test-Path -LiteralPath (Join-Path $SdkDir 'CMakeLists.txt'))) {
    throw "PS3Recomp not found at $SdkDir. Run scripts/setup.ps1 first."
}

if (-not $BuildDir) {
    $BuildDir = if ($Target -eq 'tests') {
        Join-Path $root 'build\windows-tests'
    } else {
        Join-Path $root 'build\windows-port'
    }
}
$BuildDir = [IO.Path]::GetFullPath($BuildDir)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $root 'build'))
if (-not $BuildDir.StartsWith($buildRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean/write a build directory outside $buildRoot : $BuildDir"
}

if ($Clean -and (Test-Path -LiteralPath $BuildDir)) {
    Write-I2Step "Removing $BuildDir"
    Remove-Item -LiteralPath $BuildDir -Recurse -Force
}

$toolchain = Join-Path $root 'cmake\toolchain-windows-clang-cl.cmake'
$configureArgs = @(
    '-S', $root,
    '-B', $BuildDir,
    '-G', 'Ninja',
    "-DCMAKE_TOOLCHAIN_FILE=$toolchain",
    '-DCMAKE_BUILD_TYPE=Debug',
    '-DCMAKE_EXPORT_COMPILE_COMMANDS=ON',
    "-DPS3RECOMP_DIR=$SdkDir"
)

if ($Target -eq 'tests') {
    $configureArgs += '-DPORT_BUILD_TESTS_ONLY=ON'
} else {
    if (-not $RecompDir) { $RecompDir = Join-Path $root 'build\game\recomp' }
    if (-not (Test-Path -LiteralPath (Join-Path $RecompDir 'ppu_recomp.h'))) {
        throw "No ppu_recomp.h in $RecompDir. Build the full port only with local generated sources; use -Target tests for the public test suite."
    }
    $configureArgs += @(
        "-DRECOMP_DIR=$RecompDir",
        '-DPORT_RUNNER_TESTS=ON',
        '-DPORT_STRICT_SDK_REVISION=ON'
    )
}

Write-I2Step "Configuring $Target build"
Write-Host "  build dir: $BuildDir"
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $cmake @configureArgs
    $configureCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previous
}
if ($configureCode -ne 0) { throw 'CMake configuration failed.' }
if ($ConfigureOnly) {
    Write-I2Step 'Configure only; build not started.'
    exit 0
}

Write-I2Step "Building $Target with $Jobs job(s)"
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    & $cmake --build $BuildDir --parallel $Jobs
    $buildCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $previous
}
if ($buildCode -ne 0) { throw 'Build failed.' }

Write-I2Step 'Build complete'
if ($Target -eq 'tests') {
    Write-Host "Next: ./scripts/test.ps1 -Suite all -BuildDir `"$BuildDir`""
}
exit 0
