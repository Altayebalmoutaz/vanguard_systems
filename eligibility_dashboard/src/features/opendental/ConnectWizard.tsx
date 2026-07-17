"use client";

import {
  fetchOpenDentalOnboardingKey,
  testOpenDentalConnection,
  type OpenDentalFriendlyError,
} from "@/lib/dashboardApi";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Copy,
  HelpCircle,
  KeyRound,
  Loader2,
  Monitor,
  PlugZap,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export type ConnectStepId =
  | "welcome"
  | "open_od"
  | "econnector"
  | "enable_api"
  | "paste_key"
  | "test"
  | "success";

const STEPS: ConnectStepId[] = [
  "welcome",
  "open_od",
  "econnector",
  "enable_api",
  "paste_key",
  "test",
  "success",
];

const STORAGE_KEY = "od_connect_wizard_step";

type StepDef = {
  id: ConnectStepId;
  title: string;
  body: string;
  tip?: string;
  art?: string;
  cta: string;
};

const STEP_COPY: Record<Exclude<ConnectStepId, "success">, StepDef> = {
  welcome: {
    id: "welcome",
    title: "Connect OpenDental",
    body: "About five minutes. Do this on the OpenDental server (the always-on PC that runs eConnector) — we’ll walk you through each click.",
    tip: "Keep this browser tab open while you work in OpenDental.",
    cta: "Let’s start",
  },
  open_od: {
    id: "open_od",
    title: "Open OpenDental",
    body: "On the OpenDental server, launch the OpenDental app and sign in to your clinic database.",
    tip: "If OpenDental won’t open, restart that server PC and try again. eConnector should run on one machine only — usually the database server.",
    art: "/onboarding/od-open.svg",
    cta: "I’ve opened OpenDental",
  },
  econnector: {
    id: "econnector",
    title: "Start eConnector",
    body: "In OpenDental go to eServices → eConnector Service (main menu, not under Setup). If status is Stopped or None, click Install or Start until it says Working.",
    tip: "eConnector is OpenDental’s bridge to the cloud. Keep that server awake — if it sleeps, Remote API calls will fail.",
    art: "/onboarding/od-econnector.svg",
    cta: "eConnector is Working",
  },
  enable_api: {
    id: "enable_api",
    title: "Enable the API",
    body: "Go to Setup → Advanced Setup → API. Check Enabled so OpenDental can accept secure connections.",
    tip: "You don’t need to change permissions — just make sure Enabled is checked.",
    art: "/onboarding/od-api.svg",
    cta: "API is enabled",
  },
  paste_key: {
    id: "paste_key",
    title: "Paste your clinic key",
    body: "Copy the key below, then in OpenDental click Add Key and paste it. This links your clinic to ezFi — we never ask you for passwords.",
    tip: "If the key area is empty, your setup partner still needs to finish provisioning. Message them and refresh this page.",
    art: "/onboarding/od-key.svg",
    cta: "I’ve pasted the key",
  },
  test: {
    id: "test",
    title: "Test the connection",
    body: "We’ll verify we can reach your OpenDental through the cloud. This takes a few seconds.",
    tip: "Leave OpenDental open and keep the eConnector server awake during the test.",
    cta: "Test connection",
  },
};

function stepIndex(id: ConnectStepId): number {
  return STEPS.indexOf(id);
}

function ProgressRail({ current }: { current: ConnectStepId }) {
  const idx = stepIndex(current);
  const visible = STEPS.filter((s) => s !== "success");
  return (
    <div className="mb-8 flex items-center gap-1.5" aria-label="Setup progress">
      {visible.map((id, i) => {
        const done = i < idx || current === "success";
        const active = id === current;
        return (
          <div
            key={id}
            className={`h-1.5 flex-1 rounded-full transition-colors duration-300 ${
              done || active ? "bg-indigo-500" : "bg-slate-200"
            }`}
          />
        );
      })}
    </div>
  );
}

function StuckTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-slate-500 hover:text-indigo-600"
      >
        <HelpCircle size={14} />
        {open ? "Hide tip" : "I’m stuck"}
      </button>
      {open ? (
        <p className="mt-2 rounded-xl border border-slate-100 bg-slate-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-slate-600">
          {text}
        </p>
      ) : null}
    </div>
  );
}

function KeyCopyPanel({ practiceId }: { practiceId: string }) {
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(false);
  const [key, setKey] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let active = true;
    void fetchOpenDentalOnboardingKey(practiceId).then((result) => {
      if (!active) return;
      setLoading(false);
      if (!result.ok) {
        setConfigured(false);
        setMessage(result.message ?? "Could not load clinic key");
        return;
      }
      setConfigured(result.configured);
      setKey(result.customerKey);
      setMessage(result.message ?? null);
    });
    return () => {
      active = false;
    };
  }, [practiceId]);

  const copy = async () => {
    if (!key) return;
    try {
      await navigator.clipboard.writeText(key);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setMessage(
        "Couldn’t copy automatically — select the key and copy manually.",
      );
    }
  };

  if (loading) {
    return (
      <div className="mt-5 flex items-center gap-2 text-[13px] text-slate-500">
        <Loader2 size={15} className="animate-spin" />
        Loading your clinic key…
      </div>
    );
  }

  if (!configured || !key) {
    return (
      <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-900">
        {message ??
          "Your clinic key isn’t ready yet. Contact your setup partner."}
      </div>
    );
  }

  return (
    <div className="mt-5 rounded-xl border border-slate-200 bg-slate-50/80 p-4">
      <div className="mb-2 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-slate-500">
        <KeyRound size={13} />
        Clinic key
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <code className="mono min-w-0 flex-1 break-all rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-[13px] text-slate-800">
          {key}
        </code>
        <button
          type="button"
          onClick={() => void copy()}
          className="lift-on-hover inline-flex h-10 items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-semibold text-slate-700 hover:bg-slate-50"
        >
          {copied ? (
            <Check size={14} className="text-emerald-600" />
          ) : (
            <Copy size={14} />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

export function ConnectWizard({
  practiceId,
  canControl,
  onComplete,
}: {
  practiceId: string;
  canControl: boolean;
  onComplete: () => void;
}) {
  const [step, setStep] = useState<ConnectStepId>("welcome");
  const [testing, setTesting] = useState(false);
  const [friendly, setFriendly] = useState<OpenDentalFriendlyError | null>(
    null,
  );

  useEffect(() => {
    try {
      const saved = window.sessionStorage.getItem(
        STORAGE_KEY,
      ) as ConnectStepId | null;
      if (saved && STEPS.includes(saved) && saved !== "success") {
        setStep(saved);
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, step);
    } catch {
      /* ignore */
    }
  }, [step]);

  const go = useCallback((next: ConnectStepId) => {
    setFriendly(null);
    setStep(next);
  }, []);

  const back = () => {
    const idx = stepIndex(step);
    if (idx > 0) go(STEPS[idx - 1]!);
  };

  const advance = async () => {
    if (step === "test") {
      if (!canControl) {
        setFriendly({
          code: "role",
          title: "Ask an admin",
          message: "Only an admin or billing lead can run the connection test.",
          recovery_step: "test",
        });
        return;
      }
      setTesting(true);
      setFriendly(null);
      const result = await testOpenDentalConnection(practiceId);
      setTesting(false);
      if (result.ok) {
        try {
          window.sessionStorage.removeItem(STORAGE_KEY);
        } catch {
          /* ignore */
        }
        go("success");
        return;
      }
      setFriendly(
        result.friendly ?? {
          code: "unknown",
          title: "Connection test failed",
          message:
            result.error ?? "Something went wrong. Try again in a moment.",
          recovery_step: "test",
        },
      );
      return;
    }
    if (step === "success") {
      onComplete();
      return;
    }
    const idx = stepIndex(step);
    const next = STEPS[idx + 1];
    if (next) go(next);
  };

  if (step === "success") {
    return (
      <div className="card wizard-shell overflow-hidden p-0">
        <div className="wizard-step-enter px-8 py-12 text-center sm:px-12">
          <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 ring-1 ring-emerald-100">
            <CheckCircle2 size={32} />
          </div>
          <h2 className="text-[22px] font-semibold tracking-tight text-slate-900">
            You’re connected
          </h2>
          <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-slate-600">
            OpenDental is linked — we can reach your clinic through the cloud.
            Polling and write-back use the toggles on this page once your setup
            partner enables them.
          </p>
          <button
            type="button"
            onClick={onComplete}
            className="btn-sheen lift-on-hover mt-8 inline-flex h-11 items-center gap-2 rounded-xl bg-[var(--accent-primary)] px-6 text-[14px] font-semibold text-white "
          >
            <Sparkles size={16} />
            Continue to OpenDental
          </button>
        </div>
      </div>
    );
  }

  const def = STEP_COPY[step];
  const idx = stepIndex(step);

  return (
    <div className="card wizard-shell overflow-hidden p-0">
      <div className="border-b border-slate-100 bg-gradient-to-b from-indigo-50/40 to-white px-6 py-5 sm:px-8">
        <div className="mb-1 flex items-center gap-2 text-[12px] font-semibold uppercase tracking-wide text-indigo-600">
          <PlugZap size={14} />
          OpenDental setup
        </div>
        <ProgressRail current={step} />
        <p className="text-[12.5px] text-slate-500">
          Step {Math.min(idx + 1, STEPS.length - 1)} of {STEPS.length - 1}
        </p>
      </div>

      <div key={step} className="wizard-step-enter px-6 py-7 sm:px-8 sm:py-9">
        <div className="grid gap-8 lg:grid-cols-[1.1fr_0.9fr] lg:items-center">
          <div>
            <h2 className="text-[22px] font-semibold tracking-tight text-slate-900">
              {def.title}
            </h2>
            <p className="mt-3 text-[14.5px] leading-relaxed text-slate-600">
              {def.body}
            </p>

            {step === "paste_key" && canControl ? (
              <KeyCopyPanel practiceId={practiceId} />
            ) : null}
            {step === "paste_key" && !canControl ? (
              <div className="mt-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-[13px] text-amber-900">
                Ask an admin or billing lead to open this page and copy the
                clinic key for you.
              </div>
            ) : null}

            {friendly ? (
              <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3">
                <div className="text-[13.5px] font-semibold text-red-800">
                  {friendly.title}
                </div>
                <p className="mt-1 text-[13px] leading-relaxed text-red-700">
                  {friendly.message}
                </p>
                {friendly.recovery_step && friendly.recovery_step !== step ? (
                  <button
                    type="button"
                    onClick={() => go(friendly.recovery_step as ConnectStepId)}
                    className="mt-3 text-[12.5px] font-semibold text-red-800 underline-offset-2 hover:underline"
                  >
                    Go back to that step
                  </button>
                ) : null}
              </div>
            ) : null}

            {def.tip ? <StuckTip text={def.tip} /> : null}

            <div className="mt-8 flex flex-wrap items-center gap-3">
              {idx > 0 ? (
                <button
                  type="button"
                  onClick={back}
                  disabled={testing}
                  className="lift-on-hover inline-flex h-11 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-4 text-[13.5px] font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <ArrowLeft size={15} />
                  Back
                </button>
              ) : null}
              <button
                type="button"
                onClick={() => void advance()}
                disabled={testing}
                className="btn-sheen lift-on-hover inline-flex h-11 items-center gap-2 rounded-xl bg-[var(--accent-primary)] px-5 text-[13.5px] font-semibold text-white disabled:opacity-60"
              >
                {testing ? (
                  <Loader2 size={15} className="animate-spin" />
                ) : null}
                {def.cta}
                {!testing && step !== "test" ? <ArrowRight size={15} /> : null}
                {!testing && step === "test" ? <ShieldCheck size={15} /> : null}
              </button>
            </div>
          </div>

          <div className="hidden lg:block">
            {def.art ? (
              <img
                src={def.art}
                alt=""
                className="wizard-art w-full rounded-2xl border border-slate-100 bg-white shadow-sm"
              />
            ) : (
              <div className="flex aspect-[4/3] flex-col items-center justify-center rounded-2xl border border-dashed border-indigo-100 bg-indigo-50/40 text-indigo-400">
                <Monitor size={40} strokeWidth={1.25} />
                <span className="mt-3 text-[13px] font-medium">
                  Clinic computer
                </span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
