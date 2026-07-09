export type BarDatum = { label: string; value: number; color?: string };

export function BarChart({
  data,
  height = 180,
  formatValue = (v) => String(v),
  accent = "#1880f0",
}: {
  data: BarDatum[];
  height?: number;
  formatValue?: (v: number) => string;
  accent?: string;
}) {
  const max = Math.max(...data.map((d) => d.value), 1);
  return (
    <div className="flex items-end gap-3" style={{ height }}>
      {data.map((d) => {
        const pct = Math.max(4, (d.value / max) * 100);
        return (
          <div
            key={d.label}
            className="group flex h-full flex-1 flex-col items-center justify-end gap-2"
          >
            <div className="text-[12px] font-semibold tabular-nums text-slate-700">
              {formatValue(d.value)}
            </div>
            <div className="flex w-full flex-1 items-end">
              <div
                className="w-full rounded-t-md transition-all duration-500 ease-[cubic-bezier(0.2,0.8,0.2,1)]"
                style={{
                  height: `${pct}%`,
                  background: `linear-gradient(180deg, ${d.color ?? accent} 0%, ${d.color ?? accent}cc 100%)`,
                }}
              />
            </div>
            <div className="line-clamp-1 max-w-full text-center text-[10.5px] font-medium text-slate-500">
              {d.label}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export type DonutSegment = { label: string; value: number; color: string };

export function DonutChart({
  segments,
  size = 168,
  thickness = 22,
  centerLabel,
  centerSub,
}: {
  segments: DonutSegment[];
  size?: number;
  thickness?: number;
  centerLabel?: string;
  centerSub?: string;
}) {
  const total = segments.reduce((sum, s) => sum + s.value, 0) || 1;
  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="flex items-center gap-6">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="shrink-0 -rotate-90"
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#eef1f6"
          strokeWidth={thickness}
        />
        {segments.map((s) => {
          const len = (s.value / total) * circumference;
          const dash = `${len} ${circumference - len}`;
          const circle = (
            <circle
              key={s.label}
              cx={size / 2}
              cy={size / 2}
              r={radius}
              fill="none"
              stroke={s.color}
              strokeWidth={thickness}
              strokeDasharray={dash}
              strokeDashoffset={-offset}
              strokeLinecap="butt"
            />
          );
          offset += len;
          return circle;
        })}
      </svg>
      <div className="min-w-0">
        {centerLabel ? (
          <div className="mb-3">
            <div className="text-[26px] font-bold leading-none tracking-tight text-slate-900">
              {centerLabel}
            </div>
            {centerSub ? (
              <div className="text-[12px] text-slate-500">{centerSub}</div>
            ) : null}
          </div>
        ) : null}
        <ul className="space-y-1.5">
          {segments.map((s) => (
            <li key={s.label} className="flex items-center gap-2 text-[12.5px]">
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-sm"
                style={{ background: s.color }}
              />
              <span className="text-slate-600">{s.label}</span>
              <span className="ml-auto font-semibold tabular-nums text-slate-900">
                {s.value}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export type FunnelStageDatum = { label: string; count: number; value?: number };

export function FunnelChart({
  stages,
  formatValue,
}: {
  stages: FunnelStageDatum[];
  formatValue?: (v: number) => string;
}) {
  const max = Math.max(...stages.map((s) => s.count), 1);
  const first = stages[0]?.count || 1;
  return (
    <div className="space-y-2.5">
      {stages.map((s, i) => {
        const width = Math.max(12, (s.count / max) * 100);
        const conv = Math.round((s.count / first) * 100);
        return (
          <div key={s.label} className="flex items-center gap-3">
            <div className="w-28 shrink-0 text-right text-[12px] font-medium text-slate-600">
              {s.label}
            </div>
            <div className="relative h-9 flex-1 overflow-hidden rounded-lg bg-slate-50">
              <div
                className="flex h-full items-center rounded-lg bg-[var(--accent-primary)] px-3 transition-all duration-700 ease-[cubic-bezier(0.2,0.8,0.2,1)]"
                style={{ width: `${width}%`, opacity: 1 - i * 0.12 }}
              >
                <span className="text-[12px] font-semibold tabular-nums text-white">
                  {s.count}
                </span>
              </div>
            </div>
            <div className="w-24 shrink-0 text-[11px] text-slate-500">
              {s.value && formatValue ? formatValue(s.value) : `${conv}%`}
            </div>
          </div>
        );
      })}
    </div>
  );
}
