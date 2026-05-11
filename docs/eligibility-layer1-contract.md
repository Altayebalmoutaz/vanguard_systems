# Eligibility Layer 1 Contract

Layer 1 is the preflight gate before any external eligibility API call.

## Scope

- Input normalization and preflight checks in `eligibility_agent/triggers.py`
- Deterministic failures represented by `Layer1ValidationError` in `eligibility_agent/models.py`

## Guarantees

1. `primary_payer_id` must exist in `payer_network` with `coverage_type='dental'`.
2. `secondary_payer_id` (if present) must satisfy the same dental payer rule.
3. `primary_payer_id`, `secondary_payer_id`, and `cdt_codes` are normalized to uppercase trimmed tokens.
4. Invalid CDT codes are removed and emitted as warnings with stable prefix:
   - `L1_INVALID_CDT_REMOVED|code=<CDT>|...`
5. If Layer 1 rejects request, downstream Stedi call must not execute.

## Stable error codes

- `L1_INVALID_PRIMARY_PAYER`
- `L1_INVALID_SECONDARY_PAYER`

## HTTP shape for Layer 1 failures

Routes surface Layer 1 failures as `400`:

```json
{
  "code": "L1_INVALID_PRIMARY_PAYER",
  "message": "...",
  "layer": "layer1",
  "detail": {
    "field": "primary_payer_id",
    "payer_id": "..."
  }
}
```
