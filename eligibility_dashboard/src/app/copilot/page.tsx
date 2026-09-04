"use client";

import { CopilotPanel } from "@/components/CopilotPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import { fetchEligibilityQueue } from "@/lib/eligibilityApi";
import type { EligibilityDashboardRow } from "@/lib/types";
import { Loader2, Search, Smile } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type PatientOption = {
  patientId: string;
  name: string;
  payer: string;
};

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

function toOptions(rows: EligibilityDashboardRow[]): PatientOption[] {
  const seen = new Map<string, PatientOption>();
  for (const row of rows) {
    if (!row.patient_id || seen.has(row.patient_id)) continue;
    seen.set(row.patient_id, {
      patientId: row.patient_id,
      name: row.patient_name || `${row.first_name} ${row.last_name}`.trim() || "Patient",
      payer: row.payer_label || row.primary_payer_id || "",
    });
  }
  return [...seen.values()];
}

export default function CopilotPage() {
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [options, setOptions] = useState<PatientOption[]>([]);
  const [selected, setSelected] = useState<PatientOption | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let active = true;
    void (async () => {
      setLoading(true);
      const result = await fetchEligibilityQueue();
      if (!active) return;
      if (!result.ok) {
        setBanner(result.message ?? "Unable to load patients.");
        setOptions([]);
      } else {
        setBanner(null);
        setOptions(toOptions(result.rows));
      }
      setLoading(false);
    })();
    return () => {
      active = false;
    };
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return options;
    return options.filter(
      (option) =>
        option.name.toLowerCase().includes(needle) ||
        option.payer.toLowerCase().includes(needle),
    );
  }, [options, query]);

  return (
    <main className="ml-[60px] min-h-screen overflow-y-auto px-6 pb-12 pt-6">
      <PageHeader
        icon={Smile}
        title="SmileSuites Copilot"
        subtitle="Your assistant for this patient's chart"
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">
          {banner}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <section className="card flex min-h-[28rem] flex-col p-4">
          <div className="relative mb-3">
            <Search
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search patients…"
              className="h-9 w-full rounded-lg border border-slate-200 pl-8 pr-3 text-[13px] outline-none focus:border-indigo-400"
            />
          </div>

          {loading ? (
            <div className="flex items-center gap-2 text-[12px] text-slate-500">
              <Loader2 size={14} className="animate-spin" />
              Loading patients…
            </div>
          ) : filtered.length === 0 ? (
            <p className="text-[13px] text-slate-500">
              {query.trim()
                ? "No patients match your search."
                : "No patients found in the eligibility queue."}
            </p>
          ) : (
            <>
              <p className="mb-1.5 px-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">
                {filtered.length} patient{filtered.length === 1 ? "" : "s"}
              </p>
              <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
                {filtered.map((option) => {
                  const active = selected?.patientId === option.patientId;
                  return (
                    <li key={option.patientId}>
                      <button
                        type="button"
                        onClick={() => setSelected(option)}
                        className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] transition-colors ${
                          active
                            ? "bg-[var(--accent-primary-soft)] ring-1 ring-[var(--accent-primary)]/20"
                            : "hover:bg-slate-50"
                        }`}
                      >
                        <span
                          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                            active
                              ? "bg-[var(--accent-primary-soft-strong)] text-[var(--accent-primary-hover)]"
                              : "bg-slate-100 text-slate-500"
                          }`}
                        >
                          {initials(option.name)}
                        </span>
                        <span className="min-w-0">
                          <span className="block truncate font-bold text-slate-900">
                            {option.name}
                          </span>
                          {option.payer ? (
                            <span className="block truncate text-[11px] font-medium text-slate-600">
                              {option.payer}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </section>

        {selected ? (
          <CopilotPanel
            key={selected.patientId}
            patientId={selected.patientId}
            patientName={selected.name}
          />
        ) : (
          <section className="card flex min-h-[28rem] items-center justify-center p-6 text-center">
            <div className="max-w-sm">
              <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--accent-primary-soft)] text-[var(--accent-primary)]">
                <Smile size={18} strokeWidth={2} />
              </div>
              <h2 className="text-[15px] font-bold text-slate-900">Pick a patient to start</h2>
              <p className="mt-1 text-[13px] font-medium text-slate-600">
                Choose a patient on the left, then ask your assistant about their coverage,
                appointments, or account. It reads the chart only and cannot make changes.
              </p>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
