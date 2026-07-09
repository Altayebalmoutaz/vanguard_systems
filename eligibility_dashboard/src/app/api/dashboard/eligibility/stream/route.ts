import { createSupabaseServerClient } from "@/lib/supabase/server";

// SSE must run on the Node runtime and never be statically optimized or buffered.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.RCM_API_KEY ?? "";
const PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";

export async function GET(request: Request) {
  if (!BASE_URL) {
    return Response.json({ error: "FASTAPI_BASE_URL is not configured" }, { status: 503 });
  }

  const supabase = await createSupabaseServerClient();
  const session = supabase ? (await supabase.auth.getSession()).data.session : null;
  const token = session?.access_token;

  const headers: Record<string, string> = { accept: "text/event-stream" };
  if (token) {
    headers.authorization = `Bearer ${token}`;
  } else if (API_KEY) {
    headers["x-api-key"] = API_KEY;
  }
  if (PRACTICE_ID) {
    headers["x-practice-id"] = PRACTICE_ID;
  }
  const lastEventId = request.headers.get("last-event-id");
  if (lastEventId) {
    headers["last-event-id"] = lastEventId;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BASE_URL.replace(/\/$/, "")}/dashboard/eligibility/stream`, {
      headers,
      // Tie upstream lifetime to the browser connection so we don't leak streams.
      signal: request.signal,
    });
  } catch {
    return Response.json({ error: "Upstream FastAPI is unreachable" }, { status: 502 });
  }

  if (!upstream.ok || !upstream.body) {
    return Response.json(
      { error: "Upstream stream unavailable" },
      { status: upstream.status || 502 },
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      "cache-control": "no-cache, no-transform",
      connection: "keep-alive",
      "x-accel-buffering": "no",
    },
  });
}
