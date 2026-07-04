import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";

import { isSupabaseAuthConfigured } from "@/lib/authConfig";
import { type CookieToSet } from "@/lib/supabase/cookies";

export async function createSupabaseServerClient() {
  if (!isSupabaseAuthConfigured()) {
    return null;
  }

  const cookieStore = await cookies();

  return createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet: CookieToSet[]) {
          try {
            cookiesToSet.forEach(({ name, value, options }) => {
              cookieStore.set(name, value, options);
            });
          } catch {
            // setAll from a Server Component — middleware owns session refresh.
          }
        },
      },
    },
  );
}
