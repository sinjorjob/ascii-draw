# ASCII Draw — installer
# Copies this skill into the current user's Claude Code skills directory.
# No admin rights, no developer mode, no symlinks — pure Copy-Item.
# Works on locked-down corporate machines.
#
# Usage:
#   # Default: copy to $env:USERPROFILE\.claude\skills\ascii-draw\
#   powershell -NoProfile -ExecutionPolicy Bypass -File <source>\scripts\install.ps1
#
#   # Override destination (e.g., shared CLAUDE_HOME):
#   powershell -File install.ps1 -Destination "D:\team-claude\skills"
#
#   # Force overwrite without prompt:
#   powershell -File install.ps1 -Force
#
# Returns a single JSON line on stdout (last line) for programmatic parsing:
#   {"status":"installed","path":"...","action":"created|updated|already_correct"}
#   {"status":"error","reason":"..."}

[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $env:USERPROFILE '.claude\skills'),
    [switch]$Force,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Resolve source: this script lives in <skill>/scripts/, so the skill root is
# the parent directory. SKILL.md must be present there.
# ---------------------------------------------------------------------------
$SkillRoot = Split-Path -Parent $PSScriptRoot
$SkillName = Split-Path -Leaf $SkillRoot          # "ascii-draw"
$SkillManifest = Join-Path $SkillRoot 'SKILL.md'
$Target = Join-Path $Destination $SkillName

function Out-Json($obj) {
    Write-Output ($obj | ConvertTo-Json -Compress)
}
function Say($msg, $color = 'White') {
    if (-not $Quiet) { Write-Host $msg -ForegroundColor $color }
}

# ---------------------------------------------------------------------------
# 1. Sanity-check source
# ---------------------------------------------------------------------------
if (-not (Test-Path $SkillManifest)) {
    Out-Json @{
        status = 'error'
        reason = "SKILL.md not found at '$SkillManifest'. install.ps1 must live in <skill>/scripts/."
    }
    exit 1
}

Say ""
Say "ASCII Draw — Skill Installer" 'Cyan'
Say "  Source     : $SkillRoot"
Say "  Target     : $Target"
Say ""

# ---------------------------------------------------------------------------
# 2. Same-location detection — running from inside ~/.claude/skills/
# ---------------------------------------------------------------------------
try {
    $srcReal = (Resolve-Path -LiteralPath $SkillRoot).Path.TrimEnd('\')
} catch {
    $srcReal = $SkillRoot.TrimEnd('\')
}
if (Test-Path $Target) {
    try {
        $dstReal = (Resolve-Path -LiteralPath $Target).Path.TrimEnd('\')
    } catch {
        $dstReal = $Target.TrimEnd('\')
    }
    if ($srcReal -ieq $dstReal) {
        Say "✓ Already installed at this exact path. Nothing to do." 'Green'
        Out-Json @{
            status = 'installed'
            path   = $Target
            action = 'already_correct'
        }
        exit 0
    }
}

# ---------------------------------------------------------------------------
# 3. Confirm overwrite if target exists
# ---------------------------------------------------------------------------
$action = 'created'
if (Test-Path $Target) {
    $action = 'updated'
    if (-not $Force) {
        Say "⚠ Target already exists: $Target" 'Yellow'
        Say "  Re-install (delete + copy)?  [y/N]: " 'Yellow'
        $resp = Read-Host
        if ($resp -notmatch '^[Yy]') {
            Out-Json @{ status = 'error'; reason = 'User cancelled overwrite' }
            exit 1
        }
    }
    Say "  Removing previous installation..." 'Gray'
    Remove-Item -Recurse -Force $Target
}

# ---------------------------------------------------------------------------
# 4. Ensure parent dir, copy
# ---------------------------------------------------------------------------
$null = New-Item -ItemType Directory -Force -Path $Destination

try {
    Copy-Item -Path $SkillRoot -Destination $Target -Recurse -Force
    Get-ChildItem -Path $SkillRoot -Recurse -Force -Hidden -ErrorAction SilentlyContinue |
        ForEach-Object {
            $rel = $_.FullName.Substring($SkillRoot.Length).TrimStart('\')
            $dest = Join-Path $Target $rel
            if (-not (Test-Path $dest)) {
                Copy-Item -LiteralPath $_.FullName -Destination $dest -Force
            }
        }
} catch {
    Out-Json @{
        status = 'error'
        reason = "Copy failed: $($_.Exception.Message)"
    }
    exit 1
}

# ---------------------------------------------------------------------------
# 5. Verify
# ---------------------------------------------------------------------------
$installedManifest = Join-Path $Target 'SKILL.md'
if (-not (Test-Path $installedManifest)) {
    Out-Json @{
        status = 'error'
        reason = "Copy reported success but SKILL.md is missing at '$installedManifest'."
    }
    exit 1
}

# Strip __pycache__ (stale bytecode from dev environment, not needed)
$cache = Join-Path $Target '__pycache__'
if (Test-Path $cache) { Remove-Item -Recurse -Force $cache }

Say "✓ Installed to: $Target" 'Green'
Say ""
Say "Next steps:" 'Cyan'
Say "  1. Restart Claude Code (or close + reopen the terminal)."
Say "  2. Invoke with /ascii-draw or just say e.g. 「構成図を描いて」."
Say ""

Out-Json @{
    status = 'installed'
    path   = $Target
    action = $action
}
