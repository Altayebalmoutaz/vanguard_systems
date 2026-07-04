"use client";

import { EncounterThread } from "@/components/EncounterThread";
import { PatientAvatar } from "@/components/PatientAvatar";
import { PayerLogo } from "@/components/PayerLogo";
import { BarChart } from "@/components/ui/charts";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { SlideOver } from "@/components/ui/SlideOver";
import { StatusPill, type PillTone } from "@/components/ui/StatusPill";
import { formatDob, timeAgo } from "@/lib/format";
import { resolveHitlTask } from "@/lib/dashboardApi";
import { EMPTY_JOURNEY, fetchCodingCases, reviewCodingDecision } from "@/lib/rcmApi";
import type { CodingCase, CodingStatus } from "@/lib/rcm/types";
import { useStaffProfile } from "@/hooks/useStaffSession";
import { Check, Loader2, Sparkles, Stethoscope, ThumbsUp, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

const STATUS_TONE: Record<CodingStatus, PillTone> = {
  pending_review: "warn",
  approved: "success",
  rejected: "danger",
};

const STATUS_LABEL: Record<CodingStatus, string> = {
  pending_review: "Pending review",
  approved: "Approved",
  rejected: "Rejected",
};

function confidenceTone(c: number): PillTone {
  if (c >= 0.85) return "success";
  if (c >= 0.7) return "warn";
  return "danger";
}

const RESOLVE_ROLES = new Set(["admin", "billing_lead"]);

export default function CodingPage() {
  const { role } = useStaffProfile();
  const canResolve = role !== null && RESOLVE_ROLES.has(role);

  const [cases, setCases] = useState<CodingCase[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = cases.find((c) => c.id === selectedId) ?? null;

  const loadCases = useCallback(async () => {
    const result = await fetchCodingCases();
    if (!result.ok) {
      setBanner("Coding queue unavailable. Check FASTAPI_BASE_URL and Neon configuration.");
      setCases([]);
    } else {
      setBanner(null);
      setCases(result.cases);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  const kpi = useMemo(() => {
    const pending = cases.filter((c) => c.status === "pending_review").length;
    const flagged = cases.filter((c) => c.payer_flags.length > 0).length;
    const avg = cases.length ? cases.reduce((s, c) => s + c.confidence, 0) / cases.length : 0;
    return { pending, flagged, avg: Math.round(avg * 100) };
  }, [cases]);

  const histogram = useMemo(() => {
    const buckets = [
      { label: "<70%", value: 0, color: "#ef4444" },
      { label: "70–80%", value: 0, color: "#f59e0b" },
      { label: "80–90%", value: 0, color: "#6366f1" },
      { label: "90–100%", value: 0, color: "#10b981" },
    ];
    for (const c of cases) {
      const p = c.confidence * 100;
      if (p < 70) buckets[0].value += 1;
      else if (p < 80) buckets[1].value += 1;
      else if (p < 90) buckets[2].value += 1;
      else buckets[3].value += 1;
    }
    return buckets;
  }, [cases]);

  const decide = async (codingCase: CodingCase, status: CodingStatus) => {
    if (!canResolve) return;
    setDecisionBusy(true);
    setBanner(null);

    const isHitlTask = codingCase.source_type === "rcm_task" || Boolean(codingCase.hitl_task_id);
    const result = isHitlTask
      ? await resolveHitlTask(codingCase.hitl_task_id ?? codingCase.id, {
          action: status === "rejected" ? "reject" : "approve",
          final_codes: codingCase.cdt_codes,
          actor_label: "dashboard_staff",
          reason: status === "rejected" ? "Rejected from coding queue" : undefined,
        })
      : await reviewCodingDecision({
          decision_id: codingCase.decision_id ?? codingCase.id,
          status: status === "rejected" ? "rejected" : "approved",
        });

    setDecisionBusy(false);

    if (!result.ok) {
      setBanner(result.message ?? "Failed to save review decision.");
      return;
    }

    setCases((prev) => prev.map((c) => (c.id === codingCase.id ? { ...c, status } : c)));
    setSelectedId(null);
    setBanner(
      status === "rejected"
        ? "Coding decision rejected."
        : "Coding decision approved — encounter marked coded.",
    );
  };

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={Stethoscope}
        title="Medical Coding"
        subtitle="AI-assigned CDT and ICD-10 codes with confidence scoring and human-in-the-loop review."
      />

      {banner ? (
        <div
          className={`mb-5 rounded-xl border px-4 py-3 text-[13px] ${
            banner.includes("approved") || banner.includes("rejected")
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-amber-200 bg-amber-50 text-amber-800"
          }`}
        >
          {banner}
        </div>
      ) : null}

      {loading ? (
        <div className="mb-5 flex items-center gap-2 text-[13px] text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          Loading coding queue…
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Auto-Coded" value="82%" sublabel="AI-assigned" icon={Sparkles} />
        <KpiCard label="Avg Confidence" value={`${kpi.avg}%`} icon={Check} iconBg="bg-emerald-50" iconColor="text-emerald-600" />
        <KpiCard label="Pending Review" value={String(kpi.pending)} icon={ThumbsUp} iconBg="bg-amber-50" iconColor="text-amber-600" />
        <KpiCard label="Payer Flagged" value={String(kpi.flagged)} icon={X} iconBg="bg-red-50" iconColor="text-red-500" />
      </section>

      <section className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="card overflow-hidden">
          <div className="flex items-center gap-2.5 border-b border-slate-100 bg-gradient-to-b from-slate-50/80 to-slate-50/40 px-5 py-3.5">
            <Stethoscope size={17} className="text-indigo-600" strokeWidth={2} />
            <h2 className="text-[14px] font-semibold text-slate-900">Coding queue</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-slate-100">
                  {["Patient", "Payer", "CDT", "ICD-10", "Confidence", "Status", ""].map((h, i) => (
                    <th key={`${h}-${i}`} className="px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cases.map((c, i) => (
                  <tr
                    key={c.id}
                    className="group row-stagger cursor-pointer border-b border-slate-100 transition-colors duration-150 last:border-b-0 hover:bg-slate-50/80"
                    style={{ ["--i" as string]: Math.min(i, 24) }}
                    onClick={() => setSelectedId(c.id)}
                  >
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
                      <div className="flex flex-wrap gap-1">
                        {c.cdt_codes.map((code) => (
                          <code key={code} className="mono rounded bg-indigo-50 px-1.5 py-0.5 text-[11.5px] font-semibold text-indigo-700">{code}</code>
                        ))}
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <div className="flex flex-wrap gap-1">
                        {c.icd10_codes.map((code) => (
                          <code key={code} className="mono rounded bg-slate-100 px-1.5 py-0.5 text-[11.5px] font-semibold text-slate-600">{code}</code>
                        ))}
                      </div>
                    </td>
                    <td className="px-5 py-4 w-[140px]">
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                          <div
                            className={`h-full rounded-full ${c.confidence >= 0.85 ? "bg-emerald-500" : c.confidence >= 0.7 ? "bg-amber-400" : "bg-red-500"}`}
                            style={{ width: `${Math.round(c.confidence * 100)}%` }}
                          />
                        </div>
                        <span className="text-[12px] font-semibold tabular-nums text-slate-700">{Math.round(c.confidence * 100)}%</span>
                      </div>
                    </td>
                    <td className="px-5 py-4">
                      <StatusPill tone={STATUS_TONE[c.status]} label={STATUS_LABEL[c.status]} size="sm" />
                    </td>
                    <td className="px-3 py-4 text-right text-slate-400 group-hover:text-indigo-600">→</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card h-fit p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Confidence distribution</h2>
          <BarChart data={histogram} height={180} />
        </div>
      </section>

      <SlideOver open={Boolean(selected)} onClose={() => setSelectedId(null)} width={480}>
        {selected ? (
          <>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-600">
              <Sparkles size={13} /> AI coding result
            </div>
            <h3 className="mt-2 text-[22px] font-semibold tracking-tight text-slate-900">{selected.patient_name}</h3>
            <div className="mono mt-1 text-[12px] text-slate-500">{selected.encounter_id} · {selected.provider_name}</div>
            <div className="mt-3 flex items-center gap-2">
              <StatusPill tone={STATUS_TONE[selected.status]} label={STATUS_LABEL[selected.status]} size="sm" />
              <StatusPill tone={confidenceTone(selected.confidence)} label={`${Math.round(selected.confidence * 100)}% confidence`} size="sm" dot={false} />
            </div>

            <div className="my-5 h-px bg-slate-200" />

            <div className="mb-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Clinical note</div>
              <p className="text-[12.5px] leading-relaxed text-slate-600">{selected.clinical_note}</p>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">CDT codes</div>
                <div className="flex flex-wrap gap-1.5">
                  {selected.cdt_codes.map((code) => (
                    <code key={code} className="mono rounded-md bg-indigo-50 px-2 py-1 text-[12px] font-semibold text-indigo-700">{code}</code>
                  ))}
                </div>
              </div>
              <div>
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">ICD-10 codes</div>
                <div className="flex flex-wrap gap-1.5">
                  {selected.icd10_codes.map((code) => (
                    <code key={code} className="mono rounded-md bg-slate-100 px-2 py-1 text-[12px] font-semibold text-slate-600">{code}</code>
                  ))}
                </div>
              </div>
            </div>

            <section className="mt-5 rounded-lg border border-indigo-100 bg-indigo-50/50 p-3">
              <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-indigo-600">
                <Sparkles size={12} /> AI justification
              </div>
              <p className="text-[12.5px] leading-relaxed text-slate-700">{selected.justification}</p>
            </section>

            {selected.payer_flags.length ? (
              <section className="mt-4">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Payer flags</div>
                <div className="flex flex-wrap gap-1.5">
                  {selected.payer_flags.map((f) => (
                    <span key={f} className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">{f}</span>
                  ))}
                </div>
              </section>
            ) : null}

            {selected.payer_rules_matched.length ? (
              <section className="mt-4">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Payer rules matched</div>
                {selected.payer_rules_matched.map((r) => (
                  <div key={r.rule} className="mb-1.5 rounded-md border border-slate-200 bg-white px-2.5 py-1.5">
                    <div className="mono text-[11.5px] font-semibold text-slate-700">{r.rule}</div>
                    <div className="text-[11.5px] text-slate-500">{r.detail}</div>
                  </div>
                ))}
              </section>
            ) : null}

            <div className="mt-5">
              <EncounterThread stages={EMPTY_JOURNEY} />
            </div>

            <div className="mt-5 text-[11px] text-slate-400">Coded {timeAgo(selected.created_at)}</div>

            {selected.status === "pending_review" && canResolve ? (
              <div className="mt-auto flex gap-2 pt-5">
                <button
                  type="button"
                  disabled={decisionBusy}
                  onClick={() => void decide(selected, "rejected")}
                  className="lift-on-hover flex-1 rounded-lg border border-slate-200 py-3 text-[14px] font-semibold text-slate-600 hover:border-red-300 hover:text-red-600 disabled:opacity-50"
                >
                  Reject
                </button>
                <button
                  type="button"
                  disabled={decisionBusy}
                  onClick={() => void decide(selected, "approved")}
                  className="btn-sheen lift-on-hover flex-1 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 disabled:opacity-50"
                >
                  Approve codes
                </button>
              </div>
            ) : selected.status === "pending_review" ? (
              <div className="mt-auto pt-5 text-center text-[12.5px] text-slate-500">
                Billing lead or admin role required to approve or reject.
              </div>
            ) : (
              <div className="mt-auto pt-5 text-center text-[12.5px] text-slate-500">
                This encounter has been {STATUS_LABEL[selected.status].toLowerCase()}.
              </div>
            )}
          </>
        ) : null}
      </SlideOver>
    </main>
  );
}
