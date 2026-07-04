"use client";

import { PageHeader } from "@/components/ui/PageHeader";
import { PayerLogo } from "@/components/PayerLogo";
import {
  dashboardPracticeName,
  dashboardUserDisplayName,
} from "@/lib/dashboardEnv";
import { staffRole, useStaffSession } from "@/hooks/useStaffSession";
import { PAYER_CATALOG } from "@/lib/payerCatalog";
import { Settings } from "lucide-react";
import { useState } from "react";

type ToggleRow = { id: string; label: string; description: string; enabled: boolean };

const TEAM = [
  { name: dashboardUserDisplayName, role: "Practice Owner", email: "owner@brightsmiles.com" },
  { name: "Maria Gomez", role: "Billing Lead", email: "maria@brightsmiles.com" },
  { name: "Devon Clark", role: "Front Office", email: "devon@brightsmiles.com" },
];

function Toggle({ enabled, onChange }: { enabled: boolean; onChange: () => void }) {
  return (
    <button
      type="button"
      onClick={onChange}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${enabled ? "bg-indigo-500" : "bg-slate-200"}`}
      aria-pressed={enabled}
    >
      <span
        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform ${enabled ? "translate-x-[22px]" : "translate-x-0.5"}`}
      />
    </button>
  );
}

export default function SettingsPage() {
  const sessionUser = useStaffSession();
  const role = staffRole(sessionUser);
  const [rules, setRules] = useState<ToggleRow[]>([
    { id: "auto_coding", label: "Auto-run coding agent", description: "Generate codes automatically on new encounters.", enabled: true },
    { id: "auto_eligibility", label: "Auto-verify eligibility", description: "Run eligibility on appointment booking.", enabled: true },
    { id: "auto_appeal", label: "Auto-draft appeal letters", description: "Draft appeals on denial when appealable.", enabled: true },
    { id: "human_review", label: "Require human review", description: "Hold low-confidence coding for sign-off.", enabled: true },
    { id: "auto_submit", label: "Auto-submit clean claims", description: "Submit claims that pass all scrubber checks.", enabled: false },
  ]);

  const toggle = (id: string) => setRules((prev) => prev.map((r) => (r.id === id ? { ...r, enabled: !r.enabled } : r)));

  if (role && role !== "admin") {
    return (
      <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
        <PageHeader icon={Settings} title="Settings" subtitle="Admin access is required for automation and team settings." />
        <div className="card max-w-2xl p-5">
          <h2 className="mb-2 text-[14px] font-semibold text-slate-900">Read-only access</h2>
          <p className="text-[13px] leading-relaxed text-slate-500">
            Your current role is <span className="font-semibold text-slate-700">{role.replace("_", " ")}</span>.
            Ask an admin to update automation rules, payer settings, or team access.
          </p>
        </div>
      </main>
    );
  }

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader icon={Settings} title="Settings" subtitle={`Configure automation, payers, and team access for ${dashboardPracticeName}.`} />

      <section className="mb-6 grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Automation rules</h2>
          <div className="space-y-3">
            {rules.map((r) => (
              <div key={r.id} className="flex items-center justify-between gap-4 rounded-lg border border-slate-100 bg-slate-50/60 px-3.5 py-3">
                <div>
                  <div className="text-[13.5px] font-semibold text-slate-900">{r.label}</div>
                  <div className="text-[12px] text-slate-500">{r.description}</div>
                </div>
                <Toggle enabled={r.enabled} onChange={() => toggle(r.id)} />
              </div>
            ))}
          </div>
        </div>

        <div className="card p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Team</h2>
          <div className="space-y-2">
            {TEAM.map((m) => (
              <div key={m.email} className="flex items-center justify-between rounded-lg border border-slate-100 bg-white px-3.5 py-3">
                <div className="flex items-center gap-3">
                  <div className="flex h-9 w-9 items-center justify-center rounded-full bg-indigo-100 text-[12px] font-bold text-indigo-700">
                    {m.name.replace(/^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i, "").split(" ").map((p) => p[0]).join("").slice(0, 2)}
                  </div>
                  <div>
                    <div className="text-[13.5px] font-semibold text-slate-900">{m.name}</div>
                    <div className="text-[12px] text-slate-500">{m.email}</div>
                  </div>
                </div>
                <span className="rounded-md bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-600">{m.role}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section className="card p-5">
        <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Connected payers</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {PAYER_CATALOG.slice(0, 12).map((p) => (
            <div key={p.slug} className="flex items-center justify-between rounded-lg border border-slate-100 bg-white px-3 py-2.5">
              <PayerLogo label={p.displayName} />
              <span className="h-2 w-2 rounded-full bg-emerald-500" title="Connected" />
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
