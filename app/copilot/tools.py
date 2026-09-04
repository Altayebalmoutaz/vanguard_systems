"""Read-only copilot tools. The registry is the write-boundary: no write methods."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from app.config import Settings
from app.dashboard.store import list_procedure_estimates_for_check
from app.integrations.opendental.client import OpenDentalClient
from app.integrations.opendental.errors import OpenDentalAPIError
from app.rcm.posting_rules import lookup_carc

READ_ONLY_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "get_patient_overview",
        "get_insurance_and_benefits",
        "get_recent_procedures",
        "get_claims_and_payments",
        "get_appointments",
        "get_treatment_plan",
        "get_account_ledger",
        "get_claim_procedures",
        "get_recalls",
        "get_commlogs",
        "get_documents",
        "get_referrals",
        "get_statements",
        "get_health_history",
        "get_perio_exams",
        "get_clinical_notes",
        "get_family_members",
        "get_eligibility_history",
        "explain_carc_code",
    }
)

# Methods that must never be reachable from execute_tool.
BLOCKED_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "create_commlog",
        "put_claimproc_insadjust",
        "update_benefit",
        "create_benefit",
        "create_insverify",
        "update_inssub_benefit_notes",
        "update_inssub_subscriber_note",
        "create_insurance_history",
        "short_query",
    }
)


@dataclass
class ToolContext:
    settings: Settings
    practice_id: str
    patient_id: UUID
    od_pat_num: int | None
    client: OpenDentalClient | None
    profile: dict[str, Any]


def _dump(model: Any) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if hasattr(model, "__dict__"):
        return dict(model.__dict__)
    return {"value": model}


def _require_od(ctx: ToolContext) -> tuple[OpenDentalClient, int]:
    if ctx.client is None:
        raise OpenDentalAPIError("OpenDental is not connected for this practice")
    if ctx.od_pat_num is None:
        raise OpenDentalAPIError("No OpenDental PatNum is on file for this patient")
    return ctx.client, ctx.od_pat_num


def _strip_raw_check(check: dict[str, Any] | None) -> dict[str, Any] | None:
    if not check:
        return None
    return {key: value for key, value in check.items() if key != "raw_response"}


def tool_get_patient_overview(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    patient = ctx.profile.get("patient") or {}
    latest = _strip_raw_check(ctx.profile.get("latest_eligibility_check"))
    od_patient: dict[str, Any] | None = None
    if ctx.client is not None and ctx.od_pat_num is not None:
        od_patient = _dump(ctx.client.get_patient(ctx.od_pat_num))
    return {
        "source": "vanguard_patient_360" + ("+opendental" if od_patient else ""),
        "patient": patient,
        "latest_eligibility_check": latest,
        "opendental_patient": od_patient,
        "od_pat_num": ctx.od_pat_num,
    }


def tool_get_insurance_and_benefits(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    rows = client.get_patient_insurance(pat_num)
    plans: list[dict[str, Any]] = []
    for row in rows:
        carrier = _dump(client.get_carrier(row.CarrierNum))
        benefits = [_dump(item) for item in client.get_benefits(row.PlanNum)]
        plans.append(
            {
                "insurance": _dump(row),
                "carrier": carrier,
                "benefits": benefits,
            }
        )
    return {"source": "opendental", "plans": plans}


def tool_get_recent_procedures(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.procedurelog",
        "procedures": client.get_procedures_for_patient(pat_num),
    }


def tool_get_claims_and_payments(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.claim",
        "claims": client.get_claims_for_patient(pat_num),
    }


def tool_get_appointments(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.appointment",
        "appointments": client.get_appointments_for_patient(pat_num),
    }


def tool_get_treatment_plan(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.procedurelog.tp",
        "treatment_plan": client.get_treatment_plan_for_patient(pat_num),
    }


def tool_get_account_ledger(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    summary = client.get_account_summary_for_patient(pat_num)
    return {
        "source": "opendental.account",
        "summary": summary[0] if summary else None,
        "payments": client.get_payments_for_patient(pat_num),
        "adjustments": client.get_adjustments_for_patient(pat_num),
    }


def tool_get_claim_procedures(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.claimproc",
        "claim_procedures": client.get_claim_procedures_for_patient(pat_num),
    }


def tool_get_recalls(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.recall",
        "recalls": client.get_recalls_for_patient(pat_num),
    }


def tool_get_commlogs(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.commlog",
        "commlogs": client.get_commlogs_for_patient(pat_num),
    }


def tool_get_documents(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.document",
        "documents": client.get_documents_for_patient(pat_num),
    }


def tool_get_referrals(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.refattach",
        "referrals": client.get_referrals_for_patient(pat_num),
    }


def tool_get_statements(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.statement",
        "statements": client.get_statements_for_patient(pat_num),
    }


def tool_get_health_history(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.health_history",
        "medications": client.get_medications_for_patient(pat_num),
        "allergies": client.get_allergies_for_patient(pat_num),
        "problems": client.get_problems_for_patient(pat_num),
    }


def tool_get_perio_exams(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.perioexam",
        "perio_exams": client.get_perio_exams_for_patient(pat_num),
    }


def tool_get_clinical_notes(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.procnote",
        "clinical_notes": client.get_clinical_notes_for_patient(pat_num),
    }


def tool_get_family_members(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    client, pat_num = _require_od(ctx)
    return {
        "source": "opendental.patient.family",
        "family_members": client.get_family_members_for_patient(pat_num),
    }


def tool_get_eligibility_history(ctx: ToolContext, _args: dict[str, Any]) -> dict[str, Any]:
    latest = _strip_raw_check(ctx.profile.get("latest_eligibility_check"))
    estimates: list[dict[str, Any]] = []
    check_id = (latest or {}).get("id")
    if check_id:
        estimates = list_procedure_estimates_for_check(
            ctx.settings,
            practice_id=ctx.practice_id,
            eligibility_check_id=UUID(str(check_id)),
        )
    return {
        "source": "vanguard.eligibility",
        "latest_eligibility_check": latest,
        "procedure_estimates": estimates,
    }


def tool_explain_carc_code(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    del ctx
    reason = str(args.get("reason_code") or "").strip()
    remarks_raw = args.get("remark_codes") or []
    remarks = [str(code) for code in remarks_raw] if isinstance(remarks_raw, list) else []
    policy = lookup_carc(reason, remark_codes=remarks)
    if policy is None:
        return {
            "source": "vanguard.carc_policy",
            "reason_code": reason,
            "found": False,
            "note": "No v1 posting policy for this CARC.",
        }
    return {
        "source": "vanguard.carc_policy",
        "reason_code": reason,
        "found": True,
        "policy": asdict(policy),
    }


_HANDLERS: dict[str, Any] = {
    "get_patient_overview": tool_get_patient_overview,
    "get_insurance_and_benefits": tool_get_insurance_and_benefits,
    "get_recent_procedures": tool_get_recent_procedures,
    "get_claims_and_payments": tool_get_claims_and_payments,
    "get_appointments": tool_get_appointments,
    "get_treatment_plan": tool_get_treatment_plan,
    "get_account_ledger": tool_get_account_ledger,
    "get_claim_procedures": tool_get_claim_procedures,
    "get_recalls": tool_get_recalls,
    "get_commlogs": tool_get_commlogs,
    "get_documents": tool_get_documents,
    "get_referrals": tool_get_referrals,
    "get_statements": tool_get_statements,
    "get_health_history": tool_get_health_history,
    "get_perio_exams": tool_get_perio_exams,
    "get_clinical_notes": tool_get_clinical_notes,
    "get_family_members": tool_get_family_members,
    "get_eligibility_history": tool_get_eligibility_history,
    "explain_carc_code": tool_explain_carc_code,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_patient_overview",
            "description": (
                "Demographics and latest eligibility summary for the anchored patient "
                "from Vanguard, plus the OpenDental patient record when connected."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_insurance_and_benefits",
            "description": (
                "OpenDental insurance plans, carriers, and structured benefit-grid rows "
                "for the anchored patient."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_procedures",
            "description": "The 25 most recent OpenDental procedurelog rows for this patient.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_claims_and_payments",
            "description": (
                "Recent OpenDental claim headers for this patient, including fees and "
                "insurance paid amounts."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_appointments",
            "description": (
                "OpenDental appointments for this patient (date/time, decoded status, "
                "provider, operatory, hygiene flag, note)."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_treatment_plan",
            "description": (
                "Treatment-planned (TP) OpenDental procedures: code, description, fee, "
                "tooth, surface, and planned date."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_ledger",
            "description": (
                "Account aging/balance plus recent payments and adjustments from "
                "OpenDental for this patient."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_claim_procedures",
            "description": (
                "OpenDental claimproc rows for this patient: estimate vs paid, "
                "deductible, write-off, and decoded status."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recalls",
            "description": "Hygiene/recall due and scheduled dates from OpenDental.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_commlogs",
            "description": "Recent OpenDental communication-log notes for this patient.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_documents",
            "description": (
                "Document metadata from OpenDental (filename, category, date) — not file bytes."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_referrals",
            "description": "Referral attachments and referred-to providers from OpenDental.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_statements",
            "description": "Recent billing statements sent to this patient from OpenDental.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_health_history",
            "description": (
                "Active medications, allergies, and problems/diagnoses from the "
                "OpenDental clinical chart."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_perio_exams",
            "description": ("Periodontal exam headers (exam date, provider) — no pocket measures."),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clinical_notes",
            "description": "Recent OpenDental procedure notes (procnote) for this patient.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_family_members",
            "description": ("Family members who share this patient's OpenDental guarantor."),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_eligibility_history",
            "description": (
                "Latest Vanguard eligibility check and per-procedure estimates "
                "(allowed, insurance pays, patient responsibility)."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "explain_carc_code",
            "description": (
                "Explain an 835 CARC/reason code using Vanguard posting policy "
                "(next action, whether to bill the patient, whether to appeal)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason_code": {
                        "type": "string",
                        "description": "CARC number or token such as 45, 16, or CO-45.",
                    },
                    "remark_codes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional RARC / remark codes (e.g. M127).",
                    },
                },
                "required": ["reason_code"],
                "additionalProperties": False,
            },
        },
    },
]


class UnknownCopilotToolError(ValueError):
    """The model asked for a tool that is not in the read-only registry."""


def execute_tool(name: str, args: dict[str, Any], *, ctx: ToolContext) -> dict[str, Any]:
    if name in BLOCKED_WRITE_METHODS or name not in _HANDLERS:
        raise UnknownCopilotToolError(name)
    handler = _HANDLERS[name]
    try:
        return handler(ctx, args or {})
    except OpenDentalAPIError as exc:
        return {
            "error": "opendental_unavailable",
            "tool": name,
            "message": "OpenDental read failed or is not connected.",
            "status_code": exc.status_code,
        }
