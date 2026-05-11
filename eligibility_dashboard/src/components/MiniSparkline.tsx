type MiniSparklineProps = {
  values: number[];
  className?: string;
  strokeColor?: string;
  fillOpacity?: number;
  width?: number;
  height?: number;
};

export function MiniSparkline({
  values,
  className = "",
  strokeColor = "#3B82F6",
  fillOpacity = 0.18,
  width = 140,
  height = 44,
}: MiniSparklineProps) {
  const safe = values.length ? values : [0];
  const min = Math.min(...safe);
  const max = Math.max(...safe);
  const span = max - min || 1;
  const pts = safe.map((v, i) => {
    const x = (i / Math.max(safe.length - 1, 1)) * (width - 4) + 2;
    const y = height - 3 - ((v - min) / span) * (height - 6);
    return [x, y] as const;
  });
  const linePath = `M ${pts.map((p) => `${p[0]},${p[1]}`).join(" L ")}`;
  const areaPath = `${linePath} L ${pts[pts.length - 1][0]},${height} L ${pts[0][0]},${height} Z`;
  const gradId = `spark-${strokeColor.replace(/[^a-zA-Z0-9]/g, "")}`;
  return (
    <svg
      className={className}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden
    >
      <defs>
        <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={strokeColor} stopOpacity={fillOpacity} />
          <stop offset="100%" stopColor={strokeColor} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={areaPath} fill={`url(#${gradId})`} className="fade-in" />
      <path
        d={linePath}
        stroke={strokeColor}
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="sparkline-path"
      />
      {pts.length > 0 && (
        <circle
          cx={pts[pts.length - 1][0]}
          cy={pts[pts.length - 1][1]}
          r={2.5}
          fill={strokeColor}
          className="fade-in"
          style={{ animationDelay: "0.9s" }}
        />
      )}
    </svg>
  );
}
