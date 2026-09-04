import { cookies } from "next/headers";

import { proxyFastApi } from "@/lib/bff/fastapiProxy";
import { PRACTICE_COOKIE, resolveActivePracticeId } from "@/lib/practice";

export async function GET() {
  const upstream = await proxyFastApi("/auth/me");
  const payload = (await upstream.json().catch(() => ({}))) as {
    practice_roles?: { practice_id?: string; role?: string }[];
  };
  if (!upstream.ok) {
    return Response.json(payload, { status: upstream.status });
  }
  const cookieStore = await cookies();
  const allowed = (payload.practice_roles ?? [])
    .map((row) => String(row.practice_id ?? "").trim())
    .filter(Boolean);
  const active = resolveActivePracticeId({
    cookiePracticeId: cookieStore.get(PRACTICE_COOKIE)?.value,
    allowedPracticeIds: allowed,
    fallbackPracticeId: process.env.DASHBOARD_PRACTICE_ID ?? "",
  });
  return Response.json({ ...payload, active_practice_id: active });
}
