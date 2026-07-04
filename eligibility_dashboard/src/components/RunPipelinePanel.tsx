"use client";

import { SlideOver } from "@/components/ui/SlideOver";
import { StatusPill } from "@/components/ui/StatusPill";
import { CheckCircle2, FileText, Loader2, Receipt, Sparkles, Stethoscope } from "lucide-react";
import { useState, type ReactNode } from "react";

const EXAMPLE_NOTE =
  "Patient presents with fractured cusp on tooth #14 with deep distal caries. Pulp vitality confirmed. Full-coverage porcelain-fused-to-metal crown indicated following caries excavation and core buildup.";

type PipelineResult = {
  source?: string;
  coding: {
    cdt_codes: string[];
    icd10_codes: string[];
    confidence: number;
    justification: string;
    payer_flags: string[];
    status: string;
  };
  prior_auth: {
    requires_auth: boolean;
    required_documents: string[];
    payer_rules: string[];
    risk_level: "low" | "medium" | "high";
    risk_reason: string;
    status: string;
  };
  claim_draft: {
    status: string;
    blockers: string[];
    available_actions: string[];
    details: { cdt_codes: string[]; icd10_codes: string[] };
  };
};

const STAGE_LABELS = ["Generating codes", "Checking prior auth", "Assembling claim draft"];
const ASYNC_POLL_INTERVAL_MS = 1500;
const ASYNC_MAX_WAIT_MS = 120_000;

type PipelineJobStatus = {
  run_id?: string;
  status?: string;
  result?: PipelineResult;
  error_message?: string;
};

async function runSyncPipeline(note: string, insurance: string): Promise<PipelineResult> {
  const res = await fetch("/api/full-pipeline", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clinical_note: note, insurance, patient_age: 42 }),
  });
  if (!res.ok) throw new Error(`Pipeline failed (${res.status})`);
  return (await res.json()) as PipelineResult;
}

async function runAsyncPipeline(note: string, insurance: string): Promise<PipelineResult> {
  const enqueueRes = await fetch("/api/rcm/full-pipeline/jobs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ clinical_note: note, insurance, patient_age: 42 }),
  });
  if (!enqueueRes.ok) throw new Error("async_enqueue_failed");

  const enqueue = (await enqueueRes.json()) as PipelineJobStatus;
  const runId = enqueue.run_id?.trim();
  if (!runId) throw new Error("async_enqueue_failed");

  const deadline = Date.now() + ASYNC_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, ASYNC_POLL_INTERVAL_MS));
    const statusRes = await fetch(`/api/rcm/full-pipeline/jobs/${encodeURIComponent(runId)}`);
    if (!statusRes.ok) throw new Error("async_poll_failed");

    const job = (await statusRes.json()) as PipelineJobStatus;
    if (job.status === "completed") {
      const payload = job.result;
      if (!payload?.coding || !payload?.prior_auth || !payload?.claim_draft) {
        throw new Error("async_result_invalid");
      }
      return { ...payload, source: payload.source ?? "live" };
    }
    if (job.status === "failed" || job.status === "cancelled") {
      throw new Error(job.error_message ?? "async_job_failed");
    }
  }
  throw new Error("async_timeout");
}

export function RunPipelinePanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [note, setNote] = useState(EXAMPLE_NOTE);
  const [insurance, setInsurance] = useState("Anthem BCBS");
  const [running, setRunning] = useState(false);
  const [stage, setStage] = useState(0);
  const [result, setResult] = useState<PipelineResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setResult(null);
    setError(null);
    setStage(0);
    const stageTimer = window.setInterval(() => setStage((s) => Math.min(s + 1, STAGE_LABELS.length - 1)), 700);
    try {
      let data: PipelineResult;
      try {
        data = await runAsyncPipeline(note, insurance);
      } catch {
        data = await runSyncPipeline(note, insurance);
      }
      // Minimum visible run time so the agent animation reads clearly.
      await new Promise((r) => setTimeout(r, 1600));
      setResult(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pipeline failed");
    } finally {
      window.clearInterval(stageTimer);
      setRunning(false);
    }
  };

  const reset = () => {
    setResult(null);
    setError(null);
  };

  const riskTone = (level: string) => (level === "high" ? "danger" : level === "medium" ? "warn" : "success");

  return (
    <SlideOver open={open} onClose={onClose} width={520}>
      <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-indigo-600">
        <Sparkles size={14} /> AI Revenue Pipeline
      </div>
      <h3 className="mt-2 text-[22px] font-semibold tracking-tight text-slate-900">Run AI pipeline</h3>
      <p className="mt-1.5 text-[13px] leading-snug text-slate-500">
        One clinical note in — coding, prior authorization, and a scrubbed claim draft out. Powered by the
        Vanguard MD agent runtime.
      </p>

      {!result ? (
        <>
          <label className="mt-5 block">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Clinical note</span>
            <textarea
              className="mono mt-1.5 h-44 w-full resize-none rounded-lg border border-slate-200 bg-slate-50 p-3 text-[12.5px] leading-relaxed text-slate-800 outline-none focus:border-indigo-400"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={running}
            />
          </label>
          <label className="mt-3 block">
            <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Payer</span>
            <input
              className="mt-1.5 h-10 w-full rounded-lg border border-slate-200 px-3 text-[14px] text-slate-800 outline-none focus:border-indigo-400"
              value={insurance}
              onChange={(e) => setInsurance(e.target.value)}
              disabled={running}
            />
          </label>

          {running ? (
            <div className="mt-5 space-y-2.5 rounded-xl border border-indigo-100 bg-indigo-50/50 p-4">
              {STAGE_LABELS.map((label, i) => (
                <div key={label} className="flex items-center gap-2.5 text-[13px]">
                  {i < stage ? (
                    <CheckCircle2 size={16} className="text-emerald-500" />
                  ) : i === stage ? (
                    <Loader2 size={16} className="animate-spin text-indigo-500" />
                  ) : (
                    <span className="h-4 w-4 rounded-full border border-slate-300" />
                  )}
                  <span className={i <= stage ? "font-medium text-slate-800" : "text-slate-400"}>{label}</span>
                </div>
              ))}
            </div>
          ) : null}

          {error ? (
            <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[13px] text-red-700">{error}</div>
          ) : null}

          <button
            type="button"
            onClick={run}
            disabled={running || !note.trim()}
            className="btn-sheen lift-on-hover mt-auto inline-flex w-full items-center justify-center gap-2 rounded-lg bg-gradient-to-b from-indigo-500 to-indigo-600 py-3 text-[14px] font-semibold text-white ring-1 ring-inset ring-white/15 hover:from-indigo-500 hover:to-indigo-700 disabled:opacity-60"
          >
            {running ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />}
            {running ? "Running pipeline…" : "Run AI pipeline"}
          </button>
        </>
      ) : (
        <div className="mt-5 space-y-4">
          <ResultBlock icon={Stethoscope} title="Coding" tone="indigo">
            <div className="flex flex-wrap gap-1.5">
              {result.coding.cdt_codes.map((c) => (
                <code key={c} className="mono rounded-md bg-indigo-50 px-2 py-0.5 text-[12px] font-semibold text-indigo-700">{c}</code>
              ))}
              {result.coding.icd10_codes.map((c) => (
                <code key={c} className="mono rounded-md bg-slate-100 px-2 py-0.5 text-[12px] font-semibold text-slate-600">{c}</code>
              ))}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
                <div className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-500" style={{ width: `${Math.round(result.coding.confidence * 100)}%` }} />
              </div>
              <span className="text-[12px] font-semibold tabular-nums text-slate-700">{Math.round(result.coding.confidence * 100)}%</span>
            </div>
            <p className="mt-2 text-[12.5px] leading-snug text-slate-600">{result.coding.justification}</p>
          </ResultBlock>

          <ResultBlock icon={FileText} title="Prior Authorization" tone={riskTone(result.prior_auth.risk_level)}>
            <div className="flex items-center gap-2">
              <StatusPill tone={result.prior_auth.requires_auth ? "warn" : "success"} label={result.prior_auth.requires_auth ? "Auth required" : "No auth needed"} size="sm" />
              <StatusPill tone={riskTone(result.prior_auth.risk_level)} label={`${result.prior_auth.risk_level} risk`} size="sm" dot={false} />
            </div>
            {result.prior_auth.required_documents.length ? (
              <ul className="mt-2 list-inside list-disc text-[12.5px] text-slate-600">
                {result.prior_auth.required_documents.map((d) => (
                  <li key={d}>{d}</li>
                ))}
              </ul>
            ) : null}
            <p className="mt-1.5 text-[12.5px] leading-snug text-slate-600">{result.prior_auth.risk_reason}</p>
          </ResultBlock>

          <ResultBlock icon={Receipt} title="Claim Draft" tone={result.claim_draft.blockers.length ? "warn" : "success"}>
            <StatusPill
              tone={result.claim_draft.status === "draft" ? "info" : "warn"}
              label={result.claim_draft.status.replace("_", " ")}
              size="sm"
            />
            {result.claim_draft.blockers.length ? (
              <ul className="mt-2 list-inside list-disc text-[12.5px] text-amber-700">
                {result.claim_draft.blockers.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-[12.5px] text-emerald-700">Clean claim — ready to submit.</p>
            )}
          </ResultBlock>

          <div className="flex items-center justify-between pt-1 text-[11px] text-slate-400">
            <span>Source: {result.source === "live" ? "Live agent runtime" : "Simulated (backend offline)"}</span>
            <button type="button" onClick={reset} className="font-semibold text-indigo-600 hover:text-indigo-700">
              Run another
            </button>
          </div>
        </div>
      )}
    </SlideOver>
  );
}

function ResultBlock({
  icon: Icon,
  title,
  tone,
  children,
}: {
  icon: typeof Sparkles;
  title: string;
  tone: "indigo" | "success" | "warn" | "danger";
  children: ReactNode;
}) {
  const ring: Record<string, string> = {
    indigo: "border-indigo-100",
    success: "border-emerald-100",
    warn: "border-amber-100",
    danger: "border-red-100",
  };
  return (
    <div className={`fade-rise rounded-xl border bg-white p-4 shadow-sm ${ring[tone]}`}>
      <div className="mb-2 flex items-center gap-2 text-[13px] font-semibold text-slate-900">
        <Icon size={15} className="text-indigo-600" /> {title}
      </div>
      {children}
    </div>
  );
}
