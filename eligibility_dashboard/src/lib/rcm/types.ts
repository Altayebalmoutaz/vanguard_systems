// Display types for the RCM module pages. Field names mirror the backend
// Pydantic schemas (app/schemas/*.py) so demo data maps 1:1 to real responses.

export type RiskLevel = "low" | "medium" | "high";

export type CodingStatus = "pending_review" | "approved" | "rejected";

export type CodingCaseSourceType = "agent_decision" | "rcm_task";

export type CodingCase = {
  id: string;
  source_type?: CodingCaseSourceType;
  decision_id?: string;
  hitl_task_id?: string;
  encounter_id: string;
  patient_name: string;
  dob: string;
  provider_name: string;
  payer: string;
  clinical_note: string;
  cdt_codes: string[];
  icd10_codes: string[];
  confidence: number; // 0..1
  justification: string;
  payer_flags: string[];
  payer_rules_matched: { rule: string; detail: string }[];
  status: CodingStatus;
  created_at: string;
};

export type PriorAuthStatus = "pending_review" | "submitted" | "approved";

export type PriorAuthCase = {
  id: string;
  patient_name: string;
  dob: string;
  procedure: string; // primary CDT
  procedure_label: string;
  payer: string;
  requires_auth: boolean;
  required_documents: string[];
  payer_rules: string[];
  risk_level: RiskLevel;
  risk_reason: string;
  status: PriorAuthStatus;
  created_at: string;
};

export type ClaimStatus = "draft" | "pending_auth" | "submitted" | "paid";

export type ClaimServiceLine = {
  cdt_code: string;
  description: string;
  charge_amount: number;
};

export type ClaimCase = {
  claim_id: string;
  patient_name: string;
  dob: string;
  payer: string;
  provider_name: string;
  status: ClaimStatus;
  submission_channel: "none" | "stedi_mock" | "stedi";
  diagnosis_codes: string[];
  service_lines: ClaimServiceLine[];
  total_charge_amount: number;
  blockers: string[];
  available_actions: ("edit" | "submit")[];
  created_at: string;
};

export type DenialStatus = "denied" | "partial" | "paid";

export type DenialCase = {
  claim_id: string;
  patient_name: string;
  dob: string;
  payer: string;
  status: DenialStatus;
  reason: string; // reason token
  reason_label: string;
  next_action: string;
  amount_at_risk: number;
  resubmission_steps: string[];
  required_evidence: string[];
  reasoning_summary: string;
  appeal_letter: string;
  requires_human_review: boolean;
  created_at: string;
};

export type JourneyStageKey = "eligibility" | "coding" | "prior_auth" | "claim" | "denial";

export type JourneyStageStatus = "done" | "current" | "blocked" | "pending" | "skipped";

export type JourneyStage = {
  key: JourneyStageKey;
  label: string;
  status: JourneyStageStatus;
  detail: string;
};
