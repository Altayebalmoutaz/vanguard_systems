"use client";

import { usePathname } from "next/navigation";

import { ClinicSwitcher } from "@/components/ClinicSwitcher";
import { Sidebar } from "@/components/Sidebar";
import { isPublicAuthPath } from "@/lib/authConfig";

export function DashboardChrome({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() ?? "/";

  if (isPublicAuthPath(pathname)) {
    return <>{children}</>;
  }

  return (
    <>
      <Sidebar />
      <ClinicSwitcher />
      <div className="pt-10">{children}</div>
    </>
  );
}
