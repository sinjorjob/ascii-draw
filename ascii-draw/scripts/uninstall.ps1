# ASCII Draw — uninstaller
# Removes the installed skill from $env:USERPROFILE\.claude\skills\ascii-draw\
# (or a custom -Destination). No admin rights required.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File uninstall.ps1
#   powershell -File uninstall.ps1 -Destination "D:\team-claude\skills" -Force

[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE '.claude\skills'),
    [switch]$Force,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$SkillName = 'ascii-draw'
$Target    = Join-Path $Destination $SkillName

function Out-Json($obj) { Write-Output ($obj | ConvertTo-Json -Compress) }
function Say($msg, $color = 'White') { if (-not $Quiet) { Write-Host $msg -ForegroundColor $color } }

if (-not (Test-Path $Target)) {
    Say "Nothing to uninstall — '$Target' does not exist." 'Gray'
    Out-Json @{ status = 'not_installed'; path = $Target }
    exit 0
}

# Stop any running server on port 8765 to release file handles
$conn = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue
if ($conn) {
    Say "Stopping running ASCII Draw server (port 8765, PID $($conn.OwningProcess))..." 'Gray'
    Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 300
}

if (-not $Force) {
    Say "Remove $Target ?  [y/N]: " 'Yellow'
    $resp = Read-Host
    if ($resp -notmatch '^[Yy]') {
        Out-Json @{ status = 'error'; reason = 'User cancelled' }
        exit 1
    }
}

try {
    Remove-Item -Recurse -Force $Target
    Say "✓ Removed: $Target" 'Green'
    Out-Json @{ status = 'uninstalled'; path = $Target }
} catch {
    Out-Json @{ status = 'error'; reason = $_.Exception.Message }
    exit 1
}
