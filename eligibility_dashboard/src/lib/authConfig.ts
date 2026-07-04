const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

export function isSupabaseAuthConfigured(): boolean {
  return Boolean(supabaseUrl && supabaseAnonKey);
}

/** Mirrors backend `REQUIRE_AUTH` — staff must sign in before reaching dashboard routes. */
export function isDashboardAuthRequired(): boolean {
  const flag = (process.env.DASHBOARD_REQUIRE_AUTH ?? "").toLowerCase();
  if (flag === "0" || flag === "false" || flag === "no") {
    return false;
  }
  if (flag === "1" || flag === "true" || flag === "yes") {
    return true;
  }
  if (process.env.NODE_ENV === "production" && isSupabaseAuthConfigured()) {
    return true;
  }
  return false;
}

export const PUBLIC_AUTH_PATHS = ["/login", "/auth/callback", "/auth/signout"] as const;

export function isPublicAuthPath(pathname: string): boolean {
  return PUBLIC_AUTH_PATHS.some(
    (path) => pathname === path || pathname.startsWith(`${path}/`),
  );
}
