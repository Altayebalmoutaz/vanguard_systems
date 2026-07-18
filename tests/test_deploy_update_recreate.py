from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


@pytest.fixture
def deploy_script(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    repo = tmp_path / "repo"
    script_dir = repo / "scripts" / "deploy"
    deploy_dir = repo / "deploy"
    fake_bin = tmp_path / "bin"
    script_dir.mkdir(parents=True)
    deploy_dir.mkdir()
    fake_bin.mkdir()

    script = script_dir / "update.sh"
    shutil.copy2(Path(__file__).parents[1] / "scripts" / "deploy" / "update.sh", script)
    (repo / "docker-compose.prod.yml").write_text("services: {}\n")
    (deploy_dir / ".env.production").write_text("ENVIRONMENT=production\n")
    (deploy_dir / "dashboard.env.production").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co\n"
    )

    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$CALL_LOG"
exit 0
""",
    )
    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "$CALL_LOG"
exit 0
""",
    )
    _write_executable(fake_bin / "curl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")

    call_log = tmp_path / "calls.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    return script, env, call_log


def _run(
    script: Path,
    env: dict[str, str],
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_backend_recreate_does_not_pull_or_rebuild(
    deploy_script: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, call_log = deploy_script

    result = _run(script, env, "--recreate", "--service", "backend")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text()
    assert "git fetch" not in calls
    assert "git checkout" not in calls
    assert "git pull" not in calls
    assert "docker compose -f docker-compose.prod.yml up -d --force-recreate backend" in calls
    assert "--build" not in calls


@pytest.mark.parametrize("args", [("--recreate",), ("--recreate", "--service", "frontend")])
def test_recreate_rejects_targets_with_baked_frontend_config(
    deploy_script: tuple[Path, dict[str, str], Path],
    args: tuple[str, ...],
) -> None:
    script, env, _ = deploy_script

    result = _run(script, env, *args)

    assert result.returncode != 0
    assert "--recreate requires --service backend" in result.stderr
