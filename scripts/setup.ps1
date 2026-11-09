#requires -Version 5.1
<#
.SYNOPSIS
Prepare the pinned toolchain and Python environment for inFAMOUS 2 Recomp.

.DESCRIPTION
Checks Git, Python, CMake, Ninja, clang-cl, Visual Studio Build Tools, the
Windows SDK and the pinned PS3Recomp checkout. With -InstallDependencies it can
install public, redistributable tools through winget. It never downloads game
files and never runs the game.

The script is idempotent: an existing checkout at the pinned revision and an
existing working virtual environment are reused. A modified SDK is reported,
not reset. Patches are applied only through scripts/patch_stack.py, which
verifies the expected prefix first.

.EXAMPLE
./scripts/setup.ps1 -CheckOnly
./scripts/setup.ps1 -SkipPythonEnv
./scripts/setup.ps1 -InstallDependencies
#>
param(
    [switch]$CheckOnly,
    [switch]$InstallDependencies,
    [switch]$SkipSdk,
    [switch]$SkipPatches,
    [switch]$SkipPythonEnv,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'lib\common.ps1')

$root = Get-I2Root
$pinned = Get-I2PinnedRevision
$failures = [System.Collections.Generic.List[string]]::new()
$warnings = [System.Collections.Generic.List[string]]::new()

function Add-Failure([string]$Message) { $script:failures.Add($Message) | Out-Null }
function Add-Warning([string]$Message) { $script:warnings.Add($Message) | Out-Null }

function Get-VersionLine([string]$Command, [string[]]$Arguments) {
    try {
        $output = & $Command @Arguments 2>&1 | Select-Object -First 1
        return [string]$output
    } catch {
        return $null
    }
}

Write-I2Step 'Checking the local toolchain'
$envInfo = Set-I2BuildEnvironment -Quiet

# Git -----------------------------------------------------------------------
$git = Get-I2CommandPath 'git.exe'
if ($git) {
    Write-I2Ok "git             $(Get-VersionLine $git @('--version'))"
} else {
    Add-Failure 'git was not found. Install Git for Windows (winget: Git.Git).'
}

# Python --------------------------------------------------------------------
$python = Get-I2CommandPath 'python.exe'
if ($python) {
    $pythonVersion = & $python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
    if ($LASTEXITCODE -eq 0 -and [version]$pythonVersion -ge [version]'3.11.0') {
        Write-I2Ok "python          $pythonVersion ($python)"
    } else {
        Add-Failure "Python 3.11 or newer is required; found '$pythonVersion'."
    }
} else {
    Add-Failure 'python was not found. Install Python 3.11+ (winget: Python.Python.3.11).'
}

# CMake ---------------------------------------------------------------------
$cmake = Get-I2CommandPath 'cmake.exe'
if ($cmake) {
    $cmakeVersionText = Get-VersionLine $cmake @('--version')
    if ($cmakeVersionText -match 'cmake version\s+([0-9]+\.[0-9]+\.[0-9]+)') {
        $cmakeVersion = [version]$matches[1]
        if ($cmakeVersion -ge [version]'3.25.0') {
            Write-I2Ok "cmake           $cmakeVersion"
        } else {
            Add-Failure "CMake 3.25+ is required for CMakePresets.json; found $cmakeVersion."
        }
    }
} else {
    Add-Failure 'cmake was not found (winget: Kitware.CMake).'
}

# Ninja ---------------------------------------------------------------------
$ninja = Get-I2CommandPath 'ninja.exe'
if ($ninja) {
    Write-I2Ok "ninja           $(& $ninja --version)"
} else {
    Add-Failure 'ninja was not found (winget: Ninja-build.Ninja).'
}

# clang-cl ------------------------------------------------------------------
if ($envInfo.ClangCl) {
    $clangVersion = Get-VersionLine $envInfo.ClangCl @('--version')
    Write-I2Ok "clang-cl        $clangVersion"
} else {
    Add-Failure 'clang-cl was not found. Put LLVM in tools/llvm or install LLVM (winget: LLVM.LLVM).'
}

# Visual Studio / SDK -------------------------------------------------------
if ($envInfo.VisualStudio) {
    Write-I2Ok "Visual Studio   $($envInfo.VisualStudio)"
} else {
    Add-Failure 'Visual Studio Build Tools with the C++ workload were not found.'
}

if ($envInfo.Rc) {
    Write-I2Ok "resource comp.  $($envInfo.Rc)"
} else {
    Add-Failure 'rc.exe was not found. Install the Windows 10/11 SDK.'
}

$sdkLib = $null
if ($env:WindowsSdkDir -and $env:WindowsSDKVersion) {
    $sdkLib = Join-Path $env:WindowsSdkDir ("Lib\" + $env:WindowsSDKVersion.TrimEnd('\') + "\um\x64\d3dcompiler.lib")
}
if ($sdkLib -and (Test-Path -LiteralPath $sdkLib)) {
    Write-I2Ok "D3DCompiler     $sdkLib"
} else {
    Add-Warning 'd3dcompiler.lib was not located; the GPU-optional D3D12 fixtures may not link.'
}

# Optional free tools -------------------------------------------------------
if (Get-I2CommandPath 'python.exe') {
    $pythonForModules = if (Test-Path (Join-Path $root '.venv\Scripts\python.exe')) {
        Join-Path $root '.venv\Scripts\python.exe'
    } else { $python }
    if (Test-I2PythonModule $pythonForModules 'pytest') {
        Write-I2Ok 'pytest          available'
    } else {
        Add-Warning 'pytest is not importable; run setup.ps1 without -SkipPythonEnv or install requirements-dev.txt.'
    }
}

if ($InstallDependencies -and $failures.Count -gt 0) {
    Write-I2Step 'Installing missing public tools with winget (user scope)'
    $winget = Get-I2CommandPath 'winget.exe'
    if (-not $winget) {
        Add-Warning 'winget is unavailable; install the missing tools manually.'
    } else {
        $packages = @(
            @{ Id = 'Git.Git'; Need = -not $git },
            @{ Id = 'Python.Python.3.11'; Need = -not $python },
            @{ Id = 'Kitware.CMake'; Need = -not $cmake },
            @{ Id = 'Ninja-build.Ninja'; Need = -not $ninja },
            @{ Id = 'LLVM.LLVM'; Need = -not $envInfo.ClangCl }
        )
        foreach ($package in $packages) {
            if (-not $package.Need) { continue }
            Write-I2Step "winget install $($package.Id)"
            & $winget install --id $package.Id --scope user --silent `
                --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) {
                Add-Warning "winget could not install $($package.Id); install it manually."
            }
        }
        Write-I2Warn 'Visual Studio Build Tools and the Windows SDK may require administrator rights and were not installed automatically.'
    }
}

# Pinned SDK ----------------------------------------------------------------
$sdk = Join-Path $root 'tools\ps3recomp'
if (-not $SkipSdk) {
    if (-not (Test-Path -LiteralPath (Join-Path $sdk 'CMakeLists.txt'))) {
        if ($CheckOnly) {
            Add-Failure "PS3Recomp is missing at $sdk. Run setup.ps1 without -CheckOnly to clone the pinned revision."
        } elseif ($git) {
            Write-I2Step "Cloning PS3Recomp at $pinned"
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $sdk) | Out-Null
            & $git clone --filter=blob:none https://github.com/sp00nznet/ps3recomp.git $sdk
            if ($LASTEXITCODE -ne 0) {
                Add-Failure "git clone failed for $sdk."
            } else {
                & $git -C $sdk checkout --detach $pinned
                if ($LASTEXITCODE -ne 0) { Add-Failure "Could not check out PS3Recomp $pinned." }
            }
        }
    } else {
        $head = (& $git -C $sdk rev-parse HEAD 2>$null).Trim()
        if ($head -ne $pinned) {
            Add-Failure "PS3Recomp is at $head, expected $pinned. Refusing to reset a developer checkout; check it out manually."
        } else {
            Write-I2Ok "PS3Recomp       $head"
        }
        $dirty = @(& $git -C $sdk status --porcelain 2>$null)
        if ($dirty.Count -gt 0) {
            Write-I2Warn "PS3Recomp has $($dirty.Count) modified/untracked path(s); these may include local work outside the ordered patch series."
        }
    }
} else {
    Write-I2Warn 'SDK checkout check skipped (-SkipSdk).'
}

# Patch stack ---------------------------------------------------------------
if (-not $SkipPatches -and -not $CheckOnly -and (Test-Path (Join-Path $sdk 'CMakeLists.txt'))) {
    Write-I2Step 'Verifying the ordered patch stack'
    $patchJson = & $python (Join-Path $root 'scripts\patch_stack.py') --sdk $sdk --check --json 2>&1
    $patchCheck = $null
    try { $patchCheck = ($patchJson -join "`n") | ConvertFrom-Json } catch { }
    if ($LASTEXITCODE -eq 0) {
        Write-I2Ok 'SDK matches the complete ordered patch stack'
    } elseif ($patchCheck -and $null -ne $patchCheck.applied_prefix) {
        Write-I2Warn "SDK is a partial stack prefix ($($patchCheck.applied_prefix)/$($patchCheck.expected_prefix)); applying the remaining patches."
        & $python (Join-Path $root 'scripts\patch_stack.py') --sdk $sdk --apply
        if ($LASTEXITCODE -ne 0) {
            Add-Failure 'The patch stack could not be applied. Inspect scripts/patch_stack.py --check output.'
        }
    } else {
        $count = if ($patchCheck) { @($patchCheck.differing_files).Count } else { 'some' }
        Add-Warning "SDK diverges from the patch series in $count file(s). Local changes were preserved and nothing was reset. A fresh clone is required to reproduce the ordered tree exactly."
    }
} elseif ($CheckOnly) {
    Write-I2Warn 'Patch application skipped in -CheckOnly mode.'
}

# Python environment --------------------------------------------------------
$requirements = Join-Path $root 'requirements-dev.txt'
if (-not $SkipPythonEnv -and -not $CheckOnly -and $python) {
    $venvPython = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython) -or $Force) {
        Write-I2Step 'Creating the isolated Python environment (.venv)'
        & $python -m venv (Join-Path $root '.venv')
        if ($LASTEXITCODE -ne 0) { Add-Failure 'Could not create .venv.' }
    }
    if (Test-Path -LiteralPath $venvPython) {
        $needsInstall = $Force -or -not (Test-I2PythonModule $venvPython 'pytest')
        if ($needsInstall -and (Test-Path -LiteralPath $requirements)) {
            Write-I2Step 'Installing Python test dependencies'
            & $venvPython -m pip install --disable-pip-version-check -r $requirements
            if ($LASTEXITCODE -ne 0) { Add-Failure 'pip install failed.' }
        }
        if (Test-I2PythonModule $venvPython 'pytest') {
            Write-I2Ok "python env      $venvPython"
        }
    }
} else {
    Write-I2Warn 'Python environment setup skipped.'
}

# Report --------------------------------------------------------------------
$reportDir = Join-Path $root 'build\setup'
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$report = [ordered]@{
    generated_at = (Get-Date).ToUniversalTime().ToString('o')
    pinned_sdk_revision = $pinned
    tools = [ordered]@{
        git = $git
        python = $python
        cmake = $cmake
        ninja = $ninja
        clang_cl = $envInfo.ClangCl
        resource_compiler = $envInfo.Rc
        visual_studio = $envInfo.VisualStudio
    }
    warnings = @($warnings)
    failures = @($failures)
}
$report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $reportDir 'environment.json') -Encoding UTF8

Write-Host ''
if ($warnings.Count -gt 0) {
    Write-I2Step 'Warnings'
    foreach ($warning in $warnings) { Write-I2Warn $warning }
}
if ($failures.Count -gt 0) {
    Write-I2Step 'Missing requirements'
    foreach ($failure in $failures) { Write-I2Fail $failure }
    Write-Host ''
    Write-Host 'Nothing was changed by -CheckOnly. Fix the items above and re-run.' -ForegroundColor Yellow
    exit 1
}

Write-I2Step 'Setup complete'
Write-Host 'Next:'
Write-Host '  ./scripts/build.ps1 -Target tests'
Write-Host '  ./scripts/test.ps1 -Suite all'
Write-Host '  ./scripts/build.ps1 -Target port    # requires local game inputs'
exit 0
