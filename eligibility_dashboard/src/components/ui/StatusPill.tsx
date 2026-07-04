export type PillTone = "success" | "warn" | "danger" | "info" | "neutral" | "indigo";

const TONES: Record<PillTone, { wrap: string; dot: string }> = {
  success: { wrap: "border-emerald-200 bg-emerald-50 text-emerald-700", dot: "bg-emerald-500" },
  warn: { wrap: "border-amber-200 bg-amber-50 text-amber-700", dot: "bg-amber-400" },
  danger: { wrap: "border-red-200 bg-red-50 text-red-700", dot: "bg-red-500" },
  info: { wrap: "border-blue-200 bg-blue-50 text-blue-700", dot: "bg-blue-400" },
  neutral: { wrap: "border-slate-200 bg-slate-50 text-slate-600", dot: "bg-slate-400" },
  indigo: { wrap: "border-indigo-200 bg-indigo-50 text-indigo-700", dot: "bg-indigo-500" },
};

export function StatusPill({
  tone,
  label,
  dot = true,
  pulse = false,
  size = "md",
}: {
  tone: PillTone;
  label: string;
  dot?: boolean;
  pulse?: boolean;
  size?: "sm" | "md";
}) {
  const t = TONES[tone];
  const pad = size === "sm" ? "px-2 py-0.5 text-[10px]" : "px-3 py-1.5 text-[12px]";
  return (
    <span className={`inline-flex items-center gap-2 rounded-full border font-medium tracking-tight ${pad} ${t.wrap}`}>
      {dot ? (
        <span className={`relative h-[7px] w-[7px] shrink-0 rounded-full ${t.dot} ${pulse ? "status-dot-pulse" : ""}`} />
      ) : null}
      {label}
    </span>
  );
}
