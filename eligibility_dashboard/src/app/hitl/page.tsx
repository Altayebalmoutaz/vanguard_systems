"use client";

import { PageHeader } from "@/components/ui/PageHeader";
import { SlideOver } from "@/components/ui/SlideOver";
import { StatusPill } from "@/components/ui/StatusPill";
import { ConfidenceGauge } from "@/components/ui/Gauges";
import { useStaffProfile } from "@/hooks/useStaffSession";
import {
  fetchHitlTasks,
  resolveHitlTask,
  type HitlResolveAction,
  type HitlTask,
} from "@/lib/dashboardApi";
import { ClipboardList, Loader2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

function taskConfidence(task: HitlTask): number {
  if (typeof task.confidence !== "number") return 0;
  return task.confidence <= 1 ? task.confidence * 100 : task.confidence;
}

const RESOLVE_ROLES = new Set(["admin", "billing_lead"]);

export default function HitlInboxPage() {
  const { role } = useStaffProfile();
  const canResolve = role !== null && RESOLVE_ROLES.has(role);

  const [tasks, setTasks] = useState<HitlTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [selected, setSelected] = useState<HitlTask | null>(null);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [overrideCodes, setOverrideCodes] = useState("");
  const [rejectReason, setRejectReason] = useState("");

  const loadTasks = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    const result = await fetchHitlTasks("pending");
    if (!result.ok) {
      setBanner("HITL queue unavailable. Check FASTAPI_BASE_URL and Neon configuration.");
      setTasks([]);
    } else {
      setBanner(null);
      setTasks(result.tasks);
    }
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void loadTasks();
    const interval = window.setInterval(() => void loadTasks(true), 8000);
    return () => window.clearInterval(interval);
  }, [loadTasks]);

  useEffect(() => {
    if (selected) {
      setOverrideCodes((selected.ai_codes ?? []).join(", "));
      setRejectReason("");
    }
  }, [selected]);

  const handleResolve = async (action: HitlResolveAction) => {
    if (!selected || !canResolve) return;
    setDecisionBusy(true);
    setBanner(null);

    const body =
      action === "override"
        ? {
            action,
            override_codes: overrideCodes
              .split(",")
              .map((code) => code.trim())
              .filter(Boolean),
            actor_label: "dashboard_staff",
          }
        : action === "reject"
          ? { action, reason: rejectReason || "Rejected by biller", actor_label: "dashboard_staff" }
          : {
              action,
              final_codes: selected.ai_codes ?? [],
              actor_label: "dashboard_staff",
            };

    const result = await resolveHitlTask(selected.id, body);
    setDecisionBusy(false);

    if (!result.ok) {
      setBanner(result.message ?? "Failed to resolve task.");
      return;
    }

    setSelected(null);
    setTasks((prev) => prev.filter((task) => task.id !== selected.id));
    setBanner(
      action === "reject"
        ? "Task rejected and removed from queue."
        : "Task approved — claim accepted for submission workflow.",
    );
  };

  return (
    <main className="ml-[64px] min-h-screen overflow-y-auto px-7 pb-14 pt-7">
      <PageHeader
        icon={ClipboardList}
        title="HITL Inbox"
        subtitle="Low-confidence agent outputs and pipeline items awaiting human review."
        actions={
          <button
            type="button"
            onClick={() => void loadTasks()}
            className="lift-on-hover inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-medium text-slate-600 shadow-sm hover:border-slate-300 hover:bg-slate-50"
          >
            {refreshing ? (
              <Loader2 size={15} className="animate-spin text-slate-500" />
            ) : (
              <RefreshCw size={15} className="text-slate-500" />
            )}
            <span>Refresh</span>
          </button>
        }
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
        <div className="flex items-center gap-2 text-[13px] text-slate-500">
          <Loader2 size={16} className="animate-spin" />
          Loading queue…
        </div>
      ) : tasks.length === 0 ? (
        <div className="card p-8 text-center text-[14px] text-slate-500">
          No pending review tasks. Pipeline outputs above the confidence threshold auto-clear.
        </div>
      ) : (
        <div className="space-y-3">
          {tasks.map((task) => (
            <button
              key={task.id}
              type="button"
              onClick={() => setSelected(task)}
              className="card lift-on-hover w-full p-5 text-left"
            >
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-[15px] font-semibold text-slate-900">{task.patient_name}</h2>
                    <StatusPill label={task.task_type} tone="indigo" />
                    <StatusPill label={task.status} tone="warn" />
                  </div>
                  <p className="mt-1 text-[12.5px] text-slate-500">
                    {task.payer ?? "Unknown payer"}
                    {task.patient_dob ? ` · DOB ${task.patient_dob}` : ""}
                  </p>
                  {task.ai_summary ? (
                    <p className="mt-3 line-clamp-3 text-[13px] leading-relaxed text-slate-700">
                      {task.ai_summary}
                    </p>
                  ) : task.clinical_note ? (
                    <p className="mt-3 line-clamp-3 text-[13px] leading-relaxed text-slate-700">
                      {task.clinical_note}
                    </p>
                  ) : null}
                  {task.ai_codes && task.ai_codes.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {task.ai_codes.map((code) => (
                        <span
                          key={code}
                          className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-700"
                        >
                          {code}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
                <div className="flex shrink-0 flex-col items-end gap-3">
                  <ConfidenceGauge value={taskConfidence(task)} size={72} />
                  <span className="text-[11px] text-slate-500">
                    {new Date(task.created_at).toLocaleString()}
                  </span>
                  {task.backend_record_id ? (
                    <Link
                      href="/coding"
                      onClick={(event) => event.stopPropagation()}
                      className="text-[12px] font-semibold text-indigo-600 hover:text-indigo-700"
                    >
                      Open in Coding →
                    </Link>
                  ) : null}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      <SlideOver open={selected !== null} onClose={() => setSelected(null)} width={480}>
        {selected ? (
          <div className="flex h-full flex-col">
            <div>
              <h2 className="text-[17px] font-semibold text-slate-900">{selected.patient_name}</h2>
              <p className="mt-1 text-[12.5px] text-slate-500">
                {selected.task_type} · {selected.payer ?? "Unknown payer"}
              </p>
            </div>

            <div className="mt-5 space-y-4 overflow-y-auto">
              {selected.clinical_note ? (
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Clinical note
                  </p>
                  <p className="mt-1 text-[13px] leading-relaxed text-slate-700">{selected.clinical_note}</p>
                </div>
              ) : null}

              {selected.ai_summary ? (
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    AI summary
                  </p>
                  <p className="mt-1 text-[13px] leading-relaxed text-slate-700">{selected.ai_summary}</p>
                </div>
              ) : null}

              {selected.ai_codes && selected.ai_codes.length > 0 ? (
                <div>
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                    Suggested codes
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {selected.ai_codes.map((code) => (
                      <span
                        key={code}
                        className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-semibold text-slate-700"
                      >
                        {code}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}

              {canResolve ? (
                <div>
                  <label
                    htmlFor="override-codes"
                    className="text-[11px] font-semibold uppercase tracking-wide text-slate-500"
                  >
                    Override codes (comma-separated)
                  </label>
                  <input
                    id="override-codes"
                    value={overrideCodes}
                    onChange={(event) => setOverrideCodes(event.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-[13px] text-slate-800"
                  />
                  <label
                    htmlFor="reject-reason"
                    className="mt-3 block text-[11px] font-semibold uppercase tracking-wide text-slate-500"
                  >
                    Rejection reason
                  </label>
                  <input
                    id="reject-reason"
                    value={rejectReason}
                    onChange={(event) => setRejectReason(event.target.value)}
                    placeholder="Optional"
                    className="mt-1 w-full rounded-lg border border-slate-200 px-3 py-2 text-[13px] text-slate-800"
                  />
                </div>
              ) : null}
            </div>

            {canResolve && selected.status === "pending" ? (
              <div className="mt-auto flex flex-col gap-2 pt-5">
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={decisionBusy}
                    onClick={() => void handleResolve("reject")}
                    className="lift-on-hover flex-1 rounded-lg border border-slate-200 py-3 text-[14px] font-semibold text-slate-600 hover:border-red-300 hover:text-red-600 disabled:opacity-50"
                  >
                    Reject
                  </button>
                  <button
                    type="button"
                    disabled={decisionBusy}
                    onClick={() => void handleResolve("approve")}
                    className="btn-sheen lift-on-hover flex-1 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 disabled:opacity-50"
                  >
                    Approve
                  </button>
                </div>
                <button
                  type="button"
                  disabled={decisionBusy}
                  onClick={() => void handleResolve("override")}
                  className="lift-on-hover rounded-lg border border-amber-200 bg-amber-50 py-3 text-[14px] font-semibold text-amber-800 hover:border-amber-300 disabled:opacity-50"
                >
                  Approve with override codes
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
      </SlideOver>
    </main>
  );
}
