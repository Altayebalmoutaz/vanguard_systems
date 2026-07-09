function initials(first: string, last: string): string {
  const a = first.trim().charAt(0);
  const b = last.trim().charAt(0);
  return `${a}${b}`.toUpperCase() || "?";
}

// Tasteful palette — restrained, professional. Two-stop gradients per pair.
const PALETTE: Array<[string, string]> = [
  ["#1880f0", "#0f6ad6"], // ezFi blue
  ["#0ea5e9", "#0284c7"], // sky
  ["#5cc82c", "#3f9a1c"], // ezFi lime
  ["#f59e0b", "#d97706"], // amber
  ["#0f6ad6", "#0c56b0"], // deep blue
  ["#3b82f6", "#1880f0"], // blue → brand
  ["#14b8a6", "#0d9488"], // teal
  ["#64748b", "#475569"], // slate
  ["#06b6d4", "#0891b2"], // cyan
  ["#84cc16", "#5cc82c"], // lime
  ["#2563eb", "#1880f0"], // royal → brand
  ["#0c56b0", "#133c75"], // navy
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
