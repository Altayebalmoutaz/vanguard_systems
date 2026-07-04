"use client";

import { usePathname } from "next/navigation";

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
      {children}
    </>
  );
}
