import { createSupabaseServerClient } from "@/lib/supabase/server";

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.RCM_API_KEY ?? "";
const PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";

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
  if (!BASE_URL) {
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
  // Prefer staff Bearer when present; always fall back to compose API key so
  // REQUIRE_AUTH=false pilot stacks still reach FastAPI without a session.
  if (token) {
    headers.authorization = `Bearer ${token}`;
  }
  if (API_KEY) {
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
    return Response.json(payload, { status: upstream.status });
  } catch {
    return fastApiUnavailableResponse("Upstream FastAPI is unreachable", 502);
  }
}
