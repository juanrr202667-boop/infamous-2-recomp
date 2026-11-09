$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot '../../../scripts/lib/common.ps1')
if (-not (Import-I2VsEnvironment)) { Write-Output 'Visual Studio environment unavailable'; exit 1 }
$root = Get-I2Root
$env:TEMP = Join-Path $root 'build/sprint-temp'
$env:TMP = $env:TEMP
$clang = Get-I2ClangCl
$rc = Get-I2RcPath
if (-not $clang -or -not $rc) { Write-Output 'Clang and resource compiler are required'; exit 1 }
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$build = Join-Path $root 'build/spurs-abi-fixture'
& cmake -S $PSScriptRoot -B $build -G Ninja "-DCMAKE_C_COMPILER=$clang" "-DCMAKE_CXX_COMPILER=$clang" "-DCMAKE_RC_COMPILER=$rc" '-DCMAKE_BUILD_TYPE=Release'
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& cmake --build $build --parallel 2
exit $LASTEXITCODE
