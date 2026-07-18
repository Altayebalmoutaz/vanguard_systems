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

    shutil.copy2(
        Path(__file__).parents[1] / "scripts" / "deploy" / "update.sh",
        script_dir / "update.sh",
    )
    (repo / "docker-compose.prod.yml").write_text("services: {}\n")
    (deploy_dir / ".env.production").write_text("ENVIRONMENT=production\n")
    (deploy_dir / "dashboard.env.production").write_text(
        "NEXT_PUBLIC_SUPABASE_URL=https://example.supabase.co\n"
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=test-anon-key\n"
    )

    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$CALL_LOG"
if [[ "$1" == "compose" ]] && [[ " $* " == *" up "* ]] && [[ -n "${FAIL_COMPOSE_UP:-}" ]]; then
    exit 1
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "$CALL_LOG"
if [[ "$*" == "rev-parse --short HEAD" ]]; then
    printf 'abc1234\\n'
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
url="${!#}"
printf 'curl %s\\n' "$url" >> "$CALL_LOG"
[[ "$url" != "${FAIL_URL:-}" ]]
""",
    )
    _write_executable(fake_bin / "sleep", "#!/usr/bin/env bash\nexit 0\n")

    call_log = tmp_path / "calls.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    return script_dir / "update.sh", env, call_log


def _run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), "--no-pull", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_full_deploy_waits_for_health_and_checks_public_application(
    deploy_script: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, call_log = deploy_script

    result = _run(script, env)

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text()
    assert (
        "docker compose -f docker-compose.prod.yml up -d --build "
        "--wait --wait-timeout 120" in calls
    )
    assert "curl https://ezfi.smilesuite.ai/health" in calls
    assert "curl https://ezfi.smilesuite.ai/ready" in calls
    assert "curl https://ezfi.smilesuite.ai/" in calls


def test_backend_deploy_checks_readiness_without_requiring_dashboard_probe(
    deploy_script: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, call_log = deploy_script

    result = _run(script, env, "--service", "backend")

    assert result.returncode == 0, result.stderr
    calls = call_log.read_text()
    assert "--wait --wait-timeout 120 backend" in calls
    assert "curl https://ezfi.smilesuite.ai/ready" in calls
    assert "curl https://ezfi.smilesuite.ai/\n" not in calls


@pytest.mark.parametrize(
    "failed_url",
    [
        "https://ezfi.smilesuite.ai/health",
        "https://ezfi.smilesuite.ai/ready",
        "https://ezfi.smilesuite.ai/",
    ],
)
def test_public_check_failure_fails_deploy(
    deploy_script: tuple[Path, dict[str, str], Path],
    failed_url: str,
) -> None:
    script, env, _ = deploy_script
    env["FAIL_URL"] = failed_url

    result = _run(script, env)

    assert result.returncode != 0
    assert "ERROR:" in result.stderr


def test_unhealthy_compose_service_fails_before_public_checks(
    deploy_script: tuple[Path, dict[str, str], Path],
) -> None:
    script, env, call_log = deploy_script
    env["FAIL_COMPOSE_UP"] = "1"

    result = _run(script, env, "--service", "frontend")

    assert result.returncode != 0
    assert "--wait --wait-timeout 120 frontend" in call_log.read_text()
    assert "curl " not in call_log.read_text()
