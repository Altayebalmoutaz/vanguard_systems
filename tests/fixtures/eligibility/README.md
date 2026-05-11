# TR3-style 271 eligibility fixtures

Bundled file: [`271_fixtures.json`](271_fixtures.json) — 100 synthetic scenarios derived from X12 005010X279A1 TR3 semantics (not live Stedi captures).

## Bridge strategy

[`tests/eligibility_agent/fixture_bridge.py`](../../eligibility_agent/fixture_bridge.py) maps each record to **Stedi-shaped** JSON (`benefitsInformation`, `planStatus`, camelCase financial keys) so [`app/eligibility/normalizer.normalize`](../../../app/eligibility/normalizer.py) can run unchanged.

[`tests/eligibility_agent/test_tr3_fixture_normalize.py`](../../eligibility_agent/test_tr3_fixture_normalize.py) parameterizes all `fixture_id` values and asserts **semantic slices** (STC 35 dental totals, AAA rejections, inactive subscribers). Assertion hints point back to this JSON path on failure.

## Crosswalk vs manual Layer 3 tests

[`tests/eligibility_agent/test_normalizer.py`](../../eligibility_agent/test_normalizer.py) keeps **hand-crafted** Stedi snippets for regressions (AAA dedupe, `planCoverage` MET, unmet deductible phrases, composite procedure IDs, Stedi warnings).

The TR3 corpus adds **breadth** (COB, dependents, Medicare/Medicaid labels, frequency limits, ortho, rejections) but does not replace targeted unit tests above — themes overlap where both cover deductibles / annual max / coinsurance parsing.

## Corpus taxonomy (edge-case tags)

| Edge-case tag | Fixtures |
|---|---:|
| `active_inn_only` | 23 |
| `active_inn_oon_split` | 15 |
| `dependent_coverage` | 10 |
| `minimal_response` | 10 |
| `missing_deductible` | 9 |
| `cob_secondary` | 7 |
| `ortho_benefit` | 7 |
| `cob_primary` | 6 |
| `inactive_terminated` | 6 |
| `medicare_crossover` | 6 |
| `frequency_limitation` | 5 |
| `aaa_rejection_payer_unavailable` | 4 |
| `aaa_rejection_subscriber_not_found` | 4 |
| `authorization_required` | 4 |
| `waiting_period` | 4 |
| `missing_annual_max` | 3 |
| `partial_year_plan` | 3 |
| `plan_exclusion` | 3 |
| `zero_remaining_deductible` | 3 |
| `aaa_rejection_invalid_npi` | 2 |
| `malformed_dates` | 1 |
| `medicaid_dual_eligible` | 1 |
| `multiple_group_plans` | 1 |

## Response types (`normalization_flags.response_type`)

| Response type | Count |
|---|---:|
| `full_benefits` | 76 |
| `minimal` | 9 |
| `rejection` | 9 |
| `inactive` | 6 |

## Live Stedi JSON

For production fidelity, continue to use archived responses such as [`examples/eligibility_beaver_umr_52133_response.latest.json`](../../../examples/eligibility_beaver_umr_52133_response.latest.json) alongside this corpus.
