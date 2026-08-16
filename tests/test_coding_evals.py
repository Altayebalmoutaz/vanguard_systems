"""CI gate: every golden coding eval case must pass.

Runs the same deterministic path as `python -m evals.runner` but inside pytest so
regressions (e.g. crown/negation false gaps) fail the build.
"""

from __future__ import annotations

import unittest

from evals.runner import _load_cases, _run_coding_case


class TestGoldenCodingEvals(unittest.TestCase):
    def test_all_coding_cases_pass(self) -> None:
        cases = _load_cases("coding")
        self.assertGreaterEqual(len(cases), 3, "expected golden coding cases to exist")
        failures = [
            f"{path.name} [{case.get('name') or path.stem}]: {failure.message}"
            for path, case in cases
            if (failure := _run_coding_case(path, case)) is not None
        ]
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
