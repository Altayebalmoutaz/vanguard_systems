"use client";

// SVG gauges used by the eligibility result card. `pathLength={100}` normalizes
// the stroke math so `value` maps directly to a 0–100 percentage.

export function RadialDonut({
  value,
  color,
  trackColor = "#eef1f6",
  size = 132,
  thickness = 12,
  centerValue,
  centerLabel,
}: {
  /** 0–100 percentage to fill. */
  value: number;
  color: string;
  trackColor?: string;
  size?: number;
  thickness?: number;
  centerValue: string;
  centerLabel?: string;
}) {
  const pct = Math.max(0, Math.min(100, value));
  const r = (size - thickness) / 2;
  const c = size / 2;

  return (
    <div className="relative" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90">
        <circle cx={c} cy={c} r={r} fill="none" stroke={trackColor} strokeWidth={thickness} />
        <circle
          cx={c}
          cy={c}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={thickness}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={100}
          strokeDashoffset={100 - pct}
          style={{ transition: "stroke-dashoffset 900ms cubic-bezier(0.2,0.8,0.2,1)" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[24px] font-bold leading-none tracking-tight text-slate-900">{centerValue}</span>
        {centerLabel ? (
          <span className="mt-1 text-[9px] font-semibold uppercase tracking-[0.1em] text-slate-400">{centerLabel}</span>
        ) : null}
      </div>
    </div>
  );
}

export function ConfidenceGauge({
  value,
  size = 168,
  thickness = 13,
}: {
  /** 0–100 confidence. */
  value: number;
  size?: number;
  thickness?: number;
}) {
  const pct = Math.max(0, Math.min(100, value));
  const r = (size - thickness) / 2;
  const cx = size / 2;
  const cy = size / 2;
  // Top semicircle, drawn left → right (clockwise) so the sweep tracks 0→100%.
  const arc = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  const color = pct >= 90 ? "#10b981" : pct >= 70 ? "#6366f1" : pct >= 50 ? "#f59e0b" : "#ef4444";
  const height = cy + thickness / 2 + 2;

  return (
    <div className="relative" style={{ width: size, height }}>
      <svg width={size} height={height} viewBox={`0 0 ${size} ${height}`}>
        <path d={arc} fill="none" stroke="#eef1f6" strokeWidth={thickness} strokeLinecap="round" />
        <path
          d={arc}
          fill="none"
          stroke={color}
          strokeWidth={thickness}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={100}
          strokeDashoffset={100 - pct}
          style={{ transition: "stroke-dashoffset 900ms cubic-bezier(0.2,0.8,0.2,1)" }}
        />
      </svg>
      <div
        className="absolute inset-x-0 flex flex-col items-center"
        style={{ top: cy - size * 0.34 }}
      >
        <span className="text-[30px] font-bold leading-none tracking-tight" style={{ color }}>
          {Math.round(pct)}%
        </span>
      </div>
      <div className="absolute inset-x-0 flex justify-between px-1 text-[10px] font-medium text-slate-400" style={{ top: cy - 2 }}>
        <span>0%</span>
        <span>100%</span>
      </div>
    </div>
  );
}
