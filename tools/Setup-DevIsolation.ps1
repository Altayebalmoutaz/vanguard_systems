<#
.SYNOPSIS
    One-time setup that permanently relocates Cursor user data and all dev-toolchain
    caches from C: to D:, eliminating system-drive bloat.

.DESCRIPTION
    Implements the "Permanent Cursor / Dev Disk-Isolation Design" plan:
      - Builds the D:\CursorData and D:\dev directory architecture.
      - Sets User-scope environment variables for pip, cargo, rustup, pnpm,
        playwright and uv caches.
      - Points npm cache + global prefix at D: and adds the prefix to PATH.
      - Writes a global .gitignore and registers it with git.
      - Backs up, moves, and junctions %APPDATA%\Cursor and %USERPROFILE%\.cursor
        onto D: so Cursor transparently reads/writes from the data drive.

    The Cursor data move REQUIRES Cursor to be fully closed. The script aborts if
    Cursor.exe is running (run it from a plain PowerShell window, not Cursor's
    integrated terminal).

.PARAMETER DataRoot
    Root on the data drive for relocated Cursor data. Default: D:\CursorData

.PARAMETER DevRoot
    Root on the data drive for code + caches. Default: D:\dev

.PARAMETER BackupRoot
    Where pre-move backups are written. Default: D:\_backup

.PARAMETER SkipCursorMove
    Do only the non-destructive scaffolding (dirs, env vars, npm, gitignore) and
    skip moving/junctioning Cursor data. Useful to run safely while Cursor is open.

.PARAMETER DryRun
    Print every action without changing anything.

.EXAMPLE
    # Full migration (close Cursor first, run from Windows PowerShell):
    powershell -ExecutionPolicy Bypass -File .\tools\Setup-DevIsolation.ps1

.EXAMPLE
    # Just set up D: structure + caches now, migrate Cursor later:
    .\tools\Setup-DevIsolation.ps1 -SkipCursorMove
#>
[CmdletBinding()]
param(
    [string]$DataRoot   = 'D:\CursorData',
    [string]$DevRoot    = 'D:\dev',
    [string]$BackupRoot = 'D:\_backup',
    [switch]$SkipCursorMove,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# --- helpers ---------------------------------------------------------------

function Write-Step  ([string]$m) { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Info  ([string]$m) { Write-Host "    $m" -ForegroundColor Gray }
function Write-Ok    ([string]$m) { Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Warn2 ([string]$m) { Write-Host "    [!]  $m" -ForegroundColor Yellow }

function Invoke-Action {
    param([string]$Description, [scriptblock]$Action)
    if ($DryRun) { Write-Info "[dry-run] $Description"; return }
    & $Action
}

function Test-IsJunction {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function New-Dir {
    param([string]$Path)
    if (Test-Path $Path) { return }
    Invoke-Action "create directory $Path" { New-Item -ItemType Directory -Force -Path $Path | Out-Null }
    Write-Ok "created $Path"
}

function Set-UserEnv {
    param([string]$Name, [string]$Value)
    $current = [Environment]::GetEnvironmentVariable($Name, 'User')
    if ($current -eq $Value) { Write-Info "$Name already set"; return }
    Invoke-Action "set user env $Name=$Value" {
        [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
        Set-Item -Path "Env:$Name" -Value $Value   # current session too
    }
    Write-Ok "$Name=$Value"
}

function Add-ToUserPath {
    param([string]$Dir)
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($null -eq $userPath) { $userPath = '' }
    $parts = $userPath -split ';' | Where-Object { $_ -ne '' }
    if ($parts -contains $Dir) { Write-Info "PATH already contains $Dir"; return }
    Invoke-Action "append $Dir to user PATH" {
        $new = (@($parts) + $Dir) -join ';'
        [Environment]::SetEnvironmentVariable('Path', $new, 'User')
    }
    Write-Ok "added $Dir to user PATH"
}

function Move-AndJunction {
    param(
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$Target,
        [Parameter(Mandatory)][string]$Backup
    )

    if (Test-IsJunction $Source) {
        Write-Ok "$Source is already a junction - skipping"
        return
    }

    if (-not (Test-Path $Source)) {
        Write-Warn2 "$Source does not exist - creating empty target + junction"
        New-Dir $Target
        Invoke-Action "mklink /J `"$Source`" `"$Target`"" {
            cmd /c mklink /J "$Source" "$Target" | Out-Null
        }
        return
    }

    # 1) backup
    Write-Info "backing up $Source -> $Backup"
    Invoke-Action "robocopy backup" {
        robocopy "$Source" "$Backup" /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy backup failed (code $LASTEXITCODE) for $Source" }
    }
    Write-Ok "backup complete"

    # 2) move into target
    New-Dir (Split-Path $Target -Parent)
    Write-Info "moving $Source -> $Target"
    Invoke-Action "robocopy move" {
        robocopy "$Source" "$Target" /E /MOVE /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) { throw "robocopy move failed (code $LASTEXITCODE) for $Source" }
    }
    # robocopy /MOVE leaves the (now empty) source root behind
    Invoke-Action "remove empty source $Source" {
        if (Test-Path $Source) { Remove-Item -LiteralPath $Source -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Write-Ok "moved to $Target"

    # 3) junction
    Invoke-Action "mklink /J `"$Source`" `"$Target`"" {
        cmd /c mklink /J "$Source" "$Target" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "mklink failed for $Source -> $Target" }
    }
    Write-Ok "junction created: $Source -> $Target"
}

# --- 0. preflight ----------------------------------------------------------

Write-Step 'Preflight checks'

$dataDrive = ($DataRoot -split ':')[0] + ':'
if (-not (Test-Path "$dataDrive\")) {
    throw "Data drive $dataDrive is not available. Adjust -DataRoot/-DevRoot."
}
Write-Ok "data drive $dataDrive present"

$cursorProc = Get-Process -Name 'Cursor' -ErrorAction SilentlyContinue
if ($cursorProc -and -not $SkipCursorMove -and -not $DryRun) {
    Write-Warn2 'Cursor is running. The Cursor data move cannot proceed safely.'
    Write-Warn2 'Close Cursor completely (check Task Manager) and re-run, OR run with'
    Write-Warn2 '-SkipCursorMove to perform only the cache/env scaffolding now.'
    throw 'Aborting: Cursor.exe is running.'
}

$appdataCursor = Join-Path $env:APPDATA 'Cursor'
$dotCursor     = Join-Path $env:USERPROFILE '.cursor'

# --- 1. directory architecture --------------------------------------------

Write-Step 'Creating D: directory architecture'
$cacheNames = 'npm','pip','cargo','rustup','pnpm','yarn','playwright','uv'
New-Dir $DataRoot
New-Dir (Join-Path $DevRoot 'code')
New-Dir (Join-Path $DevRoot 'venvs')
New-Dir (Join-Path $DevRoot 'npm-global')
foreach ($c in $cacheNames) { New-Dir (Join-Path $DevRoot "caches\$c") }

# --- 2. toolchain environment variables ------------------------------------

Write-Step 'Setting toolchain cache environment variables (User scope)'
Set-UserEnv 'PIP_CACHE_DIR'             (Join-Path $DevRoot 'caches\pip')
Set-UserEnv 'CARGO_HOME'                (Join-Path $DevRoot 'caches\cargo')
Set-UserEnv 'RUSTUP_HOME'               (Join-Path $DevRoot 'caches\rustup')
Set-UserEnv 'PNPM_HOME'                 (Join-Path $DevRoot 'caches\pnpm')
Set-UserEnv 'PLAYWRIGHT_BROWSERS_PATH'  (Join-Path $DevRoot 'caches\playwright')
Set-UserEnv 'UV_CACHE_DIR'              (Join-Path $DevRoot 'caches\uv')

# --- 3. npm cache + global prefix ------------------------------------------

Write-Step 'Configuring npm (cache + global prefix)'
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    $npmCache  = Join-Path $DevRoot 'caches\npm'
    $npmPrefix = Join-Path $DevRoot 'npm-global'
    Invoke-Action "npm config set cache $npmCache" { npm config set cache "$npmCache" --global 2>$null; npm config set cache "$npmCache" 2>$null }
    Invoke-Action "npm config set prefix $npmPrefix" { npm config set prefix "$npmPrefix" 2>$null }
    Add-ToUserPath $npmPrefix
    Write-Ok "npm cache + prefix relocated"
} else {
    Write-Warn2 'npm not found on PATH - skipped (env paths still created).'
}

# --- 4. global gitignore ---------------------------------------------------

Write-Step 'Writing global .gitignore'
$gitignorePath = Join-Path $DevRoot '.gitignore_global'
$gitignoreBody = @'
# Dev artifacts that must never bloat any drive or get committed
node_modules/
.pnpm-store/
.venv/
venv/
env/
__pycache__/
*.pyc
.mypy_cache/
.pytest_cache/
.ruff_cache/
dist/
build/
.next/
out/
target/
.gradle/
.cache/
*.log
.DS_Store
Thumbs.db
'@
Invoke-Action "write $gitignorePath" {
    Set-Content -LiteralPath $gitignorePath -Value $gitignoreBody -Encoding UTF8
}
Write-Ok "wrote $gitignorePath"

$git = Get-Command git -ErrorAction SilentlyContinue
if ($git) {
    Invoke-Action "git config --global core.excludesfile $gitignorePath" {
        git config --global core.excludesfile "$gitignorePath"
    }
    Write-Ok "registered global gitignore with git"
} else {
    Write-Warn2 'git not found on PATH - set core.excludesfile manually later.'
}

# --- 5. Cursor data move + junctions ---------------------------------------

if ($SkipCursorMove) {
    Write-Step 'Skipping Cursor data move (-SkipCursorMove)'
} else {
    Write-Step 'Relocating Cursor user data to D: (backup -> move -> junction)'
    New-Dir $BackupRoot
    Move-AndJunction -Source $appdataCursor -Target (Join-Path $DataRoot 'Roaming')   -Backup (Join-Path $BackupRoot 'Cursor')
    Move-AndJunction -Source $dotCursor     -Target (Join-Path $DataRoot 'dotcursor') -Backup (Join-Path $BackupRoot 'dotcursor')
}

# --- 6. verification -------------------------------------------------------

Write-Step 'Verification'
$cDrive = Get-PSDrive C
Write-Info ("C: free space: {0:N1} GB" -f ($cDrive.Free / 1GB))
foreach ($p in @($appdataCursor, $dotCursor)) {
    if (Test-IsJunction $p) {
        $tgt = (Get-Item -LiteralPath $p -Force).Target
        Write-Ok "$p -> JUNCTION -> $tgt"
    } elseif (Test-Path $p) {
        Write-Warn2 "$p exists but is NOT a junction (still on C:)"
    } else {
        Write-Warn2 "$p missing"
    }
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host "Open a NEW terminal so the updated environment variables/PATH take effect." -ForegroundColor Yellow
if (-not $SkipCursorMove) {
    Write-Host "Launch Cursor and confirm settings, extensions and chat history are intact." -ForegroundColor Yellow
    Write-Host "Backups are preserved at $BackupRoot until you confirm everything works." -ForegroundColor Yellow
}
