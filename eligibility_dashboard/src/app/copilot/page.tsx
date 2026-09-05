"use client";

import { CopilotPanel } from "@/components/CopilotPanel";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  fetchCopilotPatients,
  type CopilotDirectoryPatient,
} from "@/lib/copilotApi";
import { Loader2, Search, Smile } from "lucide-react";
import { useEffect, useState } from "react";

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase();
}

function sourceLabel(sources: string[]): string {
  const hasOd = sources.includes("opendental");
  const hasElig = sources.includes("eligibility");
  if (hasOd && hasElig) return "OpenDental · Eligibility";
  if (hasOd) return "OpenDental";
  if (hasElig) return "Eligibility";
  return "";
}

export default function CopilotPage() {
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [bannerTone, setBannerTone] = useState<"error" | "warning">("warning");
  const [options, setOptions] = useState<CopilotDirectoryPatient[]>([]);
  const [selected, setSelected] = useState<CopilotDirectoryPatient | null>(null);
  const [query, setQuery] = useState("");
  const [odConnected, setOdConnected] = useState(false);

  useEffect(() => {
    let active = true;
    const handle = window.setTimeout(() => {
      void (async () => {
        setLoading(true);
        const result = await fetchCopilotPatients(query);
        if (!active) return;
        if (!result.ok) {
          setBanner(result.message ?? "Unable to load patients.");
          setBannerTone("error");
          setOptions([]);
          setOdConnected(false);
        } else {
          const odTitle = result.opendentalError?.title?.trim();
          const odDetail = result.opendentalError?.message?.trim();
          const odBanner =
            odTitle && odDetail
              ? `${odTitle}. ${odDetail}`
              : odDetail || odTitle || "";
          setBanner(
            result.opendentalConnected
              ? null
              : odBanner ||
                  "OpenDental isn’t connected yet — showing eligibility patients only.",
          );
          setBannerTone("warning");
          setOptions(result.patients);
          setOdConnected(result.opendentalConnected);
        }
        setLoading(false);
      })();
    }, query.trim() ? 250 : 0);
    return () => {
      active = false;
      window.clearTimeout(handle);
    };
  }, [query]);

  return (
    <main className="ml-[60px] flex h-dvh flex-col overflow-hidden px-6 pb-4 pt-6">
      <div className="shrink-0">
        <PageHeader
          icon={Smile}
          title="SmileSuites Copilot"
          subtitle="Ask about a patient the way you would a teammate"
        />
      </div>

      {banner ? (
        <div
          className={`mb-4 shrink-0 rounded-xl border px-4 py-3 text-[13px] ${
            bannerTone === "error"
              ? "border-red-200 bg-red-50 text-red-700"
              : "border-amber-200 bg-amber-50 text-amber-800"
          }`}
        >
          {banner}
        </div>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-rows-[16rem_minmax(0,1fr)] gap-4 xl:grid-cols-[320px_minmax(0,1fr)] xl:grid-rows-none">
        <section className="card flex min-h-0 flex-col overflow-hidden p-4">
          <div className="relative mb-3 shrink-0">
            <Search
              size={14}
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={odConnected ? "Search OpenDental…" : "Search patients…"}
              className="h-9 w-full rounded-lg border border-slate-200 pl-8 pr-3 text-[13px] outline-none focus:border-indigo-400"
            />
          </div>

          {loading ? (
            <div className="flex items-center gap-2 text-[12px] text-slate-500">
              <Loader2 size={14} className="animate-spin" />
              Loading patients…
            </div>
          ) : options.length === 0 ? (
            <p className="text-[13px] text-slate-500">
              {query.trim()
                ? "No patients match your search."
                : "No patients found in OpenDental or the eligibility queue."}
            </p>
          ) : (
            <div className="flex min-h-0 flex-1 flex-col">
              <p className="mb-1.5 shrink-0 px-1 text-[10.5px] font-semibold uppercase tracking-wide text-slate-400">
                {options.length} patient{options.length === 1 ? "" : "s"}
              </p>
              <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto pr-1">
                {options.map((option) => {
                  const active = selected?.patient_id === option.patient_id;
                  const secondary = option.subtitle || sourceLabel(option.sources);
                  return (
                    <li key={`${option.patient_id}:${option.od_pat_num ?? ""}`}>
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
                          {secondary ? (
                            <span className="block truncate text-[11px] font-medium text-slate-600">
                              {secondary}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </section>

          {selected ? (
          <CopilotPanel
            key={selected.patient_id}
            patientId={selected.patient_id}
            patientName={selected.name}
            odPatNum={selected.od_pat_num}
            className="h-full min-h-0"
          />
        ) : (
          <section className="card flex h-full min-h-0 items-center justify-center p-6 text-center">
            <div className="max-w-sm">
              <div className="mx-auto mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-[var(--accent-primary-soft)] text-[var(--accent-primary)]">
                <Smile size={18} strokeWidth={2} />
              </div>
              <h2 className="text-[15px] font-bold text-slate-900">Pick someone to chat about</h2>
              <p className="mt-1 text-[13px] font-medium text-slate-600">
                Choose a patient on the left, then ask the way you’d ask a teammate —
                coverage, appointments, the account. It only reads the chart.
              </p>
            </div>
          </section>
        )}
      </div>
    </main>
  );
}
