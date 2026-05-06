# ASCII Draw — launcher
# Returns a single JSON line on stdout describing the result:
#   {"status":"already_running","port":8765,"url":"http://127.0.0.1:8765"}
#   {"status":"started",         "port":8765,"url":"http://127.0.0.1:8765","pid":1234}
#   {"status":"error",           "reason":"..."}
#
# Self-contained skill layout:
#   <skill>/scripts/launch.ps1      ← this file
#   <skill>/scripts/server.py       ← Python HTTP bridge (executed)
#   <skill>/assets/index.html       ← UI served at GET /
#   <skill>/SKILL.md                ← skill manifest
# Everything is resolved relative to $PSScriptRoot, so the folder can be
# copied or installed anywhere and still work.
#
# Behavior:
#   1. Probe TCP 127.0.0.1:8765
#   2. If reachable → already_running
#   3. Otherwise launch `py <skill>/server.py` in a minimized window
#   4. Poll the port for up to ~8s; report success or timeout
#
# This script never throws; all paths emit a single JSON status line.

[CmdletBinding()]
param(
    [int]$Port = 8765,
    [int]$WaitMs = 8000
)

$ErrorActionPreference = 'Stop'

# Resolve paths from this script's location. Works wherever the skill folder
# is deployed (project tree, ~/.claude/skills/, network share, anywhere).
$SkillRoot    = Split-Path -Parent $PSScriptRoot     # <skill>
$ServerScript = Join-Path $PSScriptRoot 'server.py'  # <skill>/scripts/server.py
# Inherit the caller's working directory so claude CLI's Read/Glob/Grep
# resolve against the user's project. Fall back to skill root if the caller
# is sitting inside scripts/ (avoids confusing same-dir state).
$CallerCwd = (Get-Location).Path
if ($CallerCwd -ieq $PSScriptRoot) { $CallerCwd = $SkillRoot }

function Out-Json($obj) {
    Write-Output ($obj | ConvertTo-Json -Compress)
}

function Test-Port {
    param([int]$P)
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect('127.0.0.1', $P, $null, $null)
        $reachable = $iar.AsyncWaitHandle.WaitOne(250, $false)
        if ($reachable -and $client.Connected) {
            $client.EndConnect($iar) | Out-Null
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        if ($client) { try { $client.Close() } catch {} }
    }
}

# ----- 1. Already running? -----
if (Test-Port -P $Port) {
    Out-Json @{
        status = 'already_running'
        port   = $Port
        url    = "http://127.0.0.1:$Port"
    }
    exit 0
}

# ----- 2. Validate environment -----
if (-not (Test-Path $ServerScript)) {
    Out-Json @{
        status = 'error'
        reason = "server.py not found at: $ServerScript (expected at <skill>/scripts/server.py under root '$SkillRoot')"
    }
    exit 1
}

$pyCmd = Get-Command py -ErrorAction SilentlyContinue
if (-not $pyCmd) {
    Out-Json @{
        status = 'error'
        reason = "'py' command not found. Install Python from python.org (the official installer ships the 'py' launcher)."
    }
    exit 1
}

# ----- 3. Launch server (minimized window so user can see logs / kill it) -----
$proc = $null
try {
    $proc = Start-Process `
        -FilePath 'py' `
        -ArgumentList @($ServerScript) `
        -WorkingDirectory $CallerCwd `
        -WindowStyle Minimized `
        -PassThru
} catch {
    Out-Json @{
        status = 'error'
        reason = "Failed to launch py server.py: $($_.Exception.Message)"
    }
    exit 1
}

# ----- 4. Wait for the port to be ready -----
$deadline = (Get-Date).AddMilliseconds($WaitMs)
$ready = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 200
    if (Test-Port -P $Port) { $ready = $true; break }
    if ($proc -and $proc.HasExited) {
        Out-Json @{
            status = 'error'
            reason = "Server process exited prematurely (exit code $($proc.ExitCode)). Check the minimized window for the traceback."
            pid    = $proc.Id
        }
        exit 1
    }
}

if ($ready) {
    Out-Json @{
        status = 'started'
        port   = $Port
        url    = "http://127.0.0.1:$Port"
        pid    = $proc.Id
    }
    exit 0
} else {
    Out-Json @{
        status = 'error'
        reason = "Server did not respond on port $Port within $($WaitMs)ms. Check the minimized server window."
        pid    = $proc.Id
    }
    exit 1
}
