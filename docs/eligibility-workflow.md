# Eligibility Agent Workflow 1

This document captures the current Vanguard MD Eligibility Agent workflow after the Layer 3 dental-calculator enhancement and the Stedi production-hardening updates.

The short version:

`request validation -> Supabase payer/CDT validation -> cache decision -> Stedi realtime eligibility -> normalize -> integrity check -> route -> estimate costs when safe -> persist -> return canonical + universal dental record`

The agent is close to pilot-ready for API use. The main remaining pilot dependency is provider-specific payer configuration: which payers the provider accepts, whether the provider is in-network or out-of-network, fee schedules, prior-auth rules, and payer-specific quirks.

---

## 1. Main Entry Point

The primary endpoint is:

```http
POST /eligibility/check
```

The route is implemented in:

- `eligibility_agent/main.py`
- `eligibility_agent/services.py`

The request model is `EligibilityRequest` in `eligibility_agent/models.py`.

Core request fields:

- `patient_id`
- `first_name`
- `last_name`
- `dob`
- `subscriber_id`
- `primary_payer_id`
- `secondary_payer_id` optional
- `cdt_codes` optional
- `trigger_event`
- `portal_password` optional, for payer PIN / portal credential cases
- dependent-patient fields when the patient is not the subscriber

Supported trigger events:

- `NEW_PATIENT`
- `APPOINTMENT_BOOKED`
- `PRE_APPOINTMENT`
- `BATCH_SWEEP`

`BATCH_SWEEP` is rejected on `/eligibility/check`; use `/eligibility/batch` for batch sweeps.

---

## 2. Layer 0: Request Schema Validation

Layer 0 is Pydantic validation in `eligibility_agent/models.py`.

It validates:

- UUIDs and dates
- required patient identity fields
- non-empty `subscriber_id`
- payer ID strings
- trigger event enum
- dependent-patient requirements

If `patient_is_dependent = true`, the request must include:

- `subscriber_first_name`
- `subscriber_last_name`
- `subscriber_dob`

Optional dependent fields:

- `subscriber_member_id`
- `dependent_member_id`
- `dependent_relationship_code`

Layer 0 does not call the database.

---

## 3. Layer 1: Supabase Business Validation

Layer 1 is in `eligibility_agent/triggers.py`.

Main functions:

- `layer0_supabase_validation`
- `resolve_cached_vs_api`
- `should_run_realtime`

Layer 1 uses Supabase through `eligibility_agent/db.py`.

Required env:

- `SUPABASE_URL`
- `SUPABASE_KEY` or `SUPABASE_SERVICE_ROLE_KEY`

Tables used:

- `payer_network`
- `cdt_codes`
- `eligibility_checks`

Layer 1 behavior:

1. Normalizes payer IDs and CDT codes.
2. Confirms `primary_payer_id` exists in `payer_network` with `coverage_type = dental`.
3. Confirms `secondary_payer_id` if present.
4. Filters invalid CDT codes against `cdt_codes`.
5. Returns `layer0_warnings` for removed CDT codes.
6. Decides cache vs realtime API based on trigger event and cache freshness.

Hard failures:

- Unknown primary dental payer -> `L1_INVALID_PRIMARY_PAYER`
- Unknown secondary dental payer -> `L1_INVALID_SECONDARY_PAYER`

Soft warning:

- Invalid CDT codes are removed, not fatal.

---

## 4. Cache Policy

Cache behavior is handled by `resolve_cached_vs_api`.

Rules:

- `NEW_PATIENT` always forces realtime Stedi.
- `PRE_APPOINTMENT` always forces realtime Stedi.
- `APPOINTMENT_BOOKED` can use a fresh cached `eligibility_checks` row.
- `BATCH_SWEEP` belongs to `/eligibility/batch`.

Freshness is controlled by:

- `ELIGIBILITY_CACHE_TTL_DAYS`

If cache is used, response shape is:

```json
{
  "cached": true,
  "record": "...latest eligibility_checks row...",
  "layer0_warnings": []
}
```

Cache hits do not create a new Stedi request and do not create a new `eligibility_checks` row.

---

## 5. Layer 2: Stedi Payload and Realtime Client

Layer 2 is in `eligibility_agent/api_client.py`.

Main functions:

- `build_payload`
- `call_stedi`
- `call_stedi_batch`

Stedi endpoint config:

- `STEDI_API_KEY`
- `STEDI_BASE_URL`
- `STEDI_ELIGIBILITY_PATH`
- `STEDI_MANAGER_BASE_URL`
- `STEDI_BATCH_ELIGIBILITY_PATH`
- `STEDI_TEST_HEADER`

Provider config:

- `PROVIDER_NPI`
- `PROVIDER_NAME`
- `PROVIDER_TAX_ID`

Payload shape:

```json
{
  "tradingPartnerServiceId": "payer id",
  "provider": {
    "organizationName": "provider name",
    "npi": "provider npi",
    "taxId": "optional 9 digit tax id"
  },
  "subscriber": {
    "firstName": "Jane",
    "lastName": "Doe",
    "dateOfBirth": "19900115",
    "memberId": "member id"
  },
  "encounter": {
    "serviceTypeCodes": ["35"]
  }
}
```

Dental behavior:

- General dental eligibility uses STC `35`.
- One CDT code uses `encounter.procedureCode` plus qualifier `AD`.
- Multiple CDT codes use `encounter.medicalProcedures[]` with qualifier `AD`.

Dependent behavior:

When `patient_is_dependent = true`, the policyholder goes in `subscriber`, and the patient goes in `dependents[]`.

Portal/PIN behavior:

When `portal_password` is present, it is sent to Stedi as:

```json
{
  "portalPassword": "PIN"
}
```

This supports payer cases like Medi-Cal-style portal PIN requirements.

---

## 6. Stedi Retry and AAA Handling

Structured Stedi AAA policy is in `eligibility_agent/stedi_errors.py`.

The agent does not branch on `possibleResolutions` or mutable payer free text for retry behavior. It uses:

- HTTP status
- AAA code
- structured response location/source

Retry cases:

- HTTP `429`
- HTTP `5xx`
- transport errors
- HTTP `200` with payer-connectivity AAA

AAA action table:

| Signal | HTTP status | Action |
|---|---:|---|
| AAA `42` | 200 | `retry_connectivity` |
| AAA `80` | 200 | `retry_connectivity` |
| AAA `79` | 200 | `retry_connectivity` |
| AAA `79` | 400 | `fix_input`, do not retry |
| AAA `41` | 200 | `enrollment_or_portal_credentials` |
| AAA `65`, `67`, `72`, `73`, `75` | 200 | `verify_subscriber` |
| Unknown AAA | 200 | `human_review` |

Retry timing uses:

- `STEDI_MAX_RETRIES`
- `STEDI_RETRY_BASE_SECONDS`
- `STEDI_RETRY_MAX_SECONDS`
- `STEDI_RETRY_JITTER_SECONDS`
- `STEDI_TIMEOUT_SECONDS`

Stedi response warnings are preserved in canonical as:

- `stedi_warnings`

Structured AAA actions are preserved as:

- `stedi_aaa_actions`

---

## 7. Layer 3: Normalization

Layer 3 is in `eligibility_agent/normalizer.py`.

Main function:

- `normalize(raw_271, coverage_order)`

It converts Stedi 271-style JSON into the internal canonical eligibility record.

Canonical fields include:

- `payer_id`
- `checked_at`
- `coverage_order`
- `is_active`
- `inactive_reason`
- `is_covered`
- `in_network`
- `coverage_percent`
- `copay`
- `coinsurance`
- `deductible_total`
- `deductible_met`
- `deductible_remaining`
- `annual_max_total`
- `annual_max_used`
- `annual_max_remaining`
- `procedure_details`
- `payer_aaa_errors`
- `stedi_aaa_actions`
- `stedi_warnings`
- `response_complete`
- `missing_fields`
- `normalization_version`
- `normalization_warnings`
- `dental_benefit_breakdown`
- `dental_calculator_ready`

Procedure-level details:

```json
{
  "cdt_code": "D0120",
  "procedure_covered": true,
  "waiting_period_end": null,
  "waiting_period_category": "basic",
  "non_covered_reason": null
}
```

Layer 3 handles:

- STC `35` dental rows
- benefit codes `A`, `B`, `C`, `F`, and `G`
- deductible totals, met, and remaining
- annual max totals, used, and remaining
- coinsurance and copay
- top-level `procedureCode`
- `compositeMedicalProcedureIdentifier`
- not-covered rows (`N` and `I`)
- payer AAA errors
- subscriber/dependent/provider/payer AAA locations
- Stedi warnings

Layer 3 does not call Supabase.

---

## 8. Dental Calculator Ready Output

`dental_calculator_ready` is an additive Layer 3 output for dental-practice workflows.

It does not replace the existing flat canonical fields. It gives the calculator and UI a richer view.

Shape:

```json
{
  "network_status": {
    "in_network": {},
    "out_of_network": {},
    "both": {},
    "unknown": {}
  },
  "frequency_rules": [],
  "latest_visit_or_consultation": [],
  "carve_outs": [],
  "aaa_actions": [],
  "free_text_overrides": []
}
```

Each network bucket can include:

- `remaining_deductible`
- `deductible_total`
- `deductible_met`
- `coinsurance_percent`
- `copay_amount`
- `annual_max_remaining`
- `annual_max_total`
- `coverage_levels`
- `time_periods`
- `prior_auth_required`
- `source_benefit_indexes`
- `limitations_notes`

The extractor groups benefits into:

- `in_network`
- `out_of_network`
- `both`
- `unknown`

It uses:

- `inPlanNetworkIndicatorCode`
- `coverageLevelCode`
- `timeQualifierCode`
- `benefitsServiceDelivery`
- `benefitsDateInformation.latestVisitOrConsultation`
- `benefitsRelatedEntities`

Free-text override behavior:

- `additionalInformation.description` can override structured network/prior-auth indicators for benefit interpretation.
- Overrides are recorded in `free_text_overrides`.
- Audit warnings are added to `normalization_warnings`.

Important distinction:

- Free text may influence dental benefit interpretation.
- Free text must not control Stedi retry/error state machine decisions.
- Stedi retry/error behavior uses AAA code and HTTP status only.

---

## 9. Optional Layer 3 LLM Enrichment

Optional enrichment lives in `eligibility_agent/layer3_llm_enrich.py`.

It is disabled unless:

- `ELIGIBILITY_LAYER3_LLM_ENRICH_ENABLED = true`
- an OpenRouter-compatible API key is configured

The LLM may add:

- `coverage_confidence`
- `layer3_llm_null_field_notes`
- `layer3_llm_summary`

It should not overwrite deterministic booleans or financial amounts.

For pilot, deterministic Stedi handling, provider payer profiles, and manual verification paths are more important than LLM enrichment.

---

## 10. Layer 4: Integrity and Completeness

Layer 4 is in `eligibility_agent/integrity.py`.

Main function:

- `validate_completeness(canonical)`

It sets:

- `response_complete`
- `missing_fields`
- `integrity_warnings`
- `integrity_issues`
- `integrity_policy_version`
- `eligibility_canonical`

Layer 4 policy:

- `is_active` is critical.
- `deductible_remaining` is required only when deductible total/met context exists.
- `annual_max_remaining` is required only when annual max total/used context exists.
- `in_network` being unknown is a warning, not a blocker.
- null `copay`, `coinsurance`, or `coverage_percent` are warnings.

Example warning:

```text
in_network status not reported by payer - confirm via phone if required
```

This is important for pilot. Stedi may confirm coverage but not clearly say whether this exact provider is in network. Provider-specific payer profiles should fill that gap.

---

## 11. Layer 6: Routing

Layer 6 is in `eligibility_agent/router.py`.

Routing states:

- `INACTIVE`
- `INCOMPLETE`
- `NOT_COVERED`
- `COVERAGE_AMBIGUOUS`
- `CLEARED`

Routing priority:

1. Inactive member
2. Explicit not-covered
3. Incomplete with no benefit rows
4. Coverage ambiguous
5. Cleared

Actions:

| Status | Action |
---|---|
| `INACTIVE` | `notify_front_office_inactive` |
| `INCOMPLETE` | `notify_front_office_missing_fields` |
| `NOT_COVERED` | `patient_financial_agreement_required` |
| `COVERAGE_AMBIGUOUS` | `notify_front_office_coverage_ambiguous` |
| `CLEARED` with prior auth rule | `route_prior_auth` |
| `CLEARED` without prior auth rule | `route_coding` |

Structured Stedi actions are surfaced in routing detail.

Examples:

- `retry_connectivity`: payer connectivity issue persisted after automatic retries.
- `enrollment_or_portal_credentials`: provider enrollment or portal PIN/password required.
- `verify_subscriber`: verify member ID, legal name, DOB, and payer.

---

## 12. Layer 5: Cost and Patient Responsibility

Layer 5 is in `eligibility_agent/cost_calculator.py`.

It runs only when:

- member is active
- routing status is `CLEARED`
- response is complete

It uses:

- `payer_fee_schedules`
- billed/UCR fallback when enabled
- canonical deductible/annual max/coverage values
- `procedure_details`

Layer 5 produces `procedure_estimates` rows with:

- `cdt_code`
- `procedure_covered`
- `waiting_period_end`
- `waiting_period_category`
- `non_covered_reason`
- `allowed_amount`
- `insurance_pays`
- `patient_responsibility`

If cost calculation fails, the eligibility check is still stored and the failure is logged.

If routing is `COVERAGE_AMBIGUOUS`, the system may create copay-only partial estimates when copay is known.

---

## 13. Persistence

Realtime checks write to Supabase.

Tables:

- `eligibility_checks`
- `procedure_estimates`
- `eligibility_audit_log`

`eligibility_checks` stores:

- patient ID
- payer ID
- checked timestamp
- coverage order
- active/covered/network flags
- financial fields
- raw Stedi response
- completeness fields
- routing status
- integrity warnings

`procedure_estimates` stores per-CDT estimates when Layer 5 runs.

Audit events include:

- `SSN_FALLBACK`
- `CACHE_HIT`
- `ROUTING`
- batch metadata
- COB metadata

---

## 14. Response Shape

Realtime successful response:

```json
{
  "cached": false,
  "layer0_warnings": [],
  "primary": {
    "check_id": "...",
    "canonical": {},
    "routing": {},
    "procedure_estimates": [],
    "universal_dental_record": {}
  },
  "secondary": null
}
```

When secondary payer is provided, `secondary` has the same structure as `primary`.

`universal_dental_record` is built from canonical plus stored raw Stedi response.

It includes:

- payer
- subscriber
- plan dates
- group number
- network status
- financial summary
- categories
- ortho detail
- waiting-period flag
- limitation notes
- source confidence
- raw payload hash

---

## 15. Secondary Payer and COB

If `secondary_payer_id` is provided, the service runs a second independent Stedi eligibility call.

Primary and secondary are not merged during realtime eligibility.

COB is separate:

```http
POST /eligibility/cob
```

COB requires:

- primary eligibility check ID
- secondary eligibility check ID
- both rows complete
- procedure estimates available

Layer 7 logic lives in `eligibility_agent/cob.py`.

---

## 16. Batch Workflow

Batch endpoint:

```http
POST /eligibility/batch
```

It uses Stedi batch manager endpoint.

Batch behavior:

- Validates each item through Layer 1.
- Builds Stedi payloads.
- Submits one batch request.
- Does not run the realtime normalization/persistence path per item.

Batch is submit-oriented and is best for scheduled sweeps.

---

## 17. Two-Pass Workflow

Two-pass endpoint:

```http
POST /eligibility/two-pass
```

Flow:

1. Pass 1 eligibility without CDT codes.
2. If not `CLEARED`, stop.
3. If cleared, run coding agent.
4. Pass 2 eligibility with generated CDT codes and `PRE_APPOINTMENT`.

This is useful when clinical coding depends on first proving that coverage is active.

---

## 18. Provider-Specific Payer Profiles for Pilot

This is the biggest operational improvement needed for pilot accuracy.

Stedi often tells us whether the plan is active and covered, but does not always confirm whether this exact provider is in network.

For each pilot provider, add their top payers with:

- Stedi `tradingPartnerServiceId`
- payer name
- provider is `INN`, `OON`, or unknown
- contracted fee schedule if INN
- office/UCR fee schedule if OON
- prior-auth rules
- portal/PIN requirements
- dependent support notes
- whether the payer requires member ID
- CDT vs STC behavior
- common AAA errors
- manual verification instructions

Recommended pilot order:

1. Add top 10-20 payers for the provider.
2. Mark provider network status for each payer.
3. Add contracted fees for common CDT codes.
4. Add prior-auth rules for common high-cost procedures.
5. Run real Swagger checks for those payers.
6. Save anonymized/golden fixtures for regression tests.

This will resolve cases where Stedi says:

```text
in_network status not reported by payer
```

but the provider's contract records already know the answer.

---

## 19. How To Interpret A Good Response

A good pilot response usually has:

- `payer_aaa_errors: []`
- `stedi_aaa_actions: []`
- `stedi_warnings: []`
- `response_complete: true`
- `missing_fields: []`
- `routing.status: CLEARED`
- `universal_dental_record` present
- no blocking `integrity_issues`

Common acceptable warning:

- `in_network status not reported by payer`

That warning means coverage may be valid, but staff or local provider-payer profiles should confirm network status before quoting INN pricing.

---

## 20. Pilot Readiness

Current backend posture:

- Layers 0-2: ready for pilot.
- Layer 2: production-hardened with HTTP and AAA connectivity retries.
- Layer 3: pilot-ready with canonical and dental calculator output.
- Layer 4: usable completeness and warning policy.
- Layer 5: usable when fee schedules are populated.
- Layer 6: usable routing with structured Stedi AAA actions.
- Layer 7: available for completed primary/secondary checks.

Estimated backend pilot readiness:

```text
80-85%
```

Remaining before operational pilot:

- Validate against 3-5 real payer/member examples.
- Confirm Supabase payer/CDT/fee/prior-auth data.
- Add provider-specific payer network status.
- Add top provider payer fee schedules.
- Document payer quirks and portal PIN requirements.
- Decide whether dashboard is in pilot scope or API-only.

---

## 21. Practical Pilot Workflow

For each patient:

1. Office enters patient/member/payer information.
2. Agent validates payer and CDT codes.
3. Agent checks cache.
4. If realtime needed, agent sends Stedi request.
5. Stedi response is retried automatically on connectivity errors.
6. Agent normalizes benefits.
7. Agent validates completeness.
8. Agent routes result:
   - cleared
   - inactive
   - incomplete
   - not covered
   - ambiguous
9. If cleared, agent estimates patient responsibility when fee data exists.
10. Staff sees canonical summary and universal dental record.
11. If network is unknown, provider profile or staff confirmation resolves INN/OON.
12. Result is persisted for cache/audit/COB.

---

## 22. Known Limitations

Stedi responses vary by payer. Some payers:

- omit network status
- return broad STC `35` instead of procedure-level detail
- ignore CDT codes
- return only partial financial details
- require portal PIN/password
- require dependent payloads
- return AAA errors on HTTP 200
- provide limitations in free text

The agent handles many of these cases, but pilot accuracy still depends on:

- real payer testing
- provider-specific payer profiles
- fee schedule quality
- manual verification workflow for edge cases

Insurance discovery is not a primary dental verification path. Stedi documents it as a fallback and notes it is not reliable for dental-only coverage.

Carve-out second-hop eligibility is currently detected but should remain manual or controlled until payer-name-to-Stedi-ID mapping is reliable.

