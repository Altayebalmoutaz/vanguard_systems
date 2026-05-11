export type RequestStatus = "queued" | "processing" | "retrying" | "completed" | "failed" | "needs_attention";

export type EligibilityRequest = {
  id: string;
  patient_id: string;
  first_name: string;
  last_name: string;
  dob: string;
  subscriber_id: string;
  primary_payer_id: string;
  secondary_payer_id: string | null;
  plan_id: string | null;
  cdt_codes: string[];
  trigger_event: string;
  status: RequestStatus;
  primary_check_id: string | null;
  secondary_check_id: string | null;
  input_json: Record<string, unknown>;
  output_json: Record<string, unknown>;
  error_message: string | null;
  error_code?: string | null;
  suggested_action?: string | null;
  failure_category?: string | null;
  status_reason?: string | null;
  priority?: "low" | "medium" | "high" | null;
  appointment_date?: string | null;
  appointment_time?: string | null;
  provider_name?: string | null;
  estimated_claim_value?: number | null;
  coverage_status?: "active" | "inactive" | "unknown" | null;
  attempt_count?: number | null;
  max_attempts?: number | null;
  started_at?: string | null;
  last_attempt_at?: string | null;
  locked_at?: string | null;
  locked_by?: string | null;
  next_retry_at?: string | null;
  parent_request_id?: string | null;
  idempotency_key?: string | null;
  agent_http_status?: number | null;
  agent_duration_ms?: number | null;
  edge_duration_ms?: number | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type EligibilityCheck = {
  id: string;
  patient_id: string;
  payer_id: string;
  checked_at: string;
  coverage_order: string | null;
  is_active: boolean | null;
  inactive_reason: string | null;
  is_covered: boolean | null;
  in_network: boolean | null;
  coverage_percent: number | null;
  copay: number | null;
  coinsurance: number | null;
  deductible_total: number | null;
  deductible_met: number | null;
  deductible_remaining: number | null;
  annual_max_total: number | null;
  annual_max_used: number | null;
  annual_max_remaining: number | null;
  response_complete: boolean | null;
  missing_fields: string[] | null;
  routing_status: string | null;
  integrity_warnings: string[] | null;
  raw_response: Record<string, unknown> | null;
  created_at: string;
};

export type ProcedureEstimate = {
  id: string;
  eligibility_check_id: string;
  cdt_code: string | null;
  procedure_covered: boolean | null;
  waiting_period_end: string | null;
  waiting_period_category: string | null;
  non_covered_reason: string | null;
  allowed_amount: number | null;
  insurance_pays: number | null;
  patient_responsibility: number | null;
  created_at: string;
};

export type DashboardRow = {
  request: EligibilityRequest;
  check: EligibilityCheck | null;
};

export type DashboardStatusLabel =
  | "Queued"
  | "Processing"
  | "Retrying"
  | "Verified"
  | "Needs Attention"
  | "Inactive"
  | "Failed";

export type EligibilityDashboardRow = {
  request_id: string;
  patient_id: string;
  first_name: string;
  last_name: string;
  patient_name: string;
  dob: string;
  subscriber_id: string;
  primary_payer_id: string;
  payer_label: string;
  secondary_payer_id: string | null;
  plan_id: string | null;
  cdt_codes: string[];
  trigger_event: string;
  request_status: RequestStatus;
  primary_check_id: string | null;
  secondary_check_id: string | null;
  error_message: string | null;
  error_code: string | null;
  suggested_action: string | null;
  failure_category: string | null;
  status_reason: string | null;
  priority: "low" | "medium" | "high";
  priority_rank: number;
  appointment_date: string | null;
  appointment_time: string | null;
  provider_name: string | null;
  estimated_claim_value: number | null;
  request_coverage_status: "active" | "inactive" | "unknown" | null;
  attempt_count: number;
  max_attempts: number;
  started_at: string | null;
  last_attempt_at: string | null;
  locked_at: string | null;
  locked_by: string | null;
  next_retry_at: string | null;
  parent_request_id: string | null;
  idempotency_key: string | null;
  agent_http_status: number | null;
  agent_duration_ms: number | null;
  edge_duration_ms: number | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  check_id: string | null;
  checked_at: string | null;
  coverage_order: string | null;
  is_active: boolean | null;
  inactive_reason: string | null;
  is_covered: boolean | null;
  in_network: boolean | null;
  coverage_percent: number | null;
  copay: number | null;
  coinsurance: number | null;
  deductible_total: number | null;
  deductible_met: number | null;
  deductible_remaining: number | null;
  annual_max_total: number | null;
  annual_max_used: number | null;
  annual_max_remaining: number | null;
  estimated_patient_responsibility: number | null;
  coverage_status: "active" | "inactive" | "unknown";
  response_complete: boolean | null;
  missing_fields_count: number;
  missing_fields: string[] | null;
  routing_status: string | null;
  integrity_warnings_count: number;
  integrity_warnings: string[] | null;
  raw_response: Record<string, unknown> | null;
  status_label: DashboardStatusLabel;
  status_detail: string | null;
};

export type EligibilityRequestEvent = {
  id: string;
  request_id: string;
  event_type: string;
  detail: Record<string, unknown>;
  created_at: string;
};

export type EligibilityAgentSettings = {
  id: boolean;
  auto_check_enabled: boolean;
  auto_retry_enabled: boolean;
  last_sync_at: string | null;
  next_retry_at: string | null;
  updated_at: string;
};

export type AgentActivityItem = {
  id: string;
  request_id: string;
  patient_name: string | null;
  event_type: string;
  detail: Record<string, unknown>;
  created_at: string;
};

export type AgentStatusSummary = {
  online: boolean;
  last_event_at: string | null;
  next_retry_at: string | null;
  today_total: number;
  today_verified: number;
  today_retried: number;
  today_awaiting_human: number;
  today_auto_handled: number;
  auto_handled_pct: number;
};
