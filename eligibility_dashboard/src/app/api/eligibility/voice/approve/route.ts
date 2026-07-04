import { NextResponse } from "next/server";

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.ELIGIBILITY_AGENT_API_KEY ?? process.env.RCM_API_KEY ?? "";

type ReviewBody = {
  session_id?: string;
  action?: "approve" | "reject";
  approved_by?: string;
};

export async function POST(request: Request) {
  if (!BASE_URL) {
    return NextResponse.json({ error: "FASTAPI_BASE_URL is not configured" }, { status: 503 });
  }

  let body: ReviewBody = {};
  try {
    body = (await request.json()) as ReviewBody;
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }

  const sessionId = body.session_id?.trim();
  if (!sessionId) {
    return NextResponse.json({ error: "session_id required" }, { status: 400 });
  }

  const action = body.action === "reject" ? "reject" : "approve";
  const url = `${BASE_URL.replace(/\/$/, "")}/eligibility-agent/eligibility/voice/sessions/${sessionId}/review`;

  const upstream = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {}),
    },
    body: JSON.stringify({
      action,
      approved_by: body.approved_by ?? "dashboard_staff",
    }),
  });

  const payload = await upstream.json().catch(() => ({}));
  if (!upstream.ok) {
    return NextResponse.json(payload, { status: upstream.status });
  }
  return NextResponse.json(payload);
}
