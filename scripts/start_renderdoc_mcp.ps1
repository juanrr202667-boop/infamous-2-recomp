<# Starts the installed RenderDoc adapter. Its MCP worker owns port 9766.
   No automatic downloads, gateway daemon, remote interface, or login startup.
   See stop_renderdoc_mcp.ps1 after exiting. Captures stay local. #>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$diagnosticsRoot = Join-Path $env:LOCALAPPDATA 'Programs/RecompDiagnostics'
$adapter = Join-Path $diagnosticsRoot 'renderdoc-venv/Scripts/dcc-mcp-renderdoc.exe'
$renderdocCommand = 'C:/Program Files/RenderDoc/renderdoccmd.exe'
if (!(Test-Path -LiteralPath $adapter) -or !(Test-Path -LiteralPath $renderdocCommand)) {
    throw 'RenderDoc or its MCP adapter is missing. See docs/instalacao-diagnostico.md.'
}
if (Get-NetTCPConnection -LocalPort 9766 -State Listen -ErrorAction SilentlyContinue) {
    throw 'Port 9766 is already in use. Check the existing process before starting another server.'
}

$settings = @{
    DCC_MCP_RENDERDOC_CMD = $renderdocCommand
    DCC_MCP_RENDERDOC_PORT = '9766'
    DCC_MCP_RENDERDOC_AUTO_DOWNLOAD = '0'
    DCC_MCP_GATEWAY_PORT = '0'
    DCC_MCP_GATEWAY_REMOTE_PORT = '0'
    DCC_MCP_GATEWAY_HOST = '127.0.0.1'
}
$previous = @{}
foreach ($name in $settings.Keys) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
}
try {
    Write-Host 'RenderDoc MCP: http://127.0.0.1:9766/mcp — Ctrl+C to stop.'
    & $adapter
    if ($LASTEXITCODE -ne 0) { throw "RenderDoc MCP exited with code $LASTEXITCODE." }
}
finally {
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
