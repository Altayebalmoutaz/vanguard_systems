"use client";

import { EncounterThread } from "@/components/EncounterThread";
import { PatientAvatar } from "@/components/PatientAvatar";
import { PayerLogo } from "@/components/PayerLogo";
import { BarChart, type BarDatum } from "@/components/ui/charts";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { SlideOver } from "@/components/ui/SlideOver";
import { StatusPill, type PillTone } from "@/components/ui/StatusPill";
import { formatCurrency, formatDob, timeAgo } from "@/lib/format";
import { EMPTY_JOURNEY, fetchDenialCases } from "@/lib/rcmApi";
import type { DenialCase, DenialStatus } from "@/lib/rcm/types";
import { DollarSign, FileText, Loader2, RotateCw, Sparkles, TrendingDown, UserCheck, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const STATUS_TONE: Record<DenialStatus, PillTone> = { denied: "danger", partial: "warn", paid: "success" };

export default function DenialsPage() {
  const [cases, setCases] = useState<DenialCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [generated, setGenerated] = useState<Record<string, boolean>>({});
  const selected = cases.find((c) => c.claim_id === selectedId) ?? null;

  useEffect(() => {
    void (async () => {
      const result = await fetchDenialCases();
      if (!result.ok) {
        setBanner("Denial queue unavailable. Check FASTAPI_BASE_URL and backend configuration.");
        setCases([]);
      } else {
        setBanner(null);
        setCases(result.cases);
      }
      setLoading(false);
    })();
  }, []);

  const kpi = useMemo(() => {
    const atRisk = cases.reduce((s, c) => s + c.amount_at_risk, 0);
    const appeals = cases.filter((c) => c.appeal_letter || c.next_action.includes("appeal")).length;
    return { atRisk, appeals };
  }, [cases]);

  const reasonChart = useMemo<BarDatum[]>(() => {
    const map = new Map<string, number>();
    for (const c of cases) map.set(c.reason_label, (map.get(c.reason_label) ?? 0) + 1);
    const colors = ["#ef4444", "#f59e0b", "#6366f1", "#8b5cf6", "#0ea5e9"];
    return [...map.entries()].map(([label, value], i) => ({ label, value, color: colors[i % colors.length] }));
  }, [cases]);

  const showAppeal = (c: DenialCase) => Boolean(c.appeal_letter) || generated[c.claim_id];

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={XCircle}
        title="Denial Management"
        subtitle="AI triages denials, recommends the next action, and drafts payer-ready appeal letters automatically."
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
          {banner}
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Denial Rate" value="7%" icon={TrendingDown} iconBg="bg-red-50" iconColor="text-red-500" delta={{ value: "1.1%", positive: true, label: "trending down" }} />
        <KpiCard label="At Risk" value={formatCurrency(kpi.atRisk)} sublabel="Open denied + partial" icon={DollarSign} iconBg="bg-amber-50" iconColor="text-amber-600" />
        <KpiCard label="Appeals In Progress" value={String(kpi.appeals)} icon={FileText} />
        <KpiCard label="Recovery Rate" value="71%" sublabel="Last 90 days" icon={UserCheck} iconBg="bg-emerald-50" iconColor="text-emerald-600" />
      </section>

      <section className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="card overflow-hidden">
          <div className="flex items-center gap-2.5 border-b border-slate-100 bg-gradient-to-b from-slate-50/80 to-slate-50/40 px-5 py-3.5">
            <XCircle size={17} className="text-red-500" strokeWidth={2} />
            <h2 className="text-[14px] font-semibold text-slate-900">Denial queue</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-slate-100">
                  {["Claim ID", "Patient", "Payer", "Reason", "At Risk", "Status", ""].map((h, i) => (
                    <th key={`${h}-${i}`} className="px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr>
                    <td colSpan={7} className="px-5 py-10 text-center text-[13px] text-slate-500">
                      <span className="inline-flex items-center gap-2">
                        <Loader2 size={16} className="animate-spin" />
                        Loading denial queue…
                      </span>
                    </td>
                  </tr>
                ) : null}
                {!loading && cases.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-5 py-10 text-center text-[13px] text-slate-500">
                      No denials in queue.
                    </td>
                  </tr>
                ) : null}
                {!loading &&
                  cases.map((c, i) => (
                  <tr
                    key={c.claim_id}
                    className="group row-stagger cursor-pointer border-b border-slate-100 transition-colors duration-150 last:border-b-0 hover:bg-slate-50/80"
                    style={{ ["--i" as string]: Math.min(i, 24) }}
                    onClick={() => setSelectedId(c.claim_id)}
                  >
                    <td className="px-5 py-4">
                      <code className="mono text-[12px] font-semibold text-slate-700">{c.claim_id}</code>
                      {c.requires_human_review ? (
                        <div className="mt-1 inline-flex items-center rounded border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700">
                          Human review
                        </div>
                      ) : null}
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-3">
                        <PatientAvatar firstName={c.patient_name.split(" ")[0]} lastName={c.patient_name.split(" ")[1] ?? ""} />
                        <div>
                          <div className="text-[13.5px] font-semibold text-slate-900">{c.patient_name}</div>
                          <div className="text-[11.5px] text-slate-500">DOB: {formatDob(c.dob)}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <PayerLogo label={c.payer} />
                    </td>
                    <td className="px-5 py-4">
                      <div className="text-[13px] font-medium text-slate-900">{c.reason_label}</div>
                      <div className="mono text-[11px] text-slate-500">{c.next_action}</div>
                    </td>
                    <td className="px-5 py-4 num text-[13px] font-semibold text-slate-900">{formatCurrency(c.amount_at_risk)}</td>
                    <td className="px-5 py-4">
                      <StatusPill tone={STATUS_TONE[c.status]} label={c.status} size="sm" />
                    </td>
                    <td className="px-3 py-4 text-right text-slate-400 group-hover:text-indigo-600">→</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card h-fit p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Denial reasons</h2>
          <BarChart data={reasonChart} height={180} />
        </div>
      </section>

      <SlideOver open={Boolean(selected)} onClose={() => setSelectedId(null)} width={540}>
        {selected ? (
          <>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-600">
              <XCircle size={13} /> Denial triage
            </div>
            <h3 className="mt-2 text-[22px] font-semibold tracking-tight text-slate-900">{selected.claim_id}</h3>
            <div className="mt-1 text-[12px] text-slate-500">{selected.patient_name} · {selected.payer}</div>
            <div className="mt-3 flex items-center gap-2">
              <StatusPill tone={STATUS_TONE[selected.status]} label={selected.status} size="sm" />
              <StatusPill tone="danger" label={selected.reason_label} size="sm" dot={false} />
              {selected.requires_human_review ? <StatusPill tone="warn" label="Human review" size="sm" dot={false} /> : null}
            </div>

            <div className="my-5 h-px bg-slate-200" />

            <section className="rounded-lg border border-indigo-100 bg-indigo-50/50 p-3">
              <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-indigo-600">
                <Sparkles size={12} /> AI reasoning
              </div>
              <p className="text-[12.5px] leading-relaxed text-slate-700">{selected.reasoning_summary}</p>
              <div className="mt-2 flex items-center gap-2 text-[12px]">
                <span className="font-semibold text-slate-500">Recommended:</span>
                <code className="mono rounded bg-white px-1.5 py-0.5 text-[11.5px] font-semibold text-indigo-700">{selected.next_action}</code>
              </div>
            </section>

            <section className="mt-4">
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Resubmission steps</div>
              <ol className="space-y-1.5">
                {selected.resubmission_steps.map((step, idx) => (
                  <li key={step} className="flex gap-2.5 text-[12.5px] text-slate-700">
                    <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-bold text-indigo-700">{idx + 1}</span>
                    {step}
                  </li>
                ))}
              </ol>
            </section>

            {selected.required_evidence.length ? (
              <section className="mt-4">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Required evidence</div>
                <div className="flex flex-wrap gap-1.5">
                  {selected.required_evidence.map((e) => (
                    <span key={e} className="rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11.5px] text-slate-600">{e}</span>
                  ))}
                </div>
              </section>
            ) : null}

            <section className="mt-5">
              <div className="mb-1.5 flex items-center justify-between">
                <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Appeal letter</span>
                {showAppeal(selected) ? <span className="text-[10.5px] font-semibold text-emerald-600">AI-drafted</span> : null}
              </div>
              {showAppeal(selected) ? (
                <pre className="mono max-h-64 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 text-[11.5px] leading-relaxed text-slate-700">
                  {selected.appeal_letter || "Appeal letter generated and queued for review."}
                </pre>
              ) : (
                <div className="rounded-lg border border-dashed border-slate-300 bg-slate-50/60 p-4 text-center">
                  <p className="text-[12.5px] text-slate-500">
                    {selected.appeal_letter === "" && selected.next_action === "notify_patient"
                      ? "Not appealable — patient notification recommended."
                      : "No appeal letter generated yet."}
                  </p>
                  {selected.next_action !== "notify_patient" ? (
                    <button
                      onClick={() => setGenerated((g) => ({ ...g, [selected.claim_id]: true }))}
                      className="mt-2 inline-flex items-center gap-1.5 rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-[12.5px] font-semibold text-indigo-600 hover:bg-indigo-50"
                    >
                      <Sparkles size={13} /> Generate appeal
                    </button>
                  ) : null}
                </div>
              )}
            </section>

            <div className="mt-5">
              <EncounterThread stages={EMPTY_JOURNEY} />
            </div>

            <div className="mt-5 text-[11px] text-slate-400">Denied {timeAgo(selected.created_at)}</div>

            <div className="mt-auto flex gap-2 pt-5">
              {selected.next_action === "notify_patient" ? (
                <button className="lift-on-hover flex-1 rounded-lg border border-slate-200 py-3 text-[14px] font-semibold text-slate-600 hover:border-slate-300 hover:bg-slate-50">
                  <span className="inline-flex items-center justify-center gap-2"><UserCheck size={15} /> Notify patient</span>
                </button>
              ) : (
                <button className="btn-sheen lift-on-hover flex-1 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700">
                  <span className="inline-flex items-center justify-center gap-2"><RotateCw size={15} /> Resubmit claim</span>
                </button>
              )}
            </div>
          </>
        ) : null}
      </SlideOver>
    </main>
  );
}
