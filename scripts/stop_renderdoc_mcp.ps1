<# Stops only the installed DCC-MCP worker currently listening on loopback 9766. #>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$adapter = (Join-Path $env:LOCALAPPDATA 'Programs/RecompDiagnostics/renderdoc-venv/Scripts/dcc-mcp-renderdoc.exe') -replace '/', '\'
$python = (Join-Path $env:LOCALAPPDATA 'Programs/RecompDiagnostics/renderdoc-venv/Scripts/python.exe') -replace '/', '\'
$listeners = @(Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 9766 -State Listen -ErrorAction SilentlyContinue)
if (!$listeners) {
    Write-Host 'RenderDoc MCP is not active on this port.'
    return
}

$stopped = $false
foreach ($listener in $listeners) {
    $worker = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if (!$worker -or $worker.ExecutablePath -ne $python -or !$worker.CommandLine.Contains($adapter)) {
        throw 'Port 9766 belongs to another process. No process was stopped.'
    }
    Stop-Process -Id $worker.ProcessId
    $stopped = $true
}
if ($stopped) { Write-Host 'RenderDoc MCP stopped.' }
