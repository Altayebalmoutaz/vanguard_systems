"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ShieldCheck } from "lucide-react";

import { dashboardAppName } from "@/lib/dashboardEnv";
import { getSupabaseBrowserClient, isSupabaseConfigured } from "@/lib/supabase";

const ERROR_MESSAGES: Record<string, string> = {
  auth_not_configured: "Staff authentication is not configured. Contact your administrator.",
  missing_code: "Sign-in could not be completed. Please try again.",
  exchange_failed: "Sign-in session expired or was invalid. Please sign in again.",
  invalid_credentials: "Invalid email or password.",
};

export function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirect = searchParams.get("redirect") ?? "/";
  const queryError = searchParams.get("error");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const bannerError = useMemo(() => {
    if (!queryError) {
      return null;
    }
    return ERROR_MESSAGES[queryError] ?? "Sign-in failed. Please try again.";
  }, [queryError]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    const client = getSupabaseBrowserClient();
    if (!client) {
      setFormError(ERROR_MESSAGES.auth_not_configured);
      return;
    }

    setSubmitting(true);
    const { error } = await client.auth.signInWithPassword({ email, password });
    setSubmitting(false);

    if (error) {
      setFormError(ERROR_MESSAGES.invalid_credentials);
      return;
    }

    router.replace(redirect);
    router.refresh();
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-md rounded-2xl border border-slate-200/80 bg-white/95 p-8 shadow-[var(--card-shadow-lg)] backdrop-blur-sm">
        <div className="mb-8 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
            <ShieldCheck size={22} strokeWidth={2} />
          </div>
          <div>
            <h1 className="text-[20px] font-semibold tracking-tight text-slate-900">{dashboardAppName}</h1>
            <p className="text-[13px] text-slate-500">Staff sign-in</p>
          </div>
        </div>

        {!isSupabaseConfigured() ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
            Set <code className="mono text-[12px]">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code className="mono text-[12px]">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to enable staff login.
          </p>
        ) : (
          <form className="space-y-4" onSubmit={onSubmit}>
            {(bannerError || formError) && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">
                {formError ?? bannerError}
              </p>
            )}

            <label className="block">
              <span className="mb-1.5 block text-[12px] font-semibold uppercase tracking-[0.06em] text-slate-500">
                Work email
              </span>
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="h-11 w-full rounded-lg border border-slate-200 px-3 text-[14px] text-slate-800 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
              />
            </label>

            <label className="block">
              <span className="mb-1.5 block text-[12px] font-semibold uppercase tracking-[0.06em] text-slate-500">
                Password
              </span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-11 w-full rounded-lg border border-slate-200 px-3 text-[14px] text-slate-800 outline-none focus:border-indigo-400 focus:ring-2 focus:ring-indigo-100"
              />
            </label>

            <button
              type="submit"
              disabled={submitting}
              className="btn-sheen h-11 w-full rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 text-[14px] font-semibold text-white shadow-sm shadow-indigo-300/50 ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}

        <p className="mt-6 text-center text-[11.5px] text-slate-500">
          Workforce access only. Patient data is never stored in the auth provider.
        </p>
      </div>
    </main>
  );
}
