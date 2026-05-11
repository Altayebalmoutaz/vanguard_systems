// process-eligibility-request — Postgres webhook → FastAPI eligibility agent.
//
// Hardening notes:
// - This function is server-to-server (Postgres trigger → edge function → FastAPI).
//   Browsers must not call it. CORS is therefore restricted to an explicit allow
//   list driven by `ALLOWED_ORIGINS` (comma-separated). The default is empty,
//   which means cross-origin browser calls fail their preflight.
// - Every request must carry `X-Webhook-Signature: sha256=<hex>` where the
//   payload is the raw request body and the key is `WEBHOOK_SECRET` (Vault).
//   The companion DB trigger (migration 038) computes and sends this header.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type EligibilityRequestRow = {
  id: string;
  patient_id: string;
  first_name: string;
  last_name: string;
  dob: string;
  subscriber_id: string;
  primary_payer_id: string;
  secondary_payer_id: string | null;
  plan_id: string | null;
  cdt_codes: string[] | null;
  trigger_event: string;
  status: string;
  attempt_count?: number | null;
  max_attempts?: number | null;
  priority?: "low" | "medium" | "high" | null;
  appointment_date?: string | null;
  estimated_claim_value?: number | null;
};

type DatabaseWebhookPayload = {
  type?: string;
  table?: string;
  schema?: string;
  record?: EligibilityRequestRow;
  old_record?: EligibilityRequestRow;
  agent_url?: string;
  supabase_key?: string;
};

type FailureCategory = "config_error" | "agent_error" | "payer_error" | "timeout" | "validation_error" | "unknown";
type ActionableError = {
  error_code: string;
  suggested_action: string;
  terminal_status: "failed" | "retrying" | "needs_attention";
};

// --- Origin / CORS ---------------------------------------------------------

const allowedOrigins = (Deno.env.get("ALLOWED_ORIGINS") ?? "")
  .split(",")
  .map((origin) => origin.trim())
  .filter(Boolean);

function corsHeadersFor(originHeader: string | null): Record<string, string> {
  // Server-to-server callers (the Postgres trigger) do not send Origin; we
  // respond with no CORS headers, which browsers treat as a same-origin denial.
  if (!originHeader) return {};
  if (!allowedOrigins.includes(originHeader)) return {};
  return {
    "Access-Control-Allow-Origin": originHeader,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-webhook-signature",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    Vary: "Origin",
  };
}

// --- Webhook signature -----------------------------------------------------

const SIGNATURE_HEADER = "x-webhook-signature";
const encoder = new TextEncoder();

async function importHmacKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i++) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

async function verifySignature(rawBody: string, header: string | null, secret: string): Promise<boolean> {
  if (!header) return false;
  const prefix = "sha256=";
  if (!header.startsWith(prefix)) return false;
  const provided = header.slice(prefix.length).toLowerCase();
  const key = await importHmacKey(secret);
  const sigBytes = await crypto.subtle.sign("HMAC", key, encoder.encode(rawBody));
  const expected = bytesToHex(new Uint8Array(sigBytes));
  return timingSafeEqualHex(provided, expected);
}

// --- helpers ---------------------------------------------------------------

function env(name: string): string {
  const value = Deno.env.get(name);
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function buildEligibilityPayload(row: EligibilityRequestRow): Record<string, unknown> {
  return {
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
  };
}

function elapsedMs(startedAt: number): number {
  return Math.max(0, Date.now() - startedAt);
}

function classifyFailure(message: string, status?: number): FailureCategory {
  const lower = message.toLowerCase();
  if (lower.includes("missing required environment variable") || lower.includes("configuration")) return "config_error";
  if (lower.includes("timeout") || lower.includes("timed out")) return "timeout";
  if (status === 400 || status === 422 || lower.includes("validation")) return "validation_error";
  if (status === 404) return "agent_error";
  if (status && status >= 500) return "agent_error";
  if (status && status >= 400) return "payer_error";
  return "unknown";
}

function actionableError(message: string, status?: number): ActionableError {
  const lower = message.toLowerCase();
  if (lower.includes("member") || lower.includes("subscriber")) {
    return {
      error_code: "INVALID_MEMBER_ID",
      suggested_action: "Request updated insurance information from the patient.",
      terminal_status: "needs_attention",
    };
  }
  if (lower.includes("dob") || lower.includes("birth")) {
    return {
      error_code: "DOB_MISMATCH",
      suggested_action: "Verify patient demographics before retrying.",
      terminal_status: "needs_attention",
    };
  }
  if (lower.includes("inactive") || lower.includes("not active")) {
    return {
      error_code: "INACTIVE_COVERAGE",
      suggested_action: "Notify the patient that coverage appears inactive.",
      terminal_status: "needs_attention",
    };
  }
  if (lower.includes("timeout") || lower.includes("timed out") || status === 408 || status === 429 || (status && status >= 500)) {
    return {
      error_code: "PAYER_TIMEOUT",
      suggested_action: "Retry automatically when the next retry window opens.",
      terminal_status: "retrying",
    };
  }
  if (status === 404) {
    return {
      error_code: "AGENT_ENDPOINT_NOT_FOUND",
      suggested_action:
        "Eligibility URL returned 404: use /eligibility/check when running uvicorn app.eligibility.main:app (e.g. port 8010), or /eligibility-agent/eligibility/check with uvicorn app.main:app. Ngrok port must match the server port.",
      terminal_status: "failed",
    };
  }
  return {
    error_code: "AGENT_ERROR",
    suggested_action: "Review the request details and retry if the payer information is correct.",
    terminal_status: "failed",
  };
}

function extractCheckId(result: Record<string, unknown>, key: "primary" | "secondary"): string | null {
  const section = result[key];
  if (section && typeof section === "object" && "check_id" in section) {
    const checkId = (section as { check_id?: unknown }).check_id;
    return typeof checkId === "string" ? checkId : null;
  }

  if (key === "primary" && result.cached === true) {
    const record = result.record;
    if (record && typeof record === "object" && "id" in record) {
      const id = (record as { id?: unknown }).id;
      return typeof id === "string" ? id : null;
    }
  }

  return null;
}

// --- handler ---------------------------------------------------------------

Deno.serve(async (request) => {
  const origin = request.headers.get("origin");
  const cors = corsHeadersFor(origin);

  if (request.method === "OPTIONS") {
    // Preflights only succeed for explicit allow-listed origins.
    return new Response("ok", { headers: cors });
  }

  if (request.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const supabaseUrl = env("SUPABASE_URL");
  const webhookSecret = env("WEBHOOK_SECRET");
  const agentApiKey = Deno.env.get("ELIGIBILITY_AGENT_API_KEY");
  const edgeStartedAt = Date.now();
  let agentDurationMs: number | null = null;
  let agentHttpStatus: number | null = null;
  let row: EligibilityRequestRow | undefined;
  let supabaseKey = "";

  // Read the raw body once so we can verify the signature BEFORE parsing JSON.
  const rawBody = await request.text();
  const signatureHeader = request.headers.get(SIGNATURE_HEADER);
  const signatureValid = await verifySignature(rawBody, signatureHeader, webhookSecret);
  if (!signatureValid) {
    return new Response(JSON.stringify({ error: "invalid_signature" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  try {
    const payload = JSON.parse(rawBody) as DatabaseWebhookPayload;
    row = payload.record;
    supabaseKey = payload.supabase_key ?? env("SUPABASE_ANON_KEY");
    const supabase = createClient(supabaseUrl, supabaseKey, {
      auth: { persistSession: false, autoRefreshToken: false },
    });

    const writeEvent = async (requestId: string, eventType: string, detail: Record<string, unknown> = {}) => {
      const { error } = await supabase.from("eligibility_request_events").insert({
        request_id: requestId,
        event_type: eventType,
        detail,
      });
      if (error) throw new Error(`Failed to write event: ${error.message}`);
    };

    const updateRequest = async (requestId: string, values: Record<string, unknown>) => {
      const { error } = await supabase.from("eligibility_requests").update(values).eq("id", requestId);
      if (error) throw new Error(`Failed to update request: ${error.message}`);
    };
    const updateSettings = async (values: Record<string, unknown>) => {
      const { error } = await supabase.from("eligibility_agent_settings").update(values).eq("id", true);
      if (error) throw new Error(`Failed to update settings: ${error.message}`);
    };

    if (!row?.id) {
      return new Response(JSON.stringify({ error: "Missing webhook record" }), {
        status: 400,
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    if (row.status !== "queued") {
      return new Response(JSON.stringify({ skipped: true, status: row.status }), {
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    const attemptCount = (row.attempt_count ?? 0) + 1;
    await updateRequest(row.id, {
      status: "processing",
      status_reason: "Calling eligibility agent",
      error_message: null,
      error_code: null,
      suggested_action: null,
      failure_category: null,
      attempt_count: attemptCount,
      started_at: new Date().toISOString(),
      last_attempt_at: new Date().toISOString(),
      locked_at: new Date().toISOString(),
      locked_by: "process-eligibility-request",
      next_retry_at: null,
    });
    await updateSettings({ last_sync_at: new Date().toISOString() });
    await writeEvent(row.id, "started", { attempt_count: attemptCount });

    const eligibilityEndpoint = payload.agent_url ?? env("ELIGIBILITY_AGENT_CHECK_URL");
    const eligibilityPayload = buildEligibilityPayload(row);
    await writeEvent(row.id, "agent_call_started", {
      agent_url: eligibilityEndpoint,
      payload_keys: Object.keys(eligibilityPayload),
    });

    const agentStartedAt = Date.now();
    const response = await fetch(eligibilityEndpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        ...(agentApiKey ? { Authorization: `Bearer ${agentApiKey}` } : {}),
      },
      body: JSON.stringify(eligibilityPayload),
    });
    agentDurationMs = elapsedMs(agentStartedAt);
    agentHttpStatus = response.status;

    const result = (await response.json().catch(() => ({}))) as Record<string, unknown>;

    if (!response.ok) {
      const message =
        typeof result.detail === "string"
          ? result.detail
          : typeof result.error === "string"
            ? result.error
            : `Eligibility agent returned ${response.status}`;
      throw new Error(message);
    }
    await writeEvent(row.id, "agent_call_completed", {
      http_status: response.status,
      duration_ms: agentDurationMs,
    });

    const primaryCheckId = extractCheckId(result, "primary");
    const secondaryCheckId = extractCheckId(result, "secondary");

    await updateRequest(row.id, {
      status: "completed",
      status_reason: "Eligibility agent completed",
      primary_check_id: primaryCheckId,
      secondary_check_id: secondaryCheckId,
      output_json: result,
      error_message: null,
      error_code: null,
      suggested_action: null,
      failure_category: null,
      agent_http_status: agentHttpStatus,
      agent_duration_ms: agentDurationMs,
      edge_duration_ms: elapsedMs(edgeStartedAt),
      locked_at: null,
      locked_by: null,
      completed_at: new Date().toISOString(),
    });
    await writeEvent(row.id, "result_linked", {
      primary_check_id: primaryCheckId,
      secondary_check_id: secondaryCheckId,
      edge_duration_ms: elapsedMs(edgeStartedAt),
    });

    return new Response(JSON.stringify({ ok: true, request_id: row.id, primary_check_id: primaryCheckId }), {
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Eligibility processing failed";
    const category = classifyFailure(message, agentHttpStatus ?? undefined);
    const action = actionableError(message, agentHttpStatus ?? undefined);
    const nextRetryAt =
      action.terminal_status === "retrying" && (row?.max_attempts ?? 3) > (row?.attempt_count ?? 0) + 1
        ? new Date(Date.now() + 5 * 60_000).toISOString()
        : null;

    if (row?.id) {
      const supabase = createClient(supabaseUrl, supabaseKey || env("SUPABASE_ANON_KEY"), {
        auth: { persistSession: false, autoRefreshToken: false },
      });
      const updateRequest = async (requestId: string, values: Record<string, unknown>) => {
        const { error } = await supabase.from("eligibility_requests").update(values).eq("id", requestId);
        if (error) throw new Error(`Failed to update request: ${error.message}`);
      };
      const updateSettings = async (values: Record<string, unknown>) => {
        const { error } = await supabase.from("eligibility_agent_settings").update(values).eq("id", true);
        if (error) throw new Error(`Failed to update settings: ${error.message}`);
      };
      const writeEvent = async (requestId: string, eventType: string, detail: Record<string, unknown> = {}) => {
        const { error } = await supabase.from("eligibility_request_events").insert({
          request_id: requestId,
          event_type: eventType,
          detail,
        });
        if (error) throw new Error(`Failed to write event: ${error.message}`);
      };
      await updateRequest(row.id, {
        status: action.terminal_status,
        status_reason: message,
        error_message: message,
        error_code: action.error_code,
        suggested_action: action.suggested_action,
        failure_category: category,
        agent_http_status: agentHttpStatus,
        agent_duration_ms: agentDurationMs,
        edge_duration_ms: elapsedMs(edgeStartedAt),
        locked_at: null,
        locked_by: null,
        next_retry_at: nextRetryAt,
      });
      if (nextRetryAt) {
        await updateSettings({ next_retry_at: nextRetryAt });
      }
      await writeEvent(row.id, "failed", {
        failure_category: category,
        error_code: action.error_code,
        suggested_action: action.suggested_action,
        message,
        http_status: agentHttpStatus,
        agent_duration_ms: agentDurationMs,
        edge_duration_ms: elapsedMs(edgeStartedAt),
      });
    }

    return new Response(JSON.stringify({ error: message, request_id: row?.id ?? null }), {
      status: 500,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
});
