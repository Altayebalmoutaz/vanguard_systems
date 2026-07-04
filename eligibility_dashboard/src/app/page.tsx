"use client";

import { EncounterThread } from "@/components/EncounterThread";
import { RunPipelinePanel } from "@/components/RunPipelinePanel";
import { BarChart, FunnelChart } from "@/components/ui/charts";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusPill } from "@/components/ui/StatusPill";
import { useClientValue } from "@/hooks/useClientValue";
import { dashboardUserDisplayName } from "@/lib/dashboardEnv";
import { formatCurrency } from "@/lib/format";
import {
  EMPTY_JOURNEY,
  fetchDashboardOverview,
  type FunnelStage,
  type WorklistItem,
} from "@/lib/rcmApi";
import {
  Activity,
  AlertTriangle,
  Calendar,
  ClipboardCheck,
  Clock,
  LayoutDashboard,
  Loader2,
  Receipt,
  ShieldCheck,
  Sparkles,
  TrendingDown,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const MODULE_TONE: Record<WorklistItem["module"], { bg: string; fg: string }> = {
  Coding: { bg: "bg-indigo-50", fg: "text-indigo-700" },
  "Prior Auth": { bg: "bg-violet-50", fg: "text-violet-700" },
  Claims: { bg: "bg-blue-50", fg: "text-blue-700" },
  Denials: { bg: "bg-red-50", fg: "text-red-700" },
};

const EMPTY_KPIS = {
  clean_claim_rate: 0,
  denial_rate: 0,
  eligibility_verified_today: 0,
  coding_pending: 0,
  claims_open: 0,
  denials_open: 0,
  revenue_at_risk: 0,
};

function compactCurrency(value: number): string {
  if (value >= 1000) return `$${(value / 1000).toFixed(1)}K`;
  return `$${value}`;
}

export default function ExecutiveDashboard() {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const [selected, setSelected] = useState<WorklistItem | null>(null);
  const [worklist, setWorklist] = useState<WorklistItem[]>([]);
  const [revenueFunnel, setRevenueFunnel] = useState<FunnelStage[]>([]);
  const [monthlyTrend, setMonthlyTrend] = useState<number[]>([]);
  const [denialTrend, setDenialTrend] = useState<number[]>([]);
  const [kpis, setKpis] = useState(EMPTY_KPIS);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);

  const loadOverview = useCallback(async () => {
    const result = await fetchDashboardOverview();
    if (!result.ok || !result.data) {
      setBanner("Dashboard unavailable. Check FASTAPI_BASE_URL and Neon configuration.");
      setWorklist([]);
      setRevenueFunnel([]);
      setMonthlyTrend([]);
      setDenialTrend([]);
      setKpis(EMPTY_KPIS);
    } else {
      setBanner(null);
      setWorklist(result.data.worklist);
      setRevenueFunnel(result.data.revenue_funnel);
      setMonthlyTrend(result.data.monthly_trend);
      setDenialTrend(result.data.denial_trend);
      setKpis(result.data.kpis);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  // Time-based greeting is client-only: the server clock/timezone would differ
  // from the browser and cause a hydration mismatch. Render "Welcome" on the
  // server + first paint, then the real greeting once hydrated.
  const greeting = useClientValue(() => {
    const h = new Date().getHours();
    return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
  }, "Welcome");

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={LayoutDashboard}
        title={`${greeting}, ${dashboardUserDisplayName.replace(/^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i, "Dr. ").split(" ").slice(0, 2).join(" ")}`}
        subtitle="Your revenue cycle at a glance — across eligibility, coding, prior auth, claims, and denials."
        actions={
          <>
            <button
              type="button"
              className="lift-on-hover inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-medium text-slate-600 shadow-sm hover:border-slate-300 hover:bg-slate-50"
            >
              <Calendar size={15} className="text-slate-500" />
              <span>Last 30 days</span>
            </button>
            <button
              type="button"
              onClick={() => setPipelineOpen(true)}
              className="btn-sheen lift-on-hover inline-flex h-9 items-center gap-1.5 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 px-3.5 text-[13px] font-semibold text-white shadow-sm shadow-indigo-300/50 ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 active:scale-[0.98]"
            >
              <Sparkles size={15} />
              <span>Run AI Pipeline</span>
            </button>
          </>
        }
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
          {banner}
        </div>
      ) : null}

      {loading ? (
        <div className="mb-6 flex items-center gap-2 text-[13px] text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          Loading dashboard…
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <KpiCard
          label="Clean Claim Rate"
          value={`${kpis.clean_claim_rate}%`}
          icon={ShieldCheck}
          iconBg="bg-emerald-50"
          iconColor="text-emerald-600"
          spark={monthlyTrend.length > 0 ? { values: monthlyTrend, color: "#10B981" } : undefined}
          delta={{ value: "2.4%", positive: true, label: "vs last month" }}
        />
        <KpiCard
          label="Revenue at Risk"
          value={compactCurrency(kpis.revenue_at_risk)}
          sublabel="Open denials + pending auth"
          icon={AlertTriangle}
          iconBg="bg-amber-50"
          iconColor="text-amber-600"
          footerAction={{ label: "View worklist", onClick: () => document.getElementById("worklist")?.scrollIntoView({ behavior: "smooth" }), tone: "amber" }}
        />
        <KpiCard
          label="Denial Rate"
          value={`${kpis.denial_rate}%`}
          icon={TrendingDown}
          iconBg="bg-red-50"
          iconColor="text-red-500"
          spark={denialTrend.length > 0 ? { values: denialTrend, color: "#ef4444" } : undefined}
          delta={{ value: "1.1%", positive: true, label: "trending down" }}
        />
        <KpiCard
          label="Avg Days in A/R"
          value="18"
          sublabel="Target: < 25 days"
          icon={Clock}
          iconBg="bg-blue-50"
          iconColor="text-blue-600"
          delta={{ value: "3 days", positive: true, label: "vs last month" }}
        />
        <KpiCard
          label="Auto-Coded"
          value="82%"
          sublabel={`${kpis.coding_pending} pending review`}
          icon={Sparkles}
          iconBg="bg-indigo-50"
          iconColor="text-indigo-600"
          delta={{ value: "6%", positive: true, label: "vs last month" }}
        />
        <KpiCard
          label="Eligibility Verified Today"
          value={String(kpis.eligibility_verified_today)}
          sublabel={`${kpis.claims_open} claims open · ${kpis.denials_open} denials open`}
          icon={ClipboardCheck}
          iconBg="bg-violet-50"
          iconColor="text-violet-600"
          delta={{ value: "12", positive: true, label: "vs yesterday" }}
        />
      </section>

      <section className="mb-6 grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="card p-5">
          <div className="mb-4 flex items-center gap-2.5">
            <Receipt size={17} className="text-indigo-600" strokeWidth={2} />
            <h2 className="text-[14px] font-semibold text-slate-900">Revenue cycle funnel</h2>
            <span className="ml-auto text-[12px] text-slate-500">Last 30 days</span>
          </div>
          <FunnelChart stages={revenueFunnel} formatValue={(v) => formatCurrency(v)} />
        </div>
        <div className="card p-5">
          <div className="mb-4 flex items-center gap-2.5">
            <Activity size={17} className="text-indigo-600" strokeWidth={2} />
            <h2 className="text-[14px] font-semibold text-slate-900">Clean claim rate trend</h2>
          </div>
          <BarChart
            data={["Jul", "Aug", "Sep", "Oct", "Nov", "Dec"].map((m, i) => ({
              label: m,
              value: monthlyTrend[i + 6] ?? 0,
            }))}
            height={172}
            formatValue={(v) => `${v}%`}
            accent="#6366f1"
          />
        </div>
      </section>

      <section id="worklist" className="card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 bg-gradient-to-b from-slate-50/80 to-slate-50/40 px-5 py-3.5">
          <div className="flex items-center gap-2.5">
            <AlertTriangle size={17} className="text-amber-500" strokeWidth={2} />
            <h2 className="text-[14px] font-semibold text-slate-900">Needs attention</h2>
            <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">{worklist.length}</span>
          </div>
          <Link
            href="/hitl"
            className="text-[12px] font-semibold text-indigo-600 hover:text-indigo-700"
          >
            Open HITL inbox →
          </Link>
          <span className="text-[12px] text-slate-500">Cross-module worklist</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-slate-100">
                {["Module", "Patient", "Payer", "Summary", "Amount", "Priority"].map((h) => (
                  <th key={h} className="px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {worklist.length === 0 && !loading ? (
                <tr>
                  <td colSpan={6} className="px-5 py-8 text-center text-[13px] text-slate-500">
                    No items need attention right now.
                  </td>
                </tr>
              ) : (
                worklist.map((item, i) => {
                  const tone = MODULE_TONE[item.module];
                  return (
                    <tr
                      key={item.id}
                      className="group row-stagger cursor-pointer border-b border-slate-100 transition-colors duration-150 last:border-b-0 hover:bg-slate-50/80"
                      style={{ ["--i" as string]: Math.min(i, 24) }}
                      onClick={() => setSelected(item)}
                    >
                      <td className="px-5 py-3.5">
                        <span className={`inline-flex items-center rounded-md px-2 py-1 text-[11px] font-semibold ${tone.bg} ${tone.fg}`}>
                          {item.module}
                        </span>
                      </td>
                      <td className="px-5 py-3.5 text-[13.5px] font-semibold text-slate-900">{item.patient_name}</td>
                      <td className="px-5 py-3.5 text-[13px] text-slate-600">{item.payer}</td>
                      <td className="px-5 py-3.5 text-[13px] text-slate-600">{item.summary}</td>
                      <td className="px-5 py-3.5 num text-[13px] font-semibold text-slate-900">{formatCurrency(item.amount)}</td>
                      <td className="px-5 py-3.5">
                        <StatusPill
                          tone={item.severity === "high" ? "danger" : item.severity === "medium" ? "warn" : "neutral"}
                          label={item.severity}
                          size="sm"
                        />
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>

      <RunPipelinePanel open={pipelineOpen} onClose={() => setPipelineOpen(false)} />

      {selected ? (
        <div className="fade-in fixed inset-0 z-40 bg-slate-900/25 backdrop-blur-sm" onClick={() => setSelected(null)}>
          <aside
            className="slide-in-right absolute right-0 top-0 flex h-full w-[440px] flex-col overflow-y-auto border-l border-slate-200 bg-white px-6 pb-6 pt-8 shadow-[-12px_0px_32px_-12px_rgba(15,23,42,0.18)]"
            style={{ animationDuration: "0.32s" }}
            onClick={(e) => e.stopPropagation()}
          >
            <span className={`inline-flex w-fit items-center rounded-md px-2 py-1 text-[11px] font-semibold ${MODULE_TONE[selected.module].bg} ${MODULE_TONE[selected.module].fg}`}>
              {selected.module}
            </span>
            <h3 className="mt-3 text-[22px] font-semibold tracking-tight text-slate-900">{selected.patient_name}</h3>
            <div className="mt-1 text-[13px] text-slate-500">
              {selected.payer} · {formatCurrency(selected.amount)}
            </div>
            <p className="mt-3 text-[13px] leading-snug text-slate-600">{selected.summary}</p>
            <div className="my-5 h-px bg-slate-200" />
            <EncounterThread stages={EMPTY_JOURNEY} />
          </aside>
        </div>
      ) : null}
    </main>
  );
}
