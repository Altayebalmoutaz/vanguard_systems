"""Run golden agent evals (non-PHI fixtures). Usage: python -m evals.runner"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.agents.denial_agent import run_denial_agent
from app.coding.config import CodingSettings
from app.coding.schemas import CodingSuggestRequest
from app.coding.service import run_coding_suggest
from app.config import Settings
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


def _run_coding_case(path: Path, case: dict[str, Any]) -> EvalFailure | None:
    name = str(case.get("name") or path.stem)
    request_raw = case.get("request")
    expect = case.get("expect")
    mock_llm = case.get("mock_llm")
    if not isinstance(request_raw, dict) or not isinstance(expect, dict):
        return EvalFailure(str(path), name, "case must include request and expect objects")
    if not isinstance(mock_llm, dict):
        return EvalFailure(str(path), name, "coding case must include mock_llm")

    request = CodingSuggestRequest.model_validate(request_raw)
    with (
        patch("app.coding.service.llm_generate_line_recommendations", return_value=mock_llm),
        patch("app.coding.service.fetch_run_by_request_id", return_value=None),
        patch("app.coding.service.insert_coding_run", return_value=None),
        patch("app.coding.service.write_audit_log"),
        patch("app.coding.service.create_supabase", return_value=None),
    ):
        response = run_coding_suggest(
            request,
            settings=Settings(openrouter_api_key="eval-key"),
            coding_settings=CodingSettings(coding_confidence_review_threshold=0.75),
        )

    if "status" in expect and response.status != expect["status"]:
        return EvalFailure(
            str(path),
            name,
            f"status: expected {expect['status']!r}, got {response.status!r}",
        )
    line_cdt = expect.get("line_cdt")
    if isinstance(line_cdt, dict):
        actual_map = {r.line_id: r.cdt_code for r in response.recommendations}
        for line_id, cdt in line_cdt.items():
            if actual_map.get(str(line_id)) != cdt:
                return EvalFailure(
                    str(path),
                    name,
                    f"line {line_id}: expected CDT {cdt!r}, got {actual_map.get(str(line_id))!r}",
                )
    min_conf = expect.get("min_overall_confidence")
    if min_conf is not None and response.overall_confidence < float(min_conf):
        return EvalFailure(
            str(path),
            name,
            f"overall_confidence {response.overall_confidence} < {min_conf}",
        )
    return None


def run_all() -> list[EvalFailure]:
    failures: list[EvalFailure] = []
    for path, case in _load_cases("denial"):
        failure = _run_denial_case(path, case)
        if failure:
            failures.append(failure)
    for path, case in _load_cases("coding"):
        failure = _run_coding_case(path, case)
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
