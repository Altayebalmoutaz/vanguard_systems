import { createSupabaseServerClient } from "@/lib/supabase/server";

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.RCM_API_KEY ?? "";
const PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";

// #region agent log
function agentDebugLog(hypothesisId: string, message: string, data: Record<string, unknown>) {
  fetch("http://127.0.0.1:7677/ingest/e55c7c2e-a901-4e1a-bb82-11c3edc5bd87", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Debug-Session-Id": "c16f79" },
    body: JSON.stringify({
      sessionId: "c16f79",
      runId: "initial",
      hypothesisId,
      location: "eligibility_dashboard/src/lib/bff/fastapiProxy.ts",
      message,
      data,
      timestamp: Date.now(),
    }),
  }).catch(() => {});
}
// #endregion

export type FastApiProxyOptions = {
  method?: string;
  body?: unknown;
  searchParams?: Record<string, string | number | undefined>;
  timeoutMs?: number;
};

export function fastApiUnavailableResponse(detail: string, status = 503): Response {
  return Response.json({ error: detail, source: "unavailable" }, { status });
}

export async function proxyFastApi(path: string, options: FastApiProxyOptions = {}): Promise<Response> {
  // #region agent log
  agentDebugLog("H1,H2,H3", "BFF proxy entry", {
    path,
    hasBaseUrl: Boolean(BASE_URL),
    baseUrlHost: BASE_URL ? new URL(BASE_URL).host : null,
    hasApiKey: Boolean(API_KEY),
    hasPracticeId: Boolean(PRACTICE_ID),
  });
  // #endregion

  if (!BASE_URL) {
    // #region agent log
    agentDebugLog("H1", "BFF missing FASTAPI_BASE_URL", { path });
    // #endregion
    return fastApiUnavailableResponse("FASTAPI_BASE_URL is not configured");
  }

  const supabase = await createSupabaseServerClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  const token = session?.access_token;

  const url = new URL(`${BASE_URL.replace(/\/$/, "")}${path}`);
  if (options.searchParams) {
    for (const [key, value] of Object.entries(options.searchParams)) {
      if (value !== undefined && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }

  const headers: Record<string, string> = {
    accept: "application/json",
  };
  if (options.body !== undefined) {
    headers["content-type"] = "application/json";
  }
  if (token) {
    headers.authorization = `Bearer ${token}`;
  } else if (API_KEY) {
    headers["x-api-key"] = API_KEY;
  }
  if (PRACTICE_ID) {
    headers["x-practice-id"] = PRACTICE_ID;
  }

  try {
    const upstream = await fetch(url.toString(), {
      method: options.method ?? (options.body !== undefined ? "POST" : "GET"),
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      signal: AbortSignal.timeout(options.timeoutMs ?? 15_000),
    });
    const payload = await upstream.json().catch(() => ({}));
    // #region agent log
    agentDebugLog("H2,H3,H4,H5", "BFF upstream response", {
      path,
      status: upstream.status,
      ok: upstream.ok,
      payloadKeys: payload && typeof payload === "object" ? Object.keys(payload).slice(0, 8) : [],
      error: payload && typeof payload === "object" && "error" in payload ? String(payload.error) : null,
      detail: payload && typeof payload === "object" && "detail" in payload ? String(payload.detail) : null,
    });
    // #endregion
    return Response.json(payload, { status: upstream.status });
  } catch (error) {
    // #region agent log
    agentDebugLog("H2", "BFF upstream fetch failed", {
      path,
      errorName: error instanceof Error ? error.name : typeof error,
      errorMessage: error instanceof Error ? error.message : String(error),
    });
    // #endregion
    return fastApiUnavailableResponse("Upstream FastAPI is unreachable", 502);
  }
}
