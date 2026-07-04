import { NextResponse } from "next/server";

// Proxy to the FastAPI RCM pipeline (POST /agents/rcm/full-pipeline).
// Keeps the server-to-server API key off the client. In development only,
// returns a field-accurate mock when the backend is not configured/reachable.

type PipelineRequest = {
  clinical_note?: string;
  patient_age?: number;
  insurance?: string;
};

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const API_KEY = process.env.RCM_API_KEY ?? "";
const PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";
const IS_PRODUCTION = process.env.NODE_ENV === "production";

function mockResponse(body: PipelineRequest) {
  const note = (body.clinical_note ?? "").toLowerCase();
  const isCrown = note.includes("crown") || note.includes("fracture") || note.includes("caries");
  const isEndo = note.includes("pulp") || note.includes("root canal") || note.includes("endo");

  const cdt = isCrown ? ["D2750", "D2950"] : isEndo ? ["D3330"] : ["D0120", "D1110"];
  const icd = isCrown ? ["K02.9", "K08.89"] : isEndo ? ["K04.0"] : ["Z01.20"];
  const requiresAuth = isEndo;

  return {
    source: "mock",
    coding: {
      cdt_codes: cdt,
      icd10_codes: icd,
      confidence: isCrown ? 0.95 : isEndo ? 0.91 : 0.97,
      justification: isCrown
        ? "Clinical note documents coronal fracture with caries supporting a full-coverage crown and core buildup."
        : isEndo
          ? "Irreversible pulpitis with periapical findings supports molar endodontic therapy."
          : "Documentation supports preventive prophylaxis and periodic evaluation.",
      payer_flags: isEndo ? ["prior_auth_recommended"] : [],
      payer_rules_matched: [],
      status: "pending_review",
    },
    prior_auth: {
      requires_auth: requiresAuth,
      required_documents: requiresAuth ? ["Pre-treatment periapical", "Pulp vitality test results"] : [],
      payer_rules: requiresAuth ? ["Pre-treatment estimate recommended for molar endo"] : ["No authorization required"],
      risk_level: requiresAuth ? "medium" : "low",
      risk_reason: requiresAuth
        ? "Molar endodontics often requires pre-treatment estimate; documentation present."
        : "Routine procedure with satisfied coverage requirements.",
      status: "pending_review",
    },
    claim_draft: {
      status: requiresAuth ? "pending_auth" : "draft",
      claim_payload: {
        codes: { cdt, icd10: icd },
        diagnosis_codes: icd,
        service_lines: cdt.map((code) => ({ cdt_code: code })),
      },
      blockers: requiresAuth ? ["Prior authorization not yet approved"] : [],
      available_actions: requiresAuth ? ["edit"] : ["edit", "submit"],
      details: { cdt_codes: cdt, icd10_codes: icd },
    },
  };
}

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
    if (IS_PRODUCTION) {
      return pipelineUnavailableResponse("FASTAPI_BASE_URL is not configured");
    }
    return NextResponse.json(mockResponse(body));
  }
  if (IS_PRODUCTION && !PRACTICE_ID) {
    return pipelineUnavailableResponse("DASHBOARD_PRACTICE_ID is not configured");
  }

  try {
    const upstream = await fetch(`${BASE_URL.replace(/\/$/, "")}/agents/rcm/full-pipeline`, {
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
      signal: AbortSignal.timeout(20_000),
    });

    if (!upstream.ok) {
      if (IS_PRODUCTION) {
        return pipelineUnavailableResponse(
          `Upstream pipeline returned HTTP ${upstream.status}`,
          502,
        );
      }
      return NextResponse.json({ ...mockResponse(body), source: "mock_fallback", upstream_status: upstream.status });
    }

    const data = await upstream.json();
    return NextResponse.json({ ...data, source: "live" });
  } catch {
    if (IS_PRODUCTION) {
      return pipelineUnavailableResponse("Upstream pipeline is unreachable", 502);
    }
    return NextResponse.json({ ...mockResponse(body), source: "mock_fallback" });
  }
}
