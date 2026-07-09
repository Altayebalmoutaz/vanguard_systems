"use client";

import { PageHeader } from "@/components/ui/PageHeader";
import { StatusPill } from "@/components/ui/StatusPill";
import { ConnectWizard } from "@/features/opendental/ConnectWizard";
import {
  staffRole,
  useStaffProfile,
  useStaffSession,
} from "@/hooks/useStaffSession";
import {
  fetchOpenDentalConnections,
  fetchOpenDentalRuns,
  pollOpenDentalNow,
  testOpenDentalConnection,
  updateOpenDentalConnection,
  type OpenDentalConnection,
  type OpenDentalConnectionUpdate,
  type OpenDentalRun,
} from "@/lib/dashboardApi";
import { Loader2, PlugZap, RefreshCw, Sparkles } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

const CONTROL_ROLES = new Set(["admin", "billing_lead"]);

const POLL_INTERVAL_PRESETS = [
  { label: "1 minute", seconds: 60 },
  { label: "5 minutes", seconds: 300 },
  { label: "15 minutes", seconds: 900 },
  { label: "1 hour", seconds: 3600 },
  { label: "Daily (24h)", seconds: 86_400 },
] as const;

function nearestPollPreset(seconds: number): number | "custom" {
  const match = POLL_INTERVAL_PRESETS.find(
    (preset) => preset.seconds === seconds,
  );
  return match ? match.seconds : "custom";
}

function formatPollInterval(
  seconds: number | string | null | undefined,
): string {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return "—";
  const preset = POLL_INTERVAL_PRESETS.find((item) => item.seconds === value);
  if (preset) return preset.label;
  if (value % 3600 === 0) return `${value / 3600} hour(s)`;
  if (value % 60 === 0) return `${value / 60} minute(s)`;
  return `${value} seconds`;
}

function healthTone(status: string | null): "success" | "warn" | "danger" {
  if (status === "ok") return "success";
  if (status === "error") return "danger";
  return "warn";
}

function formatWhen(value: string | null | undefined): string {
  if (!value) return "never";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

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

function ConnectionCard({
  connection,
  canControl,
  onChanged,
  onBanner,
}: {
  connection: OpenDentalConnection;
  canControl: boolean;
  onChanged: () => void;
  onBanner: (message: string) => void;
}) {
  const [busy, setBusy] = useState<"test" | "poll" | "save" | null>(null);
  const [editing, setEditing] = useState(false);
  const initialSeconds = Number(connection.poll_interval_seconds ?? 60);
  const [intervalPreset, setIntervalPreset] = useState<number | "custom">(
    nearestPollPreset(initialSeconds),
  );
  const [interval, setIntervalValue] = useState(String(initialSeconds));
  const [windowDays, setWindowDays] = useState(
    String(connection.poll_window_days ?? 0),
  );
  const [cdtCodes, setCdtCodes] = useState(connection.cdt_codes ?? "D1110");

  const run = async (
    kind: "test" | "poll",
    action: () => Promise<{ ok: boolean; error?: string; message?: string }>,
  ) => {
    setBusy(kind);
    const result = await action();
    setBusy(null);
    if (!result.ok) {
      onBanner(
        result.error ??
          result.message ??
          `${kind === "test" ? "Test" : "Poll"} failed`,
      );
    } else {
      onBanner(
        kind === "test"
          ? "Connection test passed."
          : "Poll queued — watch the runs feed below.",
      );
    }
    onChanged();
  };

  const save = async (updates: OpenDentalConnectionUpdate) => {
    setBusy("save");
    const result = await updateOpenDentalConnection(
      connection.practice_id,
      updates,
    );
    setBusy(null);
    if (!result.ok) {
      onBanner(result.message ?? "Update failed");
      return;
    }
    setEditing(false);
    onChanged();
  };

  return (
    <div className="card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[14px] font-semibold text-slate-900">
              {connection.display_name || connection.practice_id}
            </h2>
            <StatusPill
              label={connection.health_status ?? "unknown"}
              tone={healthTone(connection.health_status)}
            />
            {connection.poll_enabled ? (
              <StatusPill label="polling on" tone="success" />
            ) : (
              <StatusPill label="polling off" tone="warn" />
            )}
            {!connection.customer_key_configured ? (
              <StatusPill label="customer key missing" tone="danger" />
            ) : null}
          </div>
          <p className="mt-1 text-[12.5px] text-slate-500">
            {connection.base_url}
          </p>
          <p className="mt-2 text-[12.5px] text-slate-600">
            Auto-poll interval:{" "}
            <span className="font-semibold">
              {formatPollInterval(connection.poll_interval_seconds)}
            </span>
            {" · "}
            Last poll:{" "}
            <span className="font-semibold">
              {formatWhen(connection.last_poll_at)}
            </span>
            {connection.last_poll_status
              ? ` · ${connection.last_poll_status}`
              : ""}
            {typeof connection.last_poll_appointments === "number"
              ? ` · ${connection.last_poll_appointments} appt(s)`
              : ""}
          </p>
          <p className="text-[12.5px] text-slate-600">
            Health checked: {formatWhen(connection.health_checked_at)}
          </p>
          {connection.last_error ? (
            <p className="mt-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12px] text-red-700">
              {connection.last_error}
            </p>
          ) : null}
        </div>

        {canControl ? (
          <div className="flex flex-col items-end gap-2">
            <div className="flex items-center gap-2 text-[12.5px] text-slate-600">
              <span>Auto-poll</span>
              <Toggle
                enabled={connection.poll_enabled}
                disabled={busy !== null}
                onChange={() =>
                  void save({ poll_enabled: !connection.poll_enabled })
                }
              />
            </div>
            <div className="flex items-center gap-2 text-[12.5px] text-slate-600">
              <span>Write-back</span>
              <Toggle
                enabled={connection.writeback_enabled}
                disabled={busy !== null}
                onChange={() =>
                  void save({
                    writeback_enabled: !connection.writeback_enabled,
                  })
                }
              />
            </div>
            <div className="flex items-center gap-2 text-[12.5px] text-slate-600">
              <span title="Notes, commlog, verifies, insadjust, and benefits grid">
                Full writeback
              </span>
              <Toggle
                enabled={Boolean(connection.writeback_full)}
                disabled={busy !== null || !connection.writeback_enabled}
                onChange={() =>
                  void save({ writeback_full: !connection.writeback_full })
                }
              />
            </div>
            <div className="mt-1 flex gap-2">
              <button
                type="button"
                disabled={busy !== null}
                aria-busy={busy === "test"}
                onClick={() =>
                  void run("test", () =>
                    testOpenDentalConnection(connection.practice_id),
                  )
                }
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-[12.5px] font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "test" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : null}
                {busy === "test" ? "Testing…" : "Test connection"}
              </button>
              <button
                type="button"
                disabled={busy !== null}
                aria-busy={busy === "poll"}
                onClick={() =>
                  void run("poll", () =>
                    pollOpenDentalNow(connection.practice_id),
                  )
                }
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[var(--accent-primary)] px-3 text-[12.5px] font-semibold text-white shadow-sm shadow-[rgba(24,128,240,0.2)] hover:bg-[var(--accent-primary-hover)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy === "poll" ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : null}
                {busy === "poll" ? "Polling…" : "Poll now"}
              </button>
            </div>
            <button
              type="button"
              onClick={() => setEditing((prev) => !prev)}
              className="text-[12px] font-semibold text-[var(--accent-primary)] hover:text-[var(--accent-primary-hover)]"
            >
              {editing ? "Close settings" : "Edit poll settings"}
            </button>
          </div>
        ) : null}
      </div>

      {editing && canControl ? (
        <div className="mt-3 grid gap-3 rounded-lg border border-slate-100 bg-slate-50/60 p-3 sm:grid-cols-3">
          <label className="text-[12px] text-slate-600">
            Auto-poll interval
            <select
              value={
                intervalPreset === "custom" ? "custom" : String(intervalPreset)
              }
              onChange={(event) => {
                const value = event.target.value;
                if (value === "custom") {
                  setIntervalPreset("custom");
                  return;
                }
                const seconds = Number(value);
                setIntervalPreset(seconds);
                setIntervalValue(String(seconds));
              }}
              className="mt-1 h-9 w-full rounded-md border border-slate-200 bg-white px-2.5 text-[13px] text-slate-800 outline-none focus:border-[var(--accent-primary)]"
            >
              {POLL_INTERVAL_PRESETS.map((preset) => (
                <option key={preset.seconds} value={preset.seconds}>
                  {preset.label}
                </option>
              ))}
              <option value="custom">Custom (seconds)</option>
            </select>
            <p className="mt-1 text-[11px] text-slate-500">
              Daily runs about every 24 hours after the last successful poll.
            </p>
          </label>
          {intervalPreset === "custom" ? (
            <label className="text-[12px] text-slate-600">
              Custom interval (seconds)
              <input
                value={interval}
                onChange={(event) => setIntervalValue(event.target.value)}
                className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2.5 text-[13px] text-slate-800 outline-none focus:border-[var(--accent-primary)]"
              />
            </label>
          ) : (
            <div className="text-[12px] text-slate-600">
              <div className="mb-1">Resolved interval</div>
              <div className="flex h-9 items-center rounded-md border border-slate-200 bg-white px-2.5 text-[13px] font-semibold text-slate-800">
                {formatPollInterval(Number(interval))}
              </div>
            </div>
          )}
          <label className="text-[12px] text-slate-600">
            Window (days ahead)
            <input
              value={windowDays}
              onChange={(event) => setWindowDays(event.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2.5 text-[13px] text-slate-800 outline-none focus:border-[var(--accent-primary)]"
            />
          </label>
          <label className="text-[12px] text-slate-600 sm:col-span-2">
            CDT codes (comma-separated)
            <input
              value={cdtCodes}
              onChange={(event) => setCdtCodes(event.target.value)}
              className="mt-1 h-9 w-full rounded-md border border-slate-200 px-2.5 text-[13px] text-slate-800 outline-none focus:border-[var(--accent-primary)]"
            />
          </label>
          <div className="sm:col-span-3">
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => {
                const seconds =
                  intervalPreset === "custom"
                    ? Number(interval) || 60
                    : intervalPreset;
                void save({
                  poll_interval_seconds: Math.min(86_400, Math.max(5, seconds)),
                  poll_window_days: Number(windowDays) || 0,
                  cdt_codes: cdtCodes,
                });
              }}
              className="inline-flex h-8 items-center rounded-md border border-slate-200 bg-white px-3 text-[12.5px] font-semibold text-slate-700 hover:border-slate-300 hover:bg-slate-50 disabled:opacity-50"
            >
              {busy === "save" ? "Saving…" : "Save settings"}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export default function OpenDentalPage() {
  return (
    <Suspense
      fallback={
        <div className="ml-[60px] flex min-h-screen items-center justify-center text-[13px] text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" />
          Loading OpenDental…
        </div>
      }
    >
      <OpenDentalConnectionsPage />
    </Suspense>
  );
}

function OpenDentalConnectionsPage() {
  const sessionUser = useStaffSession();
  const role = staffRole(sessionUser);
  const { practiceId: profilePracticeId } = useStaffProfile();
  const canControl = role === null || CONTROL_ROLES.has(role);
  const searchParams = useSearchParams();
  const forceConnect = searchParams.get("connect") === "1";

  const [connections, setConnections] = useState<OpenDentalConnection[]>([]);
  const [runs, setRuns] = useState<OpenDentalRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const [showWizard, setShowWizard] = useState(false);

  // Prefer staff profile, then any loaded connection, then local dashboard default.
  const practiceId =
    profilePracticeId || connections[0]?.practice_id || "vgd_mock_brooklyn";

  const load = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    const [connectionsResult, runsResult] = await Promise.all([
      fetchOpenDentalConnections(),
      fetchOpenDentalRuns(30),
    ]);
    if (!connectionsResult.ok) {
      setBanner(
        connectionsResult.message ?? "OpenDental connections unavailable.",
      );
      setConnections([]);
    } else {
      setConnections(connectionsResult.connections);
    }
    setRuns(runsResult.ok ? runsResult.runs : []);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-open when empty, or when ?connect=1 (even if a connection already exists).
  useEffect(() => {
    if (loading) return;
    if (forceConnect || connections.length === 0) {
      setShowWizard(true);
    }
  }, [loading, forceConnect, connections.length]);

  useEffect(() => {
    let source: EventSource | null = null;
    if (typeof window.EventSource !== "undefined") {
      source = new EventSource("/api/dashboard/eligibility/stream");
      source.addEventListener("rcm", () => void load(true));
    }
    const fallback = window.setInterval(() => void load(true), 30_000);
    return () => {
      window.clearInterval(fallback);
      source?.close();
    };
  }, [load]);

  return (
    <main className="ml-[60px] min-h-screen overflow-y-auto px-6 pb-12 pt-6">
      <PageHeader
        icon={PlugZap}
        title="OpenDental Connections"
        subtitle={
          showWizard
            ? "Guided setup — connect your clinic OpenDental in a few steps."
            : "Per-clinic OpenDental Remote API connectivity, polling control, and health."
        }
        actions={
          <div className="flex items-center gap-2">
            {showWizard ? (
              <button
                type="button"
                onClick={() => setShowWizard(false)}
                className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-[12.5px] font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50"
              >
                <span>Exit wizard</span>
              </button>
            ) : (
              <button
                type="button"
                onClick={() => setShowWizard(true)}
                className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[var(--accent-primary)] px-3 text-[12.5px] font-semibold text-white shadow-sm shadow-[rgba(24,128,240,0.2)] hover:bg-[var(--accent-primary-hover)]"
              >
                <Sparkles size={14} />
                <span>Connect wizard</span>
              </button>
            )}
            <button
              type="button"
              onClick={() => void load()}
              disabled={refreshing}
              aria-busy={refreshing}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-200 bg-white px-2.5 text-[12.5px] font-medium text-slate-600 hover:border-slate-300 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {refreshing ? (
                <Loader2 size={14} className="animate-spin text-slate-500" />
              ) : (
                <RefreshCw size={14} className="text-slate-500" />
              )}
              <span>Refresh</span>
            </button>
          </div>
        }
      />

      {banner ? (
        <div
          className={`mb-4 rounded-lg border px-3.5 py-2.5 text-[12.5px] ${
            banner.includes("passed") || banner.includes("queued")
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
          Loading connections…
        </div>
      ) : showWizard ? (
        <ConnectWizard
          practiceId={practiceId}
          canControl={canControl}
          onComplete={() => {
            setShowWizard(false);
            setBanner(
              "OpenDental connected. You can poll appointments anytime.",
            );
            void load(true);
          }}
        />
      ) : connections.length === 0 ? (
        <div className="card p-6 text-center">
          <p className="text-[13px] text-slate-600">
            No OpenDental connection yet.
          </p>
          <button
            type="button"
            onClick={() => setShowWizard(true)}
            className="mt-3 inline-flex h-8 items-center gap-1.5 rounded-md bg-[var(--accent-primary)] px-3 text-[12.5px] font-semibold text-white shadow-sm shadow-[rgba(24,128,240,0.2)] hover:bg-[var(--accent-primary-hover)]"
          >
            <Sparkles size={14} />
            Start Connect wizard
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <div className="card flex flex-wrap items-center justify-between gap-3 border-[var(--accent-primary-soft-strong)] bg-[var(--accent-primary-soft)] p-4">
            <div>
              <div className="text-[13.5px] font-semibold text-slate-900">
                Connect OpenDental wizard
              </div>
              <p className="mt-0.5 text-[12.5px] text-slate-600">
                Guided setup for a clinic PC — eConnector, API key, and
                connection test.
              </p>
            </div>
            <button
              type="button"
              onClick={() => setShowWizard(true)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[var(--accent-primary)] px-3 text-[12.5px] font-semibold text-white shadow-sm shadow-[rgba(24,128,240,0.2)] hover:bg-[var(--accent-primary-hover)]"
            >
              <Sparkles size={14} />
              Open Connect wizard
            </button>
          </div>
          {connections.map((connection) => (
            <ConnectionCard
              key={connection.practice_id}
              connection={connection}
              canControl={canControl}
              onChanged={() => void load(true)}
              onBanner={setBanner}
            />
          ))}
        </div>
      )}

      {!showWizard ? (
        <section className="mt-6">
          <h2 className="mb-2.5 text-[13.5px] font-semibold text-slate-900">
            Recent poll & write-back runs
          </h2>
          {runs.length === 0 ? (
            <div className="card p-5 text-center text-[12.5px] text-slate-500">
              No OpenDental pipeline runs yet.
            </div>
          ) : (
            <div className="card divide-y divide-slate-100">
              {runs.map((run) => (
                <div
                  key={run.id}
                  className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2.5"
                >
                  <div className="flex items-center gap-2">
                    <StatusPill
                      label={run.status}
                      tone={
                        run.status === "completed"
                          ? "success"
                          : run.status === "failed"
                            ? "danger"
                            : "warn"
                      }
                    />
                    <span className="text-[13px] font-medium text-slate-700">
                      {run.run_type}
                    </span>
                  </div>
                  <div className="text-right">
                    <div className="text-[12px] text-slate-500">
                      {formatWhen(run.created_at)}
                    </div>
                    {run.error_message ? (
                      <div className="max-w-md truncate text-[12px] text-red-600">
                        {run.error_message}
                      </div>
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : null}
    </main>
  );
}
