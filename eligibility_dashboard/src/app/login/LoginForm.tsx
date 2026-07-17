"use client";

import { FormEvent, useMemo, useState } from "react";
import Image from "next/image";
import { useSearchParams } from "next/navigation";

import { dashboardAppName, dashboardAppSubtitle } from "@/lib/dashboardEnv";
import { getSupabaseBrowserClient, isSupabaseConfigured } from "@/lib/supabase";

const ERROR_MESSAGES: Record<string, string> = {
  auth_not_configured:
    "Staff authentication is not configured. Contact your administrator.",
  missing_code: "Sign-in could not be completed. Please try again.",
  exchange_failed:
    "Sign-in session expired or was invalid. Please sign in again.",
  invalid_credentials: "Invalid email or password.",
};

export function LoginForm() {
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
    const trimmedEmail = email.trim().toLowerCase();
    const { error } = await client.auth.signInWithPassword({
      email: trimmedEmail,
      password,
    });

    if (error) {
      setSubmitting(false);
      const status = (error as { status?: number }).status;
      const msg = (error.message || "").toLowerCase();
      if (status === 429 || msg.includes("rate") || msg.includes("too many")) {
        setFormError("Too many sign-in attempts. Wait a minute and try again.");
      } else if (
        msg.includes("fetch") ||
        msg.includes("network") ||
        msg.includes("failed to fetch")
      ) {
        setFormError(
          "Cannot reach the auth server from this network. Check internet / VPN / firewall, then retry.",
        );
      } else if (msg.includes("invalid") || msg.includes("credentials")) {
        setFormError(ERROR_MESSAGES.invalid_credentials);
      } else {
        setFormError(error.message || ERROR_MESSAGES.invalid_credentials);
      }
      return;
    }

    // Hard navigation so middleware sees the new auth cookies on the next request.
    window.location.assign(redirect.startsWith("/") ? redirect : "/");
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-[400px] rounded-xl border border-slate-200/90 bg-white p-7 shadow-[var(--card-shadow)]">
        <div className="mb-7 flex flex-col items-center text-center">
          <Image
            src="/ezfi-logo.png"
            alt={dashboardAppName}
            width={132}
            height={132}
            className="h-auto w-[108px] object-contain"
            priority
          />
          <p className="mt-2 text-[12px] font-medium text-slate-500">{dashboardAppSubtitle}</p>
          <p className="mt-0.5 text-[13px] text-slate-400">Staff sign-in</p>
        </div>

        {!isSupabaseConfigured() ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
            Set <code className="mono text-[12px]">NEXT_PUBLIC_SUPABASE_URL</code> and{" "}
            <code className="mono text-[12px]">NEXT_PUBLIC_SUPABASE_ANON_KEY</code> to enable staff
            login.
          </p>
        ) : (
          <form className="space-y-3.5" onSubmit={onSubmit}>
            {(bannerError || formError) && (
              <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">
                {formError ?? bannerError}
              </p>
            )}

            <label className="block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[0.07em] text-slate-500">
                Work email
              </span>
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="h-10 w-full rounded-lg border border-slate-200 px-3 text-[13.5px] text-slate-800 outline-none transition focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary-soft)]"
              />
            </label>

            <label className="block">
              <span className="mb-1 block text-[11px] font-semibold uppercase tracking-[0.07em] text-slate-500">
                Password
              </span>
              <input
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                className="h-10 w-full rounded-lg border border-slate-200 px-3 text-[13.5px] text-slate-800 outline-none transition focus:border-[var(--accent-primary)] focus:ring-2 focus:ring-[var(--accent-primary-soft)]"
              />
            </label>

            <button
              type="submit"
              disabled={submitting}
              className="mt-1 h-10 w-full rounded-lg bg-[var(--accent-primary)] text-[13.5px] font-semibold text-white shadow-sm shadow-[rgba(24,128,240,0.2)] transition hover:bg-[var(--accent-primary-hover)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        )}

        <p className="mt-5 text-center text-[11px] leading-relaxed text-slate-400">
          Workforce access only. Patient data is never stored in the auth provider.
        </p>
      </div>
    </main>
  );
}
