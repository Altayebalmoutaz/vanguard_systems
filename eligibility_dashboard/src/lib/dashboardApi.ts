import type { StaffRole } from "@/hooks/useStaffSession";

export type HitlTask = {
  id: string;
  practice_id: string;
  backend_record_id: string;
  backend_claim_id: string;
  task_type: string;
  patient_name: string;
  patient_dob: string | null;
  payer: string | null;
  clinical_note: string;
  demographics_block?: string | null;
  ai_codes: string[] | null;
  ai_summary: string | null;
  biller_edited_codes?: string[] | null;
  pipeline_json?: Record<string, unknown> | null;
  confidence: number | null;
  status: string;
  created_at: string;
  updated_at: string | null;
};

export type HitlResolveAction = "approve" | "reject" | "override";

export type HitlResolveRequest = {
  action: HitlResolveAction;
  actor_label?: string;
  reason?: string;
  final_codes?: string[];
  override_codes?: string[];
  final_summary?: string;
};

export type HitlResolveResponse = {
  message?: string;
  task_id?: string;
  status?: string;
  accepted_claim_id?: string | null;
  task?: HitlTask;
  error?: string;
  detail?: string | { message?: string };
};

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

export async function fetchHitlTasks(status = "pending"): Promise<{ ok: boolean; tasks: HitlTask[] }> {
  const resp = await fetch(`/api/dashboard/hitl/tasks?status=${encodeURIComponent(status)}`, {
    cache: "no-store",
  });
  const payload = await parseJson<{ tasks?: HitlTask[] }>(resp);
  if (!resp.ok) {
    return { ok: false, tasks: [] };
  }
  return { ok: true, tasks: payload.tasks ?? [] };
}

export async function resolveHitlTask(
  taskId: string,
  body: HitlResolveRequest,
): Promise<{ ok: boolean; result: HitlResolveResponse | null; message?: string }> {
  const resp = await fetch(`/api/dashboard/hitl/tasks/${encodeURIComponent(taskId)}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await parseJson<HitlResolveResponse>(resp);
  if (!resp.ok) {
    const detail = payload.detail;
    const message =
      typeof payload.error === "string"
        ? payload.error
        : typeof detail === "string"
          ? detail
          : typeof detail === "object" && detail && "message" in detail
            ? String(detail.message)
            : "Failed to resolve task";
    return { ok: false, result: null, message };
  }
  return { ok: true, result: payload };
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

export async function fetchAuthMe(): Promise<AuthMeResponse | null> {
  const resp = await fetch("/api/auth/me", { cache: "no-store" });
  if (!resp.ok) {
    return null;
  }
  return parseJson<AuthMeResponse>(resp);
}
