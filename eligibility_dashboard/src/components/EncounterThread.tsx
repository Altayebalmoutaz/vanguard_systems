import type { JourneyStage, JourneyStageStatus } from "@/lib/rcm/types";
import { AlertTriangle, Check, Circle, Loader2, Minus } from "lucide-react";

const STATUS_STYLES: Record<
  JourneyStageStatus,
  { ring: string; bg: string; fg: string; icon: typeof Check; line: string }
> = {
  done: { ring: "ring-emerald-200", bg: "bg-emerald-500", fg: "text-white", icon: Check, line: "bg-emerald-300" },
  current: { ring: "ring-indigo-200", bg: "bg-indigo-500", fg: "text-white", icon: Loader2, line: "bg-slate-200" },
  blocked: { ring: "ring-amber-200", bg: "bg-amber-500", fg: "text-white", icon: AlertTriangle, line: "bg-slate-200" },
  pending: { ring: "ring-slate-200", bg: "bg-white", fg: "text-slate-400", icon: Circle, line: "bg-slate-200" },
  skipped: { ring: "ring-slate-200", bg: "bg-slate-100", fg: "text-slate-400", icon: Minus, line: "bg-slate-200" },
};

export function EncounterThread({ stages }: { stages: JourneyStage[] }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">Patient journey</div>
      <ol className="space-y-0">
        {stages.map((stage, i) => {
          const s = STATUS_STYLES[stage.status];
          const Icon = s.icon;
          const last = i === stages.length - 1;
          return (
            <li key={stage.key} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-2 ${s.ring} ${s.bg} ${s.fg}`}
                >
                  <Icon size={14} strokeWidth={2.4} className={stage.status === "current" ? "animate-spin" : ""} />
                </span>
                {!last ? <span className={`my-1 w-px flex-1 ${s.line}`} style={{ minHeight: 18 }} /> : null}
              </div>
              <div className={`pb-4 ${last ? "pb-0" : ""}`}>
                <div className="text-[13px] font-semibold text-slate-900">{stage.label}</div>
                <div className="text-[12px] leading-snug text-slate-500">{stage.detail}</div>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
