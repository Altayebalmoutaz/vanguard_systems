"use client";

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
import {
  BarChart3,
  ClipboardList,
  FileText,
  LayoutDashboard,
  Receipt,
  Settings,
  ShieldCheck,
  Stethoscope,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = {
  label: string;
  href: string;
  icon: typeof LayoutDashboard;
  roles?: readonly StaffRole[];
};

const NAV: NavItem[] = [
  { label: "Executive Dashboard", href: "/", icon: LayoutDashboard },
  { label: "Eligibility", href: "/eligibility", icon: ShieldCheck },
  { label: "HITL Inbox", href: "/hitl", icon: ClipboardList, roles: ["admin", "billing_lead", "front_office"] },
  { label: "Coding", href: "/coding", icon: Stethoscope, roles: ["admin", "billing_lead"] },
  { label: "Prior Authorization", href: "/prior-auth", icon: FileText, roles: ["admin", "billing_lead"] },
  { label: "Claims", href: "/claims", icon: Receipt, roles: ["admin", "billing_lead"] },
  { label: "Denials", href: "/denials", icon: XCircle, roles: ["admin", "billing_lead"] },
  { label: "Analytics", href: "/analytics", icon: BarChart3, roles: ["admin", "billing_lead", "read_only"] },
  { label: "Settings", href: "/settings", icon: Settings, roles: ["admin"] },
];

function SidebarBrandMark() {
  return (
    <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden>
      <path
        d="M10 6c-2.5 0-4.5 2-4.5 4.5v6c0 4 2.2 8 5.2 11l3.4 3.2a2.6 2.6 0 003.6 0l3.4-3.2c3-3 5.2-7 5.2-11v-6C26.5 8 24.5 6 22 6c-1.5 0-2.8.6-3.7 1.6L16 9.9l-2.3-2.3A4.9 4.9 0 0010 6z"
        fill="#4F46E5"
      />
      <path
        d="M14.2 13.8a1.2 1.2 0 011.7 0l1.5 1.5 3.4-3.4a1.2 1.2 0 011.7 1.7l-4.3 4.3a1.2 1.2 0 01-1.7 0l-2.3-2.4a1.2 1.2 0 010-1.7z"
        fill="#fff"
      />
    </svg>
  );
}

export function Sidebar() {
  const pathname = usePathname();
  const sessionUser = useStaffSession();
  const { role } = useStaffProfile();
  const displayName = staffDisplayName(sessionUser, dashboardUserDisplayName);
  const userInitials = staffInitials(displayName);
  const visibleNav = NAV.filter((item) => canAccessWithRole(role, item.roles));

  return (
    <aside
      className="group/sidebar fixed inset-y-0 left-0 z-30 flex w-[64px] flex-col overflow-hidden border-r border-slate-200/80 bg-white/90 backdrop-blur-sm transition-[width] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] hover:w-[244px] hover:shadow-[8px_0_30px_-12px_rgba(15,23,42,0.08)]"
      style={{ willChange: "width" }}
    >
      <div className="flex h-[64px] shrink-0 items-center gap-3 px-4">
        <div className="shrink-0">
          <SidebarBrandMark />
        </div>
        <div className="-translate-x-1 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
          <div className="whitespace-nowrap text-[15px] font-semibold tracking-tight text-slate-900">{dashboardAppName}</div>
          <div className="whitespace-nowrap text-[11px] font-normal text-slate-500">{dashboardAppSubtitle}</div>
        </div>
      </div>

      <nav className="mt-2 flex-1 space-y-0.5 px-2">
        {visibleNav.map((item) => {
          const Icon = item.icon;
          const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              title={item.label}
              className={`flex h-10 w-full items-center gap-3 rounded-lg px-3 text-left text-[13.5px] font-medium transition-colors ${
                active ? "bg-indigo-50 text-indigo-700" : "text-slate-500 hover:bg-slate-50 hover:text-slate-800"
              }`}
            >
              <Icon
                size={18}
                strokeWidth={active ? 2 : 1.75}
                className={`shrink-0 ${active ? "text-indigo-600" : "text-slate-400"}`}
              />
              <span className="-translate-x-1 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
                {item.label}
              </span>
            </Link>
          );
        })}
      </nav>

      <div className="px-2 pb-4">
        <div className="mb-3 flex items-center gap-2.5 overflow-hidden rounded-lg border border-slate-100 bg-slate-50 px-2.5 py-2">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-indigo-50 text-indigo-600">
            <ShieldCheck size={14} strokeWidth={2} />
          </div>
          <div className="-translate-x-1 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
            <div className="whitespace-nowrap text-[11.5px] font-semibold text-slate-800">HIPAA Compliant</div>
            <div className="whitespace-nowrap text-[10.5px] text-slate-500">SOC 2 Type II</div>
          </div>
        </div>
        <div className="border-t border-slate-100 pt-3">
          <div className="flex items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-indigo-100 text-[11px] font-bold text-indigo-700">
              {userInitials}
            </div>
            <div className="-translate-x-1 overflow-hidden opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100" style={{ transitionDelay: "80ms" }}>
              <div className="truncate whitespace-nowrap text-[12.5px] font-semibold text-slate-900">{displayName}</div>
              <div className="truncate whitespace-nowrap text-[11px] text-slate-500">
                {role ? `${role.replace("_", " ")} · ${sessionUser?.email ?? dashboardPracticeName}` : sessionUser?.email ?? dashboardPracticeName}
              </div>
            </div>
          </div>
          <form
            action="/auth/signout"
            method="post"
            className="-translate-x-1 overflow-hidden pl-12 opacity-0 transition-all duration-200 ease-out group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100"
            style={{ transitionDelay: "120ms" }}
          >
            <button
              type="submit"
              className="mt-2 whitespace-nowrap text-[11px] font-semibold text-slate-500 hover:text-indigo-600"
            >
              Sign out
            </button>
          </form>
        </div>
      </div>
    </aside>
  );
}
