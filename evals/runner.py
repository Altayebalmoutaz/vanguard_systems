"""Run golden agent evals (non-PHI fixtures). Usage: python -m evals.runner"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents.denial_agent import run_denial_agent
from app.schemas.denial import DenialAgentRequest

GOLDEN_ROOT = Path(__file__).resolve().parent / "golden"


@dataclass
class EvalFailure:
    path: str
    name: str
    message: str


def _load_cases(agent: str) -> list[tuple[Path, dict[str, Any]]]:
    agent_dir = GOLDEN_ROOT / agent
    if not agent_dir.is_dir():
        return []
    cases: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(agent_dir.glob("*.json")):
        cases.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return cases


def _run_denial_case(path: Path, case: dict[str, Any]) -> EvalFailure | None:
    name = str(case.get("name") or path.stem)
    request_raw = case.get("request")
    expect = case.get("expect")
    if not isinstance(request_raw, dict) or not isinstance(expect, dict):
        return EvalFailure(str(path), name, "case must include request and expect objects")

    request = DenialAgentRequest.model_validate(request_raw)
    response = run_denial_agent(request)

    for key, expected in expect.items():
        actual = getattr(response, key, None)
        if actual != expected:
            return EvalFailure(
                str(path),
                name,
                f"field {key!r}: expected {expected!r}, got {actual!r}",
            )
    return None


def run_all() -> list[EvalFailure]:
    failures: list[EvalFailure] = []
    for path, case in _load_cases("denial"):
        failure = _run_denial_case(path, case)
        if failure:
            failures.append(failure)
    return failures


def main() -> int:
    failures = run_all()
    if not failures:
        print(f"evals: all golden cases passed ({GOLDEN_ROOT})")
        return 0
    for item in failures:
        print(f"FAIL [{item.name}] {item.path}: {item.message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
