"use client";

import { EncounterThread } from "@/components/EncounterThread";
import { PatientAvatar } from "@/components/PatientAvatar";
import { PayerLogo } from "@/components/PayerLogo";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { SlideOver } from "@/components/ui/SlideOver";
import { StatusPill, type PillTone } from "@/components/ui/StatusPill";
import { formatCurrency, formatDob, timeAgo } from "@/lib/format";
import { EMPTY_JOURNEY, fetchClaimCases } from "@/lib/rcmApi";
import type { ClaimCase, ClaimStatus } from "@/lib/rcm/types";
import { AlertTriangle, CheckCircle2, DollarSign, Loader2, Receipt, Send, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

const STATUS_TONE: Record<ClaimStatus, PillTone> = {
  draft: "neutral",
  pending_auth: "warn",
  submitted: "info",
  paid: "success",
};

const STATUS_LABEL: Record<ClaimStatus, string> = {
  draft: "Draft",
  pending_auth: "Pending auth",
  submitted: "Submitted",
  paid: "Paid",
};

export default function ClaimsPage() {
  const [cases, setCases] = useState<ClaimCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = cases.find((c) => c.claim_id === selectedId) ?? null;

  useEffect(() => {
    void (async () => {
      const result = await fetchClaimCases();
      if (!result.ok) {
        setBanner("Claims queue unavailable. Check FASTAPI_BASE_URL and backend configuration.");
        setCases([]);
      } else {
        setBanner(null);
        setCases(result.cases);
      }
      setLoading(false);
    })();
  }, []);

  const kpi = useMemo(() => {
    const submitted = cases.filter((c) => c.status === "submitted" || c.status === "paid").length;
    const blocked = cases.filter((c) => c.status === "pending_auth").length;
    const billed = cases.reduce((s, c) => s + c.total_charge_amount, 0);
    return { submitted, blocked, billed };
  }, [cases]);

  const submit = (id: string) => {
    setCases((prev) =>
      prev.map((c) =>
        c.claim_id === id ? { ...c, status: "submitted", submission_channel: "stedi_mock", available_actions: ["edit", "submit"] } : c,
      ),
    );
    setSelectedId(null);
  };

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={Receipt}
        title="Claims"
        subtitle="Automated 837 claim assembly, clean-claim scrubbing, and one-click submission to the clearinghouse."
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
          {banner}
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Submitted" value={String(kpi.submitted)} icon={Send} iconBg="bg-blue-50" iconColor="text-blue-600" />
        <KpiCard label="Clean Claim Rate" value="93%" icon={ShieldCheck} iconBg="bg-emerald-50" iconColor="text-emerald-600" />
        <KpiCard label="Blocked on Auth" value={String(kpi.blocked)} icon={AlertTriangle} iconBg="bg-amber-50" iconColor="text-amber-600" />
        <KpiCard label="Total Billed" value={formatCurrency(kpi.billed)} icon={DollarSign} />
      </section>

      <section className="card mb-6 overflow-hidden">
        <div className="flex items-center gap-2.5 border-b border-slate-100 bg-gradient-to-b from-slate-50/80 to-slate-50/40 px-5 py-3.5">
          <Receipt size={17} className="text-indigo-600" strokeWidth={2} />
          <h2 className="text-[14px] font-semibold text-slate-900">Claims queue</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-slate-100">
                {["Claim ID", "Patient", "Payer", "Charge", "Channel", "Status", ""].map((h, i) => (
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
                      Loading claims queue…
                    </span>
                  </td>
                </tr>
              ) : null}
              {!loading && cases.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-5 py-10 text-center text-[13px] text-slate-500">
                    No claims in queue.
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
                  <td className="px-5 py-4 num text-[13px] font-semibold text-slate-900">{formatCurrency(c.total_charge_amount)}</td>
                  <td className="px-5 py-4 text-[12px] text-slate-500">{c.submission_channel === "none" ? "—" : c.submission_channel}</td>
                  <td className="px-5 py-4">
                    <StatusPill tone={STATUS_TONE[c.status]} label={STATUS_LABEL[c.status]} size="sm" />
                  </td>
                  <td className="px-3 py-4 text-right text-slate-400 group-hover:text-indigo-600">→</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <SlideOver open={Boolean(selected)} onClose={() => setSelectedId(null)} width={500}>
        {selected ? (
          <>
            <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-600">
              <Receipt size={13} /> Claim draft
            </div>
            <h3 className="mt-2 text-[22px] font-semibold tracking-tight text-slate-900">{selected.claim_id}</h3>
            <div className="mt-1 text-[12px] text-slate-500">{selected.patient_name} · {selected.payer} · {selected.provider_name}</div>
            <div className="mt-3">
              <StatusPill tone={STATUS_TONE[selected.status]} label={STATUS_LABEL[selected.status]} size="sm" />
            </div>

            <div className="my-5 h-px bg-slate-200" />

            {selected.blockers.length ? (
              <section className="mb-4 rounded-lg border border-amber-200 bg-amber-50/60 p-3">
                <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-amber-700">
                  <AlertTriangle size={12} /> Scrubber blockers
                </div>
                <ul className="list-inside list-disc text-[12.5px] text-amber-800">
                  {selected.blockers.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </section>
            ) : (
              <section className="mb-4 flex items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50/60 px-3 py-2.5 text-[12.5px] font-medium text-emerald-700">
                <CheckCircle2 size={15} /> Clean claim — passed all scrubber checks.
              </section>
            )}

            <section>
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Service lines</div>
              <div className="overflow-hidden rounded-lg border border-slate-200">
                {selected.service_lines.map((line, idx) => (
                  <div key={line.cdt_code} className={`flex items-center justify-between px-3 py-2.5 text-[12.5px] ${idx > 0 ? "border-t border-slate-100" : ""}`}>
                    <div className="flex items-center gap-2">
                      <code className="mono rounded bg-indigo-50 px-1.5 py-0.5 text-[11.5px] font-semibold text-indigo-700">{line.cdt_code}</code>
                      <span className="text-slate-600">{line.description}</span>
                    </div>
                    <span className="num font-semibold text-slate-900">{formatCurrency(line.charge_amount)}</span>
                  </div>
                ))}
                <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-3 py-2.5 text-[13px] font-semibold">
                  <span className="text-slate-600">Total charge</span>
                  <span className="num text-slate-900">{formatCurrency(selected.total_charge_amount)}</span>
                </div>
              </div>
            </section>

            <section className="mt-4">
              <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Diagnosis codes</div>
              <div className="flex flex-wrap gap-1.5">
                {selected.diagnosis_codes.map((code) => (
                  <code key={code} className="mono rounded-md bg-slate-100 px-2 py-1 text-[12px] font-semibold text-slate-600">{code}</code>
                ))}
              </div>
            </section>

            <div className="mt-5">
              <EncounterThread stages={EMPTY_JOURNEY} />
            </div>

            <div className="mt-5 text-[11px] text-slate-400">Drafted {timeAgo(selected.created_at)}</div>

            <div className="mt-auto flex gap-2 pt-5">
              <button className="lift-on-hover flex-1 rounded-lg border border-slate-200 py-3 text-[14px] font-semibold text-slate-600 hover:border-slate-300 hover:bg-slate-50">
                Edit
              </button>
              <button
                disabled={selected.blockers.length > 0 || selected.status === "submitted"}
                onClick={() => submit(selected.claim_id)}
                className="btn-sheen lift-on-hover flex-1 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {selected.status === "submitted" ? "Submitted" : "Submit claim"}
              </button>
            </div>
          </>
        ) : null}
      </SlideOver>
    </main>
  );
}
