# Coding Agent response to Field Gap Report (`md-1.1.0` → `POST /v1/suggest` §4.1)

**To:** Scribe / extraction team  
**From:** Vanguard Coding Agent  
**Date:** 2026-08-13  
**Live:** `https://ezfi.smilesuite.ai/coding-agent`  
**Contract:** `schema_version` stays `"1.0"` (additive fields; no version bump)  
**OpenAPI:** `https://ezfi.smilesuite.ai/coding-agent/openapi.json`

This is the reply to `03-field-gap-report`. The §4.1 envelope is accepted. Host vs note ownership is accepted. The Prompt 1 patches in your §4 should be applied. Several of the predicted API gaps are already fixed on our side.

---

## 1. What we implemented (coding agent)

### Live now (v1.3 + v1.4)

| Item | Gap ID | Behavior |
|---|---|---|
| Radiograph aliases | G-09 | `full_mouth_series`, `fmx`, `pano` / `panoramic` satisfy radiograph docs. `periodontal_chart` is accepted but is **not** a radiograph. |
| False restorative gaps | — | Exam / eval / imaging lines no longer demand tooth or surface. “Buccal mucosa” and furcation “buccal” are anatomy, not filling surfaces. Crowns that mention an existing amalgam or lingual cusp do not require surfaces. |
| Crown material | G-07 | If the planned material is not documented, we return `cdt_code: null` and `CDT_UNCERTAIN`. We do **not** guess D2740. Existing gold is not assumed to be the replacement material. |
| D4346 vs laser / SRP | G-08 | D4346 is gingivitis-only. We clear it when the encounter has SRP, periodontitis, or laser-assisted therapy. |
| Gingival irrigation | G-08 | D4999 on an irrigation line is rewritten to **D4921**. |
| Same-day evals | G-12 | D0150 and D0180 on the same DOS: we keep **D0180** and drop D0150. Prefer you emit only the billed eval. |
| Perio stakes | — | `D43` is high-stakes (no silent auto-approve). D4346 always gets a verifier pass. |
| **`procedures[].quadrant`** | **G-13** | Optional: `UR` \| `UL` \| `LR` \| `LL`. Aliases (`upper right`, `maxillary left`, …) accepted. |
| **`procedures[].arch`** | **G-13** | Optional: `maxillary` \| `mandibular` \| `full_mouth`. |
| Findings fallback | G-13 | `"quadrant: UR"` in `findings[]` still works if the field is omitted. |
| D4341 vs D4342 | G-06 | Chosen from **tooth count in that quadrant**: 4+ → D4341, 1–3 → D4342. Quadrant alone is not enough. |
| D4921 scope | G-08 | A resolved quadrant is required (`quadrant` field or findings token). Missing → `CDT_UNCERTAIN`. |

Existing payloads without `quadrant` / `arch` remain valid.

### Example SRP line

```json
{
  "line_id": "P3-UR",
  "quadrant": "UR",
  "tooth_numbers": ["2", "3", "4", "5"],
  "findings": ["Non-surgical periodontal therapy"],
  "planned_or_performed": "planned"
}
```

---

## 2. What we need from your end

### Integration layer (your §6) — do this on every call

- [ ] Substitute every `<<HOST:…>>` sentinel, or **omit** the `payer` object if the plan is unknown.
- [ ] Fail closed: do not transmit any payload that still contains `<<HOST:`.
- [ ] Mint `request_id` (UUID) once per encounter; persist it; resend unchanged on retry / 5xx.
- [ ] Persist `coding_run_id` from the response.
- [ ] Always render `recommendations[]`, including when `status` is `needs_info`.
- [ ] Never send `extraction_report` to the API.
- [ ] `request_id` stays host-owned forever. It is the idempotency key.

### Prompt 1 — apply these patches

| ID | Change | Required? |
|---|---|---|
| **G-01–G-05** | Add `## Encounter Metadata`: Date of Service (ISO-8601 + offset), Practice ID, Patient ID, Provider ID, Provider Role, Payer / Payer ID. Host fills what the note cannot. | **Yes.** Missing DOS / ids → HTTP 422. Payer missing is advisory only. |
| **G-06** | One SRP line per quadrant, **naming the teeth** (`- **UR (#2, #3, #4, #5)** — Non-surgical periodontal therapy`). Never “all four quadrants” as one line. | **Yes.** This is the D4341 vs D4342 switch. |
| **G-07** | Planned crown / onlay / inlay / bridge / denture must state **planned material**. Existing restoration material is not assumed. | **Yes.** |
| **G-08** | Adjuncts carry their own scope (`- Gingival irrigation, all four quadrants` or per-quad lines with `quadrant`). | **Yes.** |
| **G-09** | Declare attachments with a closed list. Normalize spaces to underscores: `full mouth series` → `full_mouth_series`. Tokens we recognize as radiographs: `full_mouth_series`, `fmx`, `bitewing_radiograph`, `periapical_radiograph`, `panoramic_radiograph`. | **Yes.** |
| **G-11** | Keep PPE and pre-procedural rinse **out** of `### Completed`. | **Yes**, unless you intend a permanent `ask` line every visit. |
| **G-12** | One billed evaluation per date of service. | **Yes.** If both arrive we keep D0180. |
| **G-10** | Care coordination / case presentation as Completed lines only if the practice bills them. | Optional. |
| **G-13** | Emit `procedures[].quadrant` (`UR`/`UL`/`LR`/`LL`) and `procedures[].arch` when applicable. Omit on tooth-specific or whole-mouth lines. | **Please start.** Findings token still works as a fallback. |
| **G-14** | Restored / missing teeth with no procedure. | Optional. `supporting_note` is enough for now. |

### Attachment token mapping

| Prompt 1 label | Send as |
|---|---|
| full mouth series | `full_mouth_series` |
| panoramic | `panoramic_radiograph` |
| bitewings | `bitewing_radiograph` |
| periapical | `periapical_radiograph` |
| CBCT | `cbct` |
| periodontal chart | `periodontal_chart` (not a radiograph) |

---

## 3. Predicted status after both sides land

| | Your report (“today”) | After Prompt 1 §4 + these agent updates |
|---|---|---|
| Host-injected required fields | 4 + `request_id` | `request_id` only |
| Typical `status` | `needs_info` | `pending_review` |
| Blocking gaps on the sample note | 10 | 0, unless PPE/rinse stay on the chart |
| Quadrant fidelity | findings token | `procedures[].quadrant` (token still accepted) |

Until G-06 (teeth per SRP quadrant) is in Prompt 1, that encounter will still return `needs_info` for the four SRP lines.

---

## 4. Support

Correlate with `request_id` + `coding_run_id`. Pilot data only (synthetic / approved test). Do not put names, member ids, or dates of birth on the wire.
