import { NextResponse } from "next/server";

// Proxy to FastAPI async pipeline enqueue (POST /agents/rcm/full-pipeline/jobs).

type PipelineRequest = {
  clinical_note?: string;
  patient_age?: number;
  insurance?: string;
};

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.RCM_API_KEY ?? "";
const PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";
const IS_PRODUCTION = process.env.NODE_ENV === "production";

function pipelineUnavailableResponse(detail: string, status = 503) {
  return NextResponse.json({ error: detail, source: "unavailable" }, { status });
}

export async function POST(request: Request) {
  let body: PipelineRequest = {};
  try {
    body = (await request.json()) as PipelineRequest;
  } catch {
    body = {};
  }

  if (!BASE_URL) {
    return pipelineUnavailableResponse("FASTAPI_BASE_URL is not configured");
  }
  if (IS_PRODUCTION && !PRACTICE_ID) {
    return pipelineUnavailableResponse("DASHBOARD_PRACTICE_ID is not configured");
  }

  try {
    const upstream = await fetch(`${BASE_URL.replace(/\/$/, "")}/agents/rcm/full-pipeline/jobs`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(API_KEY ? { "x-api-key": API_KEY } : {}),
        ...(PRACTICE_ID ? { "x-practice-id": PRACTICE_ID } : {}),
      },
      body: JSON.stringify({
        clinical_note: body.clinical_note ?? "",
        patient_age: body.patient_age ?? 40,
        insurance: body.insurance ?? "Anthem BCBS",
      }),
      signal: AbortSignal.timeout(10_000),
    });

    const payload = await upstream.json().catch(() => ({}));
    if (!upstream.ok) {
      return NextResponse.json(payload, { status: upstream.status });
    }
    return NextResponse.json(payload);
  } catch {
    return pipelineUnavailableResponse("Upstream pipeline job enqueue is unreachable", 502);
  }
}
