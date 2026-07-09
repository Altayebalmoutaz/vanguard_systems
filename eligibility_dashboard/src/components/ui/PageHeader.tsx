import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

export function PageHeader({
  icon: Icon,
  title,
  subtitle,
  actions,
}: {
  icon: LucideIcon;
  title: string;
  subtitle: string;
  actions?: ReactNode;
}) {
  return (
    <section className="mb-5 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
      <div className="flex items-center gap-3">
        <div className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-primary)] shadow-sm shadow-[rgba(24,128,240,0.22)]">
          <Icon size={18} className="text-white" strokeWidth={2} />
        </div>
        <div>
          <h1 className="text-[20px] font-semibold leading-tight tracking-tight text-slate-900">
            {title}
          </h1>
          <p className="mt-0.5 text-[12.5px] text-slate-500">{subtitle}</p>
        </div>
      </div>
      {actions ? (
        <div className="flex flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </section>
  );
}
