"use client";

import { MiniSparkline } from "@/components/MiniSparkline";
import { BarChart, DonutChart } from "@/components/ui/charts";
import { KpiCard } from "@/components/ui/KpiCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { formatCurrency } from "@/lib/format";
import { fetchDashboardAnalytics } from "@/lib/rcmApi";
import { BarChart3, DollarSign, Loader2, Sparkles, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const EMPTY_KPIS = {
  ai_actions_per_day: 0,
  first_pass_yield: 0,
  claims_this_month: 0,
};

export default function AnalyticsPage() {
  const [payerMix, setPayerMix] = useState<{ label: string; value: number; color: string }[]>([]);
  const [agentThroughput, setAgentThroughput] = useState<{ label: string; value: number }[]>([]);
  const [monthlyTrend, setMonthlyTrend] = useState<number[]>([]);
  const [denialTrend, setDenialTrend] = useState<number[]>([]);
  const [kpis, setKpis] = useState(EMPTY_KPIS);
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);

  const loadAnalytics = useCallback(async () => {
    const result = await fetchDashboardAnalytics();
    if (!result.ok || !result.data) {
      setBanner("Analytics unavailable. Check FASTAPI_BASE_URL and Neon configuration.");
      setPayerMix([]);
      setAgentThroughput([]);
      setMonthlyTrend([]);
      setDenialTrend([]);
      setKpis(EMPTY_KPIS);
    } else {
      setBanner(null);
      setPayerMix(
        result.data.payer_mix.map((segment) => ({
          ...segment,
          color: segment.color ?? "#94a3b8",
        })),
      );
      setAgentThroughput(result.data.agent_throughput);
      setMonthlyTrend(result.data.monthly_trend);
      setDenialTrend(result.data.denial_trend);
      setKpis(result.data.kpis);
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void loadAnalytics();
  }, [loadAnalytics]);

  const latestCleanRate = monthlyTrend[monthlyTrend.length - 1] ?? kpis.first_pass_yield;
  const latestDenialRate = denialTrend[denialTrend.length - 1] ?? 0;

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={BarChart3}
        title="Analytics"
        subtitle="Revenue cycle performance across payers, modules, and the AI agent workforce."
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-800">
          {banner}
        </div>
      ) : null}

      {loading ? (
        <div className="mb-6 flex items-center gap-2 text-[13px] text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          Loading analytics…
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Net Collection Rate" value="96.4%" icon={DollarSign} iconBg="bg-emerald-50" iconColor="text-emerald-600" delta={{ value: "1.8%", positive: true, label: "vs last quarter" }} />
        <KpiCard label="Revenue (MTD)" value={formatCurrency(358200)} icon={TrendingUp} delta={{ value: "12%", positive: true, label: "vs last month" }} />
        <KpiCard label="AI Actions / Day" value={String(kpis.ai_actions_per_day)} sublabel="Across 5 agents" icon={Sparkles} iconBg="bg-violet-50" iconColor="text-violet-600" />
        <KpiCard label="First-Pass Yield" value={`${kpis.first_pass_yield}%`} icon={BarChart3} iconBg="bg-blue-50" iconColor="text-blue-600" delta={{ value: "4%", positive: true, label: "vs last month" }} />
      </section>

      <section className="mb-6 grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Volume by payer</h2>
          <DonutChart segments={payerMix} centerLabel={String(kpis.claims_this_month)} centerSub="claims this month" />
        </div>
        <div className="card p-5">
          <h2 className="mb-4 text-[14px] font-semibold text-slate-900">Agent throughput (this week)</h2>
          <BarChart data={agentThroughput} height={180} accent="#8b5cf6" />
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-2">
        <div className="card p-5">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="text-[14px] font-semibold text-slate-900">Clean claim rate</h2>
            <span className="text-[12px] font-semibold text-emerald-600">↑ trending up</span>
          </div>
          <div className="mb-3 text-[28px] font-bold tabular-nums tracking-tight text-slate-900">{latestCleanRate}%</div>
          <MiniSparkline values={monthlyTrend} strokeColor="#10B981" width={520} height={120} fillOpacity={0.12} className="w-full" />
          <div className="mt-2 flex justify-between text-[10.5px] text-slate-400">
            {MONTHS.map((m) => (
              <span key={m}>{m}</span>
            ))}
          </div>
        </div>
        <div className="card p-5">
          <div className="mb-1 flex items-center justify-between">
            <h2 className="text-[14px] font-semibold text-slate-900">Denial rate</h2>
            <span className="text-[12px] font-semibold text-emerald-600">↓ trending down</span>
          </div>
          <div className="mb-3 text-[28px] font-bold tabular-nums tracking-tight text-slate-900">{latestDenialRate}%</div>
          <MiniSparkline values={denialTrend} strokeColor="#ef4444" width={520} height={120} fillOpacity={0.12} className="w-full" />
          <div className="mt-2 flex justify-between text-[10.5px] text-slate-400">
            {MONTHS.map((m) => (
              <span key={m}>{m}</span>
            ))}
          </div>
        </div>
      </section>
    </main>
  );
}
