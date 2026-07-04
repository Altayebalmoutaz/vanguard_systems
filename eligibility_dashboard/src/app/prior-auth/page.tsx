"use client";

import { EncounterThread } from "@/components/EncounterThread";
import { PatientAvatar } from "@/components/PatientAvatar";
import { PayerLogo } from "@/components/PayerLogo";
import { DonutChart } from "@/components/ui/charts";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { SlideOver } from "@/components/ui/SlideOver";
import { StatusPill, type PillTone } from "@/components/ui/StatusPill";
import { formatDob, timeAgo } from "@/lib/format";
import { EMPTY_JOURNEY, fetchPriorAuthCases } from "@/lib/rcmApi";
import type { PriorAuthCase, RiskLevel } from "@/lib/rcm/types";
import { AlertTriangle, Clock, FileText, FileUp, Send, ShieldAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const RISK_TONE: Record<RiskLevel, PillTone> = { low: "success", medium: "warn", high: "danger" };

const STATUS_TONE: Record<PriorAuthCase["status"], PillTone> = {
  pending_review: "warn",
  submitted: "info",
  approved: "success",
};

export default function PriorAuthPage() {
  const [cases, setCases] = useState<PriorAuthCase[]>([]);
  const [banner, setBanner] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = cases.find((c) => c.id === selectedId) ?? null;

  useEffect(() => {
    void fetchPriorAuthCases().then((result) => {
      if (!result.ok) {
        setBanner("Prior authorization queue unavailable. Check FASTAPI_BASE_URL and Neon configuration.");
        setCases([]);
        return;
      }
      setBanner(null);
      setCases(result.cases);
    });
  }, []);

  const kpi = useMemo(() => {
    const required = cases.filter((c) => c.requires_auth).length;
    const high = cases.filter((c) => c.risk_level === "high").length;
    const docs = cases.reduce((s, c) => s + c.required_documents.length, 0);
    return { required, high, docs };
  }, [cases]);

  const riskSegments = useMemo(
    () => [
      { label: "Low", value: cases.filter((c) => c.risk_level === "low").length, color: "#10b981" },
      { label: "Medium", value: cases.filter((c) => c.risk_level === "medium").length, color: "#f59e0b" },
      { label: "High", value: cases.filter((c) => c.risk_level === "high").length, color: "#ef4444" },
    ],
    [cases],
  );

  const submit = (id: string) => {
    setCases((prev) => prev.map((c) => (c.id === id ? { ...c, status: "submitted" } : c)));
    setSelectedId(null);
  };

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={FileText}
        title="Prior Authorization"
        subtitle="AI determines authorization necessity, surfaces required documents, and scores payer denial risk."
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
          {banner}
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Auths Required" value={String(kpi.required)} icon={FileText} />
        <KpiCard label="High Risk" value={String(kpi.high)} icon={ShieldAlert} iconBg="bg-red-50" iconColor="text-red-500" />
        <KpiCard label="Avg Turnaround" value="2.4d" sublabel="Payer response time" icon={Clock} iconBg="bg-blue-50" iconColor="text-blue-600" />
        <KpiCard label="Documents Needed" value={String(kpi.docs)} icon={FileUp} iconBg="bg-amber-50" iconColor="text-amber-600" />
      </section>

      <section className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="card overflow-hidden">
          <div className="flex items-center gap-2.5 border-b border-slate-100 bg-gradient-to-b from-slate-50/80 to-slate-50/40 px-5 py-3.5">
            <FileText size={17} className="text-indigo-600" strokeWidth={2} />
            <h2 className="text-[14px] font-semibold text-slate-900">Authorization queue</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-slate-100">
                  {["Patient", "Procedure", "Payer", "Auth", "Risk", "Status", ""].map((h, i) => (
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
                      <code className="mono rounded bg-indigo-50 px-1.5 py-0.5 text-[11.5px] font-semibold text-indigo-700">{c.procedure}</code>
                      <div className="mt-1 max-w-[200px] text-[11.5px] text-slate-500">{c.procedure_label}</div>
                    </td>
                    <td className="px-5 py-4">
                      <PayerLogo label={c.payer} />
                    </td>
                    <td className="px-5 py-4">
                      <StatusPill tone={c.requires_auth ? "warn" : "success"} label={c.requires_auth ? "Required" : "Not needed"} size="sm" dot={false} />
                    </td>
                    <td className="px-5 py-4">
                      <StatusPill tone={RISK_TONE[c.risk_level]} label={c.risk_level} size="sm" />
                    </td>
                    <td className="px-5 py-4">
                      <StatusPill tone={STATUS_TONE[c.status]} label={c.status.replace("_", " ")} size="sm" dot={false} />
                    </td>
                    <td className="px-3 py-4 text-right text-slate-400 group-hover:text-indigo-600">→</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card h-fit p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Risk distribution</h2>
          <DonutChart segments={riskSegments} centerLabel={String(cases.length)} centerSub="open auths" />
        </div>
      </section>

      <SlideOver open={Boolean(selected)} onClose={() => setSelectedId(null)} width={480}>
        {selected ? (
          <>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-600">
              <FileText size={13} /> Prior authorization
            </div>
            <h3 className="mt-2 text-[22px] font-semibold tracking-tight text-slate-900">{selected.patient_name}</h3>
            <div className="mono mt-1 text-[12px] text-slate-500">{selected.procedure} · {selected.procedure_label}</div>
            <div className="mt-3 flex items-center gap-2">
              <StatusPill tone={selected.requires_auth ? "warn" : "success"} label={selected.requires_auth ? "Auth required" : "No auth needed"} size="sm" />
              <StatusPill tone={RISK_TONE[selected.risk_level]} label={`${selected.risk_level} risk`} size="sm" dot={false} />
            </div>

            <div className="my-5 h-px bg-slate-200" />

            <section className={`rounded-lg border p-3 ${selected.risk_level === "high" ? "border-red-100 bg-red-50/50" : "border-amber-100 bg-amber-50/40"}`}>
              <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                <AlertTriangle size={12} /> Risk assessment
              </div>
              <p className="text-[12.5px] leading-relaxed text-slate-700">{selected.risk_reason}</p>
            </section>

            {selected.required_documents.length ? (
              <section className="mt-4">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Required documents</div>
                <ul className="space-y-1.5">
                  {selected.required_documents.map((d) => (
                    <li key={d} className="flex items-center justify-between rounded-md border border-slate-200 bg-white px-3 py-2 text-[12.5px] text-slate-700">
                      {d}
                      <button className="text-[11.5px] font-semibold text-indigo-600 hover:text-indigo-700">Upload</button>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            {selected.payer_rules.length ? (
              <section className="mt-4">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Payer rules</div>
                <ul className="list-inside list-disc text-[12.5px] text-slate-600">
                  {selected.payer_rules.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </section>
            ) : null}

            <div className="mt-5">
              <EncounterThread stages={EMPTY_JOURNEY} />
            </div>

            <div className="mt-5 text-[11px] text-slate-400">Assessed {timeAgo(selected.created_at)}</div>

            {selected.requires_auth && selected.status === "pending_review" ? (
              <button
                onClick={() => submit(selected.id)}
                className="btn-sheen lift-on-hover mt-auto inline-flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700"
              >
                <Send size={15} /> Submit authorization
              </button>
            ) : (
              <div className="mt-auto pt-5 text-center text-[12.5px] text-slate-500">
                {selected.requires_auth ? `Authorization ${selected.status}.` : "No authorization action required."}
              </div>
            )}
          </>
        ) : null}
      </SlideOver>
    </main>
  );
}
