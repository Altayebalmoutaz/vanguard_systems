export type PillTone =
  | "success"
  | "warn"
  | "danger"
  | "info"
  | "neutral"
  | "indigo";

const TONES: Record<PillTone, { wrap: string; dot: string }> = {
  success: {
    wrap: "border-[var(--success-border)] bg-[var(--success-bg)] text-emerald-900",
    dot: "bg-[var(--accent-lime)]",
  },
  warn: {
    wrap: "border-amber-300 bg-amber-50 text-amber-900",
    dot: "bg-amber-500",
  },
  danger: {
    wrap: "border-red-300 bg-red-50 text-red-900",
    dot: "bg-red-600",
  },
  info: {
    wrap: "border-[var(--info-border)] bg-[var(--info-bg)] text-[var(--accent-primary-hover)]",
    dot: "bg-[var(--accent-primary)]",
  },
  neutral: {
    wrap: "border-slate-300 bg-slate-50 text-slate-800",
    dot: "bg-slate-500",
  },
  indigo: {
    wrap: "border-[var(--info-border)] bg-[var(--info-bg)] text-[var(--accent-primary-hover)]",
    dot: "bg-[var(--accent-primary)]",
  },
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
  const pad =
    size === "sm"
      ? "px-2 py-0.5 text-[10.5px] font-semibold"
      : "px-3 py-1.5 text-[12px] font-semibold";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border tracking-tight ${pad} ${t.wrap}`}
    >
      {dot ? (
        <span
          className={`relative h-[7px] w-[7px] shrink-0 rounded-full ${t.dot} ${pulse ? "status-dot-pulse" : ""}`}
          aria-hidden
        />
      ) : null}
      {label}
    </span>
  );
}
