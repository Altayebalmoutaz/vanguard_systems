"use client";

import { ClinicSwitcher } from "@/components/ClinicSwitcher";
import {
  dashboardAppName,
  dashboardAppSubtitle,
  dashboardPracticeName,
  dashboardUserDisplayName,
} from "@/lib/dashboardEnv";
import {
  canAccessWithRole,
  staffDisplayName,
  staffInitials,
  useStaffProfile,
  useStaffSession,
  type StaffRole,
} from "@/hooks/useStaffSession";
import { Phone, PlugZap, Settings, ShieldCheck, Smile } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  label: string;
  href: string;
  icon: typeof ShieldCheck;
  roles?: readonly StaffRole[];
};

const NAV: NavItem[] = [
  { label: "Dashboard", href: "/", icon: ShieldCheck },
  {
    label: "Voice",
    href: "/voice",
    icon: Phone,
    roles: ["admin", "billing_lead", "front_office"],
  },
  {
    label: "OpenDental",
    href: "/opendental",
    icon: PlugZap,
    roles: ["admin", "billing_lead"],
  },
  {
    label: "SmileSuites Copilot",
    href: "/copilot",
    icon: Smile,
    roles: ["admin", "billing_lead"],
  },
  { label: "Settings", href: "/settings", icon: Settings, roles: ["admin"] },
];

export function Sidebar() {
  const pathname = usePathname();
  const sessionUser = useStaffSession();
  const { role } = useStaffProfile();
  const displayName = staffDisplayName(sessionUser, dashboardUserDisplayName);
  const userInitials = staffInitials(displayName);
  const visibleNav = NAV.filter((item) => canAccessWithRole(role, item.roles));

  return (
    <aside className="fixed inset-y-0 left-0 z-30 flex w-[228px] flex-col border-r border-slate-200/90 bg-white">
      <div className="flex h-[56px] shrink-0 items-center gap-2.5 px-3.5">
        <Image
          src="/ezfi-logo.png"
          alt={dashboardAppName}
          width={100}
          height={100}
          className="h-9 w-auto object-contain"
          priority
        />
        <div className="min-w-0">
          <div className="sr-only">{dashboardAppName}</div>
          <div className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[0.05em] text-slate-600">
            {dashboardAppSubtitle}
          </div>
        </div>
      </div>
      <ClinicSwitcher />

      <nav className="mt-1 flex-1 space-y-0.5 px-2">
        {visibleNav.map((item) => {
          const Icon = item.icon;
          const active =
            item.href === "/"
              ? pathname === "/" || pathname.startsWith("/eligibility")
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              className={`flex h-9 w-full items-center gap-2.5 rounded-md px-2.5 text-left text-[13px] font-semibold transition-colors ${
                active
                  ? "bg-[var(--accent-primary-soft)] text-[var(--accent-primary-hover)]"
                  : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
              }`}
            >
              <Icon
                size={17}
                strokeWidth={active ? 2.25 : 2}
                className={`shrink-0 ${active ? "text-[var(--accent-primary)]" : "text-slate-500"}`}
              />
              <span className="overflow-hidden whitespace-nowrap">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      <div className="px-2 pb-3">
        <div className="mb-2.5 flex items-center gap-2 overflow-hidden rounded-md border border-slate-100 bg-slate-50/80 px-2 py-1.5">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-[var(--accent-primary-soft)] text-[var(--accent-primary)]">
            <ShieldCheck size={12} strokeWidth={2.2} />
          </div>
          <div>
            <div className="whitespace-nowrap text-[11px] font-semibold text-slate-700">HIPAA</div>
            <div className="whitespace-nowrap text-[10px] text-slate-400">SOC 2 Type II</div>
          </div>
        </div>
        <div className="border-t border-slate-100 pt-2.5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--accent-primary-soft-strong)] text-[10.5px] font-bold text-[var(--accent-primary-hover)]">
              {userInitials}
            </div>
            <div className="min-w-0">
              <div className="truncate whitespace-nowrap text-[12px] font-semibold text-slate-900">
                {displayName}
              </div>
              <div className="truncate whitespace-nowrap text-[10.5px] text-slate-500">
                {role
                  ? `${role.replace("_", " ")} · ${sessionUser?.email ?? dashboardPracticeName}`
                  : (sessionUser?.email ?? dashboardPracticeName)}
              </div>
            </div>
          </div>
          <form action="/auth/signout" method="post" className="pl-10">
            <button
              type="submit"
              className="mt-1.5 whitespace-nowrap text-[11px] font-semibold text-slate-400 hover:text-[var(--accent-primary)]"
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    </aside>
  );
}
