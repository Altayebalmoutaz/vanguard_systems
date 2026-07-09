"use client";

import { PageHeader } from "@/components/ui/PageHeader";
import { PayerLogo } from "@/components/PayerLogo";
import {
  dashboardPracticeName,
  dashboardUserDisplayName,
} from "@/lib/dashboardEnv";
import { staffRole, useStaffSession } from "@/hooks/useStaffSession";
import {
  fetchEligibilitySettings,
  updateEligibilitySettings,
  type UpdateEligibilitySettingsPayload,
} from "@/lib/eligibilityApi";
import type { EligibilityAgentSettings } from "@/lib/types";
import { PAYER_CATALOG } from "@/lib/payerCatalog";
import { Loader2, Settings } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const TEAM = [
  {
    name: dashboardUserDisplayName,
    role: "Practice Owner",
    email: "owner@brightsmiles.com",
  },
  {
    name: "Maria Gomez",
    role: "Billing Lead",
    email: "maria@brightsmiles.com",
  },
  {
    name: "Devon Clark",
    role: "Front Office",
    email: "devon@brightsmiles.com",
  },
];

type SettingKey = keyof UpdateEligibilitySettingsPayload;

const RULES: {
  key: SettingKey;
  label: string;
  description: string;
}[] = [
  {
    key: "auto_check_enabled",
    label: "Auto-verify eligibility",
    description: "Run eligibility when appointments are booked or polled.",
  },
  {
    key: "auto_retry_enabled",
    label: "Auto-retry failed checks",
    description: "Retry transient payer or network failures automatically.",
  },
  {
    key: "voice_verification_enabled",
    label: "Voice agent",
    description: "Allow outbound payer verification calls.",
  },
  {
    key: "voice_verification_auto_queue",
    label: "Auto-queue voice",
    description: "Queue a call when EDI/portal data is incomplete.",
  },
];

function Toggle({
  enabled,
  disabled,
  onChange,
  label,
}: {
  enabled: boolean;
  disabled?: boolean;
  onChange: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onChange}
      aria-label={label}
      aria-pressed={enabled}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 ${enabled ? "bg-[var(--accent-primary)]" : "bg-slate-200"}`}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${enabled ? "translate-x-[18px]" : "translate-x-0.5"}`}
      />
    </button>
  );
}

function settingValue(
  settings: EligibilityAgentSettings | null,
  key: SettingKey,
): boolean {
  if (!settings) return false;
  if (key === "voice_verification_enabled") {
    return settings.voice_verification_enabled !== false;
  }
  if (key === "voice_verification_auto_queue") {
    return Boolean(settings.voice_verification_auto_queue);
  }
  return Boolean(settings[key]);
}

export default function SettingsPage() {
  const sessionUser = useStaffSession();
  const role = staffRole(sessionUser);
  const [settings, setSettings] = useState<EligibilityAgentSettings | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState<SettingKey | null>(null);
  const [banner, setBanner] = useState<string | null>(null);

  const load = useCallback(async () => {
    const result = await fetchEligibilitySettings();
    if (!result.ok || !result.settings) {
      setBanner("Could not load agent settings. Showing defaults until retry.");
      setSettings({
        id: true,
        auto_check_enabled: true,
        auto_retry_enabled: true,
        voice_verification_enabled: true,
        voice_verification_auto_queue: false,
        last_sync_at: null,
        next_retry_at: null,
        updated_at: new Date().toISOString(),
      });
    } else {
      setBanner(null);
      setSettings(result.settings);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (key: SettingKey) => {
    if (!settings || busyKey) return;
    const next = !settingValue(settings, key);
    if (
      key === "voice_verification_auto_queue" &&
      next &&
      settings.voice_verification_enabled === false
    ) {
      setBanner("Turn on Voice agent before enabling auto-queue.");
      return;
    }

    const previous = settings;
    setBusyKey(key);
    setSettings((current) =>
      current ? { ...current, [key]: next } : current,
    );

    const result = await updateEligibilitySettings({ [key]: next });
    setBusyKey(null);
    if (!result.ok) {
      setSettings(previous);
      setBanner(result.message ?? "Failed to update settings");
      return;
    }
    if (result.settings) setSettings(result.settings);
    setBanner(null);
  };

  if (role && role !== "admin") {
    return (
      <main className="ml-[60px] min-h-screen overflow-y-auto px-6 pb-12 pt-6">
        <PageHeader
          icon={Settings}
          title="Settings"
          subtitle="Admin access is required for automation and team settings."
        />
        <div className="card max-w-2xl p-4">
          <h2 className="mb-1.5 text-[13.5px] font-semibold text-slate-900">
            Read-only access
          </h2>
          <p className="text-[12.5px] leading-relaxed text-slate-500">
            Your current role is{" "}
            <span className="font-semibold text-slate-700">
              {role.replace("_", " ")}
            </span>
            . Ask an admin to update automation rules, payer settings, or team
            access.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="ml-[60px] min-h-screen overflow-y-auto px-6 pb-12 pt-6">
      <PageHeader
        icon={Settings}
        title="Settings"
        subtitle={`Configure eligibility automation, payers, and team access for ${dashboardPracticeName}.`}
      />

      {banner ? (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-[12.5px] text-amber-800">
          {banner}
        </div>
      ) : null}

      <section className="mb-5 grid gap-3 lg:grid-cols-2">
        <div className="card p-4">
          <h2 className="mb-3 text-[13.5px] font-semibold text-slate-900">
            Automation rules
          </h2>
          {loading ? (
            <div className="flex items-center gap-2 py-6 text-[12.5px] text-slate-500">
              <Loader2 size={15} className="animate-spin" />
              Loading settings…
            </div>
          ) : (
            <div className="space-y-2.5">
              {RULES.map((rule) => {
                const enabled = settingValue(settings, rule.key);
                const disabled =
                  busyKey !== null ||
                  (rule.key === "voice_verification_auto_queue" &&
                    settings?.voice_verification_enabled === false);
                return (
                  <div
                    key={rule.key}
                    className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5"
                  >
                    <div>
                      <div className="text-[13px] font-semibold text-slate-900">
                        {rule.label}
                      </div>
                      <div className="text-[11.5px] text-slate-500">
                        {rule.description}
                      </div>
                    </div>
                    <Toggle
                      enabled={enabled}
                      disabled={disabled}
                      label={rule.label}
                      onChange={() => void toggle(rule.key)}
                    />
                  </div>
                );
              })}
            </div>
          )}
          {settings?.updated_at ? (
            <p className="mt-3 text-[11px] text-slate-400">
              Last updated {new Date(settings.updated_at).toLocaleString()}
            </p>
          ) : null}
        </div>

        <div className="card p-4">
          <h2 className="mb-3 text-[13.5px] font-semibold text-slate-900">
            Team
          </h2>
          <div className="space-y-2">
            {TEAM.map((m) => (
              <div
                key={m.email}
                className="flex items-center justify-between rounded-lg border border-slate-100 bg-white px-3 py-2.5"
              >
                <div className="flex items-center gap-2.5">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--accent-primary-soft)] text-[11px] font-bold text-[var(--accent-primary)]">
                    {m.name
                      .replace(/^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i, "")
                      .split(" ")
                      .map((p) => p[0])
                      .join("")
                      .slice(0, 2)}
                  </div>
                  <div>
                    <div className="text-[13px] font-semibold text-slate-900">
                      {m.name}
                    </div>
                    <div className="text-[11.5px] text-slate-500">{m.email}</div>
                  </div>
                </div>
                <span className="rounded-md bg-slate-100 px-2 py-0.5 text-[10.5px] font-semibold text-slate-600">
                  {m.role}
                </span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="card p-4">
        <h2 className="mb-3 text-[13.5px] font-semibold text-slate-900">
          Connected payers
        </h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {PAYER_CATALOG.slice(0, 12).map((p) => (
            <div
              key={p.slug}
              className="flex items-center justify-between rounded-lg border border-slate-100 bg-white px-3 py-2"
            >
              <PayerLogo label={p.displayName} />
              <span
                className="h-2 w-2 rounded-full bg-emerald-500"
                title="Connected"
              />
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
