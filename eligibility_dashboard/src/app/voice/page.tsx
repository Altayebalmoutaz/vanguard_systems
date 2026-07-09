"use client";

import { PageHeader } from "@/components/ui/PageHeader";
import { StatusPill, type PillTone } from "@/components/ui/StatusPill";
import {
  fetchEligibilityActivity,
  fetchEligibilityQueue,
  fetchEligibilitySettings,
  reviewVoiceSession,
  updateEligibilitySettings,
} from "@/lib/eligibilityApi";
import type {
  EligibilityAgentSettings,
  EligibilityDashboardRow,
  EligibilityRequestEvent,
} from "@/lib/types";
import { Check, Loader2, Phone, RefreshCw, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

const VOICE_ACTIVE = new Set([
  "queued",
  "calling",
  "pending_review",
  "in_progress",
]);
const VOICE_ROUTING = new Set(["INCOMPLETE", "COVERAGE_AMBIGUOUS"]);
const VOICE_EVENT_PREFIX = "voice_verification_";

function Toggle({
  enabled,
  disabled,
  onChange,
}: {
  enabled: boolean;
  disabled?: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onChange}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] focus-visible:ring-offset-2 disabled:opacity-50 ${enabled ? "bg-[var(--accent-primary)]" : "bg-slate-200"}`}
      aria-pressed={enabled}
    >
      <span
        className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${enabled ? "translate-x-[18px]" : "translate-x-0.5"}`}
      />
    </button>
  );
}

function voiceStatusTone(status: string | null | undefined): PillTone {
  if (status === "pending_review") return "warn";
  if (status === "queued" || status === "calling" || status === "in_progress")
    return "info";
  if (status === "approved" || status === "auto_approved") return "success";
  if (status === "rejected" || status === "failed") return "danger";
  return "neutral";
}

function voiceStatusLabel(status: string | null | undefined): string {
  if (!status) return "Needs call";
  return status.replace(/_/g, " ");
}

function needsVoiceCall(row: EligibilityDashboardRow): boolean {
  const routing = row.routing_status ?? "";
  if (!VOICE_ROUTING.has(routing)) return false;
  if (VOICE_ACTIVE.has(row.voice_session_status ?? "")) return false;
  return true;
}

function isVoiceWorkItem(row: EligibilityDashboardRow): boolean {
  const status = row.voice_session_status;
  if (status && VOICE_ACTIVE.has(status)) return true;
  return needsVoiceCall(row);
}

function humanizeVoiceEvent(
  eventType: string,
  detail: Record<string, unknown>,
): string {
  const map: Record<string, string> = {
    voice_verification_queued: "Voice agent queued",
    voice_verification_calling: "Voice agent calling payer",
    voice_verification_completed: "Voice verification complete",
    voice_verification_approved: "Voice verification approved",
    voice_verification_auto_approved: "Stedi + Voice complete",
    voice_verification_rejected: "Voice verification rejected",
    voice_verification_failed: "Voice agent failed",
    voice_verification_skipped: "Voice agent skipped",
  };
  if (eventType === "voice_verification_skipped") {
    const reason = typeof detail.reason === "string" ? detail.reason : "";
    if (reason === "not_voice_eligible")
      return "Voice skipped — check complete";
    if (reason === "auto_queue_disabled")
      return "Voice skipped — auto-call off";
    if (reason === "voice_verification_disabled")
      return "Voice skipped — disabled";
  }
  return map[eventType] ?? eventType.replace(/_/g, " ");
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

export default function VoicePage() {
  const [settings, setSettings] = useState<EligibilityAgentSettings | null>(
    null,
  );
  const [rows, setRows] = useState<EligibilityDashboardRow[]>([]);
  const [activity, setActivity] = useState<EligibilityRequestEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [busySessionId, setBusySessionId] = useState<string | null>(null);
  const [settingsBusy, setSettingsBusy] = useState(false);

  const load = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    const [queueResult, settingsResult, activityResult] = await Promise.all([
      fetchEligibilityQueue(),
      fetchEligibilitySettings(),
      fetchEligibilityActivity(40),
    ]);

    if (!queueResult.ok) {
      setBanner(queueResult.message ?? "Voice queue unavailable.");
      setRows([]);
    } else {
      setBanner(null);
      setRows(queueResult.rows);
    }
    if (settingsResult.ok) {
      setSettings(settingsResult.settings);
    }
    if (activityResult.ok) {
      setActivity(
        activityResult.events.filter((event) =>
          event.event_type.startsWith(VOICE_EVENT_PREFIX),
        ),
      );
    }

    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => void load(true), 10_000);
    return () => window.clearInterval(interval);
  }, [load]);

  const workItems = useMemo(() => {
    const items = rows.filter(isVoiceWorkItem);
    const rank = (row: EligibilityDashboardRow) => {
      if (row.voice_session_status === "pending_review") return 0;
      if (
        row.voice_session_status === "calling" ||
        row.voice_session_status === "in_progress"
      )
        return 1;
      if (row.voice_session_status === "queued") return 2;
      return 3;
    };
    return [...items].sort((a, b) => rank(a) - rank(b));
  }, [rows]);

  const pendingReview = useMemo(
    () =>
      workItems.filter((row) => row.voice_session_status === "pending_review"),
    [workItems],
  );
  const inFlight = useMemo(
    () =>
      workItems.filter((row) => {
        const status = row.voice_session_status;
        return (
          status === "queued" ||
          status === "calling" ||
          status === "in_progress"
        );
      }),
    [workItems],
  );
  const needsCall = useMemo(
    () => workItems.filter((row) => needsVoiceCall(row)),
    [workItems],
  );

  const patientNameByRequest = useMemo(() => {
    const map = new Map<string, string>();
    for (const row of rows) {
      map.set(row.request_id, row.patient_name.trim() || row.subscriber_id);
    }
    return map;
  }, [rows]);

  const saveVoiceSetting = async (
    field: "voice_verification_enabled" | "voice_verification_auto_queue",
    value: boolean,
  ) => {
    const previous = settings;
    setSettingsBusy(true);
    setSettings((current) =>
      current
        ? { ...current, [field]: value }
        : {
            id: true,
            auto_check_enabled: true,
            auto_retry_enabled: true,
            voice_verification_enabled:
              field === "voice_verification_enabled" ? value : true,
            voice_verification_auto_queue:
              field === "voice_verification_auto_queue" ? value : false,
            last_sync_at: null,
            next_retry_at: null,
            updated_at: new Date().toISOString(),
          },
    );

    const result = await updateEligibilitySettings({ [field]: value });
    setSettingsBusy(false);
    if (!result.ok) {
      setSettings(previous);
      setBanner(result.message ?? "Failed to update voice settings");
      return;
    }
    if (result.settings) setSettings(result.settings);
  };

  const onReview = useCallback(
    async (sessionId: string, action: "approve" | "reject") => {
      setBusySessionId(sessionId);
      const result = await reviewVoiceSession(sessionId, action);
      setBusySessionId(null);
      if (!result.ok) {
        setBanner(result.message ?? "Voice review failed");
        return;
      }
      setBanner(
        action === "approve"
          ? "Voice verification approved."
          : "Voice verification rejected.",
      );
      await load(true);
    },
    [load],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (busySessionId) return;
      const sessionId = pendingReview[0]?.voice_session_id;
      if (!sessionId) return;
      const key = event.key.toLowerCase();
      if (key === "a") {
        event.preventDefault();
        void onReview(sessionId, "approve");
      } else if (key === "r") {
        event.preventDefault();
        void onReview(sessionId, "reject");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busySessionId, onReview, pendingReview]);

  return (
    <main className="ml-[60px] min-h-screen overflow-y-auto px-6 pb-12 pt-6">
      <PageHeader
        icon={Phone}
        title="Voice Agent"
        subtitle="Review payer calls, approve extracted benefits, and control auto-call fallback."
        actions={
          <button
            type="button"
            onClick={() => void load()}
            disabled={refreshing}
            aria-busy={refreshing}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-[12.5px] font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50 disabled:opacity-50"
          >
            {refreshing ? (
              <Loader2 size={14} className="animate-spin text-slate-500" />
            ) : (
              <RefreshCw size={14} className="text-slate-500" />
            )}
            <span>Refresh</span>
          </button>
        }
      />

      {banner ? (
        <div
          className={`mb-4 rounded-lg border px-3.5 py-2.5 text-[12.5px] ${
            banner.includes("approved") || banner.includes("rejected")
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : "border-amber-200 bg-amber-50 text-amber-800"
          }`}
        >
          {banner}
        </div>
      ) : null}

      <section className="mb-5 grid gap-3 lg:grid-cols-3">
        <div className="card p-4 lg:col-span-2">
          <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-400">
            Agent controls
          </div>
          <div className="grid gap-2.5 sm:grid-cols-2">
            <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5">
              <div>
                <div className="text-[13px] font-semibold text-slate-900">
                  Voice agent
                </div>
                <div className="text-[11.5px] text-slate-500">
                  Allow outbound payer verification calls.
                </div>
              </div>
              <Toggle
                enabled={settings?.voice_verification_enabled !== false}
                disabled={settingsBusy}
                onChange={() =>
                  void saveVoiceSetting(
                    "voice_verification_enabled",
                    settings?.voice_verification_enabled === false,
                  )
                }
              />
            </div>
            <div className="flex items-center justify-between gap-3 rounded-lg border border-slate-100 bg-slate-50/60 px-3 py-2.5">
              <div>
                <div className="text-[13px] font-semibold text-slate-900">
                  Auto-call
                </div>
                <div className="text-[11.5px] text-slate-500">
                  Queue voice when EDI/portal data is incomplete.
                </div>
              </div>
              <Toggle
                enabled={Boolean(settings?.voice_verification_auto_queue)}
                disabled={
                  settingsBusy || settings?.voice_verification_enabled === false
                }
                onChange={() =>
                  void saveVoiceSetting(
                    "voice_verification_auto_queue",
                    !settings?.voice_verification_auto_queue,
                  )
                }
              />
            </div>
          </div>
        </div>

        <div className="card grid grid-cols-3 gap-2 p-4">
          <div>
            <div className="text-[20px] font-bold tabular-nums text-slate-900">
              {pendingReview.length}
            </div>
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-amber-700">
              Review
            </div>
          </div>
          <div>
            <div className="text-[20px] font-bold tabular-nums text-slate-900">
              {inFlight.length}
            </div>
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[var(--accent-primary)]">
              In flight
            </div>
          </div>
          <div>
            <div className="text-[20px] font-bold tabular-nums text-slate-900">
              {needsCall.length}
            </div>
            <div className="text-[10.5px] font-semibold uppercase tracking-[0.06em] text-slate-500">
              Needs call
            </div>
          </div>
        </div>
      </section>

      <section className="mb-5 card overflow-hidden">
        <div className="border-b border-slate-100 bg-slate-50/50 px-4 py-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-[13.5px] font-semibold text-slate-900">
                Voice worklist
              </h2>
              <p className="mt-0.5 text-[12px] text-slate-500">
                Pending review first, then active calls, then patients that still
                need a payer call.
              </p>
            </div>
            {pendingReview.length > 0 ? (
              <p className="text-[11px] font-medium text-slate-500">
                Keyboard:{" "}
                <kbd className="rounded border border-slate-200 bg-white px-1 py-0.5 font-semibold text-slate-700">
                  A
                </kbd>{" "}
                approve ·{" "}
                <kbd className="rounded border border-slate-200 bg-white px-1 py-0.5 font-semibold text-slate-700">
                  R
                </kbd>{" "}
                reject
              </p>
            ) : null}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 px-4 py-6 text-[12.5px] text-slate-500">
            <Loader2 size={16} className="animate-spin" />
            Loading voice queue…
          </div>
        ) : workItems.length === 0 ? (
          <div className="px-4 py-8 text-center text-[13px] text-slate-500">
            No voice work right now. Incomplete checks will appear here when a
            call is needed.
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {workItems.map((row) => {
              const sessionId = row.voice_session_id;
              const reviewing =
                sessionId != null && busySessionId === sessionId;
              return (
                <div
                  key={row.request_id}
                  className="flex flex-col gap-2.5 px-4 py-3 lg:flex-row lg:items-center lg:justify-between"
                >
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        href={`/?request=${encodeURIComponent(row.request_id)}`}
                        className="text-[13.5px] font-semibold text-slate-900 hover:text-[var(--accent-primary)]"
                      >
                        {row.patient_name.trim() || row.subscriber_id}
                      </Link>
                      <StatusPill
                        label={voiceStatusLabel(row.voice_session_status)}
                        tone={voiceStatusTone(row.voice_session_status)}
                        size="sm"
                      />
                      {row.routing_status ? (
                        <StatusPill
                          label={row.routing_status.replace(/_/g, " ")}
                          tone="neutral"
                          size="sm"
                        />
                      ) : null}
                    </div>
                    <p className="mt-0.5 text-[12px] text-slate-500">
                      {row.payer_label || row.primary_payer_id}
                      {row.appointment_date
                        ? ` · Appt ${row.appointment_date}`
                        : ""}
                      {row.checked_at
                        ? ` · Checked ${formatWhen(row.checked_at)}`
                        : ""}
                    </p>
                    {row.voice_extracted_fields &&
                    Object.keys(row.voice_extracted_fields).length > 0 ? (
                      <p className="mt-1.5 line-clamp-2 text-[12px] text-slate-600">
                        Extracted:{" "}
                        {Object.keys(row.voice_extracted_fields)
                          .slice(0, 6)
                          .join(", ")}
                        {Object.keys(row.voice_extracted_fields).length > 6
                          ? "…"
                          : ""}
                      </p>
                    ) : null}
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    {row.voice_session_status === "pending_review" &&
                    sessionId ? (
                      <>
                        <button
                          type="button"
                          disabled={reviewing}
                          aria-busy={reviewing}
                          aria-keyshortcuts="a"
                          onClick={() => void onReview(sessionId, "approve")}
                          className="inline-flex h-8 items-center gap-1.5 rounded-md bg-emerald-600 px-2.5 text-[12.5px] font-semibold text-white hover:bg-emerald-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {reviewing ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <Check size={13} />
                          )}
                          Approve
                        </button>
                        <button
                          type="button"
                          disabled={reviewing}
                          aria-busy={reviewing}
                          aria-keyshortcuts="r"
                          onClick={() => void onReview(sessionId, "reject")}
                          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-red-200 bg-white px-2.5 text-[12.5px] font-semibold text-red-700 hover:bg-red-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-400 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {reviewing ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <X size={13} />
                          )}
                          Reject
                        </button>
                      </>
                    ) : null}
                    <Link
                      href={`/?request=${encodeURIComponent(row.request_id)}`}
                      className="inline-flex h-8 items-center rounded-md border border-slate-200 bg-white px-2.5 text-[12.5px] font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                    >
                      Open in dashboard
                    </Link>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      <section className="card overflow-hidden">
        <div className="border-b border-slate-100 px-4 py-3">
          <h2 className="text-[13.5px] font-semibold text-slate-900">
            Recent voice activity
          </h2>
        </div>
        {activity.length === 0 ? (
          <div className="px-4 py-6 text-center text-[12.5px] text-slate-500">
            No voice events yet.
          </div>
        ) : (
          <ul className="divide-y divide-slate-100">
            {activity.slice(0, 20).map((event) => (
              <li
                key={event.id}
                className="flex items-start justify-between gap-3 px-4 py-2.5"
              >
                <div>
                  <div className="text-[12.5px] font-medium text-slate-800">
                    {humanizeVoiceEvent(event.event_type, event.detail)}
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-slate-500">
                    {patientNameByRequest.get(event.request_id) ??
                      event.request_id}
                  </div>
                </div>
                <div className="shrink-0 text-[11px] text-slate-400">
                  {formatWhen(event.created_at)}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
