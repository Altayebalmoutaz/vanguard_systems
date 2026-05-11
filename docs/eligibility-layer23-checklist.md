# Eligibility Layer 2/3 Production Checklist

## Layer 2 (Stedi client)

- Configurable retry, timeout, and backoff controls via env:
  - `STEDI_TIMEOUT_SECONDS`
  - `STEDI_BATCH_TIMEOUT_SECONDS`
  - `STEDI_MAX_RETRIES`
  - `STEDI_RETRY_BASE_SECONDS`
  - `STEDI_RETRY_MAX_SECONDS`
  - `STEDI_RETRY_JITTER_SECONDS`
- Retries on `429` and `5xx`.
- Raises deterministic `StediAPIError` on HTTP failures.
- Rejects non-JSON and non-object JSON responses before downstream normalization.

## Layer 3 (Normalizer)

- Converts Stedi 271 payload to canonical eligibility shape.
- Produces deterministic warnings for financial conflicts.
- Produces per-procedure details from request CDT context.
- Unit tests cover happy path and key conflict/edge cases.

## Go-live checks

1. Confirm retries/timeouts are tuned for your traffic pattern.
2. Monitor payer-level error rates and response latency.
3. Validate top payer responses in fixture tests each release.
4. Keep canonical field contract stable for downstream routing.
