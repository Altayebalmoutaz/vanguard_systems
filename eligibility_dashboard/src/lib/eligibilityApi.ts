import type {
  EligibilityAgentSettings,
  EligibilityDashboardRow,
  EligibilityRequestEvent,
  ProcedureEstimate,
} from "@/lib/types";

type ApiError = { error?: string; detail?: string };

async function parseJson<T>(resp: Response): Promise<T & ApiError> {
  return (await resp.json().catch(() => ({}))) as T & ApiError;
}

function errorMessage(payload: ApiError, fallback: string): string {
  if (typeof payload.error === "string") return payload.error;
  if (typeof payload.detail === "string") return payload.detail;
  return fallback;
}

export async function fetchEligibilityQueue(): Promise<{
  ok: boolean;
  rows: EligibilityDashboardRow[];
  message?: string;
}> {
  const resp = await fetch("/api/dashboard/eligibility/queue", { cache: "no-store" });
  const payload = await parseJson<{ rows?: EligibilityDashboardRow[] }>(resp);
  if (!resp.ok) {
    return { ok: false, rows: [], message: errorMessage(payload, "Failed to load eligibility queue") };
  }
  return { ok: true, rows: payload.rows ?? [] };
}

export async function fetchEligibilitySettings(): Promise<{
  ok: boolean;
  settings: EligibilityAgentSettings | null;
}> {
  const resp = await fetch("/api/dashboard/eligibility/settings", { cache: "no-store" });
  const payload = await parseJson<{ settings?: EligibilityAgentSettings | null }>(resp);
  if (!resp.ok) {
    return { ok: false, settings: null };
  }
  return { ok: true, settings: payload.settings ?? null };
}

export type UpdateEligibilitySettingsPayload = {
  voice_verification_enabled?: boolean;
  voice_verification_auto_queue?: boolean;
  auto_check_enabled?: boolean;
  auto_retry_enabled?: boolean;
};

export async function updateEligibilitySettings(
  body: UpdateEligibilitySettingsPayload,
): Promise<{ ok: boolean; settings: EligibilityAgentSettings | null; message?: string }> {
  const resp = await fetch("/api/dashboard/eligibility/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await parseJson<{ settings?: EligibilityAgentSettings | null }>(resp);
  if (!resp.ok) {
    return { ok: false, settings: null, message: errorMessage(payload, "Failed to update settings") };
  }
  return { ok: true, settings: payload.settings ?? null };
}

export async function queueVoiceForRequest(requestId: string): Promise<{
  ok: boolean;
  result: Record<string, unknown> | null;
  message?: string;
}> {
  const resp = await fetch(
    `/api/dashboard/eligibility/requests/${encodeURIComponent(requestId)}/voice/queue`,
    { method: "POST" },
  );
  const payload = await parseJson<Record<string, unknown>>(resp);
  if (!resp.ok) {
    return {
      ok: false,
      result: null,
      message: errorMessage(payload, "Failed to queue voice call"),
    };
  }
  return { ok: true, result: payload };
}

export async function reviewVoiceSession(
  sessionId: string,
  action: "approve" | "reject",
): Promise<{ ok: boolean; message?: string }> {
  const resp = await fetch("/api/eligibility/voice/approve", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, action }),
  });
  const payload = await parseJson<ApiError>(resp);
  if (!resp.ok) {
    return { ok: false, message: errorMessage(payload, "Voice review failed") };
  }
  return { ok: true };
}

export async function fetchProcedureEstimates(requestId: string): Promise<{
  ok: boolean;
  estimates: ProcedureEstimate[];
  message?: string;
}> {
  const resp = await fetch(`/api/dashboard/eligibility/requests/${requestId}/estimates`, { cache: "no-store" });
  const payload = await parseJson<{ estimates?: ProcedureEstimate[] }>(resp);
  if (!resp.ok) {
    return { ok: false, estimates: [], message: errorMessage(payload, "Failed to load estimates") };
  }
  return { ok: true, estimates: payload.estimates ?? [] };
}

export async function fetchRequestEvents(requestId: string): Promise<{
  ok: boolean;
  events: EligibilityRequestEvent[];
  message?: string;
}> {
  const resp = await fetch(`/api/dashboard/eligibility/requests/${requestId}/events`, { cache: "no-store" });
  const payload = await parseJson<{ events?: EligibilityRequestEvent[] }>(resp);
  if (!resp.ok) {
    return { ok: false, events: [], message: errorMessage(payload, "Failed to load events") };
  }
  return { ok: true, events: payload.events ?? [] };
}

export async function fetchEligibilityActivity(limit: number): Promise<{
  ok: boolean;
  events: EligibilityRequestEvent[];
}> {
  const resp = await fetch(`/api/dashboard/eligibility/activity?limit=${limit}`, { cache: "no-store" });
  const payload = await parseJson<{ events?: EligibilityRequestEvent[] }>(resp);
  if (!resp.ok) {
    return { ok: false, events: [] };
  }
  return { ok: true, events: payload.events ?? [] };
}

export type CreateEligibilityRequestPayload = {
  first_name: string;
  last_name: string;
  dob: string;
  subscriber_id: string;
  primary_payer_id: string;
  secondary_payer_id?: string | null;
  plan_id?: string | null;
  cdt_codes: string[];
  trigger_event?: string;
  priority?: "low" | "medium" | "high";
  appointment_date?: string | null;
  appointment_time?: string | null;
  provider_name?: string | null;
  estimated_claim_value?: number | null;
  idempotency_key?: string;
  input_json?: Record<string, unknown>;
};

export async function createEligibilityRequest(body: CreateEligibilityRequestPayload): Promise<{
  ok: boolean;
  message?: string;
}> {
  const resp = await fetch("/api/dashboard/eligibility/requests", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await parseJson<ApiError>(resp);
  if (!resp.ok) {
    return { ok: false, message: errorMessage(payload, "Failed to submit eligibility request") };
  }
  return { ok: true };
}
