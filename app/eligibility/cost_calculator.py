"""Layer 5 — Patient responsibility (pure; DB write handled by caller via db.py)."""

from __future__ import annotations

from typing import Any

COPAY_ONLY_AMBIGUOUS_WARNING = (
    "Full estimate unavailable — deductible remaining unknown. Copay amount confirmed."
)

# Rounding tolerance for iterative cap reconciliation (cents-level).
_CAP_EPS = 0.005


def _money(v: float) -> float:
    # Financial outputs are rounded to cents for deterministic billing behavior.
    return round(float(v), 2)


def _dedupe_flags(flags: list[str]) -> list[str]:
    out: list[str] = []
    for f in flags:
        if f not in out:
            out.append(f)
    return out


def _reconcile_line_benefit_caps(
    *,
    reconcile_total: float,
    insurance_raw: float,
    max_left: float,
    insurance_pays: float,
    patient_share: float,
    oop_left: float | None,
    sd_left: float | None,
    cc_left: float | None,
) -> tuple[float, float, list[str]]:
    """
    Balance insurer vs patient dollars while respecting annual-max pool and optional
    patient-side running caps (OOP, spend-down, cost containment).

    ``reconcile_total`` is ``allowed_amount + copay_on_this_row`` so patient + insurer
    stays consistent when a flat copay is stacked on the fee schedule line.

    Iterates AM clamp vs patient caps because shifting patient dollars to the insurer for
    one cap can violate annual max. When caps mathematically conflict (oscillation),
    resolves with ``insurance_pays = min(insurance_raw, max_left)`` and the residual patient
    share (``benefit_caps_conflict_am_priority``).
    """
    flags: list[str] = []
    ins = _money(insurance_pays)
    pat = _money(patient_share)
    pool = _money(reconcile_total)

    converged = False
    for _ in range(32):
        moved = False

        if ins > max_left + _CAP_EPS:
            excess = _money(ins - max_left)
            ins = _money(max_left)
            pat = _money(pat + excess)
            flags.append("annual_max_cap_applied")
            moved = True

        if oop_left is not None and pat > oop_left + _CAP_EPS:
            excess = _money(pat - oop_left)
            pat = _money(oop_left)
            ins = _money(ins + excess)
            flags.append("out_of_pocket_max_remaining_cap_applied")
            moved = True

        if sd_left is not None and pat > sd_left + _CAP_EPS:
            excess = _money(pat - sd_left)
            pat = _money(sd_left)
            ins = _money(ins + excess)
            flags.append("spend_down_remaining_cap_applied")
            moved = True

        if cc_left is not None and pat > cc_left + _CAP_EPS:
            excess = _money(pat - cc_left)
            pat = _money(cc_left)
            ins = _money(ins + excess)
            flags.append("cost_containment_remaining_cap_applied")
            moved = True

        drift = _money(pool - ins - pat)
        if abs(drift) > _CAP_EPS:
            pat = _money(pat + drift)
            moved = True

        if not moved:
            converged = True
            break

    if not converged:
        ins = _money(min(insurance_raw, max_left))
        pat = _money(pool - ins)
        flags.append("benefit_caps_conflict_am_priority")

    return ins, pat, _dedupe_flags(flags)


def calculate_responsibility(
    canonical: dict[str, Any], fee_schedule: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Only invoke when response_complete and is_active are True (enforced by caller).

    fee_schedule shape:
      - contracted: { payer_id: { cdt: amount } }
      - billed: { cdt: amount }  (UCR / billed charge)

    When ``canonical["copay"]`` is set (EB*B-style flat amount), it is added once to the
    first procedure row that is not explicitly non-covered. That approximates a per-visit
    copay without multiplying it across every CDT line on the same encounter.

    Optional running caps on **covered** procedures (all skip when the canonical value is
    ``None``):

    - ``out_of_pocket_max_remaining`` (EB*G-style): shift patient dollars to insurer when
      patient share would exceed remaining OOP room.
    - ``spend_down_remaining`` (EB*Y-style): same pattern for spend-down remainder.
    - ``cost_containment_remaining`` (EB*J-style): same pattern for cost containment.

    ``annual_max_remaining`` limits insurer payment from the plan-benefit pool **before**
    and **after** those shifts via iterative reconciliation. If annual max and patient caps
    cannot both be satisfied, annual max wins for insurer dollars and
    ``benefit_caps_conflict_am_priority`` is recorded (patient share may still exceed an OOP
    remainder on paper — inspect flags).
    """
    if not canonical.get("response_complete") or not canonical.get("is_active"):
        raise ValueError("calculate_responsibility requires response_complete and is_active")

    payer_id = str(canonical.get("payer_id") or "")
    _fee_network = canonical.get("in_network_for_fees")
    if _fee_network is None:
        _fee_network = canonical.get("in_network")
    in_network = bool(_fee_network)
    deductible_remaining = float(canonical.get("deductible_remaining") or 0.0)
    coverage_percent = float(canonical.get("coverage_percent") or 0.0)
    annual_max_remaining = float(canonical.get("annual_max_remaining") or 0.0)

    def _optional_float(key: str) -> float | None:
        v = canonical.get(key)
        return None if v is None else float(v)

    oop_left = _optional_float("out_of_pocket_max_remaining")
    sd_left = _optional_float("spend_down_remaining")
    cc_left = _optional_float("cost_containment_remaining")

    contracted = fee_schedule.get("contracted") or {}
    payer_map = contracted.get(payer_id) or contracted.get(payer_id.upper()) or {}
    billed = fee_schedule.get("billed") or {}

    rows: list[dict[str, Any]] = []
    ded_left = deductible_remaining
    max_left = annual_max_remaining
    flat_copay = _money(float(canonical.get("copay") or 0.0))
    copay_applied = False

    for proc in canonical.get("procedure_details") or []:
        cdt = str(proc.get("cdt_code") or "").strip().upper()
        procedure_covered = proc.get("procedure_covered")
        estimate_flags: list[str] = []

        if procedure_covered is False:
            ucr = _money(float(billed.get(cdt) or 0.0))
            if ucr <= 0.0:
                estimate_flags.append("missing_fee_schedule_or_billed_amount")
            rows.append(
                {
                    "cdt_code": cdt,
                    "allowed_amount": ucr,
                    "insurance_pays": 0.0,
                    "patient_responsibility": ucr,
                    "estimate_flags": estimate_flags,
                }
            )
            continue

        if in_network:
            allowed = _money(float(payer_map.get(cdt) or billed.get(cdt) or 0.0))
        else:
            allowed = _money(float(billed.get(cdt) or 0.0))

        if allowed <= 0.0:
            estimate_flags.append("missing_fee_schedule_or_billed_amount")

        deductible_applied = _money(min(ded_left, allowed))
        post_deductible = _money(allowed - deductible_applied)
        insurance_raw = _money(post_deductible * (coverage_percent / 100.0))
        insurance_pays = _money(min(insurance_raw, max_left))
        patient_share = _money(deductible_applied + (post_deductible - insurance_pays))

        copay_this_row = 0.0
        if flat_copay > 0.0 and not copay_applied and procedure_covered is not False:
            copay_this_row = flat_copay
            patient_share = _money(patient_share + copay_this_row)
            estimate_flags.append("flat_visit_copay_from_271_applied_once")
            copay_applied = True

        reconcile_total = _money(allowed + copay_this_row)

        cap_flags: list[str] = []
        if oop_left is not None or sd_left is not None or cc_left is not None:
            insurance_pays, patient_share, cap_flags = _reconcile_line_benefit_caps(
                reconcile_total=reconcile_total,
                insurance_raw=insurance_raw,
                max_left=max_left,
                insurance_pays=insurance_pays,
                patient_share=patient_share,
                oop_left=oop_left,
                sd_left=sd_left,
                cc_left=cc_left,
            )
            estimate_flags.extend(cap_flags)
        else:
            # No patient-side caps from 271: still enforce annual max after any rounding drift.
            if insurance_pays > max_left + _CAP_EPS:
                excess = _money(insurance_pays - max_left)
                insurance_pays = _money(max_left)
                patient_share = _money(patient_share + excess)
                estimate_flags.append("annual_max_cap_applied")
            patient_share = _money(reconcile_total - insurance_pays)

        if oop_left is not None:
            oop_left = _money(max(0.0, oop_left - patient_share))
        if sd_left is not None:
            sd_left = _money(max(0.0, sd_left - patient_share))
        if cc_left is not None:
            cc_left = _money(max(0.0, cc_left - patient_share))

        max_left = _money(max(0.0, max_left - insurance_pays))
        ded_left = _money(max(0.0, ded_left - deductible_applied))

        if (
            insurance_raw > insurance_pays + _CAP_EPS
            and "annual_max_cap_applied" not in estimate_flags
        ):
            estimate_flags.append("annual_max_cap_applied")

        rows.append(
            {
                "cdt_code": cdt,
                "allowed_amount": allowed,
                "insurance_pays": insurance_pays,
                "patient_responsibility": patient_share,
                "estimate_flags": _dedupe_flags(estimate_flags),
            }
        )

    return rows


def apply_coinsurance_ambiguous_missing_field(canonical: dict[str, Any]) -> None:
    """
    When coinsurance is known but annual max remaining is unknown, coinsurance $ estimate is skipped.
    """
    co = canonical.get("coinsurance")
    mt = canonical.get("annual_max_total")
    mr = canonical.get("annual_max_remaining")
    if co is None or mt is None or mr is not None:
        return
    tag = "max_remaining_required_for_coinsurance_estimate"
    mf = list(canonical.get("missing_fields") or [])
    if tag not in mf:
        mf.append(tag)
    canonical["missing_fields"] = mf


def build_coverage_ambiguous_partial_estimates(canonical: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Copay-only partial estimate for COVERAGE_AMBIGUOUS (no fee schedule / full actuarial path).
    """
    copay = canonical.get("copay")
    if copay is None:
        return []
    return [
        {
            "patient_responsibility": _money(float(copay)),
            "estimate_basis": "copay_only",
            "estimate_confidence": "medium",
            "warning": COPAY_ONLY_AMBIGUOUS_WARNING,
        }
    ]
