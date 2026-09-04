import { cookies } from "next/headers";

import { PRACTICE_COOKIE, resolveActivePracticeId } from "@/lib/practice";
import { createSupabaseServerClient } from "@/lib/supabase/server";

const BASE_URL = process.env.FASTAPI_BASE_URL ?? process.env.NEXT_PUBLIC_FASTAPI_BASE_URL ?? "";
const ENV_PRACTICE_ID = process.env.DASHBOARD_PRACTICE_ID ?? "";

export async function getActivePracticeId(accessToken?: string | null): Promise<string> {
  const cookieStore = await cookies();
  const cookiePracticeId = cookieStore.get(PRACTICE_COOKIE)?.value ?? "";
  if (cookiePracticeId.trim()) {
    return cookiePracticeId.trim();
  }
  if (!accessToken) {
    const supabase = await createSupabaseServerClient();
    const session = supabase ? (await supabase.auth.getSession()).data.session : null;
    accessToken = session?.access_token ?? null;
  }
  if (accessToken && BASE_URL) {
    try {
      const resp = await fetch(`${BASE_URL.replace(/\/$/, "")}/auth/me`, {
        headers: {
          accept: "application/json",
          authorization: `Bearer ${accessToken}`,
        },
        signal: AbortSignal.timeout(8_000),
      });
      const payload = (await resp.json().catch(() => ({}))) as {
        practice_roles?: { practice_id?: string }[];
      };
      const allowed = (payload.practice_roles ?? [])
        .map((row) => String(row.practice_id ?? "").trim())
        .filter(Boolean);
      return resolveActivePracticeId({
        cookiePracticeId: "",
        allowedPracticeIds: allowed,
        fallbackPracticeId: ENV_PRACTICE_ID,
      });
    } catch {
      return ENV_PRACTICE_ID;
    }
  }
  return ENV_PRACTICE_ID;
}
