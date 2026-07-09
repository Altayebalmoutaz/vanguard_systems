"use client";

import { PageHeader } from "@/components/ui/PageHeader";
import { StatusPill } from "@/components/ui/StatusPill";
import { fetchPatient360 } from "@/lib/dashboardApi";
import { ArrowLeft, Loader2, UserRound } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

function asString(value: unknown, fallback = "—"): string {
  return typeof value === "string" && value.trim() ? value : fallback;
}

export default function Patient360Page() {
  const params = useParams<{ id: string }>();
  const patientId = params.id;
  const [loading, setLoading] = useState(true);
  const [banner, setBanner] = useState<string | null>(null);
  const [patient, setPatient] = useState<Record<string, unknown> | null>(null);
  const [latestCheck, setLatestCheck] = useState<Record<string, unknown> | null>(null);
  const [agentRuns, setAgentRuns] = useState<Record<string, unknown>[]>([]);

  useEffect(() => {
    let active = true;
    void (async () => {
      setLoading(true);
      const result = await fetchPatient360(patientId);
      if (!active) return;
      if (!result.ok || !result.profile) {
        setBanner(result.message ?? "Unable to load patient profile.");
        setPatient(null);
        setLatestCheck(null);
        setAgentRuns([]);
      } else {
        setBanner(null);
        setPatient(result.profile.patient);
        setLatestCheck(result.profile.latest_eligibility_check);
        setAgentRuns(result.profile.agent_runs);
      }
      setLoading(false);
    })();
    return () => {
      active = false;
    };
  }, [patientId]);

  const displayName =
    patient && typeof patient.first_name === "string" && typeof patient.last_name === "string"
      ? `${patient.first_name} ${patient.last_name}`
      : "Patient";

  return (
    <main className="ml-[60px] min-h-screen overflow-y-auto px-6 pb-12 pt-6">
      <div className="mb-4">
        <Link href="/eligibility" className="inline-flex items-center gap-1.5 text-[12px] font-semibold text-slate-500 hover:text-indigo-600">
          <ArrowLeft size={14} />
          Back to Eligibility
        </Link>
      </div>

      <PageHeader
        icon={UserRound}
        title={displayName}
        subtitle={`Patient 360 · ${patientId}`}
      />

      {banner ? (
        <div className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-[13px] text-red-700">{banner}</div>
      ) : null}

      {loading ? (
        <div className="flex items-center gap-2 text-[13px] text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          Loading patient…
        </div>
      ) : patient ? (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="card p-5">
            <h2 className="mb-3 text-[14px] font-semibold text-slate-900">Demographics</h2>
            <dl className="grid grid-cols-2 gap-3 text-[13px]">
              <div>
                <dt className="text-slate-500">DOB</dt>
                <dd className="font-medium text-slate-900">{asString(patient.dob)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">External ID</dt>
                <dd className="font-medium text-slate-900">{asString(patient.external_id)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Created</dt>
                <dd className="font-medium text-slate-900">{asString(patient.created_at)}</dd>
              </div>
            </dl>
          </section>

          <section className="card p-5">
            <h2 className="mb-3 text-[14px] font-semibold text-slate-900">Latest Eligibility</h2>
            {latestCheck ? (
              <div className="space-y-2 text-[13px]">
                <div className="flex flex-wrap gap-2">
                  <StatusPill
                    label={latestCheck.is_active === true ? "Active" : latestCheck.is_active === false ? "Inactive" : "Unknown"}
                    tone={latestCheck.is_active === true ? "success" : "warn"}
                  />
                  <StatusPill label={asString(latestCheck.payer_id, "Payer")} tone="info" />
                </div>
                <p className="text-slate-600">Checked {asString(latestCheck.checked_at)}</p>
                <p className="text-slate-600">
                  Coverage {asString(latestCheck.coverage_percent, "—")}% · Deductible remaining{" "}
                  {asString(latestCheck.deductible_remaining)}
                </p>
              </div>
            ) : (
              <p className="text-[13px] text-slate-500">No eligibility checks on file.</p>
            )}
          </section>

          <section className="card p-5 lg:col-span-2">
            <h2 className="mb-3 text-[14px] font-semibold text-slate-900">Recent Agent Runs</h2>
            {agentRuns.length === 0 ? (
              <p className="text-[13px] text-slate-500">No agent activity recorded.</p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {agentRuns.map((run) => (
                  <li key={asString(run.id, Math.random().toString())} className="flex items-center justify-between py-3 text-[13px]">
                    <div>
                      <div className="font-semibold text-slate-900">{asString(run.agent, "Agent")}</div>
                      <div className="text-slate-500">{asString(run.created_at)}</div>
                    </div>
                    <StatusPill label={asString(run.status, "unknown")} tone="indigo" />
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      ) : null}
    </main>
  );
}
