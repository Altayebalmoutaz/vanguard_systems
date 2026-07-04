import type {
  ClaimCase,
  CodingCase,
  DenialCase,
  JourneyStage,
  PriorAuthCase,
} from "@/lib/rcm/types";

export type WorklistItem = {
  id: string;
  module: "Coding" | "Prior Auth" | "Claims" | "Denials";
  patient_name: string;
  payer: string;
  summary: string;
  amount: number;
  severity: "high" | "medium" | "low";
  href: string;
};

export type FunnelStage = {
  label: string;
  count: number;
  value: number;
};

export type DashboardOverview = {
  worklist: WorklistItem[];
  revenue_funnel: FunnelStage[];
  monthly_trend: number[];
  denial_trend: number[];
  kpis: {
    clean_claim_rate: number;
    denial_rate: number;
    eligibility_verified_today: number;
    coding_pending: number;
    claims_open: number;
    denials_open: number;
    revenue_at_risk: number;
  };
};

export type DashboardAnalytics = {
  payer_mix: { label: string; value: number; color?: string }[];
  agent_throughput: { label: string; value: number }[];
  monthly_trend: number[];
  denial_trend: number[];
  kpis: {
    ai_actions_per_day: number;
    first_pass_yield: number;
    claims_this_month: number;
  };
};

async function parseJson<T>(resp: Response): Promise<T> {
  return (await resp.json().catch(() => ({}))) as T;
}

export async function fetchDashboardOverview(): Promise<{ ok: boolean; data: DashboardOverview | null }> {
  const resp = await fetch("/api/dashboard/overview", { cache: "no-store" });
  const payload = await parseJson<DashboardOverview>(resp);
  if (!resp.ok) return { ok: false, data: null };
  return { ok: true, data: payload };
}

export async function fetchDashboardAnalytics(): Promise<{ ok: boolean; data: DashboardAnalytics | null }> {
  const resp = await fetch("/api/dashboard/analytics", { cache: "no-store" });
  const payload = await parseJson<DashboardAnalytics>(resp);
  if (!resp.ok) return { ok: false, data: null };
  return { ok: true, data: payload };
}

export type ReviewDecisionRequest = {
  decision_id: string;
  status: "approved" | "rejected";
  override?: Record<string, unknown>;
};

export async function reviewCodingDecision(
  body: ReviewDecisionRequest,
): Promise<{ ok: boolean; message?: string }> {
  const resp = await fetch("/api/review-decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await parseJson<{ message?: string; detail?: string; error?: string }>(resp);
  if (!resp.ok) {
    const message =
      typeof payload.error === "string"
        ? payload.error
        : typeof payload.detail === "string"
          ? payload.detail
          : "Failed to review decision";
    return { ok: false, message };
  }
  return { ok: true, message: payload.message };
}

export async function fetchCodingCases(status?: string): Promise<{ ok: boolean; cases: CodingCase[] }> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const resp = await fetch(`/api/dashboard/coding/cases${qs}`, { cache: "no-store" });
  const payload = await parseJson<{ cases?: CodingCase[] }>(resp);
  if (!resp.ok) return { ok: false, cases: [] };
  return { ok: true, cases: payload.cases ?? [] };
}

export async function fetchPriorAuthCases(status?: string): Promise<{ ok: boolean; cases: PriorAuthCase[] }> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const resp = await fetch(`/api/dashboard/prior-auth/cases${qs}`, { cache: "no-store" });
  const payload = await parseJson<{ cases?: PriorAuthCase[] }>(resp);
  if (!resp.ok) return { ok: false, cases: [] };
  return { ok: true, cases: payload.cases ?? [] };
}

export async function fetchClaimCases(status?: string): Promise<{ ok: boolean; cases: ClaimCase[] }> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const resp = await fetch(`/api/dashboard/claims/cases${qs}`, { cache: "no-store" });
  const payload = await parseJson<{ cases?: ClaimCase[] }>(resp);
  if (!resp.ok) return { ok: false, cases: [] };
  return { ok: true, cases: payload.cases ?? [] };
}

export async function fetchDenialCases(status?: string): Promise<{ ok: boolean; cases: DenialCase[] }> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : "";
  const resp = await fetch(`/api/dashboard/denials/cases${qs}`, { cache: "no-store" });
  const payload = await parseJson<{ cases?: DenialCase[] }>(resp);
  if (!resp.ok) return { ok: false, cases: [] };
  return { ok: true, cases: payload.cases ?? [] };
}

export async function fetchPatientJourney(patientId: string): Promise<{ ok: boolean; stages: JourneyStage[] }> {
  const resp = await fetch(`/api/dashboard/patients/${encodeURIComponent(patientId)}/journey`, {
    cache: "no-store",
  });
  const payload = await parseJson<{ stages?: JourneyStage[] }>(resp);
  if (!resp.ok) return { ok: false, stages: [] };
  return { ok: true, stages: payload.stages ?? [] };
}

export const EMPTY_JOURNEY: JourneyStage[] = [
  { key: "eligibility", label: "Eligibility", status: "pending", detail: "Awaiting data" },
  { key: "coding", label: "Coding", status: "pending", detail: "Awaiting data" },
  { key: "prior_auth", label: "Prior auth", status: "pending", detail: "Awaiting data" },
  { key: "claim", label: "Claim", status: "pending", detail: "Awaiting data" },
  { key: "denial", label: "Denial", status: "pending", detail: "Awaiting data" },
];
