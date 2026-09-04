import { cookies } from "next/headers";

import { proxyFastApi } from "@/lib/bff/fastapiProxy";
import { PRACTICE_COOKIE, resolveActivePracticeId } from "@/lib/practice";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function cookieOptions() {
  return {
    httpOnly: true,
    sameSite: "lax" as const,
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: 60 * 60 * 24 * 180,
  };
}

export async function POST(request: Request) {
  let body: { practice_id?: unknown } = {};
  try {
    body = (await request.json()) as { practice_id?: unknown };
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }
  const practiceId = typeof body.practice_id === "string" ? body.practice_id.trim() : "";
  if (!practiceId) {
    return Response.json({ error: "practice_required" }, { status: 400 });
  }

  const upstream = await proxyFastApi("/auth/me");
  const payload = (await upstream.json().catch(() => ({}))) as {
    practice_roles?: { practice_id?: string }[];
    error?: string;
  };
  if (!upstream.ok) {
    return Response.json(payload, { status: upstream.status });
  }
  const allowed = (payload.practice_roles ?? [])
    .map((row) => String(row.practice_id ?? "").trim())
    .filter(Boolean);
  if (!allowed.includes(practiceId)) {
    return Response.json({ error: "practice_forbidden" }, { status: 403 });
  }

  const cookieStore = await cookies();
  cookieStore.set(PRACTICE_COOKIE, practiceId, cookieOptions());
  return Response.json({
    ok: true,
    active_practice_id: practiceId,
    practice_roles: payload.practice_roles ?? [],
  });
}

export async function GET() {
  const upstream = await proxyFastApi("/auth/me");
  const payload = (await upstream.json().catch(() => ({}))) as {
    practice_roles?: { practice_id?: string }[];
  };
  if (!upstream.ok) {
    return Response.json(payload, { status: upstream.status });
  }
  const cookieStore = await cookies();
  const active = resolveActivePracticeId({
    cookiePracticeId: cookieStore.get(PRACTICE_COOKIE)?.value,
    allowedPracticeIds: (payload.practice_roles ?? [])
      .map((row) => String(row.practice_id ?? "").trim())
      .filter(Boolean),
    fallbackPracticeId: process.env.DASHBOARD_PRACTICE_ID ?? "",
  });
  return Response.json({
    active_practice_id: active,
    practice_roles: payload.practice_roles ?? [],
  });
}
