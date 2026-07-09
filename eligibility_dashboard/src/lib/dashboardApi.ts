import type { StaffRole } from "@/hooks/useStaffSession";

export type Patient360Profile = {
  patient: Record<string, unknown>;
  latest_eligibility_check: Record<string, unknown> | null;
  agent_runs: Record<string, unknown>[];
};

export type AuthMeResponse = {
  kind: string;
  subject: string;
  practice_roles: { practice_id: string; role: StaffRole }[];
};

async function parseJson<T>(resp: Response): Promise<T> {
  return (await resp.json().catch(() => ({}))) as T;
}

export async function fetchPatient360(patientId: string): Promise<{
  ok: boolean;
  profile: Patient360Profile | null;
  message?: string;
}> {
  const resp = await fetch(`/api/dashboard/patients/${encodeURIComponent(patientId)}`, { cache: "no-store" });
  const payload = await parseJson<Patient360Profile & { error?: string; detail?: string }>(resp);
  if (!resp.ok) {
    const message =
      typeof payload.error === "string"
        ? payload.error
        : typeof payload.detail === "string"
          ? payload.detail
          : "Patient not found";
    return { ok: false, profile: null, message };
  }
  return {
    ok: true,
    profile: {
      patient: payload.patient,
      latest_eligibility_check: payload.latest_eligibility_check,
      agent_runs: payload.agent_runs ?? [],
    },
  };
}

export type OpenDentalConnection = {
  id: string;
  practice_id: string;
  display_name: string | null;
  base_url: string;
  customer_key_ref: string | null;
  customer_key_configured: boolean;
  poll_enabled: boolean;
  poll_interval_seconds: number | string;
  poll_window_days: number;
  cdt_codes: string;
  writeback_enabled: boolean;
  writeback_full: boolean;
  last_poll_at: string | null;
  last_poll_status: string | null;
  last_poll_appointments: number | null;
  last_error: string | null;
  health_status: string | null;
  health_checked_at: string | null;
};

export type OpenDentalRun = {
  id: string;
  run_type: string;
  status: string;
  created_at: string;
  completed_at?: string | null;
  error_message?: string | null;
  result?: Record<string, unknown> | null;
};

export type OpenDentalConnectionUpdate = {
  display_name?: string;
  base_url?: string;
  customer_key_ref?: string;
  poll_enabled?: boolean;
  poll_interval_seconds?: number;
  poll_window_days?: number;
  cdt_codes?: string;
  writeback_enabled?: boolean;
  writeback_full?: boolean;
};

function errorMessage(payload: { error?: unknown; detail?: unknown }, fallback: string): string {
  if (typeof payload.error === "string") return payload.error;
  if (typeof payload.detail === "string") return payload.detail;
  return fallback;
}

export async function fetchOpenDentalConnections(): Promise<{
  ok: boolean;
  connections: OpenDentalConnection[];
  message?: string;
}> {
  const resp = await fetch("/api/dashboard/opendental/connections", { cache: "no-store" });
  const payload = await parseJson<{ connections?: OpenDentalConnection[]; error?: string; detail?: string }>(resp);
  if (!resp.ok) {
    return { ok: false, connections: [], message: errorMessage(payload, "Connections unavailable") };
  }
  return { ok: true, connections: payload.connections ?? [] };
}

export async function updateOpenDentalConnection(
  practiceId: string,
  body: OpenDentalConnectionUpdate,
): Promise<{ ok: boolean; connection: OpenDentalConnection | null; message?: string }> {
  const resp = await fetch(`/api/dashboard/opendental/connections/${encodeURIComponent(practiceId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await parseJson<{ connection?: OpenDentalConnection; error?: string; detail?: string }>(resp);
  if (!resp.ok) {
    return { ok: false, connection: null, message: errorMessage(payload, "Update failed") };
  }
  return { ok: true, connection: payload.connection ?? null };
}

export type OpenDentalFriendlyError = {
  code: string;
  title: string;
  message: string;
  recovery_step: string;
};

export async function fetchOpenDentalOnboardingKey(
  practiceId: string,
): Promise<{
  ok: boolean;
  configured: boolean;
  customerKey: string | null;
  message?: string;
}> {
  const resp = await fetch(
    `/api/dashboard/opendental/connections/${encodeURIComponent(practiceId)}/onboarding-key`,
    { cache: "no-store" },
  );
  const payload = await parseJson<{
    configured?: boolean;
    customer_key?: string | null;
    message?: string;
    error?: string;
    detail?: string;
  }>(resp);
  if (!resp.ok) {
    return {
      ok: false,
      configured: false,
      customerKey: null,
      message: errorMessage(payload, "Could not load clinic key"),
    };
  }
  return {
    ok: true,
    configured: Boolean(payload.configured),
    customerKey: payload.customer_key ?? null,
    message: payload.message,
  };
}

export async function testOpenDentalConnection(
  practiceId: string,
): Promise<{ ok: boolean; error?: string; friendly?: OpenDentalFriendlyError }> {
  const resp = await fetch(
    `/api/dashboard/opendental/connections/${encodeURIComponent(practiceId)}/test`,
    { method: "POST" },
  );
  const payload = await parseJson<{
    ok?: boolean;
    error?: string;
    detail?: string;
    friendly?: OpenDentalFriendlyError;
  }>(resp);
  if (!resp.ok) {
    return { ok: false, error: errorMessage(payload, "Connection test failed") };
  }
  return {
    ok: Boolean(payload.ok),
    error: payload.error,
    friendly: payload.friendly,
  };
}

export async function pollOpenDentalNow(
  practiceId: string,
): Promise<{ ok: boolean; pipelineRunId?: string; message?: string }> {
  const resp = await fetch(
    `/api/dashboard/opendental/connections/${encodeURIComponent(practiceId)}/poll-now`,
    { method: "POST" },
  );
  const payload = await parseJson<{ pipeline_run_id?: string; error?: string; detail?: string }>(resp);
  if (!resp.ok) {
    return { ok: false, message: errorMessage(payload, "Poll enqueue failed") };
  }
  return { ok: true, pipelineRunId: payload.pipeline_run_id };
}

export async function fetchOpenDentalRuns(limit = 50): Promise<{
  ok: boolean;
  runs: OpenDentalRun[];
  message?: string;
}> {
  const resp = await fetch(`/api/dashboard/opendental/runs?limit=${limit}`, { cache: "no-store" });
  const payload = await parseJson<{ runs?: OpenDentalRun[]; error?: string; detail?: string }>(resp);
  if (!resp.ok) {
    return { ok: false, runs: [], message: errorMessage(payload, "Runs unavailable") };
  }
  return { ok: true, runs: payload.runs ?? [] };
}

export async function fetchAuthMe(): Promise<AuthMeResponse | null> {
  const resp = await fetch("/api/auth/me", { cache: "no-store" });
  if (!resp.ok) {
    return null;
  }
  return parseJson<AuthMeResponse>(resp);
}
