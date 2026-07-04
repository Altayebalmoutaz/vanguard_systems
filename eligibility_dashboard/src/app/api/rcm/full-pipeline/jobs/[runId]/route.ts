import { NextResponse } from "next/server";

// Proxy to FastAPI async pipeline status (GET /agents/rcm/full-pipeline/jobs/{run_id}).

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.RCM_API_KEY ?? "";
const PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";
const IS_PRODUCTION = process.env.NODE_ENV === "production";

function pipelineUnavailableResponse(detail: string, status = 503) {
  return NextResponse.json({ error: detail, source: "unavailable" }, { status });
}

type RouteContext = { params: Promise<{ runId: string }> };

export async function GET(_request: Request, context: RouteContext) {
  const { runId } = await context.params;
  const trimmedRunId = runId?.trim();
  if (!trimmedRunId) {
    return NextResponse.json({ error: "run_id required" }, { status: 400 });
  }

  if (!BASE_URL) {
    return pipelineUnavailableResponse("FASTAPI_BASE_URL is not configured");
  }
  if (IS_PRODUCTION && !PRACTICE_ID) {
    return pipelineUnavailableResponse("DASHBOARD_PRACTICE_ID is not configured");
  }

  try {
    const upstream = await fetch(
      `${BASE_URL.replace(/\/$/, "")}/agents/rcm/full-pipeline/jobs/${encodeURIComponent(trimmedRunId)}`,
      {
        method: "GET",
        headers: {
          ...(API_KEY ? { "x-api-key": API_KEY } : {}),
          ...(PRACTICE_ID ? { "x-practice-id": PRACTICE_ID } : {}),
        },
        signal: AbortSignal.timeout(10_000),
      },
    );

    const payload = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return NextResponse.json(payload, { status: upstream.status });
    }
    return NextResponse.json(payload);
  } catch {
    return pipelineUnavailableResponse("Upstream pipeline job status is unreachable", 502);
  }
}
