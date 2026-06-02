"use client";

import { MiniSparkline } from "@/components/MiniSparkline";
import { PatientAvatar } from "@/components/PatientAvatar";
import { PayerLogo } from "@/components/PayerLogo";
import {
  dashboardAppName,
  dashboardAppSubtitle,
  dashboardPracticeName,
  dashboardUserDisplayName,
} from "@/lib/dashboardEnv";
import { getSupabaseBrowserClient, isSupabaseConfigured } from "@/lib/supabase";
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
  BarChart3,
  Building2,
  Calendar,
  Check,
  ChevronDown,
  ChevronRight,
  Download,
  Layers,
  LayoutDashboard,
  Loader2,
  Phone,
  Plus,
  RotateCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
  X,
} from "lucide-react";
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

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return `${Math.round(value)}%`;
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
  };
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
    return {
      title: "Verified",
      subtitle: "High confidence",
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

function SidebarBrandMark() {
  return (
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
      <path
        d="M10 6c-2.5 0-4.5 2-4.5 4.5v6c0 4 2.2 8 5.2 11l3.4 3.2a2.6 2.6 0 003.6 0l3.4-3.2c3-3 5.2-7 5.2-11v-6C26.5 8 24.5 6 22 6c-1.5 0-2.8.6-3.7 1.6L16 9.9l-2.3-2.3A4.9 4.9 0 0010 6z"
        fill="#4F46E5"
      />
      <path
        d="M14.2 13.8a1.2 1.2 0 011.7 0l1.5 1.5 3.4-3.4a1.2 1.2 0 011.7 1.7l-4.3 4.3a1.2 1.2 0 01-1.7 0l-2.3-2.4a1.2 1.2 0 010-1.7z"
        fill="#fff"
      />
    </svg>
  );
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

function Sidebar() {
  const nav = [
    { label: "Overview", icon: LayoutDashboard, active: true },
    { label: "Patients", icon: Users, active: false },
    { label: "Verifications", icon: ShieldCheck, active: false },
    { label: "Payers", icon: Building2, active: false },
    { label: "Plans", icon: Layers, active: false },
    { label: "Activity Feed", icon: Activity, active: false },
    { label: "Reports", icon: BarChart3, active: false },
    { label: "Settings", icon: Settings, active: false },
  ];

  const userInitials = dashboardUserDisplayName
    .replace(/^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i, "")
    .split(/\s+/)
    .map((p) => p.charAt(0))
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <aside
      className="group/sidebar fixed inset-y-0 left-0 z-30 flex w-[64px] flex-col overflow-hidden border-r border-slate-200/80 bg-white/90 backdrop-blur-sm transition-[width] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:w-[232px] hover:shadow-[8px_0_30px_-12px_rgba(15,23,42,0.08)]"
      style={{ willChange: "width" }}
    >
      {/* Brand */}
      <div className="flex h-[64px] shrink-0 items-center gap-3 px-4">
        <div className="shrink-0">
          <SidebarBrandMark />
        </div>
        <div className="-translate-x-1 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
          <div className="whitespace-nowrap text-[15px] font-semibold tracking-tight text-slate-900">{dashboardAppName}</div>
          <div className="whitespace-nowrap text-[11px] font-normal text-slate-500">{dashboardAppSubtitle}</div>
        </div>
      </div>

      {/* Nav */}
      <nav className="mt-2 flex-1 space-y-0.5 px-2">
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.label}
              type="button"
              disabled={!item.active}
              title={item.label}
              className={`flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-[13.5px] font-medium transition-colors ${
                item.active
                  ? "bg-indigo-50 text-indigo-700"
                  : "cursor-not-allowed text-slate-400 hover:bg-slate-50 hover:text-slate-500"
              }`}
            >
              <Icon
                size={18}
                strokeWidth={item.active ? 2 : 1.75}
                className={`shrink-0 ${item.active ? "text-indigo-600" : "text-slate-400"}`}
              />
              <span className="-translate-x-1 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
                {item.label}
              </span>
            </button>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="px-2 pb-4">
        <div className="mb-3 flex items-center gap-2.5 overflow-hidden rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-2">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
            <ShieldCheck size={14} strokeWidth={2} />
          </div>
          <div className="-translate-x-1 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
            <div className="whitespace-nowrap text-[11.5px] font-semibold text-slate-800">HIPAA Compliant</div>
            <div className="whitespace-nowrap text-[10.5px] text-slate-500">SOC 2 Type II</div>
          </div>
        </div>
        <div className="flex items-center gap-3 border-t border-slate-100 pt-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-bold text-indigo-700">
            {userInitials}
          </div>
          <div className="-translate-x-1 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
            <div className="truncate whitespace-nowrap text-[12.5px] font-semibold text-slate-900">{dashboardUserDisplayName}</div>
            <div className="truncate whitespace-nowrap text-[11px] text-slate-500">{dashboardPracticeName}</div>
          </div>
        </div>
      </div>
    </aside>
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
  if (t.includes("invoked") || t.includes("calling") || t.includes("processing") || t.includes("retry")) {
    return { label: "In Progress", cls: "border-blue-200 bg-blue-50 text-blue-700" };
  }
  if (t.includes("complet") || t.includes("verified")) {
    return { label: "Verified – High Confidence", cls: "border-emerald-200 bg-emerald-50 text-emerald-700" };
  }
  return null;
}

function AgentActivityRail({
  items,
  rowsById,
  realtimeConnected,
  expanded,
  onToggleExpand,
}: {
  items: EligibilityRequestEvent[];
  rowsById: Map<string, EligibilityDashboardRow>;
  realtimeConnected: boolean;
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
          {realtimeConnected ? "Live" : "Idle"}
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
  const [weeklyBuckets, setWeeklyBuckets] = useState<DailyBucket[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [realtimeConnected, setRealtimeConnected] = useState(false);
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
  const refreshTimerRef = useRef<number | null>(null);
  /** Set after mount so server and first client paint match (avoids hydration mismatch on time/locale). */
  const [clientGreeting] = useState(() => {
    const h = new Date().getHours();
    return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  });
  const [clientDateLabel] = useState(() =>
    new Date().toLocaleDateString(undefined, {
      month: "long",
      day: "numeric",
      year: "numeric",
    }),
  );
  const [clientAsOfTime] = useState(() =>
    new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
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
    const client = getSupabaseBrowserClient();
    if (!client) {
      setRows(demoRows);
      setReadRows(demoRows.map(syntheticReadRowFromDashboard));
      setBanner("Supabase env vars are not configured. Showing local design data.");
      setLoading(false);
      return;
    }

    setRefreshing(true);
    setBanner(null);

    const { data, error } = await client
      .from("eligibility_dashboard_rows")
      .select("*")
      .order("priority_rank", { ascending: true })
      .order("created_at", { ascending: false })
      .limit(75);

    if (error) {
      setRows((prev) => (prev.length ? prev : []));
      setBanner(error.message);
      setLoading(false);
      setRefreshing(false);
      return;
    }

    const typedRows = (data ?? []) as EligibilityDashboardRow[];
    setReadRows(typedRows);
    setRows(typedRows.map(rowFromReadModel));
    setLoading(false);
    setRefreshing(false);
  }, []);

  const loadSettings = useCallback(async () => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setSettings(null);
      return;
    }

    const { data, error } = await client.from("eligibility_agent_settings").select("*").single();
    if (error) {
      setSettings(null);
      return;
    }

    setSettings(data as EligibilityAgentSettings);
  }, []);

  const loadWeeklyBuckets = useCallback(async () => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setWeeklyBuckets([]);
      return;
    }

    const { data, error } = await client.rpc("eligibility_daily_kpi_buckets", { p_days: 7 });
    if (!error && Array.isArray(data) && data.length) {
      setWeeklyBuckets(
        (data as { bucket_date: string; total_count: number | string; verified_count: number | string }[]).map(
          (row) => ({
            bucket_date: String(row.bucket_date),
            total_count: Number(row.total_count),
            verified_count: Number(row.verified_count),
          }),
        ),
      );
      return;
    }

    setWeeklyBuckets([]);
  }, []);

  const scheduleRowsRefresh = useCallback(() => {
    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current);
    }
    refreshTimerRef.current = window.setTimeout(() => {
      void loadRows();
      void loadWeeklyBuckets();
      refreshTimerRef.current = null;
    }, 350);
  }, [loadRows, loadWeeklyBuckets]);

  const loadEstimates = useCallback(async (checkId: string | null | undefined) => {
    const client = getSupabaseBrowserClient();
    if (!client || !checkId || checkId.startsWith("demo-")) {
      setEstimates([]);
      return;
    }

    const { data, error } = await client
      .from("procedure_estimates")
      .select("*")
      .eq("eligibility_check_id", checkId)
      .order("created_at", { ascending: true });

    if (error) {
      setBanner(error.message);
      setEstimates([]);
      return;
    }

    setEstimates((data ?? []) as ProcedureEstimate[]);
  }, []);

  const loadEvents = useCallback(async (requestId: string | null | undefined) => {
    const client = getSupabaseBrowserClient();
    if (!client || !requestId || requestId.startsWith("demo-")) {
      setEvents([]);
      return;
    }

    const { data, error } = await client
      .from("eligibility_request_events")
      .select("*")
      .eq("request_id", requestId)
      .order("created_at", { ascending: false })
      .limit(20);

    if (error) {
      setBanner(error.message);
      setEvents([]);
      return;
    }

    setEvents((data ?? []) as EligibilityRequestEvent[]);
  }, []);

  const loadActivity = useCallback(async (limit: number) => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      setActivity([]);
      return;
    }

    const { data, error } = await client
      .from("eligibility_request_events")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(limit);

    if (error) {
      setActivity([]);
      return;
    }

    setActivity((data ?? []) as EligibilityRequestEvent[]);
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void loadRows();
      void loadSettings();
      void loadWeeklyBuckets();
    }, 0);
    return () => window.clearTimeout(id);
  }, [loadRows, loadSettings, loadWeeklyBuckets]);

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
    const client = getSupabaseBrowserClient();
    if (!isSupabaseConfigured() || !client) return;

    const channel = client
      .channel("eligibility_dashboard")
      .on("postgres_changes", { event: "*", schema: "rcm", table: "eligibility_requests" }, scheduleRowsRefresh)
      .on("postgres_changes", { event: "*", schema: "rcm", table: "eligibility_checks" }, scheduleRowsRefresh)
      .on("postgres_changes", { event: "*", schema: "rcm", table: "procedure_estimates" }, () => {
        if (selectedRow?.request.primary_check_id) void loadEstimates(selectedRow.request.primary_check_id);
      })
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "rcm", table: "eligibility_request_events" },
        (payload) => {
          const next = payload.new as EligibilityRequestEvent;
          const cap = activityCapRef.current;
          setActivity((prev) => {
            if (prev.some((item) => item.id === next.id)) return prev;
            return [next, ...prev].slice(0, cap);
          });
          if (selectedId && next.request_id === selectedId) void loadEvents(selectedId);
        },
      )
      .on("postgres_changes", { event: "*", schema: "rcm", table: "eligibility_agent_settings" }, () => {
        void loadSettings();
      })
      .subscribe((status) => {
        setRealtimeConnected(status === "SUBSCRIBED");
        if (status === "CHANNEL_ERROR") {
          setBanner("Realtime subscription error. Refresh if the queue looks stale.");
        }
      });

    return () => {
      void client.removeChannel(channel);
    };
  }, [loadEstimates, loadEvents, loadSettings, scheduleRowsRefresh, selectedId, selectedRow?.request.primary_check_id]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      if (panelMode === "details") {
        void loadEstimates(selectedRow?.request.primary_check_id);
        void loadEvents(selectedRow?.request.id);
      } else {
        setEstimates([]);
        setEvents([]);
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, [loadEstimates, loadEvents, panelMode, selectedRow?.request.id, selectedRow?.request.primary_check_id]);

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
    const buckets = weeklyBuckets.length ? weeklyBuckets : aggregateDailyFromReadRows(readRows, 7);
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
  }, [readRows, weeklyBuckets]);

  const agentStatus = useMemo<AgentStatusSummary>(
    () => deriveAgentStatus(readRows, settings),
    [readRows, settings],
  );

  const openDetails = (row: DashboardRow) => {
    setSelectedId(row.request.id);
    setPanelMode("details");
  };

  const rerun = async (row: DashboardRow) => {
    const client = getSupabaseBrowserClient();
    if (!client) return;

    const { error } = await client.from("eligibility_requests").insert({
      first_name: row.request.first_name,
      last_name: row.request.last_name,
      dob: row.request.dob,
      subscriber_id: row.request.subscriber_id,
      primary_payer_id: row.request.primary_payer_id,
      secondary_payer_id: row.request.secondary_payer_id,
      plan_id: row.request.plan_id,
      cdt_codes: row.request.cdt_codes ?? [],
      trigger_event: "APPOINTMENT_BOOKED",
      status: "queued",
      priority: row.request.priority ?? "medium",
      appointment_date: row.request.appointment_date ?? null,
      appointment_time: row.request.appointment_time ?? null,
      provider_name: row.request.provider_name ?? null,
      estimated_claim_value: row.request.estimated_claim_value ?? null,
      parent_request_id: row.request.id,
      idempotency_key: createIdempotencyKey("rerun", row.request.id),
      input_json: {
        rerun_of: row.request.id,
        submitted_from: "eligibility_dashboard",
      },
    });

    if (error) {
      setBanner(error.message);
      return;
    }

    await loadRows();
    await loadWeeklyBuckets();
  };

  const retryFailed = async (row: DashboardRow) => {
    const client = getSupabaseBrowserClient();
    if (!client) return;

    const { error } = await client.from("eligibility_requests").insert({
      first_name: row.request.first_name,
      last_name: row.request.last_name,
      dob: row.request.dob,
      subscriber_id: row.request.subscriber_id,
      primary_payer_id: row.request.primary_payer_id,
      secondary_payer_id: row.request.secondary_payer_id,
      plan_id: row.request.plan_id,
      cdt_codes: row.request.cdt_codes ?? [],
      trigger_event: "APPOINTMENT_BOOKED",
      status: "queued",
      priority: row.request.priority ?? "medium",
      appointment_date: row.request.appointment_date ?? null,
      appointment_time: row.request.appointment_time ?? null,
      provider_name: row.request.provider_name ?? null,
      estimated_claim_value: row.request.estimated_claim_value ?? null,
      parent_request_id: row.request.id,
      idempotency_key: createIdempotencyKey("retry", row.request.id),
      input_json: {
        retry_of: row.request.id,
        submitted_from: "eligibility_dashboard",
      },
    });

    if (error) {
      setBanner(error.message);
      return;
    }

    await loadRows();
    await loadWeeklyBuckets();
  };

  const submitRequest = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const client = getSupabaseBrowserClient();
    if (!client) {
      setBanner("Configure NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY to submit checks.");
      return;
    }

    setSubmitting(true);
    const payload = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      dob: form.dob,
      subscriber_id: form.subscriber_id.trim(),
      primary_payer_id: form.primary_payer_id.trim(),
      secondary_payer_id: form.secondary_payer_id.trim() || null,
      plan_id: form.plan_id.trim() || null,
      cdt_codes: parseCodes(form.cdt_codes),
      trigger_event: "APPOINTMENT_BOOKED",
      status: "queued",
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
    };

    const { error } = await client.from("eligibility_requests").insert(payload);
    setSubmitting(false);

    if (error) {
      setBanner(error.message);
      return;
    }

    setForm(emptyForm);
    setPanelMode(null);
    await loadRows();
    await loadWeeklyBuckets();
  };

  return (
    <div className="min-h-screen">
      <Sidebar />

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
            realtimeConnected={realtimeConnected}
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
            className="slide-in-right absolute right-0 top-0 flex h-full w-[400px] flex-col overflow-y-auto border-l border-slate-200 bg-white shadow-[-12px_0px_32px_-12px_rgba(15,23,42,0.18)]"
            style={{ animationDuration: "0.32s" }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              className="absolute right-5 top-5 text-slate-500 transition hover:text-indigo-600"
              onClick={() => setPanelMode(null)}
              aria-label="Close panel"
            >
              <X size={18} />
            </button>

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
              <div className="flex h-full flex-col px-6 pb-6 pt-16">
                <h3 className="text-[22px] font-light tracking-[-0.22px] text-slate-900">
                  {selectedRow.request.first_name} {selectedRow.request.last_name}
                </h3>
                <div className="mono mt-1 text-[12px] text-slate-500">{selectedRow.request.subscriber_id}</div>
                <div className="mt-3 flex items-center gap-2">
                  {(() => {
                    const status = selectedReadRow?.status_label ?? deriveStatus(selectedRow);
                    return (
                      <span
                        className={`inline-flex rounded-[4px] border px-2 py-1 text-[10px] font-normal uppercase tracking-[0.08em] ${statusClass(
                          status,
                        )}`}
                      >
                        {status}
                      </span>
                    );
                  })()}
                  {selectedRow.request.attempt_count ? (
                    <span className="text-[11px] text-slate-500">Attempt {selectedRow.request.attempt_count}</span>
                  ) : null}
                  <span
                    className={`inline-flex rounded-[4px] border px-2 py-1 text-[10px] font-normal uppercase tracking-[0.08em] ${priorityClass(
                      selectedReadRow?.priority ?? selectedRow.request.priority,
                    )}`}
                  >
                    {selectedReadRow?.priority ?? selectedRow.request.priority ?? "medium"}
                  </span>
                </div>
                <div className="my-5 h-px bg-slate-200" />

                {(() => {
                  const decision =
                    selectedReadRow?.status_detail ||
                    selectedRow.request.suggested_action ||
                    selectedRow.request.error_message ||
                    null;
                  return decision ? (
                    <section className="mb-5 rounded-[4px] border border-indigo-200 bg-indigo-50 px-3 py-2.5">
                      <div className="mb-1 flex items-center gap-2 text-[10px] font-normal uppercase tracking-[0.14em] text-indigo-600">
                        <Sparkles size={12} />
                        Agent decision
                      </div>
                      <div className="text-[12px] leading-[1.45] text-slate-700">{decision}</div>
                    </section>
                  ) : null;
                })()}

                <section className="mb-6">
                  <div className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                    <Activity size={14} />
                    Processing timeline
                  </div>
                  <div className="max-h-32 space-y-1.5 overflow-y-auto border-t border-slate-200 pt-2">
                    {events.length ? (
                      events.map((event) => (
                        <div key={event.id} className="flex items-baseline justify-between gap-2 text-[12px]">
                          <span className="font-normal text-slate-700">{humanizeEventType(event.event_type)}</span>
                          <span className="mono text-[10px] text-slate-500">{timeAgo(event.created_at)}</span>
                        </div>
                      ))
                    ) : (
                      <div className="text-[12px] text-slate-500">No processing events yet.</div>
                    )}
                  </div>
                </section>

                <section>
                  <div className="mb-3 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                    <Activity size={14} />
                    Coverage
                  </div>
                  <div className="text-[14px] font-normal text-slate-900">
                    {selectedRow.check?.payer_id || selectedRow.request.primary_payer_id}
                  </div>
                  <div className="mt-1 text-[12px] font-normal text-slate-500">
                    Plan {selectedRow.request.plan_id || "-"} · {selectedRow.check?.coverage_order || "primary"}
                  </div>
                  <div className="mt-1 text-[12px] font-normal text-slate-500">
                    Checked {timeAgo(selectedRow.check?.checked_at ?? selectedRow.request.updated_at)}
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2">
                    <div className="rounded-lg border border-slate-200 bg-white px-2 py-2">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">Coverage</div>
                      <div className="mono num mt-1 text-[13px] text-slate-900">
                        {formatPercent(selectedReadRow?.coverage_percent ?? selectedRow.check?.coverage_percent)}
                      </div>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-white px-2 py-2">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">Copay</div>
                      <div className="mono num mt-1 text-[13px] text-slate-900">
                        {formatCurrency(selectedRow.check?.copay)}
                      </div>
                    </div>
                    <div className="rounded-lg border border-slate-200 bg-white px-2 py-2">
                      <div className="text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-500">Patient Due</div>
                      <div className="mono num mt-1 text-[13px] text-slate-900">
                        {formatCurrency(selectedReadRow?.estimated_patient_responsibility)}
                      </div>
                    </div>
                  </div>
                </section>

                <section className="mt-6">
                  <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                    Procedure Estimates
                  </div>
                  <div className="border-t border-slate-200">
                    {(estimates.length ? estimates : []).map((estimate) => (
                      <div key={estimate.id} className="grid grid-cols-[1fr_auto_auto] gap-3 border-b border-slate-200 py-2">
                        <div className="text-[13px] font-normal text-slate-700">{estimate.cdt_code || "-"}</div>
                        <div className="text-right text-[13px] font-normal text-slate-900">
                          {estimate.procedure_covered === false ? "Not covered" : "Covered"}
                        </div>
                        <div className="mono text-right text-[12px] text-slate-500">
                          {formatCurrency(estimate.patient_responsibility)}
                        </div>
                      </div>
                    ))}
                    {!estimates.length ? (
                      <div className="border-b border-slate-200 py-3 text-[13px] font-normal text-slate-500">
                        No procedure estimates returned yet.
                      </div>
                    ) : null}
                  </div>
                </section>

                <section className="mt-6">
                  <div className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Deductible</div>
                  <div className="mt-3 flex gap-4 text-[13px] font-normal text-slate-700">
                    <span className="mono">{formatCurrency(selectedRow.check?.deductible_total)} total</span>
                    <span className="mono">{formatCurrency(selectedRow.check?.deductible_met)} met</span>
                  </div>
                  <div className="mt-3 h-[6px] w-full overflow-hidden rounded-[3px] bg-slate-200">
                    <div
                      className="h-full rounded-[3px] bg-gradient-to-r from-indigo-500 to-violet-500 transition-[width] duration-700 ease-[cubic-bezier(0.2,0.8,0.2,1)]"
                      style={{
                        width: `${Math.min(
                          100,
                          Math.round(
                            ((selectedRow.check?.deductible_met ?? 0) /
                              Math.max(selectedRow.check?.deductible_total ?? 1, 1)) *
                              100,
                          ),
                        )}%`,
                      }}
                    />
                  </div>
                  <div className="mt-2 text-[12px] font-normal text-slate-500">
                    Remaining: {formatCurrency(selectedRow.check?.deductible_remaining)}
                  </div>
                </section>

                <section className="mt-6 space-y-2 text-[12px] font-normal text-slate-500">
                  {selectedRow.request.error_message ? <div>Error: {selectedRow.request.error_message}</div> : null}
                  {selectedRow.request.error_code ? <div>Code: {selectedRow.request.error_code}</div> : null}
                  {selectedRow.request.suggested_action ? <div>Action: {selectedRow.request.suggested_action}</div> : null}
                  {selectedRow.request.failure_category ? <div>Category: {selectedRow.request.failure_category}</div> : null}
                  {selectedRow.request.agent_duration_ms ? (
                    <div>Agent call: {selectedRow.request.agent_duration_ms}ms</div>
                  ) : null}
                  {selectedRow.request.edge_duration_ms ? <div>Edge function: {selectedRow.request.edge_duration_ms}ms</div> : null}
                  {selectedRow.check?.inactive_reason ? <div>Inactive reason: {selectedRow.check.inactive_reason}</div> : null}
                  {selectedRow.check?.missing_fields?.length ? (
                    <div>Missing: {selectedRow.check.missing_fields.join(", ")}</div>
                  ) : null}
                  {selectedRow.check?.integrity_warnings?.length ? (
                    <div>Warnings: {selectedRow.check.integrity_warnings.join(", ")}</div>
                  ) : null}
                </section>

                {selectedRow.check?.raw_response ? (
                  <section className="mt-5">
                    <button
                      className="rounded-[4px] border border-indigo-300 px-3 py-1.5 text-[12px] text-indigo-600"
                      onClick={() => setShowRaw((prev) => !prev)}
                    >
                      {showRaw ? "Hide raw response" : "Show raw response"}
                    </button>
                    {showRaw ? (
                      <pre className="mt-2 max-h-32 overflow-auto rounded-[4px] bg-slate-900 p-3 text-[11px] text-white">
                        {JSON.stringify(selectedRow.check.raw_response, null, 2)}
                      </pre>
                    ) : null}
                  </section>
                ) : null}

                <div className="mt-auto space-y-2">
                  {["failed", "needs_attention", "retrying"].includes(selectedRow.request.status) ? (
                    <button
                      className="w-full rounded-[4px] border border-indigo-300 py-3 text-[14px] font-normal text-indigo-600 transition hover:border-indigo-600"
                      onClick={() => void retryFailed(selectedRow)}
                    >
                      Retry Failed Check
                    </button>
                  ) : null}
                  <button className="btn-sheen lift-on-hover w-full rounded-[4px] bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-normal text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700">
                    Submit Claim
                  </button>
                </div>
              </div>
            ) : null}
          </aside>
        </div>
      ) : null}
    </div>
  );
}
