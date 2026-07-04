"use client";

import { MiniSparkline } from "@/components/MiniSparkline";
import { PatientAvatar } from "@/components/PatientAvatar";
import { PayerLogo } from "@/components/PayerLogo";
import { dashboardUserDisplayName } from "@/lib/dashboardEnv";
import {
  createEligibilityRequest,
  fetchEligibilityActivity,
  fetchEligibilityQueue,
  fetchEligibilitySettings,
  fetchProcedureEstimates,
  fetchRequestEvents,
} from "@/lib/eligibilityApi";
import type {
  AgentStatusSummary,
  DashboardRow,
  DashboardStatusLabel,
  EligibilityAgentSettings,
  EligibilityDashboardRow,
  EligibilityRequestEvent,
  ProcedureEstimate,
} from "@/lib/types";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BrainCircuit,
  Calendar,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  Cpu,
  CreditCard,
  Database,
  Download,
  FileText,
  Loader2,
  Network,
  Phone,
  Plus,
  RotateCw,
  ScanLine,
  Search,
  ShieldCheck,
  Sparkles,
  Stethoscope,
  Users,
  X,
} from "lucide-react";
import { ConfidenceGauge, RadialDonut } from "@/components/ui/Gauges";
import { useClientValue } from "@/hooks/useClientValue";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

type FilterValue = "all" | "verified" | "inactive" | "attention";
type PanelMode = "details" | "form" | null;

type FormState = {
  first_name: string;
  last_name: string;
  dob: string;
  subscriber_id: string;
  primary_payer_id: string;
  secondary_payer_id: string;
  plan_id: string;
  cdt_codes: string;
  priority: "low" | "medium" | "high";
  appointment_date: string;
  appointment_time: string;
  provider_name: string;
  estimated_claim_value: string;
};

const emptyForm: FormState = {
  first_name: "",
  last_name: "",
  dob: "",
  subscriber_id: "",
  primary_payer_id: "",
  secondary_payer_id: "",
  plan_id: "",
  cdt_codes: "",
  priority: "medium",
  appointment_date: "",
  appointment_time: "",
  provider_name: "",
  estimated_claim_value: "",
};

const demoRows: DashboardRow[] = [
  {
    request: {
      id: "demo-1",
      patient_id: "demo-patient-1",
      first_name: "Sarah",
      last_name: "Mitchell",
      dob: "1988-04-12",
      subscriber_id: "BCB-4421-09",
      primary_payer_id: "BlueCross BlueShield",
      secondary_payer_id: null,
      plan_id: "PPO Gold",
      cdt_codes: ["D0120", "D1110"],
      trigger_event: "APPOINTMENT_BOOKED",
      status: "completed",
      primary_check_id: "demo-check-1",
      secondary_check_id: null,
      input_json: {},
      output_json: {},
      error_message: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    },
    check: {
      id: "demo-check-1",
      patient_id: "demo-patient-1",
      payer_id: "BlueCross BlueShield",
      checked_at: new Date(Date.now() - 12 * 60_000).toISOString(),
      coverage_order: "primary",
      is_active: true,
      inactive_reason: null,
      is_covered: true,
      in_network: true,
      coverage_percent: 80,
      copay: 20,
      coinsurance: 20,
      deductible_total: 750,
      deductible_met: 200,
      deductible_remaining: 550,
      annual_max_total: 1500,
      annual_max_used: 300,
      annual_max_remaining: 1200,
      response_complete: true,
      missing_fields: [],
      routing_status: "CLEARED",
      integrity_warnings: [],
      raw_response: null,
      created_at: new Date().toISOString(),
    },
  },
  {
    request: {
      id: "demo-2",
      patient_id: "demo-patient-2",
      first_name: "Priya",
      last_name: "Nair",
      dob: "1979-09-21",
      subscriber_id: "MTL-0019-22",
      primary_payer_id: "MetLife",
      secondary_payer_id: null,
      plan_id: "Basic",
      cdt_codes: ["D2740"],
      trigger_event: "APPOINTMENT_BOOKED",
      status: "completed",
      primary_check_id: "demo-check-2",
      secondary_check_id: null,
      input_json: {},
      output_json: {},
      error_message: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    },
    check: {
      id: "demo-check-2",
      patient_id: "demo-patient-2",
      payer_id: "MetLife",
      checked_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
      coverage_order: "primary",
      is_active: true,
      inactive_reason: null,
      is_covered: true,
      in_network: null,
      coverage_percent: null,
      copay: 35,
      coinsurance: null,
      deductible_total: 500,
      deductible_met: 0,
      deductible_remaining: 500,
      annual_max_total: null,
      annual_max_used: null,
      annual_max_remaining: null,
      response_complete: false,
      missing_fields: ["coverage_percent", "in_network"],
      routing_status: "NEEDS_REVIEW",
      integrity_warnings: ["Incomplete payer response"],
      raw_response: null,
      created_at: new Date().toISOString(),
    },
  },
  {
    request: {
      id: "demo-3",
      patient_id: "demo-patient-3",
      first_name: "Carlos",
      last_name: "Mendez",
      dob: "1968-12-02",
      subscriber_id: "AET-8831-QQ",
      primary_payer_id: "Aetna",
      secondary_payer_id: null,
      plan_id: "PPO Standard",
      cdt_codes: ["D7210"],
      trigger_event: "APPOINTMENT_BOOKED",
      status: "completed",
      primary_check_id: "demo-check-3",
      secondary_check_id: null,
      input_json: {},
      output_json: {},
      error_message: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
    },
    check: {
      id: "demo-check-3",
      patient_id: "demo-patient-3",
      payer_id: "Aetna",
      checked_at: new Date(Date.now() - 5 * 60 * 60_000).toISOString(),
      coverage_order: "primary",
      is_active: false,
      inactive_reason: "Coverage inactive",
      is_covered: false,
      in_network: null,
      coverage_percent: null,
      copay: null,
      coinsurance: null,
      deductible_total: null,
      deductible_met: null,
      deductible_remaining: null,
      annual_max_total: null,
      annual_max_used: null,
      annual_max_remaining: null,
      response_complete: true,
      missing_fields: [],
      routing_status: "INACTIVE",
      integrity_warnings: [],
      raw_response: null,
      created_at: new Date().toISOString(),
    },
  },
];

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `$${Math.round(value)}`;
}

function timeAgo(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  const diff = Date.now() - date.getTime();
  if (Number.isNaN(diff)) return "-";
  const minutes = Math.max(1, Math.round(diff / 60_000));
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return "Yesterday";
}

function deriveStatus(row: DashboardRow): DashboardStatusLabel {
  if (row.request.status === "failed") return "Failed";
  if (row.request.status === "queued") return "Queued";
  if (row.request.status === "processing") return "Processing";
  if (row.request.status === "retrying") return "Retrying";
  if (row.request.status === "needs_attention") return "Needs Attention";
  if (row.check?.is_active === false) return "Inactive";
  if (!row.check || row.check.response_complete === false) return "Needs Attention";
  if ((row.check.missing_fields?.length ?? 0) > 0 || (row.check.integrity_warnings?.length ?? 0) > 0) {
    return "Needs Attention";
  }
  if (row.check.routing_status && !["CLEARED", "APPROVED"].includes(row.check.routing_status)) {
    return "Needs Attention";
  }
  return "Verified";
}

function statusClass(status: ReturnType<typeof deriveStatus>): string {
  if (status === "Verified") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700";
  }
  if (status === "Needs Attention") {
    return "border-amber-200 bg-amber-50 text-amber-700";
  }
  if (status === "Inactive" || status === "Processing" || status === "Queued" || status === "Retrying") {
    return "border-slate-200 bg-slate-50 text-slate-700";
  }
  return "border-red-200 bg-red-50 text-red-700";
}

function priorityClass(priority: EligibilityDashboardRow["priority"] | null | undefined): string {
  if (priority === "high") return "border-red-200 bg-red-50 text-red-700";
  if (priority === "low") return "border-slate-200 bg-slate-50 text-slate-500";
  return "border-indigo-200 bg-indigo-50 text-indigo-700";
}

function statusFromReadModel(status: DashboardStatusLabel): DashboardStatusLabel {
  return status;
}

// ── Eligibility result card derivations ──────────────────────────────────────

const CDT_LABELS: Record<string, string> = {
  D0120: "Periodic oral evaluation",
  D0140: "Limited oral evaluation",
  D0150: "Comprehensive oral evaluation",
  D0180: "Comprehensive periodontal evaluation",
  D0210: "Intraoral - complete series",
  D0220: "Intraoral - periapical first",
  D0274: "Bitewings - four images",
  D1110: "Prophylaxis - adult",
  D1120: "Prophylaxis - child",
  D1206: "Topical fluoride varnish",
  D2391: "Resin composite - one surface",
  D2392: "Resin composite - two surfaces",
  D2393: "Resin composite - three surfaces",
  D2740: "Crown - porcelain/ceramic",
  D2750: "Crown - porcelain fused to metal",
  D2950: "Core buildup, including pins",
  D3330: "Endodontic therapy - molar",
  D4341: "Scaling & root planing - per quadrant",
  D4910: "Periodontal maintenance",
  D7140: "Extraction - erupted tooth",
  D7210: "Surgical extraction - erupted tooth",
  D7953: "Bone graft - socket preservation",
};

function serviceLabelFor(code: string | null | undefined): string | null {
  if (!code) return null;
  return CDT_LABELS[code] ? `${code} \u2013 ${CDT_LABELS[code]}` : code;
}

function formatShortDate(value: string | null | undefined): string {
  if (!value) return "\u2014";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "\u2014";
  return date.toLocaleDateString(undefined, { year: "numeric", month: "2-digit", day: "2-digit" });
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "\u2014";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "\u2014";
  return date.toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Percentage of `whole` represented by `part`, clamped 0..100. */
function pctOf(part: number | null | undefined, whole: number | null | undefined): number {
  if (!whole || whole <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round(((part ?? 0) / whole) * 100)));
}

/**
 * Heuristic confidence the parsed benefits are trustworthy. Derived from data
 * completeness signals on the check (not a stored field) and labeled as an estimate.
 */
function confidenceScore(row: DashboardRow): number {
  const check = row.check;
  if (!check) return 0;
  let score = check.response_complete === false ? 76 : 96;
  score -= (check.missing_fields?.length ?? 0) * 8;
  score -= (check.integrity_warnings?.length ?? 0) * 6;
  if (check.is_active === false) score -= 8;
  if (check.routing_status && !["CLEARED", "APPROVED"].includes(check.routing_status)) {
    score -= 12;
  }
  return Math.max(35, Math.min(99, Math.round(score)));
}

/** Estimated patient responsibility, preferring stored values then derived ones. */
function estimatedPatientPortion(
  row: DashboardRow,
  read: EligibilityDashboardRow | null,
  estimates: ProcedureEstimate[],
): number | null {
  if (read?.estimated_patient_responsibility != null) {
    return Math.round(read.estimated_patient_responsibility);
  }
  const fromEstimates = estimates.reduce((sum, e) => sum + (e.patient_responsibility ?? 0), 0);
  if (fromEstimates > 0) return Math.round(fromEstimates);
  const check = row.check;
  if (!check) return null;
  const portion = (check.copay ?? 0) + (check.deductible_remaining ?? 0);
  return portion > 0 ? Math.round(portion) : null;
}

function dataSources(read: EligibilityDashboardRow | null): string[] {
  const sources = ["EDI 271 (Stedi)"];
  if (read?.voice_session_status || read?.voice_merged_check_id) {
    sources.push("Voice verification");
  }
  return sources;
}

function buildAiSummary(
  row: DashboardRow,
  serviceLabel: string | null,
  portion: number | null,
): string {
  const check = row.check;
  if (!check) return "Awaiting payer response \u2014 no benefit details returned yet.";
  if (check.is_active === false) {
    return check.inactive_reason
      ? `Coverage is inactive: ${check.inactive_reason}.`
      : "Coverage is inactive for this member.";
  }
  const parts: string[] = [];
  const service = serviceLabel ? serviceLabel.split("\u2013").slice(1).join("\u2013").trim() : null;
  parts.push(service ? `Eligible for ${service.toLowerCase()}.` : "Coverage is active for today's visit.");
  if (check.deductible_remaining != null) {
    parts.push(`$${Math.round(check.deductible_remaining)} deductible remaining.`);
  }
  if (check.annual_max_total != null && check.annual_max_used != null && check.annual_max_total > 0) {
    parts.push(`Annual max is ${pctOf(check.annual_max_used, check.annual_max_total)}% utilized.`);
  }
  if (portion != null) parts.push(`Estimated patient responsibility $${portion}.`);
  return parts.join(" ");
}

function rowFromReadModel(row: EligibilityDashboardRow): DashboardRow {
  return {
    request: {
      id: row.request_id,
      patient_id: row.patient_id,
      first_name: row.first_name,
      last_name: row.last_name,
      dob: row.dob,
      subscriber_id: row.subscriber_id,
      primary_payer_id: row.primary_payer_id,
      secondary_payer_id: row.secondary_payer_id,
      plan_id: row.plan_id,
      cdt_codes: row.cdt_codes ?? [],
      trigger_event: row.trigger_event,
      status: row.request_status,
      primary_check_id: row.primary_check_id,
      secondary_check_id: row.secondary_check_id,
      input_json: {},
      output_json: {},
      error_message: row.error_message,
      error_code: row.error_code,
      suggested_action: row.suggested_action,
      failure_category: row.failure_category,
      status_reason: row.status_reason,
      priority: row.priority,
      appointment_date: row.appointment_date,
      appointment_time: row.appointment_time,
      provider_name: row.provider_name,
      estimated_claim_value: row.estimated_claim_value,
      coverage_status: row.coverage_status,
      attempt_count: row.attempt_count,
      max_attempts: row.max_attempts,
      started_at: row.started_at,
      last_attempt_at: row.last_attempt_at,
      locked_at: row.locked_at,
      locked_by: row.locked_by,
      next_retry_at: row.next_retry_at,
      parent_request_id: row.parent_request_id,
      idempotency_key: row.idempotency_key,
      agent_http_status: row.agent_http_status,
      agent_duration_ms: row.agent_duration_ms,
      edge_duration_ms: row.edge_duration_ms,
      created_at: row.created_at,
      updated_at: row.updated_at,
      completed_at: row.completed_at,
    },
    check: row.check_id
      ? {
          id: row.check_id,
          patient_id: row.patient_id,
          payer_id: row.payer_label,
          checked_at: row.checked_at ?? row.updated_at,
          coverage_order: row.coverage_order,
          is_active: row.is_active,
          inactive_reason: row.inactive_reason,
          is_covered: row.is_covered,
          in_network: row.in_network,
          coverage_percent: row.coverage_percent,
          copay: row.copay,
          coinsurance: row.coinsurance,
          deductible_total: row.deductible_total,
          deductible_met: row.deductible_met,
          deductible_remaining: row.deductible_remaining,
          annual_max_total: row.annual_max_total,
          annual_max_used: row.annual_max_used,
          annual_max_remaining: row.annual_max_remaining,
          response_complete: row.response_complete,
          missing_fields: row.missing_fields,
          routing_status: row.routing_status,
          integrity_warnings: row.integrity_warnings,
          raw_response: row.raw_response,
          created_at: row.created_at,
        }
      : null,
  };
}

function syntheticReadRowFromDashboard(row: DashboardRow): EligibilityDashboardRow {
  const statusLabel = deriveStatus(row);
  const check = row.check;
  return {
    request_id: row.request.id,
    patient_id: row.request.patient_id,
    first_name: row.request.first_name,
    last_name: row.request.last_name,
    patient_name: `${row.request.first_name} ${row.request.last_name}`,
    dob: row.request.dob,
    subscriber_id: row.request.subscriber_id,
    primary_payer_id: row.request.primary_payer_id,
    payer_label: check?.payer_id ?? row.request.primary_payer_id,
    secondary_payer_id: row.request.secondary_payer_id,
    plan_id: row.request.plan_id,
    cdt_codes: row.request.cdt_codes ?? [],
    trigger_event: row.request.trigger_event,
    request_status: row.request.status,
    primary_check_id: row.request.primary_check_id,
    secondary_check_id: row.request.secondary_check_id,
    error_message: row.request.error_message,
    error_code: row.request.error_code ?? null,
    suggested_action: row.request.suggested_action ?? null,
    failure_category: row.request.failure_category ?? null,
    status_reason: row.request.status_reason ?? null,
    priority: (row.request.priority ?? "medium") as "low" | "medium" | "high",
    priority_rank: 2,
    appointment_date: row.request.appointment_date ?? null,
    appointment_time: row.request.appointment_time ?? null,
    provider_name: row.request.provider_name ?? null,
    estimated_claim_value: row.request.estimated_claim_value ?? null,
    request_coverage_status: row.request.coverage_status ?? "unknown",
    attempt_count: Number(row.request.attempt_count ?? 1),
    max_attempts: Number(row.request.max_attempts ?? 3),
    started_at: row.request.started_at ?? null,
    last_attempt_at: row.request.last_attempt_at ?? null,
    locked_at: row.request.locked_at ?? null,
    locked_by: row.request.locked_by ?? null,
    next_retry_at: row.request.next_retry_at ?? null,
    parent_request_id: row.request.parent_request_id ?? null,
    idempotency_key: row.request.idempotency_key ?? null,
    agent_http_status: row.request.agent_http_status ?? null,
    agent_duration_ms: row.request.agent_duration_ms ?? null,
    edge_duration_ms: row.request.edge_duration_ms ?? null,
    created_at: row.request.created_at,
    updated_at: row.request.updated_at,
    completed_at: row.request.completed_at,
    check_id: check?.id ?? null,
    checked_at: check?.checked_at ?? null,
    coverage_order: check?.coverage_order ?? null,
    is_active: check?.is_active ?? null,
    inactive_reason: check?.inactive_reason ?? null,
    is_covered: check?.is_covered ?? null,
    in_network: check?.in_network ?? null,
    coverage_percent: check?.coverage_percent ?? null,
    copay: check?.copay ?? null,
    coinsurance: check?.coinsurance ?? null,
    deductible_total: check?.deductible_total ?? null,
    deductible_met: check?.deductible_met ?? null,
    deductible_remaining: check?.deductible_remaining ?? null,
    annual_max_total: check?.annual_max_total ?? null,
    annual_max_used: check?.annual_max_used ?? null,
    annual_max_remaining: check?.annual_max_remaining ?? null,
    estimated_patient_responsibility: null,
    coverage_status: check?.is_active === false ? "inactive" : check ? "active" : "unknown",
    response_complete: check?.response_complete ?? null,
    missing_fields_count: check?.missing_fields?.length ?? 0,
    missing_fields: check?.missing_fields ?? null,
    routing_status: check?.routing_status ?? null,
    integrity_warnings_count: check?.integrity_warnings?.length ?? 0,
    integrity_warnings: check?.integrity_warnings ?? null,
    raw_response: check?.raw_response ?? null,
    status_label: statusLabel,
    status_detail: null,
    voice_session_id: null,
    voice_session_status: null,
    voice_merged_check_id: null,
    voice_extracted_fields: null,
    voice_call_reference: null,
  };
}

function voiceSessionStatusLabel(status: string | null | undefined): string | null {
  if (!status) return null;
  const map: Record<string, string> = {
    queued: "Queued",
    calling: "Calling payer",
    pending_review: "Pending review",
    approved: "Completed",
    rejected: "Rejected",
    failed: "Failed",
    cancelled: "Cancelled",
  };
  return map[status] ?? status;
}

function isStediVoiceComplete(readRow: EligibilityDashboardRow | undefined): boolean {
  if (!readRow) return false;
  return (
    readRow.voice_session_status === "approved" &&
    readRow.response_complete === true &&
    (readRow.missing_fields_count ?? 0) === 0
  );
}

function parseCodes(value: string): string[] {
  return value
    .split(/[\n,]+/)
    .map((code) => code.trim().toUpperCase())
    .filter(Boolean);
}

function createIdempotencyKey(prefix: string, source: string): string {
  const suffix =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}:${source}:${suffix}`;
}

function countdown(value: string | null | undefined): string {
  if (!value) return "-";
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return "-";
  const diff = target - Date.now();
  if (diff <= 0) return "now";
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return "in <1m";
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.round(hours / 24);
  return `in ${days}d`;
}

function needsHumanAttention(status: DashboardStatusLabel): boolean {
  return status === "Failed" || status === "Needs Attention" || status === "Inactive";
}

function deriveAgentStatus(
  readRows: EligibilityDashboardRow[],
  settings: EligibilityAgentSettings | null,
): AgentStatusSummary {
  const today = new Date().toDateString();
  const todays = readRows.filter((row) => new Date(row.created_at).toDateString() === today);
  const todayTotal = todays.length;
  const todayVerified = todays.filter((row) => row.status_label === "Verified").length;
  const todayRetried = todays.filter((row) => (row.attempt_count ?? 0) > 1).length;
  const todayAwaitingHuman = todays.filter((row) =>
    ["Needs Attention", "Failed", "Inactive"].includes(row.status_label),
  ).length;
  const todayAutoHandled = todays.filter(
    (row) =>
      row.status_label === "Verified" && (row.attempt_count ?? 0) <= 1 && !row.failure_category,
  ).length;
  const autoHandledPct = todayTotal ? Math.round((todayAutoHandled / todayTotal) * 100) : 0;

  const lastEventCandidates = [
    settings?.last_sync_at,
    ...readRows.map((row) => row.updated_at),
  ].filter(Boolean) as string[];
  const lastEventAt = lastEventCandidates.length
    ? lastEventCandidates.sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0]
    : null;

  const upcomingRetries = readRows
    .map((row) => row.next_retry_at)
    .filter((value): value is string => Boolean(value))
    .filter((value) => new Date(value).getTime() > Date.now())
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  const nextRetryAt = upcomingRetries[0] ?? settings?.next_retry_at ?? null;

  const online = lastEventAt
    ? Date.now() - new Date(lastEventAt).getTime() < 30 * 60_000
    : Boolean(settings);

  return {
    online,
    last_event_at: lastEventAt,
    next_retry_at: nextRetryAt,
    today_total: todayTotal,
    today_verified: todayVerified,
    today_retried: todayRetried,
    today_awaiting_human: todayAwaitingHuman,
    today_auto_handled: todayAutoHandled,
    auto_handled_pct: autoHandledPct,
  };
}

function humanizeEventType(eventType: string): string {
  const map: Record<string, string> = {
    "request.created": "Queued",
    "request.queued": "Queued",
    "request.processing": "Processing",
    "agent.invoked": "Calling payer",
    "agent.completed": "Verified",
    "agent.failed": "Agent error",
    "request.completed": "Verified",
    "request.failed": "Failed",
    "request.retrying": "Auto-retry scheduled",
    "request.needs_attention": "Flagged for review",
    "request.retry_scheduled": "Auto-retry scheduled",
    voice_verification_queued: "Voice agent queued",
    voice_verification_calling: "Voice agent calling payer",
    voice_verification_completed: "Voice verification complete",
    voice_verification_approved: "Voice verification approved",
    voice_verification_auto_approved: "Stedi + Voice complete",
    voice_verification_rejected: "Voice verification rejected",
    voice_verification_failed: "Voice agent failed",
    voice_verification_skipped: "Voice agent skipped",
  };
  if (map[eventType]) return map[eventType];
  return eventType
    .replace(/[_.]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function eventToActivityLine(
  event: EligibilityRequestEvent,
  rowsById: Map<string, EligibilityDashboardRow>,
): { label: string; subject: string; payer: string | null } {
  const row = rowsById.get(event.request_id);
  const subject = row ? row.patient_name.trim() || row.subscriber_id : "Unknown patient";
  const payer = row?.payer_label ?? null;
  return {
    label: humanizeEventType(event.event_type),
    subject,
    payer,
  };
}

type DailyBucket = { bucket_date: string; total_count: number; verified_count: number };

function formatDob(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

function deriveConfidence(
  readRow: EligibilityDashboardRow | undefined,
  row: DashboardRow,
): "high" | "low" | "processing" {
  const status = readRow?.status_label ?? deriveStatus(row);
  if (status === "Queued" || status === "Processing" || status === "Retrying") return "processing";
  if (status === "Verified") {
    const complete = readRow?.response_complete !== false;
    const missing = (readRow?.missing_fields_count ?? row.check?.missing_fields?.length ?? 0) === 0;
    const warns = (readRow?.integrity_warnings_count ?? row.check?.integrity_warnings?.length ?? 0) === 0;
    if (complete && missing && warns) return "high";
    return "low";
  }
  return "low";
}

function dentaiStatusPill(readRow: EligibilityDashboardRow | undefined, row: DashboardRow) {
  const status = readRow?.status_label ?? deriveStatus(row);
  const conf = deriveConfidence(readRow, row);
  if (conf === "processing") {
    return {
      title: "Processing",
      subtitle: "In progress",
      Icon: Loader2,
      wrap: "border-blue-200 bg-blue-50/80 text-blue-700",
      dot: "bg-blue-400",
      iconClass: "animate-spin",
      spinning: true,
    };
  }
  if (status === "Failed") {
    return {
      title: "Failed",
      subtitle: "Error",
      Icon: AlertTriangle,
      wrap: "border-red-200 bg-red-50/80 text-red-700",
      dot: "bg-red-500",
      iconClass: "",
      spinning: false,
    };
  }
  if (status === "Verified" && conf === "high") {
    const stediVoice = isStediVoiceComplete(readRow);
    return {
      title: stediVoice ? "Complete" : "Verified",
      subtitle: stediVoice ? "Stedi + Voice" : "High confidence",
      Icon: Check,
      wrap: "border-emerald-200 bg-emerald-50/80 text-emerald-700",
      dot: "bg-emerald-500",
      iconClass: "",
      spinning: false,
    };
  }
  if (status === "Verified") {
    return {
      title: "Verified",
      subtitle: "Review suggested",
      Icon: AlertTriangle,
      wrap: "border-amber-200 bg-amber-50/80 text-amber-700",
      dot: "bg-amber-400",
      iconClass: "",
      spinning: false,
    };
  }
  if (status === "Inactive") {
    return {
      title: "Inactive",
      subtitle: "Coverage ended",
      Icon: AlertTriangle,
      wrap: "border-slate-200 bg-slate-50/80 text-slate-600",
      dot: "bg-slate-400",
      iconClass: "",
      spinning: false,
    };
  }
  return {
    title: "Needs Review",
    subtitle: "Low confidence",
    Icon: AlertTriangle,
    wrap: "border-amber-200 bg-amber-50/80 text-amber-700",
    dot: "bg-amber-400",
    iconClass: "",
    spinning: false,
  };
}

function aggregateDailyFromReadRows(readRows: EligibilityDashboardRow[], days: number): DailyBucket[] {
  const keys: string[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setUTCDate(d.getUTCDate() - i);
    keys.push(d.toISOString().slice(0, 10));
  }
  const map = new Map<string, { total: number; verified: number }>();
  for (const k of keys) map.set(k, { total: 0, verified: 0 });
  for (const row of readRows) {
    const day = row.created_at.slice(0, 10);
    if (!map.has(day)) continue;
    const entry = map.get(day)!;
    entry.total += 1;
    if (row.status_label === "Verified") entry.verified += 1;
  }
  return keys.map((bucket_date) => {
    const { total, verified } = map.get(bucket_date)!;
    return { bucket_date, total_count: total, verified_count: verified };
  });
}

function exportUpcomingCsv(rows: DashboardRow[], readRowById: Map<string, EligibilityDashboardRow>): void {
  const headers = ["Patient", "DOB", "MemberId", "Payer", "Plan", "Deductible", "AnnualMax", "Status", "Confidence"];
  const lines = rows.map((row) => {
    const r = readRowById.get(row.request.id);
    const status = r?.status_label ?? deriveStatus(row);
    const conf = deriveConfidence(r, row);
    const ded = row.check?.deductible_remaining ?? row.check?.deductible_total ?? "";
    const amax = row.check?.annual_max_remaining ?? row.check?.annual_max_total ?? "";
    const cells = [
      `${row.request.first_name} ${row.request.last_name}`,
      row.request.dob,
      row.request.subscriber_id,
      r?.payer_label ?? row.request.primary_payer_id,
      row.request.plan_id ?? "",
      ded === "" ? "" : String(ded),
      amax === "" ? "" : String(amax),
      status,
      conf,
    ].map((c) => `"${String(c).replace(/"/g, '""')}"`);
    return cells.join(",");
  });
  const blob = new Blob([`${headers.join(",")}\n${lines.join("\n")}`], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `eligibility-export-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function eventActivityIcon(eventType: string) {
  const t = eventType.toLowerCase();
  if (t.includes("fail") || t.includes("error") || t.includes("attention")) return AlertTriangle;
  if (t.includes("invoked") || t.includes("retry")) return Phone;
  if (t.includes("complet") || t.includes("verified")) return Check;
  return Sparkles;
}

function VoiceWaveIcon({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <rect x="2" y="11" width="2.5" height="6" rx="1.25" fill="white" opacity="0.5" />
      <rect x="6" y="8" width="2.5" height="12" rx="1.25" fill="white" opacity="0.7" />
      <rect x="10" y="4" width="2.5" height="20" rx="1.25" fill="white" opacity="0.95" />
      <rect x="14" y="7" width="2.5" height="14" rx="1.25" fill="white" opacity="0.8" />
      <rect x="18" y="10" width="2.5" height="8" rx="1.25" fill="white" opacity="0.6" />
      <rect x="22" y="12" width="2.5" height="4" rx="1.25" fill="white" opacity="0.4" />
    </svg>
  );
}

function activityIconStyle(eventType: string): {
  Icon: typeof Sparkles;
  bg: string;
  fg: string;
} {
  const t = eventType.toLowerCase();
  if (t.includes("fail") || t.includes("error") || t.includes("attention") || t.includes("low")) {
    return { Icon: AlertTriangle, bg: "bg-amber-50", fg: "text-amber-600" };
  }
  if (t.includes("invoked") || t.includes("calling") || t.includes("retry") || t.includes("voice")) {
    return { Icon: Phone, bg: "bg-blue-50", fg: "text-blue-600" };
  }
  return { Icon: Sparkles, bg: "bg-indigo-50", fg: "text-indigo-600" };
}

function activitySubPill(eventType: string): { label: string; cls: string } | null {
  const t = eventType.toLowerCase();
  if (t.includes("fail") || t.includes("attention") || t.includes("low")) {
    return { label: "Needs Review", cls: "border-amber-200 bg-amber-50 text-amber-700" };
  }
  if (t.includes("invoked") || t.includes("calling") || t.includes("processing") || t.includes("retry") || t.includes("voice")) {
    return { label: "In Progress", cls: "border-blue-200 bg-blue-50 text-blue-700" };
  }
  if (t.includes("pending_review")) {
    return { label: "Pending Review", cls: "border-violet-200 bg-violet-50 text-violet-700" };
  }
  if (t.includes("complet") || t.includes("verified")) {
    return { label: "Verified – High Confidence", cls: "border-emerald-200 bg-emerald-50 text-emerald-700" };
  }
  return null;
}

function AgentActivityRail({
  items,
  rowsById,
  pollingActive,
  expanded,
  onToggleExpand,
}: {
  items: EligibilityRequestEvent[];
  rowsById: Map<string, EligibilityDashboardRow>;
  pollingActive: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const wrapClass = expanded ? "max-h-[32rem]" : "max-h-[26rem] overflow-hidden";
  return (
    <aside className="card flex h-fit flex-col p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={16} className="text-indigo-600" strokeWidth={2} />
          <span className="text-[14px] font-semibold text-slate-900">Activity Feed</span>
        </div>
        <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-emerald-600">
          <span className="relative flex h-2 w-2 text-emerald-500">
            <span className="status-dot-pulse relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
          </span>
          {pollingActive ? "Live" : "Idle"}
        </span>
      </div>
      {items.length === 0 ? (
        <div className="text-[12px] font-normal text-slate-500">Waiting for the next agent action…</div>
      ) : (
        <ul className={`space-y-3 ${wrapClass} overflow-y-auto pr-1`}>
          {items.map((event, evIdx) => {
            const line = eventToActivityLine(event, rowsById);
            const { Icon, bg, fg } = activityIconStyle(event.event_type);
            const sub = activitySubPill(event.event_type);
            const clock = new Date(event.created_at).toLocaleTimeString([], {
              hour: "numeric",
              minute: "2-digit",
            });
            return (
              <li
                key={event.id}
                className="slide-in-right flex gap-3"
                style={{ animationDelay: `${Math.min(evIdx, 12) * 30}ms` }}
              >
                <div
                  className={`mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${bg} ${fg}`}
                >
                  <Icon size={16} strokeWidth={2} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1 text-[13px] font-semibold leading-tight text-slate-900">
                      {line.label}
                      {line.subject ? <span className="font-normal text-slate-700"> for {line.subject}</span> : null}
                    </div>
                    <span className="shrink-0 text-[11px] text-slate-500">{clock}</span>
                  </div>
                  {line.payer ? (
                    <div className="mt-0.5 text-[12px] leading-tight text-slate-500">{line.payer}</div>
                  ) : null}
                  {sub ? (
                    <span
                      className={`mt-1.5 inline-flex items-center rounded-md border px-2 py-0.5 text-[10px] font-semibold ${sub.cls}`}
                    >
                      {sub.label}
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      <button
        type="button"
        className="mt-4 w-full rounded-lg py-2 text-[13px] font-semibold text-indigo-600 transition hover:bg-indigo-50"
        onClick={onToggleExpand}
      >
        {expanded ? "Show less" : "View all activity"}
      </button>
    </aside>
  );
}

export default function EligibilityDashboard() {
  const [rows, setRows] = useState<DashboardRow[]>([]);
  const [readRows, setReadRows] = useState<EligibilityDashboardRow[]>([]);
  const [settings, setSettings] = useState<EligibilityAgentSettings | null>(null);
  const [estimates, setEstimates] = useState<ProcedureEstimate[]>([]);
  const [events, setEvents] = useState<EligibilityRequestEvent[]>([]);
  const [activity, setActivity] = useState<EligibilityRequestEvent[]>([]);
  const [activityExpanded, setActivityExpanded] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [pollingActive, setPollingActive] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [panelMode, setPanelMode] = useState<PanelMode>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterValue>("all");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  const [voiceReviewBusy, setVoiceReviewBusy] = useState(false);
  const refreshTimerRef = useRef<number | null>(null);
  // Time/locale-dependent labels are client-only: the server clock/timezone
  // would differ from the browser and cause a hydration mismatch. They render a
  // stable fallback on the server + first paint, then the real value once hydrated.
  const clientGreeting = useClientValue(() => {
    const h = new Date().getHours();
    return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  }, "Welcome");
  const clientDateLabel = useClientValue(
    () => new Date().toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" }),
    "",
  );
  const clientAsOfTime = useClientValue(
    () => new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    "",
  );

  const selectedRow = useMemo(() => rows.find((row) => row.request.id === selectedId) ?? null, [rows, selectedId]);
  const selectedReadRow = useMemo(
    () => readRows.find((row) => row.request_id === selectedId) ?? null,
    [readRows, selectedId],
  );
  const readRowById = useMemo(() => new Map(readRows.map((row) => [row.request_id, row])), [readRows]);
  const parsedCdtCodes = useMemo(() => parseCodes(form.cdt_codes), [form.cdt_codes]);
  const activityCapRef = useRef(25);

  useEffect(() => {
    activityCapRef.current = activityExpanded ? 100 : 25;
  }, [activityExpanded]);

  const loadRows = useCallback(async () => {
    setRefreshing(true);
    setBanner(null);

    const result = await fetchEligibilityQueue();
    if (!result.ok) {
      if (!rows.length) {
        setRows(demoRows);
        setReadRows(demoRows.map(syntheticReadRowFromDashboard));
      }
      setBanner(result.message ?? "Dashboard API unavailable. Showing local design data.");
      setPollingActive(false);
      setLoading(false);
      setRefreshing(false);
      return;
    }

    setPollingActive(true);
    const typedRows = result.rows;
    setReadRows(typedRows);
    setRows(typedRows.map(rowFromReadModel));
    setLoading(false);
    setRefreshing(false);
  }, [rows.length]);

  const loadSettings = useCallback(async () => {
    const result = await fetchEligibilitySettings();
    setSettings(result.ok ? result.settings : null);
  }, []);

  const loadEstimates = useCallback(async (requestId: string | null | undefined) => {
    if (!requestId || requestId.startsWith("demo-")) {
      setEstimates([]);
      return;
    }

    const result = await fetchProcedureEstimates(requestId);
    if (!result.ok) {
      if (result.message) setBanner(result.message);
      setEstimates([]);
      return;
    }

    setEstimates(result.estimates);
  }, []);

  const loadEvents = useCallback(async (requestId: string | null | undefined) => {
    if (!requestId || requestId.startsWith("demo-")) {
      setEvents([]);
      return;
    }

    const result = await fetchRequestEvents(requestId);
    if (!result.ok) {
      if (result.message) setBanner(result.message);
      setEvents([]);
      return;
    }

    setEvents(result.events);
  }, []);

  const loadActivity = useCallback(async (limit: number) => {
    const result = await fetchEligibilityActivity(limit);
    setActivity(result.ok ? result.events : []);
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void loadRows();
      void loadSettings();
    }, 0);
    return () => window.clearTimeout(id);
  }, [loadRows, loadSettings]);

  useEffect(() => {
    const limit = activityExpanded ? 100 : 25;
    const id = window.setTimeout(() => {
      void loadActivity(limit);
    }, 0);
    return () => window.clearTimeout(id);
  }, [activityExpanded, loadActivity]);

  useEffect(() => {
    return () => {
      if (refreshTimerRef.current) {
        window.clearTimeout(refreshTimerRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (!pollingActive) return;
    const interval = window.setInterval(() => {
      void loadRows();
      void loadSettings();
      const cap = activityCapRef.current;
      void loadActivity(cap);
      if (selectedId) {
        void loadEvents(selectedId);
        if (panelMode === "details") {
          void loadEstimates(selectedId);
        }
      }
    }, 5000);
    return () => window.clearInterval(interval);
  }, [loadActivity, loadEstimates, loadEvents, loadRows, loadSettings, panelMode, pollingActive, selectedId]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      if (panelMode === "details" && selectedRow?.request.id) {
        void loadEstimates(selectedRow.request.id);
        void loadEvents(selectedRow.request.id);
      } else {
        setEstimates([]);
        setEvents([]);
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, [loadEstimates, loadEvents, panelMode, selectedRow?.request.id]);

  useEffect(() => {
    document.body.style.overflow = panelMode ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [panelMode]);

  const rowsInDateRange = useMemo(() => {
    if (!dateFrom && !dateTo) return rows;
    const fromTime = dateFrom ? new Date(`${dateFrom}T00:00:00`).getTime() : null;
    const toTime = dateTo ? new Date(`${dateTo}T23:59:59.999`).getTime() : null;
    return rows.filter((row) => {
      const readRow = readRowById.get(row.request.id);
      const appt = readRow?.appointment_date ?? row.request.appointment_date;
      const raw = appt || row.request.created_at.slice(0, 10);
      const t = new Date(`${raw}T12:00:00`).getTime();
      if (fromTime !== null && t < fromTime) return false;
      if (toTime !== null && t > toTime) return false;
      return true;
    });
  }, [rows, dateFrom, dateTo, readRowById]);

  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return rowsInDateRange.filter((row) => {
      const readRow = readRowById.get(row.request.id);
      const status = readRow?.status_label ?? deriveStatus(row);
      const matchesFilter =
        filter === "all" ||
        (filter === "verified" && status === "Verified") ||
        (filter === "inactive" && status === "Inactive") ||
        (filter === "attention" && ["Needs Attention", "Failed", "Processing", "Queued", "Retrying"].includes(status));
      const haystack =
        `${row.request.first_name} ${row.request.last_name} ${row.request.subscriber_id} ${readRow?.payer_label ?? ""}`.toLowerCase();
      return matchesFilter && (!q || haystack.includes(q));
    });
  }, [filter, query, readRowById, rowsInDateRange]);

  const kpi = useMemo(() => {
    const total = readRows.length;
    const verified = readRows.filter((r) => r.status_label === "Verified").length;
    const rate = total ? Math.round((verified / total) * 1000) / 10 : 0;
    const today = new Date().toDateString();
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const yStr = yesterday.toDateString();
    const verifiedToday = readRows.filter((r) => {
      if (r.status_label !== "Verified") return false;
      const ts = r.checked_at ?? r.updated_at;
      return ts ? new Date(ts).toDateString() === today : false;
    }).length;
    const verifiedYesterday = readRows.filter((r) => {
      if (r.status_label !== "Verified") return false;
      const ts = r.checked_at ?? r.updated_at;
      return ts ? new Date(ts).toDateString() === yStr : false;
    }).length;
    const attention = readRows.filter((r) => ["Needs Attention", "Failed", "Inactive"].includes(r.status_label)).length;
    const buckets = aggregateDailyFromReadRows(readRows, 7);
    const rateSeries = buckets.map((b) => (b.total_count ? Math.round((b.verified_count / b.total_count) * 100) : 0));
    const verifiedSeries = buckets.map((b) => b.verified_count);
    const attentionSeries = buckets.map((b) => Math.max(0, b.total_count - b.verified_count));
    const lastRate = rateSeries[rateSeries.length - 1] ?? 0;
    const prevRate = rateSeries[rateSeries.length - 2] ?? lastRate;
    const deltaRate = prevRate ? Math.round(((lastRate - prevRate) / prevRate) * 1000) / 10 : 0;
    const deltaVerifiedDay = verifiedToday - verifiedYesterday;
    return {
      rate,
      verifiedToday,
      attention,
      deltaRate,
      deltaVerifiedDay,
      rateSeries,
      verifiedSeries,
      attentionSeries,
    };
  }, [readRows]);

  const agentStatus = useMemo<AgentStatusSummary>(
    () => deriveAgentStatus(readRows, settings),
    [readRows, settings],
  );

  const openDetails = (row: DashboardRow) => {
    setSelectedId(row.request.id);
    setPanelMode("details");
  };

  const rerun = async (row: DashboardRow) => {
    const result = await createEligibilityRequest({
      first_name: row.request.first_name,
      last_name: row.request.last_name,
      dob: row.request.dob,
      subscriber_id: row.request.subscriber_id,
      primary_payer_id: row.request.primary_payer_id,
      secondary_payer_id: row.request.secondary_payer_id,
      plan_id: row.request.plan_id,
      cdt_codes: row.request.cdt_codes ?? [],
      trigger_event: "APPOINTMENT_BOOKED",
      priority: row.request.priority ?? "medium",
      appointment_date: row.request.appointment_date ?? null,
      appointment_time: row.request.appointment_time ?? null,
      provider_name: row.request.provider_name ?? null,
      estimated_claim_value: row.request.estimated_claim_value ?? null,
      idempotency_key: createIdempotencyKey("rerun", row.request.id),
      input_json: {
        rerun_of: row.request.id,
        submitted_from: "eligibility_dashboard",
      },
    });

    if (!result.ok) {
      setBanner(result.message ?? "Rerun failed");
      return;
    }

    await loadRows();
  };

  const retryFailed = async (row: DashboardRow) => {
    const result = await createEligibilityRequest({
      first_name: row.request.first_name,
      last_name: row.request.last_name,
      dob: row.request.dob,
      subscriber_id: row.request.subscriber_id,
      primary_payer_id: row.request.primary_payer_id,
      secondary_payer_id: row.request.secondary_payer_id,
      plan_id: row.request.plan_id,
      cdt_codes: row.request.cdt_codes ?? [],
      trigger_event: "APPOINTMENT_BOOKED",
      priority: row.request.priority ?? "medium",
      appointment_date: row.request.appointment_date ?? null,
      appointment_time: row.request.appointment_time ?? null,
      provider_name: row.request.provider_name ?? null,
      estimated_claim_value: row.request.estimated_claim_value ?? null,
      idempotency_key: createIdempotencyKey("retry", row.request.id),
      input_json: {
        retry_of: row.request.id,
        submitted_from: "eligibility_dashboard",
      },
    });

    if (!result.ok) {
      setBanner(result.message ?? "Retry failed");
      return;
    }

    await loadRows();
  };

  const reviewVoiceSession = async (action: "approve" | "reject") => {
    const sessionId = selectedReadRow?.voice_session_id;
    if (!sessionId) {
      setBanner("No voice verification session linked to this request.");
      return;
    }
    setVoiceReviewBusy(true);
    try {
      const resp = await fetch("/api/eligibility/voice/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, action }),
      });
      const payload = (await resp.json().catch(() => ({}))) as { error?: string };
      if (!resp.ok) {
        setBanner(payload.error ?? "Voice review failed");
        return;
      }
      setBanner(action === "approve" ? "Voice verification approved." : "Voice verification rejected.");
      await loadRows();
      if (selectedRow?.request.id) {
        await loadEstimates(selectedRow.request.id);
      }
      await loadEvents(selectedRow?.request.id ?? "");
    } finally {
      setVoiceReviewBusy(false);
    }
  };

  const submitRequest = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();

    setSubmitting(true);
    const result = await createEligibilityRequest({
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      dob: form.dob,
      subscriber_id: form.subscriber_id.trim(),
      primary_payer_id: form.primary_payer_id.trim(),
      secondary_payer_id: form.secondary_payer_id.trim() || null,
      plan_id: form.plan_id.trim() || null,
      cdt_codes: parseCodes(form.cdt_codes),
      trigger_event: "APPOINTMENT_BOOKED",
      priority: form.priority,
      appointment_date: form.appointment_date || null,
      appointment_time: form.appointment_time || null,
      provider_name: form.provider_name.trim() || null,
      estimated_claim_value: form.estimated_claim_value ? Number(form.estimated_claim_value) : null,
      idempotency_key: createIdempotencyKey("ui", `${form.subscriber_id.trim()}:${form.primary_payer_id.trim()}`),
      input_json: {
        submitted_from: "eligibility_dashboard",
        parsed_cdt_codes: parseCodes(form.cdt_codes),
      },
    });
    setSubmitting(false);

    if (!result.ok) {
      setBanner(result.message ?? "Submit failed");
      return;
    }

    setForm(emptyForm);
    setPanelMode(null);
    await loadRows();
  };

  return (
    <div className="min-h-screen">
      <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
        <section className="mb-7 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3.5">
            <div className="relative flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-300/40 ring-1 ring-inset ring-white/20">
              <VoiceWaveIcon size={24} />
            </div>
            <div>
              <h1 className="text-[22px] font-semibold leading-tight tracking-tight text-slate-900">
                {clientGreeting}, {dashboardUserDisplayName.replace(/^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i, "Dr. ").split(" ").slice(0, 2).join(" ")}
              </h1>
              <p className="mt-0.5 text-[13px] text-slate-500">
                Here&apos;s what&apos;s happening with your eligibility verifications.
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="lift-on-hover inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-medium text-slate-600 shadow-sm hover:border-slate-300 hover:bg-slate-50"
              aria-label="Date filter"
            >
              <Calendar size={15} className="text-slate-500" />
              <span>{clientDateLabel}</span>
              <ChevronDown size={14} className="text-slate-400" />
            </button>
            <button
              type="button"
              className="lift-on-hover inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-medium text-slate-600 shadow-sm hover:border-slate-300 hover:bg-slate-50"
              onClick={() => exportUpcomingCsv(filteredRows, readRowById)}
            >
              <Download size={15} className="text-slate-500" />
              <span>Export Report</span>
            </button>
            <button
              type="button"
              className="btn-sheen lift-on-hover inline-flex h-9 items-center gap-1.5 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 px-3.5 text-[13px] font-semibold text-white shadow-sm shadow-indigo-300/50 ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 active:scale-[0.98]"
              onClick={() => setPanelMode("form")}
            >
              <Plus size={15} />
              <span>New Check</span>
            </button>
          </div>
        </section>

        {banner ? (
          <div className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">
            {banner}
          </div>
        ) : null}

        <section className="mb-6 grid gap-4 md:grid-cols-3">
          <div className="card lift-on-hover flex flex-col p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-emerald-50">
                <ShieldCheck size={17} className="text-emerald-600" strokeWidth={2.2} />
              </div>
              <div className="flex flex-1 items-start justify-between">
                <div>
                  <div className="text-[32px] font-bold leading-none tabular-nums tracking-tight text-slate-900">{kpi.rate}%</div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Verification Success Rate</div>
                  <div className="mt-0.5 text-[12px] text-slate-500">Last 7 days</div>
                </div>
                <MiniSparkline
                  values={kpi.rateSeries.length ? kpi.rateSeries : [0]}
                  strokeColor="#10B981"
                  width={80}
              height={36}
              fillOpacity={0.1}
                />
              </div>
            </div>
            <div className="flex items-center gap-1.5 border-t border-slate-100 pt-3 text-[11px] font-semibold">
              <span>{kpi.deltaRate >= 0 ? "↑" : "↓"} {Math.abs(kpi.deltaRate)}%</span>
              <span className="font-normal text-slate-400">vs prior week</span>
            </div>
          </div>

          <div className="card lift-on-hover flex flex-col p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-50">
                <Users size={17} className="text-blue-600" strokeWidth={2.2} />
              </div>
              <div className="flex flex-1 items-start justify-between">
                <div>
                  <div className="text-[32px] font-bold leading-none tabular-nums tracking-tight text-slate-900">{kpi.verifiedToday}</div>
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Patients Verified Today</div>
                  <div className="mt-0.5 min-h-[1.25rem] text-[12px] text-slate-500">
                    {clientAsOfTime ? `As of ${clientAsOfTime}` : "\u00a0"}
                  </div>
                </div>
                <MiniSparkline
                  values={kpi.verifiedSeries.length ? kpi.verifiedSeries : [0]}
                  strokeColor="#3B82F6"
                  width={80}
              height={36}
              fillOpacity={0.1}
                />
              </div>
            </div>
            <div className="flex items-center gap-1.5 border-t border-slate-100 pt-3 text-[11px] font-semibold">
              <span>{kpi.deltaVerifiedDay >= 0 ? "↑" : "↓"} {Math.abs(kpi.deltaVerifiedDay)}</span>
              <span className="font-normal text-slate-400">vs yesterday</span>
            </div>
          </div>

          <div className="card lift-on-hover flex flex-col p-5">
            <div className="flex items-start gap-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-amber-50">
                <AlertTriangle size={17} className="text-amber-600" strokeWidth={2.2} />
              </div>
              <div className="flex-1">
                <div className="text-[32px] font-bold leading-none tabular-nums tracking-tight text-slate-900">{kpi.attention}</div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Needs Attention</div>
                <div className="mt-0.5 text-[12px] text-slate-500">Low confidence or errors</div>
              </div>
            </div>
            <button
              type="button"
              className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-600 transition hover:text-amber-700"
              onClick={() => setFilter("attention")}
            >
              View all <ChevronRight size={12} />
            </button>
          </div>
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="card overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-gradient-to-b from-slate-50/80 to-slate-50/40 px-5 py-3.5">
              <div className="flex items-center gap-2.5">
                <Calendar size={17} className="text-indigo-600" strokeWidth={2} />
                <h2 className="text-[14px] font-semibold text-slate-900">Upcoming Patients</h2>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="relative min-w-[180px]">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={14} />
                  <input
                    className="h-9 w-full rounded-lg border border-slate-200 bg-slate-50 pl-8 pr-3 text-[13px] text-slate-800 outline-none focus:border-indigo-400"
                    placeholder="Search patient or member ID…"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                  />
                </div>
                <select
                  className="h-9 rounded-lg border border-slate-200 bg-white px-3 text-[13px] text-slate-700 outline-none focus:border-indigo-400"
                  value={filter}
                  onChange={(event) => setFilter(event.target.value as FilterValue)}
                >
                  <option value="all">All</option>
                  <option value="verified">Verified</option>
                  <option value="inactive">Inactive</option>
                  <option value="attention">Needs attention</option>
                </select>
                <button
                  type="button"
                  className="text-[12px] font-semibold text-indigo-600 hover:text-indigo-700"
                >
                  View all patients
                </button>
                {refreshing && !loading ? (
                  <span className="text-[10px] font-medium uppercase tracking-wide text-slate-400">Syncing</span>
                ) : null}
              </div>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="border-b border-slate-100">
                    {["Patient", "Payer", "Plan", "Deductible", "Annual Max", "Status", ""].map((header, idx) => (
                      <th
                        key={`${header}-${idx}`}
                        className="px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400"
                      >
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-10 text-center text-[14px] text-slate-500">
                        Loading eligibility checks…
                      </td>
                    </tr>
                  ) : filteredRows.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="px-4 py-10 text-center text-[14px] text-slate-500">
                        No patients match this view.
                      </td>
                    </tr>
                  ) : (
                    filteredRows.map((row, rowIdx) => {
                      const readRow = readRowById.get(row.request.id);
                      const status = readRow ? statusFromReadModel(readRow.status_label) : deriveStatus(row);
                      const isWorking = status === "Queued" || status === "Processing" || status === "Retrying";
                      const showRecheck = needsHumanAttention(status);
                      const pill = dentaiStatusPill(readRow, row);
                      const PillIcon = pill.Icon;
                      const payerLabel = readRow?.payer_label ?? row.request.primary_payer_id;
                      return (
                        <tr
                          key={row.request.id}
                          className={`group row-stagger cursor-pointer border-b border-slate-100 transition-colors duration-150 last:border-b-0 ${
                            isWorking ? "bg-blue-50/40" : "hover:bg-slate-50/80"
                          }`}
                          style={{ ["--i" as string]: Math.min(rowIdx, 24) }}
                          onClick={() => openDetails(row)}
                        >
                          <td className="px-5 py-4">
                            <div className="flex items-center gap-3">
                              <PatientAvatar firstName={row.request.first_name} lastName={row.request.last_name} />
                              <div>
                                <div className="text-[13.5px] font-semibold text-slate-900">
                                  {row.request.first_name} {row.request.last_name}
                                </div>
                                <div className="text-[11.5px] text-slate-500">DOB: {formatDob(row.request.dob)}</div>
                              </div>
                            </div>
                          </td>
                          <td className="px-5 py-4">
                            <PayerLogo label={payerLabel} />
                          </td>
                          <td className="px-5 py-4">
                            <div className="text-[13px] font-medium text-slate-900">{row.request.plan_id || "—"}</div>
                            <div className="text-[11.5px] text-slate-500">Plan ID: {row.request.subscriber_id}</div>
                          </td>
                          <td className="px-5 py-4">
                            <div className="num text-[13px] font-semibold text-slate-900">
                              {formatCurrency(row.check?.deductible_remaining ?? row.check?.deductible_total)}
                            </div>
                            <div className="text-[11.5px] text-slate-500">Individual</div>
                          </td>
                          <td className="px-5 py-4">
                            <div className="num text-[13px] font-semibold text-slate-900">
                              {formatCurrency(row.check?.annual_max_remaining ?? row.check?.annual_max_total)}
                            </div>
                            <div className="text-[11.5px] text-slate-500">Per Individual</div>
                          </td>
                          <td className="px-5 py-4">
                            <span
                              className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 transition-shadow duration-200 group-hover:shadow-sm ${pill.wrap}`}
                            >
                              {pill.spinning ? (
                                <PillIcon size={11} className={pill.iconClass} strokeWidth={2.5} />
                              ) : (
                                <span
                                  className={`relative h-[7px] w-[7px] shrink-0 rounded-full ${pill.dot} ${
                                    isWorking ? "status-dot-pulse" : ""
                                  }`}
                                />
                              )}
                              <span className="text-[12px] font-medium tracking-tight">{pill.title}</span>
                            </span>
                          </td>
                          <td className="px-3 py-4 text-right">
                            {status === "Retrying" ? (
                              <span className="text-[11px] text-slate-600">
                                Next {countdown(row.request.next_retry_at)}
                              </span>
                            ) : (
                              <div className="flex items-center justify-end gap-0.5">
                                {showRecheck ? (
                                  <button
                                    type="button"
                                    className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 opacity-0 transition group-hover:opacity-100 hover:bg-indigo-50 hover:text-indigo-600"
                                    title="Run re-check"
                                    aria-label="Run re-check"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      void rerun(row);
                                    }}
                                  >
                                    <RotateCw size={14} />
                                  </button>
                                ) : null}
                                <button
                                  type="button"
                                  className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                                  title="Details"
                                  aria-label="Details"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    openDetails(row);
                                  }}
                                >
                                  <ChevronRight size={16} />
                                </button>
                              </div>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
            <div className="border-t border-slate-100 bg-slate-50/40 px-5 py-3.5 text-center">
              <div className="text-[12.5px] font-medium text-slate-600">
                {filteredRows.length} of {rowsInDateRange.length} patients
              </div>
              <div className="mt-0.5 text-[12px] text-slate-500">
                Showing patients with upcoming appointments
              </div>
            </div>
          </div>
          <AgentActivityRail
            items={activity}
            rowsById={readRowById}
            pollingActive={pollingActive}
            expanded={activityExpanded}
            onToggleExpand={() => setActivityExpanded((e) => !e)}
          />
        </section>
      </main>

      {panelMode ? (
        <div
          className="fade-in fixed inset-0 z-40 bg-slate-900/25 backdrop-blur-sm"
          onClick={() => setPanelMode(null)}
        >
          <aside
            className={`slide-in-right absolute right-0 top-0 flex h-full flex-col overflow-y-auto border-l border-slate-200 bg-white shadow-[-12px_0px_32px_-12px_rgba(15,23,42,0.18)] ${
              panelMode === "details" ? "w-full max-w-[940px]" : "w-[400px]"
            }`}
            style={{ animationDuration: "0.32s" }}
            onClick={(event) => event.stopPropagation()}
          >
            {panelMode === "form" ? (
              <button
                className="absolute right-5 top-5 text-slate-500 transition hover:text-indigo-600"
                onClick={() => setPanelMode(null)}
                aria-label="Close panel"
              >
                <X size={18} />
              </button>
            ) : null}

            {panelMode === "form" ? (
              <form className="flex h-full flex-col px-6 pb-6 pt-16" onSubmit={submitRequest}>
                <h3 className="text-[22px] font-semibold tracking-tight text-slate-900">Run eligibility check</h3>
                <p className="mt-2 text-[13px] leading-snug text-slate-500">
                  Creates a queued request in Supabase. The agent picks it up and runs the eligibility workflow.
                </p>
                <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[12px] leading-snug text-slate-600">
                  Use the Stedi trading partner service ID for payer ID. CDT codes can be comma-separated or one per line.
                </div>
                <div className="mt-6 space-y-3">
                  {[
                    ["first_name", "First name"],
                    ["last_name", "Last name"],
                    ["dob", "Date of birth"],
                    ["subscriber_id", "Member ID"],
                    ["primary_payer_id", "Primary payer ID"],
                    ["plan_id", "Plan"],
                    ["cdt_codes", "CDT codes"],
                  ].map(([key, label]) => (
                    <label key={key} className="block">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">{label}</span>
                      <input
                        required={!["plan_id", "cdt_codes"].includes(key)}
                        type={key === "dob" ? "date" : "text"}
                        className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                        value={form[key as keyof FormState]}
                        onChange={(event) => setForm((prev) => ({ ...prev, [key]: event.target.value }))}
                      />
                    </label>
                  ))}
                  <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Priority</span>
                      <select
                        className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 bg-white px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                        value={form.priority}
                        onChange={(event) =>
                          setForm((prev) => ({ ...prev, priority: event.target.value as FormState["priority"] }))
                        }
                      >
                        <option value="low">Low</option>
                        <option value="medium">Medium</option>
                        <option value="high">High</option>
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                        Appointment
                      </span>
                      <input
                        type="date"
                        className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                        value={form.appointment_date}
                        onChange={(event) => setForm((prev) => ({ ...prev, appointment_date: event.target.value }))}
                      />
                    </label>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <label className="block">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Time</span>
                      <input
                        type="time"
                        className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                        value={form.appointment_time}
                        onChange={(event) => setForm((prev) => ({ ...prev, appointment_time: event.target.value }))}
                      />
                    </label>
                    <label className="block">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                        Provider
                      </span>
                      <input
                        className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                        placeholder="Dr. Smith"
                        value={form.provider_name}
                        onChange={(event) => setForm((prev) => ({ ...prev, provider_name: event.target.value }))}
                      />
                    </label>
                  </div>
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                      Estimated claim value
                    </span>
                    <input
                      type="number"
                      min="0"
                      step="1"
                      className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                      value={form.estimated_claim_value}
                      onChange={(event) => setForm((prev) => ({ ...prev, estimated_claim_value: event.target.value }))}
                    />
                  </label>
                  {parsedCdtCodes.length ? (
                    <div className="rounded-[4px] border border-slate-200 bg-white px-3 py-2 text-[12px] text-slate-500">
                      Parsed CDT codes: <span className="mono text-slate-700">{parsedCdtCodes.join(", ")}</span>
                    </div>
                  ) : null}
                  <label className="block">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                      Secondary payer ID
                    </span>
                    <input
                      className="mt-1 h-10 w-full rounded-[4px] border border-slate-200 px-3 text-[14px] font-normal text-slate-800 outline-none focus:border-indigo-400"
                      value={form.secondary_payer_id}
                      onChange={(event) => setForm((prev) => ({ ...prev, secondary_payer_id: event.target.value }))}
                    />
                  </label>
                </div>
                <button
                  className="btn-sheen lift-on-hover mt-auto w-full rounded-[4px] bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-normal text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 disabled:opacity-60"
                  disabled={submitting}
                >
                  {submitting ? "Queueing..." : "Queue Check"}
                </button>
              </form>
            ) : selectedRow ? (
              <div className="flex h-full flex-col bg-slate-50/60">
                {/* Sticky header */}
                <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-200 bg-white/85 px-6 py-3 backdrop-blur-md">
                  <button
                    onClick={() => setPanelMode(null)}
                    className="inline-flex items-center gap-1.5 text-[13px] font-medium text-indigo-600 transition hover:text-indigo-700"
                  >
                    <ArrowLeft size={16} />
                    Back to worklist
                  </button>
                  <div className="flex items-center gap-2">
                    {(() => {
                      const status = selectedReadRow?.status_label ?? deriveStatus(selectedRow);
                      return (
                        <span
                          className={`inline-flex rounded-md border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${statusClass(
                            status,
                          )}`}
                        >
                          {status}
                        </span>
                      );
                    })()}
                    <span
                      className={`inline-flex rounded-md border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em] ${priorityClass(
                        selectedReadRow?.priority ?? selectedRow.request.priority,
                      )}`}
                    >
                      {selectedReadRow?.priority ?? selectedRow.request.priority ?? "medium"}
                    </span>
                    <button
                      onClick={() => setPanelMode(null)}
                      aria-label="Close panel"
                      className="ml-1 text-slate-400 transition hover:text-slate-700"
                    >
                      <X size={18} />
                    </button>
                  </div>
                </div>

                <div className="space-y-5 px-6 py-6">
                  {/* Identity row */}
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <div className="card p-4">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Patient</div>
                      <div className="mt-2 flex items-center gap-2.5">
                        <PatientAvatar
                          firstName={selectedRow.request.first_name}
                          lastName={selectedRow.request.last_name}
                          size={38}
                        />
                        <div className="min-w-0">
                          <div className="truncate text-[15px] font-semibold tracking-[-0.01em] text-slate-900">
                            {selectedRow.request.first_name} {selectedRow.request.last_name}
                          </div>
                          <div className="text-[11px] text-slate-500">DOB {formatShortDate(selectedRow.request.dob)}</div>
                        </div>
                      </div>
                      <div className="mono mt-2 text-[11px] text-slate-500">ID: {selectedRow.request.subscriber_id}</div>
                    </div>

                    <div className="card p-4">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Payer</div>
                      <div className="mt-2">
                        <PayerLogo label={selectedRow.check?.payer_id || selectedRow.request.primary_payer_id} />
                      </div>
                      <div className="mono mt-2 text-[11px] text-slate-500">
                        Payer ID: {selectedRow.check?.payer_id || selectedRow.request.primary_payer_id}
                      </div>
                    </div>

                    <div className="card p-4">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.1em] text-slate-400">Status</div>
                      {(() => {
                        const active = selectedRow.check?.is_active === true;
                        const inactive = selectedRow.check?.is_active === false;
                        return (
                          <>
                            <div className="mt-2 flex items-center gap-2">
                              <span
                                className={`flex h-7 w-7 items-center justify-center rounded-full ${
                                  active
                                    ? "bg-emerald-100 text-emerald-600"
                                    : inactive
                                      ? "bg-red-100 text-red-600"
                                      : "bg-slate-100 text-slate-500"
                                }`}
                              >
                                {active ? <Check size={15} strokeWidth={3} /> : <AlertTriangle size={14} />}
                              </span>
                              <span
                                className={`text-[16px] font-semibold ${
                                  active ? "text-emerald-600" : inactive ? "text-red-600" : "text-slate-700"
                                }`}
                              >
                                {active ? "Active" : inactive ? "Inactive" : "Pending"}
                              </span>
                            </div>
                            <div className="mt-2 text-[11px] text-slate-500">
                              As of {formatShortDate(selectedRow.check?.checked_at)}
                            </div>
                          </>
                        );
                      })()}
                    </div>
                  </div>

                  {/* AI Summary banner */}
                  <div className="relative overflow-hidden rounded-2xl border border-indigo-100 bg-gradient-to-br from-indigo-50/90 via-white to-violet-50/80 p-5 shadow-sm">
                    <div className="pointer-events-none absolute -right-5 -top-5 text-indigo-100" aria-hidden>
                      <BrainCircuit size={120} strokeWidth={1} />
                    </div>
                    <div className="relative">
                      <div className="mb-2 flex items-center gap-2">
                        <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-white shadow-sm">
                          <Sparkles size={15} />
                        </span>
                        <span className="text-[13px] font-semibold text-slate-800">AI Summary</span>
                        <span className="inline-flex items-center gap-1 rounded-full bg-indigo-100/70 px-2 py-0.5 text-[10px] font-semibold text-indigo-600">
                          <Cpu size={10} /> Powered by AI
                        </span>
                      </div>
                      <p className="max-w-[85%] text-[18px] font-semibold leading-snug tracking-[-0.01em] text-slate-900">
                        {buildAiSummary(
                          selectedRow,
                          serviceLabelFor(selectedRow.request.cdt_codes?.[0]),
                          estimatedPatientPortion(selectedRow, selectedReadRow, estimates),
                        )}
                      </p>
                    </div>
                  </div>

                  {/* Benefits / Trust / Recommended */}
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                    {/* Benefits Overview */}
                    <div className="card p-5">
                      <div className="mb-4 flex items-center gap-2">
                        <ShieldCheck size={16} className="text-indigo-600" strokeWidth={2} />
                        <h4 className="text-[13px] font-semibold text-slate-900">Benefits Overview</h4>
                      </div>
                      <div className="flex items-start justify-around gap-2">
                        <div className="flex flex-col items-center">
                          <div className="mb-2 text-[11px] font-medium text-slate-500">Annual Max</div>
                          <RadialDonut
                            value={pctOf(selectedRow.check?.annual_max_remaining, selectedRow.check?.annual_max_total)}
                            color="#6366f1"
                            centerValue={`${pctOf(selectedRow.check?.annual_max_remaining, selectedRow.check?.annual_max_total)}%`}
                            centerLabel="Remaining"
                          />
                          <div className="mt-2 text-center text-[13px] font-semibold text-slate-900">
                            {formatCurrency(selectedRow.check?.annual_max_remaining)}
                            <span className="font-normal text-slate-400"> / {formatCurrency(selectedRow.check?.annual_max_total)}</span>
                          </div>
                        </div>
                        <div className="flex flex-col items-center">
                          <div className="mb-2 text-[11px] font-medium text-slate-500">Deductible</div>
                          <RadialDonut
                            value={pctOf(selectedRow.check?.deductible_remaining, selectedRow.check?.deductible_total)}
                            color="#10b981"
                            centerValue={`${pctOf(selectedRow.check?.deductible_remaining, selectedRow.check?.deductible_total)}%`}
                            centerLabel="Remaining"
                          />
                          <div className="mt-2 text-center text-[13px] font-semibold text-slate-900">
                            {formatCurrency(selectedRow.check?.deductible_remaining)}
                            <span className="font-normal text-slate-400"> / {formatCurrency(selectedRow.check?.deductible_total)}</span>
                          </div>
                        </div>
                      </div>
                      <div className="mt-4 flex items-center gap-1.5 rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-500">
                        <ShieldCheck size={12} className="shrink-0 text-slate-400" />
                        Benefits are {selectedRow.check?.in_network === false ? "out-of-network" : "in-network"} based on{" "}
                        {selectedRow.request.plan_id || "plan"}.
                      </div>
                    </div>

                    {/* Trust Layer */}
                    <div className="card p-5">
                      <div className="mb-2 flex items-center gap-2">
                        <ScanLine size={16} className="text-indigo-600" strokeWidth={2} />
                        <h4 className="text-[13px] font-semibold text-slate-900">Trust Layer</h4>
                      </div>
                      <div className="flex flex-col items-center">
                        <div className="text-[11px] font-medium text-slate-500">Confidence Score</div>
                        <ConfidenceGauge value={confidenceScore(selectedRow)} />
                      </div>
                      <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">Sources</div>
                        {dataSources(selectedReadRow).map((source) => (
                          <div key={source} className="flex items-center gap-2 text-[12px] text-slate-700">
                            <FileText size={13} className="shrink-0 text-slate-400" />
                            <span className="truncate">{source}</span>
                            <Check size={13} className="ml-auto shrink-0 text-emerald-500" />
                          </div>
                        ))}
                        <div className="flex items-center gap-2 text-[11px] text-slate-500">
                          <Clock size={12} className="shrink-0 text-slate-400" />
                          Retrieved {formatDateTime(selectedRow.check?.checked_at)}
                        </div>
                        <div className="flex items-center gap-2 text-[11px] text-slate-500">
                          <Database size={12} className="shrink-0 text-slate-400" />
                          Routing: {selectedRow.check?.routing_status || "\u2014"}
                        </div>
                      </div>
                    </div>

                    {/* Recommended Action */}
                    <div className="card flex flex-col p-5">
                      <div className="mb-3 flex items-center gap-2">
                        <Sparkles size={16} className="text-indigo-600" strokeWidth={2} />
                        <h4 className="text-[13px] font-semibold text-slate-900">Recommended Action</h4>
                      </div>
                      {(() => {
                        const portion = estimatedPatientPortion(selectedRow, selectedReadRow, estimates);
                        return (
                          <div className="flex flex-1 flex-col items-center justify-center rounded-xl bg-gradient-to-b from-indigo-50/70 to-violet-50/50 px-4 py-5 text-center">
                            <div className="text-[11px] font-medium text-slate-500">Collect</div>
                            <div className="my-1 text-[34px] font-bold leading-none tracking-tight text-indigo-600">
                              {portion != null ? formatCurrency(portion) : "\u2014"}
                            </div>
                            <div className="text-[11px] text-slate-500">Patient Portion (Estimated)</div>
                            <button className="btn-sheen lift-on-hover mt-4 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-2.5 text-[13px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700">
                              <CreditCard size={15} />
                              Collect {portion != null ? formatCurrency(portion) : ""}
                            </button>
                            <button className="mt-2 inline-flex items-center gap-1.5 text-[12px] font-medium text-indigo-600 hover:text-indigo-700">
                              <FileText size={13} />
                              View Breakdown
                            </button>
                          </div>
                        );
                      })()}
                    </div>
                  </div>

                  {/* Meta strip */}
                  <div className="card grid grid-cols-2 gap-px overflow-hidden bg-slate-100 p-0 sm:grid-cols-4">
                    {[
                      {
                        icon: CalendarDays,
                        label: "Eligibility Date",
                        value: formatShortDate(selectedRow.check?.checked_at),
                      },
                      {
                        icon: Stethoscope,
                        label: "Service",
                        value: serviceLabelFor(selectedRow.request.cdt_codes?.[0]) ?? "\u2014",
                      },
                      {
                        icon: Network,
                        label: "Network",
                        value:
                          selectedRow.check?.in_network == null
                            ? "\u2014"
                            : selectedRow.check.in_network
                              ? "In-Network"
                              : "Out-of-Network",
                      },
                      {
                        icon: FileText,
                        label: "Plan Type",
                        value: selectedRow.request.plan_id || "\u2014",
                      },
                    ].map((cell) => {
                      const Icon = cell.icon;
                      return (
                        <div key={cell.label} className="flex items-center gap-2.5 bg-white px-4 py-3.5">
                          <Icon size={16} className="shrink-0 text-slate-400" />
                          <div className="min-w-0">
                            <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
                              {cell.label}
                            </div>
                            <div className="truncate text-[12.5px] font-medium text-slate-800">{cell.value}</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Procedure estimates */}
                  {estimates.length ? (
                    <div className="card p-5">
                      <div className="mb-3 flex items-center gap-2">
                        <Stethoscope size={16} className="text-indigo-600" strokeWidth={2} />
                        <h4 className="text-[13px] font-semibold text-slate-900">Procedure estimates</h4>
                      </div>
                      <div className="border-t border-slate-100">
                        {estimates.map((estimate) => (
                          <div
                            key={estimate.id}
                            className="grid grid-cols-[1fr_auto_auto] items-center gap-3 border-b border-slate-100 py-2.5 last:border-b-0"
                          >
                            <div className="text-[13px] font-medium text-slate-700">
                              {serviceLabelFor(estimate.cdt_code) ?? "\u2014"}
                            </div>
                            <div className="text-right text-[12px] font-medium">
                              <span
                                className={
                                  estimate.procedure_covered === false ? "text-red-600" : "text-emerald-600"
                                }
                              >
                                {estimate.procedure_covered === false ? "Not covered" : "Covered"}
                              </span>
                            </div>
                            <div className="mono w-16 text-right text-[12px] text-slate-500">
                              {formatCurrency(estimate.patient_responsibility)}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}

                  {/* Processing timeline */}
                  <div className="card p-5">
                    <div className="mb-3 flex items-center gap-2">
                      <Activity size={16} className="text-indigo-600" strokeWidth={2} />
                      <h4 className="text-[13px] font-semibold text-slate-900">Processing timeline</h4>
                    </div>
                    <div className="max-h-40 space-y-1.5 overflow-y-auto border-t border-slate-100 pt-3">
                      {events.length ? (
                        events.map((event) => (
                          <div key={event.id} className="flex items-baseline justify-between gap-2 text-[12px]">
                            <span className="text-slate-700">{humanizeEventType(event.event_type)}</span>
                            <span className="mono text-[10px] text-slate-500">{timeAgo(event.created_at)}</span>
                          </div>
                        ))
                      ) : (
                        <div className="text-[12px] text-slate-500">No processing events yet.</div>
                      )}
                    </div>
                  </div>

                  {/* Diagnostics & notes */}
                  {(() => {
                    const hasDiagnostics =
                      selectedRow.request.error_message ||
                      selectedRow.request.error_code ||
                      selectedRow.request.suggested_action ||
                      selectedRow.request.failure_category ||
                      selectedRow.request.agent_duration_ms ||
                      selectedRow.request.edge_duration_ms ||
                      selectedRow.check?.inactive_reason ||
                      selectedRow.check?.missing_fields?.length ||
                      selectedRow.check?.integrity_warnings?.length ||
                      selectedReadRow?.voice_session_status;
                    if (!hasDiagnostics) return null;
                    return (
                      <div className="card p-5">
                        <div className="mb-3 flex items-center gap-2">
                          <AlertTriangle size={16} className="text-amber-500" strokeWidth={2} />
                          <h4 className="text-[13px] font-semibold text-slate-900">Diagnostics & notes</h4>
                        </div>
                        <div className="space-y-2 text-[12px] text-slate-500">
                          {selectedRow.request.error_message ? <div>Error: {selectedRow.request.error_message}</div> : null}
                          {selectedRow.request.error_code ? <div>Code: {selectedRow.request.error_code}</div> : null}
                          {selectedRow.request.suggested_action ? <div>Action: {selectedRow.request.suggested_action}</div> : null}
                          {selectedRow.request.failure_category ? <div>Category: {selectedRow.request.failure_category}</div> : null}
                          {selectedRow.request.agent_duration_ms ? (
                            <div>Agent call: {selectedRow.request.agent_duration_ms}ms</div>
                          ) : null}
                          {selectedRow.request.edge_duration_ms ? (
                            <div>Edge function: {selectedRow.request.edge_duration_ms}ms</div>
                          ) : null}
                          {selectedRow.check?.inactive_reason ? (
                            <div>Inactive reason: {selectedRow.check.inactive_reason}</div>
                          ) : null}
                          {selectedRow.check?.missing_fields?.length ? (
                            <div>Missing: {selectedRow.check.missing_fields.join(", ")}</div>
                          ) : null}
                          {selectedReadRow?.voice_session_status ? (
                            <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-3 text-[12px] text-blue-900">
                              <div className="flex items-center gap-1.5 font-medium">
                                <Phone size={13} />
                                Voice agent: {voiceSessionStatusLabel(selectedReadRow.voice_session_status)}
                              </div>
                              {selectedReadRow.voice_call_reference ? (
                                <div className="mt-1">Call ref: {selectedReadRow.voice_call_reference}</div>
                              ) : null}
                              {selectedReadRow.voice_extracted_fields ? (
                                <pre className="mt-2 max-h-28 overflow-auto rounded bg-white/70 p-2 text-[11px] text-slate-700">
                                  {JSON.stringify(selectedReadRow.voice_extracted_fields, null, 2)}
                                </pre>
                              ) : null}
                              {selectedReadRow.voice_session_status === "pending_review" ? (
                                <div className="mt-3 flex gap-2">
                                  <button
                                    type="button"
                                    disabled={voiceReviewBusy}
                                    className="flex-1 rounded-lg bg-emerald-600 py-2 text-[13px] font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
                                    onClick={() => void reviewVoiceSession("approve")}
                                  >
                                    Approve voice results
                                  </button>
                                  <button
                                    type="button"
                                    disabled={voiceReviewBusy}
                                    className="flex-1 rounded-lg border border-slate-300 py-2 text-[13px] font-medium text-slate-700 hover:bg-white disabled:opacity-60"
                                    onClick={() => void reviewVoiceSession("reject")}
                                  >
                                    Reject
                                  </button>
                                </div>
                              ) : isStediVoiceComplete(selectedReadRow) ? (
                                <div className="mt-2 rounded-lg border border-emerald-200 bg-emerald-50 px-2 py-1.5 text-[12px] font-medium text-emerald-800">
                                  Eligibility complete — Stedi + voice verification filled all required fields.
                                </div>
                              ) : null}
                            </div>
                          ) : null}
                          {selectedRow.check?.integrity_warnings?.length ? (
                            <div>Warnings: {selectedRow.check.integrity_warnings.join(", ")}</div>
                          ) : null}
                        </div>
                      </div>
                    );
                  })()}

                  {selectedRow.check?.raw_response ? (
                    <div className="card p-5">
                      <button
                        className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-[12px] font-medium text-slate-600 transition hover:border-indigo-300 hover:text-indigo-600"
                        onClick={() => setShowRaw((prev) => !prev)}
                      >
                        <Database size={13} />
                        {showRaw ? "Hide raw 271 response" : "Show raw 271 response"}
                      </button>
                      {showRaw ? (
                        <pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] text-white">
                          {JSON.stringify(selectedRow.check.raw_response, null, 2)}
                        </pre>
                      ) : null}
                    </div>
                  ) : null}

                  {/* Actions */}
                  <div className="flex gap-2 pt-1">
                    {["failed", "needs_attention", "retrying"].includes(selectedRow.request.status) ? (
                      <button
                        className="flex-1 rounded-lg border border-indigo-300 py-2.5 text-[13px] font-semibold text-indigo-600 transition hover:border-indigo-500 hover:bg-indigo-50"
                        onClick={() => void retryFailed(selectedRow)}
                      >
                        Retry failed check
                      </button>
                    ) : null}
                    <button className="btn-sheen lift-on-hover flex-1 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-2.5 text-[13px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700">
                      Submit claim
                    </button>
                  </div>
                </div>
              </div>
            ) : null}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
