"use client";

import { MiniSparkline } from "@/components/MiniSparkline";
import { SpotlightCard } from "@/components/ui/SpotlightCard";
import { useCountUp } from "@/hooks/useCountUp";
import type { LucideIcon } from "lucide-react";

export type KpiCardProps = {
  label: string;
  /** Display string when `numericValue` is omitted (e.g. preformatted text). */
  value: string;
  /** When set, animates from the previous number to this target. */
  numericValue?: number;
  /** Suffix appended after the animated number (e.g. "%"). */
  valueSuffix?: string;
  /** Decimal places for animated display (default 0). */
  valueDecimals?: number;
  sublabel?: string;
  icon: LucideIcon;
  iconBg?: string;
  iconColor?: string;
  spark?: { values: number[]; color: string };
  delta?: { value: string; positive: boolean; label: string };
  footerAction?: {
    label: string;
    onClick: () => void;
    tone?: "indigo" | "amber";
  };
};

export function KpiCard({
  label,
  value,
  numericValue,
  valueSuffix = "",
  valueDecimals = 0,
  sublabel,
  icon: Icon,
  iconBg = "bg-indigo-50",
  iconColor = "text-indigo-600",
  spark,
  delta,
  footerAction,
}: KpiCardProps) {
  const animated = useCountUp(numericValue ?? 0);
  const displayValue =
    numericValue == null
      ? value
      : `${animated.toFixed(valueDecimals)}${valueSuffix}`;

  return (
    <SpotlightCard className="card lift-on-hover flex flex-col p-5">
      <div className="flex items-start gap-3">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${iconBg}`}
        >
          <Icon size={17} className={iconColor} strokeWidth={2.2} />
        </div>
        <div className="flex flex-1 items-start justify-between">
          <div>
            <div className="text-[32px] font-bold leading-none tabular-nums tracking-tight text-slate-900">
              {displayValue}
            </div>
            <div className="mt-1 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
              {label}
            </div>
            {sublabel ? (
              <div className="mt-0.5 text-[12px] text-slate-500">
                {sublabel}
              </div>
            ) : null}
          </div>
          {spark ? (
            <MiniSparkline
              values={spark.values.length ? spark.values : [0]}
              strokeColor={spark.color}
              width={80}
              height={36}
              fillOpacity={0.1}
            />
          ) : null}
        </div>
      </div>
      {delta ? (
        <div className="mt-3 flex items-center gap-1.5 border-t border-slate-100 pt-3 text-[11px] font-semibold">
          <span
            className={delta.positive ? "text-emerald-600" : "text-red-500"}
          >
            {delta.positive ? "↑" : "↓"} {delta.value}
          </span>
          <span className="font-normal text-slate-400">{delta.label}</span>
        </div>
      ) : null}
      {footerAction ? (
        <button
          type="button"
          onClick={footerAction.onClick}
          className={`mt-3 inline-flex items-center gap-1 text-[11px] font-semibold transition ${
            footerAction.tone === "amber"
              ? "text-amber-600 hover:text-amber-700"
              : "text-indigo-600 hover:text-indigo-700"
          }`}
        >
          {footerAction.label} →
        </button>
      ) : null}
    </SpotlightCard>
  );
}
