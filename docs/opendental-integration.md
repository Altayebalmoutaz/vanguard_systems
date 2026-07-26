# OpenDental Integration (Eligibility Agent)

This integration pulls demographics + insurance from Open Dental, runs the Stedi
270/271 eligibility pipeline, and writes verification results back into the same
OD objects staff use for estimates (Family Module notes, InsVerify dates, Edit
Benefits, Other Benefits, and Adjustments to Insurance Benefits).

## API Modes

- Local: `http://localhost:30222/api/v1` (inside `opendental.exe`)
- Service: `http://localhost:30223/api/v1` (Windows service)
- Remote: `https://api.opendental.com/api/v1`

Auth header:

`Authorization: ODFHIR {DeveloperKey}/{CustomerKey}`

## Production flow (queue)

1. Multi-clinic **poller** (or dashboard **Poll now**) loads appointments across
   `poll_window_days` (auto-poll defaults toward a 3-day / 48–72h reverify window
   when the connection is set to today-only).
2. For each patient: `GET /patients`, `GET /familymodules/{PatNum}/Insurance`,
   carriers → map to `EligibilityRequest` (primary **and** secondary plan IDs).
3. Enqueue `rcm.eligibility_requests` → pipeline worker → Stedi.
4. On completion, `maybe_enqueue_od_writeback` queues `opendental_writeback`
   pipeline run(s) for primary (and secondary when present + full writeback).

Legacy synchronous route `POST /eligibility-agent/eligibility/from-opendental`
still exists for demos; it uses env `OPENDENTAL_WRITE_*` flags instead of the
per-clinic connection toggles.

## Honest Phase 1 vs estimate-driving writeback

| Layer | OD target | Default |
| --- | --- | --- |
| 1 | InsVerify (PatientEnrollment + InsuranceBenefit) | On with writeback |
| 2 | `InsSubs.BenefitNotes`, `SubscNote`, Commlog | On |
| 3 | `POST`/`PUT /benefits` (Edit Benefits + Other Benefits) | Off unless **Full writeback** |
| 4 | `PUT /claimprocs/InsAdjust` (YTD used) | Off unless **Full writeback** |

**Notes alone do not change Treatment Plan / Account estimates.** Layers 3–4
populate the native OD estimate engine (same surfaces as VOB Recordation).

We do **not** auto-write fee schedules, InsPlan group/employer metadata, or
fabricated AR rows. Track G detects InsPlan drift read-only; Track E alerts on
network/fee mismatches without assigning FeeSched.

## Connection toggles (dashboard)

| Toggle | Effect |
| --- | --- |
| Write-back | Master gate for OD writes after eligibility |
| Full writeback | Enables benefits grid + InsAdjust (needs **$30 Insurance** API tier) |
| Shadow compare | With Full: run L3/L4 in **dry-run** (propose diffs + review queue); notes/InsVerify/commlog still write. Default **off** so full writeback applies. |

Flags are stored on `rcm.opendental_connections` (`writeback_enabled`,
`writeback_full`, `writeback_shadow_compare`).

## Write-back order

Each step is independently flag-gated and fault-isolated:

1. **`PUT /inssubs/{InsSubNum}`** — `BenefitNotes` (deterministic ASCII snapshot, `[Verified by ezfi]`)
1b. **`PUT /inssubs/{InsSubNum}`** — `SubscNote` (bold-red on the insurance grid)
2. **`PUT /insverifies`** — enrollment + benefits last-verified dates + notes
3. **`POST /commlogs`** — front-desk summary
4. **`PUT /claimprocs/InsAdjust`** — insurance/deductible used (or proposed in shadow)
5. **`POST` / `PUT /benefits`** — structured grid:
   - `ActiveCoverage`
   - `CoInsurance` % by category (Diagnostic/Preventive/Basic/Major/Ortho/Endo/Perio/…)
   - General Deductible + Annual Max
   - Ortho lifetime max (`Limitations` / Lifetime on Orthodontics)
   - `CoPayment` when present
   - Frequency `Limitations`, `WaitingPeriod`, missing-tooth `Exclusions`
6. **Fee schedule alerts** + **InsPlan drift** (audit/review only)

### Confidence gating (Track C)

Large coinsurance (±5 pts) or monetary (±$100) deltas, and any quantity/waiting
change, are classified `review` and skipped unless shadow-compare dry-run (which
records proposed actions). Pre-write benefit snapshots are audited for rollback
comparison. Review items land in eligibility audit events
(`opendental_writeback_review`, `opendental_reverify_change`, etc.).

### Required OD permission

`Benefits POST/PUT/DELETE` require the **Insurance** API permission tier on the
Customer Key. Without it, grid calls return 401 while notes/InsVerify/Commlog
still succeed.

## BenefitNotes format (deterministic, ASCII)

```
[Verified by ezfi]
Date: YYYY-MM-DD HH:MM
Plan: PPO - Carrier Name
Status: CLEARED
Check: <check_id>

Deductible:
 - Total: $X
 - Remaining: $X
 - Individual: $X   (when 271 reports IND)
 - Family: $X       (when 271 reports FAM)

Annual Max:
 - Total: $X
 - Remaining: $X

Coverage:
 - D1110: 100%

Frequency:
 - n/a

Waiting Periods:
 - n/a

Missing Tooth Clause:
 - n/a

Prior Auth / Predetermination:
 - Required: yes|no|n/a

Last Service Dates:
 - D1110: 2024-03-15

Age Limits:
 - Sealants up to age 14

Downgrades / Alternate Benefits:
 - Composite downgraded to amalgam

Estimates:
 - Patient estimated responsibility: $XXX

Plan Clauses:
 - <downgrade / age / alternate benefit text when present>

Verified by ezfi
```

OpenDental has no InsHist / age / pre-auth API endpoints — those specialist fields are
written as BenefitNotes/Commlog text and surfaced in the dashboard `vob_details` panel.
Structured Benefits rows still cover coinsurance, deductible, max, frequency, waiting, and
exclusions.

## Environment variables

```env
OPENDENTAL_BASE_URL=http://localhost:30222/api/v1
OPENDENTAL_DEVELOPER_KEY=
OPENDENTAL_CUSTOMER_KEY=
OPENDENTAL_WRITEBACK_ENABLED=false
OPENDENTAL_WRITE_BENEFIT_NOTES_ENABLED=true
OPENDENTAL_WRITE_SUBSCRIBER_NOTE_ENABLED=true
OPENDENTAL_WRITE_COMMLOG_ENABLED=true
OPENDENTAL_WRITE_INSADJUST_ENABLED=false
OPENDENTAL_WRITE_BENEFITS_GRID_ENABLED=false
OPENDENTAL_WRITE_BENEFITS_GRID_RESPECT_MANUAL_EDITS=true
OPENDENTAL_WRITE_BENEFITS_GRID_CONFIDENCE_GATING=false
OPENDENTAL_REVERIFY_WINDOW_DAYS=3
OPENDENTAL_AUTO_POLL_ENABLED=false
PILOT_SHADOW_MODE=false
```

Per-clinic writeback is controlled on the dashboard connection row; env flags
apply mainly to the legacy `from-opendental` route. Confidence gating defaults
**off** for full writeback demos; set `OPENDENTAL_WRITE_BENEFITS_GRID_CONFIDENCE_GATING=true`
for cautious production pilots.

## Pilot / demo enablement checklist

1. Enable Insurance ($30) API permission on the clinic Customer Key.
2. Dashboard: Write-back **on**, Full writeback **on**, Shadow compare **off** (applies L3/L4).
3. Run a sandbox patient; confirm TP Ins Est + remaining max/deductible update.
4. Optional caution mode: turn Shadow compare **on** and/or set confidence gating env to `true`.
5. Set poll window to **3** days for continuous 48–72h reverification under auto-poll.

## Clinic fee-schedule simulation (Layer 5)

To exercise **INN/OON fee paths** and Track E fee alerts without mutating OpenDental
FeeSched, seed fake economics into Supabase for `vgd_mock_brooklyn` + Cigna `62308`:

```bash
py -3.12 scripts/seed_clinic_sim.py status
py -3.12 scripts/seed_clinic_sim.py apply --scenario inn_happy
py -3.12 scripts/seed_clinic_sim.py apply --scenario oon_ucr
py -3.12 scripts/seed_clinic_sim.py apply --scenario missing_fees
py -3.12 scripts/seed_clinic_sim.py reset
```

| Scenario | Network | Fees | What to observe |
| --- | --- | --- | --- |
| `inn_happy` | INN | Contracted overlays (e.g. D0220 $45) | Layer 5 contracted path |
| `oon_ucr` | OON | Higher UCR-style overlays | Billed path; higher patient $ |
| `missing_fees` | INN | Demo CDTs removed for 62308 | Missing-fee flags / Track E alert |

Sim fee overlays use effective date `2026-07-26` (no notes column on
`payer_fee_schedules`). Network rows are tagged `[clinic_sim]` in
`contract_label` / `notes`. After apply, re-run
`POST /eligibility-agent/eligibility/from-opendental` with
`practice_id=vgd_mock_brooklyn`, `rendering_provider_npi=1104023674`, and
`write_back=true` (see script `--help`). For `missing_fees`, keep
`ELIGIBILITY_UCR_FALLBACK_ENABLED` off so fallback does not hide the gap.

## Hard rules

- Never write `adjustment`, `payment`, or fabricated `procedurelog` for eligibility.
- Never auto-assign OD fee schedules.
- Prefer OD REST; do not use direct MySQL/eConnector as the default write path.
- Never invent Implant/Emergency coverage % when the 271 did not return them.
