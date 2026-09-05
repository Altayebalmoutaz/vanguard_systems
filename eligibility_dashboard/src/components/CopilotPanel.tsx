"use client";

import {
  postCopilotChat,
  type CopilotChatMessage,
  type CopilotToolTrace,
} from "@/lib/copilotApi";
import { Loader2, Lock, Send, Smile } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type VisibleMessage = {
  role: "user" | "assistant";
  content: string;
  sources?: CopilotToolTrace[];
};

const SOURCE_LABELS: Record<string, string> = {
  get_patient_overview: "Patient",
  get_insurance_and_benefits: "Insurance",
  get_recent_procedures: "Procedures",
  get_claims_and_payments: "Claims",
  get_appointments: "Appointments",
  get_treatment_plan: "Treatment plan",
  get_account_ledger: "Account",
  get_claim_procedures: "Claim procs",
  get_recalls: "Recalls",
  get_commlogs: "Comm log",
  get_documents: "Documents",
  get_referrals: "Referrals",
  get_statements: "Statements",
  get_health_history: "Health history",
  get_perio_exams: "Perio exams",
  get_clinical_notes: "Clinical notes",
  get_family_members: "Family",
  get_eligibility_history: "Eligibility",
  explain_carc_code: "CARC policy",
};

const SUGGESTIONS = [
  "What’s their coverage looking like?",
  "Anything coming up on the schedule?",
  "Where do they stand on the account?",
  "Any meds I should know about?",
];

function sourceLabel(name: string): string {
  return SOURCE_LABELS[name] ?? name;
}

export function CopilotPanel({
  patientId,
  patientName,
  odPatNum,
  className,
}: {
  patientId: string;
  patientName: string;
  odPatNum?: number | null;
  className?: string;
}) {
  const [messages, setMessages] = useState<VisibleMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    const nextUser: VisibleMessage = { role: "user", content: trimmed };
    const history: CopilotChatMessage[] = [...messages, nextUser].map((item) => ({
      role: item.role,
      content: item.content,
    }));
    setDraft("");
    setError(null);
    setMessages((prev) => [...prev, nextUser]);
    setBusy(true);
    const result = await postCopilotChat(patientId, history, odPatNum);
    setBusy(false);
    if (!result.ok || !result.reply) {
      setError(result.message ?? "Copilot request failed");
      return;
    }
    setMessages((prev) => [
      ...prev,
      { role: "assistant", content: result.reply ?? "", sources: result.toolTrace },
    ]);
  }

  return (
    <section
      className={`card flex min-h-0 flex-col overflow-hidden p-0 ${
        className ?? "min-h-[28rem] xl:min-h-[36rem]"
      }`}
    >
      <header className="flex shrink-0 items-center gap-3 border-b border-slate-100 px-5 py-3.5">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--accent-primary-soft-strong)] text-[var(--accent-primary-hover)]">
          <Smile size={18} strokeWidth={2} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[14px] font-bold text-slate-900">SmileSuites Copilot</h2>
          <p className="truncate text-[11.5px] font-medium text-slate-600">
            Your assistant for {patientName}
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10.5px] font-bold text-slate-600">
          <Lock size={11} strokeWidth={2.4} />
          Read-only
        </span>
      </header>

      <div
        ref={scrollRef}
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4"
      >
        {messages.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center text-center">
            <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl bg-[var(--accent-primary-soft)] text-[var(--accent-primary)]">
              <Smile size={20} strokeWidth={2} />
            </div>
            <p className="text-[13px] font-bold text-slate-800">What do you want to know?</p>
            <p className="mt-1 max-w-xs text-[12px] font-medium text-slate-600">
              Ask in plain language. I’ll look it up in the chart and I can’t change anything.
            </p>
            <div className="mt-4 flex flex-wrap justify-center gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => void send(suggestion)}
                  className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-[12px] font-medium text-slate-600 transition-colors hover:border-[var(--accent-primary)] hover:text-[var(--accent-primary-hover)]"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((item, index) => (
            <div
              key={`${item.role}-${index}`}
              className={`flex gap-2.5 ${item.role === "user" ? "flex-row-reverse" : ""}`}
            >
              <div
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
                  item.role === "user"
                    ? "bg-[var(--accent-primary-soft-strong)] text-[var(--accent-primary-hover)]"
                    : "bg-slate-100 text-slate-500"
                }`}
              >
                {item.role === "user" ? (
                  <span className="text-[11px] font-bold">You</span>
                ) : (
                  <Smile size={14} strokeWidth={2.2} />
                )}
              </div>
              <div className={`min-w-0 max-w-[85%] ${item.role === "user" ? "items-end" : ""}`}>
                <div
                  className={`rounded-2xl px-3.5 py-2.5 text-[13px] font-medium leading-relaxed ${
                    item.role === "user"
                      ? "rounded-tr-sm bg-[var(--accent-primary)] text-white"
                      : "rounded-tl-sm bg-slate-50 text-slate-800"
                  }`}
                >
                  <p className="whitespace-pre-wrap">{item.content}</p>
                </div>
                {item.sources && item.sources.length > 0 ? (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {item.sources.map((source, sourceIndex) => (
                      <span
                        key={`${source.name}-${sourceIndex}`}
                        className="rounded-full bg-white px-2 py-0.5 text-[10.5px] font-medium text-slate-500 ring-1 ring-slate-200"
                      >
                        {sourceLabel(source.name)}
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ))
        )}
        {busy ? (
          <div className="flex items-center gap-2 text-[12px] text-slate-500">
            <Loader2 size={14} className="animate-spin" />
            One sec, looking that up…
          </div>
        ) : null}
      </div>

      {error ? (
        <div className="mx-5 mb-2 shrink-0 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700">
          {error}
        </div>
      ) : null}

      <form
        className="flex shrink-0 gap-2 border-t border-slate-100 px-5 py-3.5"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Ask me about this patient…"
          disabled={busy}
          className="h-10 flex-1 rounded-lg border border-slate-200 px-3 text-[13px] font-medium outline-none transition-colors focus:border-[var(--accent-primary)] disabled:opacity-60"
        />
        <button
          type="submit"
          disabled={busy || !draft.trim()}
          className="inline-flex h-10 items-center gap-1.5 rounded-lg bg-[var(--accent-primary)] px-3.5 text-[13px] font-semibold text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          <Send size={14} />
          Send
        </button>
      </form>
    </section>
  );
}
