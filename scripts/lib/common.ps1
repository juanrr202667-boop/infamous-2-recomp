# Shared helpers for scripts/setup.ps1, build.ps1 and test.ps1.
# Dot-source this file; it defines functions and does not modify the caller
# until Set-I2BuildEnvironment is called.

$script:I2Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$script:I2PinnedSdk = '5244db92669b54831c6d3ebb7a96d58c4dbfa6df'

# Native tools write progress and warnings to stderr (git clone, pytest, CMake).
# In PowerShell 7.3+ those records otherwise become terminating errors when a
# caller sets ErrorActionPreference=Stop, before the exit code can be checked.
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Get-I2Root { return $script:I2Root }
function Get-I2PinnedRevision { return $script:I2PinnedSdk }

function Write-I2Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}
function Write-I2Ok([string]$Message) {
    Write-Host "  OK  $Message" -ForegroundColor Green
}
function Write-I2Warn([string]$Message) {
    Write-Host "  !!  $Message" -ForegroundColor Yellow
}
function Write-I2Fail([string]$Message) {
    Write-Host " FAIL $Message" -ForegroundColor Red
}

function Test-I2Command([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-I2CommandPath([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    return $null
}

function Get-I2VsWhere {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft Visual Studio\Installer\vswhere.exe')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-I2VsInstall {
    $vswhere = Get-I2VsWhere
    if (-not $vswhere) { return $null }
    $path = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath 2>$null
    if ($LASTEXITCODE -eq 0 -and $path) { return ($path | Select-Object -First 1).Trim() }
    return $null
}

function Import-I2VsEnvironment {
    param([string]$VsInstall)
    if (-not $VsInstall) { $VsInstall = Get-I2VsInstall }
    if (-not $VsInstall) { return $false }

    $devCmd = Join-Path $VsInstall 'Common7\Tools\VsDevCmd.bat'
    if (-not (Test-Path -LiteralPath $devCmd)) { return $false }

    # Parse `set` output instead of trying to call vcvars in-process. Values may
    # contain '=', so split on the first one only.
    $lines = & cmd.exe /d /s /c "call `"$devCmd`" -arch=x64 -host_arch=x64 >nul && set"
    if ($LASTEXITCODE -ne 0) { return $false }
    foreach ($line in $lines) {
        if ($line -match '^([^=]+)=(.*)$') {
            Set-Item -Path ("env:" + $matches[1]) -Value $matches[2] -ErrorAction SilentlyContinue
        }
    }
    return $true
}

function Get-I2RcPath {
    $candidates = @()
    if ($env:I2_RC) { $candidates += $env:I2_RC }
    if ($env:WindowsSdkVerBinPath) {
        $candidates += (Join-Path $env:WindowsSdkVerBinPath 'x64\rc.exe')
    }
    if ($env:WindowsSdkDir) {
        $candidates += (Join-Path $env:WindowsSdkDir 'bin\10.0.22621.0\x64\rc.exe')
    }
    $sdkBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $sdkBin) {
        $candidates += (Get-ChildItem -Path (Join-Path $sdkBin '*\x64\rc.exe') `
            -ErrorAction SilentlyContinue | Sort-Object FullName -Descending |
            Select-Object -ExpandProperty FullName)
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return (Resolve-Path $candidate).Path }
    }
    $onPath = Get-I2CommandPath 'rc.exe'
    if ($onPath) { return $onPath }
    return $null
}

function Get-I2ClangCl {
    $candidates = @()
    if ($env:I2_CLANG_CL) { $candidates += $env:I2_CLANG_CL }
    $candidates += (Join-Path $script:I2Root 'tools\llvm\bin\clang-cl.exe')
    if ($env:VCINSTALLDIR) {
        $candidates += (Join-Path $env:VCINSTALLDIR 'Tools\Llvm\x64\bin\clang-cl.exe')
    }
    $onPath = Get-I2CommandPath 'clang-cl.exe'
    if ($onPath) { $candidates += $onPath }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return (Resolve-Path $candidate).Path }
    }
    return $null
}

function Set-I2BuildEnvironment {
    param(
        [switch]$Quiet,
        [switch]$NoVsEnvironment
    )

    $vs = $null
    if (-not $NoVsEnvironment) {
        $vs = Get-I2VsInstall
        if ($vs) {
            if (-not (Import-I2VsEnvironment -VsInstall $vs)) {
                if (-not $Quiet) { Write-I2Warn "Could not import the Visual Studio x64 environment from $vs" }
            }
        } elseif (-not $Quiet) {
            Write-I2Warn 'Visual Studio Build Tools not found; clang-cl will need INCLUDE/LIB from another source.'
        }
    }

    $tmp = Join-Path $script:I2Root 'build\.tmp'
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $env:TMP = $tmp
    $env:TEMP = $tmp

    $clang = Get-I2ClangCl
    if ($clang) {
        $llvmBin = Split-Path -Parent $clang
        $env:I2_CLANG_CL = $clang
        $env:CC = $clang
        $env:CXX = $clang
        if ($env:PATH -notlike "*$llvmBin*") { $env:PATH = "$llvmBin;$env:PATH" }
        if (-not $env:CMAKE_LINKER -and (Test-Path (Join-Path $llvmBin 'lld-link.exe'))) {
            $env:CMAKE_LINKER = Join-Path $llvmBin 'lld-link.exe'
        }
        if (-not $env:CMAKE_MT -and (Test-Path (Join-Path $llvmBin 'llvm-mt.exe'))) {
            $env:CMAKE_MT = Join-Path $llvmBin 'llvm-mt.exe'
        }
    }

    $rc = Get-I2RcPath
    if ($rc) { $env:I2_RC = $rc }

    return [pscustomobject]@{
        VisualStudio = $vs
        ClangCl = $clang
        Rc = $rc
        Tmp = $tmp
    }
}

function Get-I2Python {
    $venv = Join-Path $script:I2Root '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) { return $venv }
    return (Get-I2CommandPath 'python.exe')
}

function Test-I2PythonModule([string]$Python, [string]$Module) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Python -c "import $Module" 2>$null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $previous
    }
}
