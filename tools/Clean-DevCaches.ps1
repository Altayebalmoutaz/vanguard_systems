<#
.SYNOPSIS
    Repeatable, safe-by-default maintenance that reclaims disk by clearing
    regenerable Cursor and dev-toolchain caches.

.DESCRIPTION
    Reports C: free space before/after and reclaims space from:
      - Cursor: state.vscdb.backup, Chromium caches, stale CachedData versions,
        orphaned workspaceStorage, User\History, logs, Crashpad.
      - Toolchain: npm cache, %LOCALAPPDATA%\Temp.
      - OneDrive: trace logs (known multi-GB bloat bug; always safe, regrows).
      - System: Recycle Bin (all drives), optional `docker system prune`.

    NEVER deletes: the current state.vscdb, settings/keybindings/snippets,
    installed extensions, or .cursor\projects (agent transcripts).

    If Cursor is running, Cursor-data cleanup is skipped automatically (those
    files are in use); toolchain/Temp/RecycleBin cleanup still runs.

.PARAMETER IncludeDocker
    Also run `docker system prune -f` (removes dangling images/containers/networks).

.PARAMETER RegisterSchedule
    Register this script as a weekly Scheduled Task ("Clean-DevCaches") and exit.

.PARAMETER DryRun
    Report what would be deleted without deleting anything.

.EXAMPLE
    .\tools\Clean-DevCaches.ps1
.EXAMPLE
    .\tools\Clean-DevCaches.ps1 -IncludeDocker
.EXAMPLE
    .\tools\Clean-DevCaches.ps1 -RegisterSchedule
#>
[CmdletBinding()]
param(
    [switch]$IncludeDocker,
    [switch]$RegisterSchedule,
    [switch]$DryRun
)

$ErrorActionPreference = 'SilentlyContinue'

function Write-Step ([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Info ([string]$m) { Write-Host "    $m" -ForegroundColor Gray }
function Write-Ok   ([string]$m) { Write-Host "    [ok] $m" -ForegroundColor Green }

function Get-FreeGB { [math]::Round((Get-PSDrive C).Free / 1GB, 2) }

function Get-SizeMB {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return 0 }
    $b = (Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
    return [math]::Round(($b / 1MB), 1)
}

function Remove-Path {
    param([string]$Path, [switch]$ContentsOnly)
    if (-not (Test-Path $Path)) { return }
    $mb = Get-SizeMB $Path
    if ($DryRun) { Write-Info ("[dry-run] would remove {0} ({1:N1} MB)" -f $Path, $mb); return }
    if ($ContentsOnly) {
        Get-ChildItem -LiteralPath $Path -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Ok ("removed {0} ({1:N1} MB)" -f $Path, $mb)
}

# --- scheduled task registration -------------------------------------------

if ($RegisterSchedule) {
    Write-Step 'Registering weekly Scheduled Task "Clean-DevCaches"'
    $scriptPath = $MyInvocation.MyCommand.Path
    $action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName 'Clean-DevCaches' -Action $action -Trigger $trigger `
        -Settings $settings -Description 'Weekly Cursor/dev cache cleanup' -Force | Out-Null
    Write-Ok 'Scheduled task registered (Sundays 9:00 AM).'
    return
}

# --- start ------------------------------------------------------------------

$freeBefore = Get-FreeGB
Write-Step ("Disk cleanup starting - C: free: {0:N2} GB" -f $freeBefore)

$appdataCursor = Join-Path $env:APPDATA 'Cursor'
$userDir       = Join-Path $appdataCursor 'User'
$globalStorage = Join-Path $userDir 'globalStorage'

$cursorRunning = [bool](Get-Process -Name 'Cursor' -ErrorAction SilentlyContinue)

# --- Cursor data (only when Cursor is closed) -------------------------------

if ($cursorRunning) {
    Write-Step 'Cursor is RUNNING - skipping Cursor-data cleanup (close Cursor to include it)'
} else {
    Write-Step 'Cleaning Cursor caches'

    # 1) duplicate state DB backup (regenerated on next launch)
    Remove-Path (Join-Path $globalStorage 'state.vscdb.backup')
    Remove-Path (Join-Path $globalStorage 'state.vscdb-wal')
    Remove-Path (Join-Path $globalStorage 'state.vscdb-shm')

    # 2) Chromium / GPU caches (regenerated)
    $chromiumCaches = 'Cache','GPUCache','Code Cache','blob_storage',
                      'DawnGraphiteCache','DawnWebGPUCache','Network'
    foreach ($c in $chromiumCaches) { Remove-Path (Join-Path $appdataCursor $c) }
    Remove-Path (Join-Path $appdataCursor 'Service Worker\CacheStorage')

    # 3) logs / crash dumps / telemetry
    Remove-Path (Join-Path $appdataCursor 'logs')      -ContentsOnly
    Remove-Path (Join-Path $appdataCursor 'Crashpad')  -ContentsOnly
    Remove-Path (Join-Path $appdataCursor 'sentry')    -ContentsOnly
    Remove-Path (Join-Path $userDir       'History')   -ContentsOnly
    Remove-Path (Join-Path $env:USERPROFILE '.cursor\ai-tracking') -ContentsOnly

    # 4) stale CachedData versions (keep the most recently written one)
    $cachedData = Join-Path $appdataCursor 'CachedData'
    if (Test-Path $cachedData) {
        $versions = Get-ChildItem -LiteralPath $cachedData -Directory -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending
        if ($versions.Count -gt 1) {
            foreach ($old in $versions | Select-Object -Skip 1) { Remove-Path $old.FullName }
        }
    }

    # 5) orphaned workspaceStorage (workspace folder no longer exists on disk)
    Write-Step 'Pruning orphaned workspaceStorage entries'
    $wsRoot = Join-Path $userDir 'workspaceStorage'
    if (Test-Path $wsRoot) {
        foreach ($ws in Get-ChildItem -LiteralPath $wsRoot -Directory -ErrorAction SilentlyContinue) {
            $meta = Join-Path $ws.FullName 'workspace.json'
            if (-not (Test-Path $meta)) { continue }   # keep when we cannot verify
            try {
                $json = Get-Content -LiteralPath $meta -Raw | ConvertFrom-Json
                $uri  = $json.folder; if (-not $uri) { $uri = $json.workspace }
                if (-not $uri) { continue }
                $local = ([Uri]$uri).LocalPath
                if ($local -and -not (Test-Path -LiteralPath $local)) {
                    Remove-Path $ws.FullName
                }
            } catch { }   # unparseable -> leave it alone (safe default)
        }
    }
}

# --- toolchain caches (safe anytime) ----------------------------------------

Write-Step 'Cleaning toolchain + system caches'

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    if ($DryRun) { Write-Info '[dry-run] would run: npm cache clean --force' }
    else { npm cache clean --force 2>$null; Write-Ok 'npm cache cleaned' }
}

Remove-Path $env:TEMP -ContentsOnly
Remove-Path (Join-Path $env:LOCALAPPDATA 'Temp') -ContentsOnly

if (-not $DryRun) {
    Clear-RecycleBin -Force -ErrorAction SilentlyContinue
    Write-Ok 'Recycle Bin emptied'
} else { Write-Info '[dry-run] would empty Recycle Bin' }

if ($IncludeDocker) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($docker) {
        if ($DryRun) { Write-Info '[dry-run] would run: docker system prune -f' }
        else { docker system prune -f 2>$null; Write-Ok 'docker system prune done' }
    } else { Write-Info 'docker not found - skipped' }
}

# --- OneDrive trace logs (known bloat bug; regrows over time) ----------------
# OneDrive accumulates *.odl/*.aold/*.odlgz trace logs under its app folder that
# can balloon to many GB. These are pure telemetry/trace logs - always safe to
# delete; OneDrive recreates the few it needs on the next sync.

Write-Step 'Clearing OneDrive trace logs'
$odLogs = Join-Path $env:LOCALAPPDATA 'Microsoft\OneDrive\logs'
if (Test-Path $odLogs) {
    $odBefore = Get-SizeMB $odLogs
    if ($DryRun) {
        Write-Info ("[dry-run] would clear OneDrive logs ({0:N1} MB)" -f $odBefore)
    } else {
        Get-ChildItem -LiteralPath $odLogs -Recurse -File -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        $odAfter = Get-SizeMB $odLogs
        Write-Ok ("OneDrive logs cleared ({0:N1} MB freed)" -f ($odBefore - $odAfter))
    }
} else {
    Write-Info 'OneDrive logs folder not found - skipped'
}

# --- report -----------------------------------------------------------------

$freeAfter = Get-FreeGB
$reclaimed = [math]::Round(($freeAfter - $freeBefore), 2)
Write-Step 'Cleanup complete'
Write-Host ("    C: free before : {0:N2} GB" -f $freeBefore) -ForegroundColor Gray
Write-Host ("    C: free after  : {0:N2} GB" -f $freeAfter)  -ForegroundColor Gray
Write-Host ("    Reclaimed      : {0:N2} GB" -f $reclaimed)  -ForegroundColor Green
if ($DryRun) { Write-Host "    (dry-run: nothing was actually deleted)" -ForegroundColor Yellow }
