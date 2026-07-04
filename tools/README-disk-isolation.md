# Cursor / Dev Disk-Isolation Runbook (Windows, C: -> D:)

Permanent fix for C: drive bloat. Relocates Cursor user data and dev-toolchain
caches onto D: via directory junctions + environment variables, and automates
cleanup of regenerable caches.

Baseline when designed: C: 96.6 GB total / 3.5 GB free (critical); D: 140.7 GB /
65.5 GB free (target).

## Scripts

- `Setup-DevIsolation.ps1` - one-time migration (run with Cursor CLOSED).
- `Clean-DevCaches.ps1` - repeatable, safe-by-default cleanup (run anytime).

Both support `-DryRun` to preview without changing anything.

## What goes where after migration

```
D:\CursorData\Roaming      <- junction target for %APPDATA%\Cursor
D:\CursorData\dotcursor    <- junction target for %USERPROFILE%\.cursor
D:\dev\code                <- all NEW repos (clone here so node_modules/.venv land on D:)
D:\dev\caches\{npm,pip,cargo,rustup,pnpm,yarn,playwright,uv}
D:\dev\npm-global          <- npm global prefix (added to PATH)
D:\dev\venvs               <- centralized Python virtualenvs
D:\dev\.gitignore_global   <- registered via git core.excludesfile
D:\Docker                  <- Docker Desktop disk image (set in GUI, see below)
D:\_backup                 <- pre-move backups (keep until verified, then delete)
```

## Run order

### 1. One-time setup (Cursor CLOSED)
Open Windows PowerShell (NOT Cursor's integrated terminal) and run:

```powershell
cd C:\Users\ZT\medical-agent-system
# preview first:
powershell -ExecutionPolicy Bypass -File .\tools\Setup-DevIsolation.ps1 -DryRun
# then execute:
powershell -ExecutionPolicy Bypass -File .\tools\Setup-DevIsolation.ps1
```

The script aborts if `Cursor.exe` is running. To set up only the D: structure +
caches while Cursor stays open, add `-SkipCursorMove` (run the Cursor move later
with Cursor closed).

### 2. Verification (the `verify` step)
After setup completes:

1. Open a NEW terminal (so updated env vars/PATH load).
2. Launch Cursor; confirm your settings, extensions, and chat/agent history are
   all present (they came along in the move).
3. Confirm the junctions in a terminal:

   ```powershell
   cmd /c dir "%APPDATA%" | findstr Cursor          # shows <JUNCTION>
   cmd /c dir "%USERPROFILE%" | findstr .cursor     # shows <JUNCTION>
   (Get-Item "$env:APPDATA\Cursor" -Force).Target   # -> D:\CursorData\Roaming
   ```

4. Clone/create a new repo under `D:\dev\code`, run `npm install` (or create a
   venv), and confirm `node_modules` / cache land on D:.
5. Re-measure free space: `Get-PSDrive C | Select Free`.
6. Once everything works, delete the safety backup: `Remove-Item D:\_backup -Recurse -Force`.

### 3. Rollback (if Cursor misbehaves)
The junction redirects only; the real data is on D: and a backup is in `D:\_backup`.

```powershell
# Close Cursor first, then:
cmd /c rmdir "%APPDATA%\Cursor"          # removes the junction only (not data)
robocopy "D:\_backup\Cursor" "%APPDATA%\Cursor" /E
cmd /c rmdir "%USERPROFILE%\.cursor"
robocopy "D:\_backup\dotcursor" "%USERPROFILE%\.cursor" /E
```

Relaunch Cursor. To undo env vars, clear them with
`[Environment]::SetEnvironmentVariable('PIP_CACHE_DIR',$null,'User')` (repeat per var)
and `npm config delete cache` / `npm config delete prefix`.

### 4. Recurring maintenance + scheduling (the `schedule` step)

```powershell
.\tools\Clean-DevCaches.ps1 -DryRun        # preview reclaim
.\tools\Clean-DevCaches.ps1                # reclaim now (close Cursor for full effect)
.\tools\Clean-DevCaches.ps1 -IncludeDocker # also docker system prune
.\tools\Clean-DevCaches.ps1 -RegisterSchedule   # weekly task (Sun 9:00 AM)
```

## Non-dev reclaim (biggest wins, GUI steps)

These three apps dominate C: but are NOT Cursor/dev; reclaim them manually.

### OneDrive trace logs - ~12 GB (`%LOCALAPPDATA%\Microsoft\OneDrive\logs`)
On this machine the OneDrive bloat was NOT synced files (only 345 MB) - it was a
runaway `logs` folder full of `*.odl`/`*.aold`/`*.odlgz` trace logs (a known
OneDrive bug). These are pure telemetry and always safe to delete; OneDrive
recreates the few it needs.

This is now purged automatically by `Clean-DevCaches.ps1` (it clears
`...\OneDrive\logs` on every run and regrows over time, so re-run periodically).

If you ever DO have large synced files on C:, enable Files On-Demand to dehydrate
them: OneDrive tray icon -> Settings -> Sync and backup -> Advanced settings ->
"Free up disk space" (files stay in the cloud, no data loss).

### Docker disk image - ~2.9 GB (`%LOCALAPPDATA%\Docker`)
Docker Desktop -> Settings -> Resources -> Advanced ->
"Disk image location" -> set to `D:\Docker` -> Apply & Restart.
Docker moves the WSL2 vhdx to D:. Then `docker system prune` for dangling layers.

### Claude desktop app - ~10.1 GB (`%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc`)
Packaged Store app (ACL-locked; do not junction). Reclaim from inside the app:
- Clear conversation history / cached attachments in Claude's settings, or
- Sign out / clear app data, or reinstall if the cache does not shrink.
Verify size after: `(Get-ChildItem "$env:LOCALAPPDATA\Packages\Claude_pzs8sxrjxfjjc" -Recurse -File -Force | Measure-Object Length -Sum).Sum/1GB`.

## Safety summary

Never deleted by these tools: current `state.vscdb`, `settings.json`,
`keybindings.json`, `snippets`, installed extensions, `.cursor\projects`
(agent transcripts), or the Cursor install at `%LOCALAPPDATA%\Programs\cursor`.
