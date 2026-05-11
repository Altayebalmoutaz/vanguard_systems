function initials(first: string, last: string): string {
  const a = first.trim().charAt(0);
  const b = last.trim().charAt(0);
  return `${a}${b}`.toUpperCase() || "?";
}

// Tasteful palette — restrained, professional. Two-stop gradients per pair.
const PALETTE: Array<[string, string]> = [
  ["#6366f1", "#8b5cf6"], // indigo → violet
  ["#0ea5e9", "#22d3ee"], // sky → cyan
  ["#10b981", "#14b8a6"], // emerald → teal
  ["#f59e0b", "#f97316"], // amber → orange
  ["#ec4899", "#f43f5e"], // pink → rose
  ["#3b82f6", "#6366f1"], // blue → indigo
  ["#8b5cf6", "#d946ef"], // violet → fuchsia
  ["#14b8a6", "#0ea5e9"], // teal → sky
  ["#f43f5e", "#f59e0b"], // rose → amber
  ["#06b6d4", "#3b82f6"], // cyan → blue
  ["#84cc16", "#10b981"], // lime → emerald
  ["#a855f7", "#6366f1"], // purple → indigo
];

function hashName(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h << 5) - h + s.charCodeAt(i);
    h |= 0;
  }
  return Math.abs(h);
}

export function PatientAvatar({
  firstName,
  lastName,
  size = 36,
}: {
  firstName: string;
  lastName: string;
  size?: number;
}) {
  const label = initials(firstName, lastName);
  const seed = hashName(`${firstName} ${lastName}`.toLowerCase());
  const [from, to] = PALETTE[seed % PALETTE.length];
  return (
    <div
      className="relative flex shrink-0 items-center justify-center rounded-full text-[12px] font-semibold tracking-tight text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.25),0_1px_2px_rgba(15,23,42,0.12)]"
      style={{
        width: size,
        height: size,
        background: `linear-gradient(135deg, ${from} 0%, ${to} 100%)`,
      }}
      aria-hidden
    >
      <span className="drop-shadow-[0_1px_0_rgba(0,0,0,0.08)]">{label}</span>
    </div>
  );
}
