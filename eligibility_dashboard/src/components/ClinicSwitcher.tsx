"use client";

import { Building2 } from "lucide-react";
import { useEffect, useState } from "react";

import { fetchAuthMe } from "@/lib/dashboardApi";
import { practiceLabel } from "@/lib/practice";

type PracticeRole = { practice_id: string; role: string };

export function ClinicSwitcher() {
  const [roles, setRoles] = useState<PracticeRole[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchAuthMe().then((profile) => {
      if (!alive || !profile?.practice_roles?.length) {
        return;
      }
      const extra = profile as { active_practice_id?: string };
      setRoles(profile.practice_roles);
      setActiveId(extra.active_practice_id ?? profile.practice_roles[0].practice_id);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (roles.length < 2) {
    return null;
  }

  const onChange = async (practiceId: string) => {
    if (!practiceId || practiceId === activeId || saving) {
      return;
    }
    setSaving(true);
    const resp = await fetch("/api/dashboard/practice", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ practice_id: practiceId }),
    });
    if (!resp.ok) {
      setSaving(false);
      return;
    }
    window.location.reload();
  };

  return (
    <div className="mb-2 overflow-hidden rounded-md border border-slate-100 bg-slate-50/80">
      <div className="flex items-center gap-2 px-2 py-1.5">
        <Building2 size={14} className="shrink-0 text-slate-500" />
        <label className="sr-only" htmlFor="clinic-switcher">
          Clinic
        </label>
        <select
          id="clinic-switcher"
          value={activeId}
          disabled={saving}
          onChange={(event) => {
            void onChange(event.target.value);
          }}
          className="-translate-x-1 pointer-events-none w-full min-w-0 bg-transparent text-[11px] font-semibold text-slate-700 opacity-0 outline-none group-hover/sidebar:pointer-events-auto group-hover/sidebar:translate-x-0 group-hover/sidebar:opacity-100"
          title={practiceLabel(activeId)}
        >
          {roles.map((row) => (
            <option key={row.practice_id} value={row.practice_id}>
              {practiceLabel(row.practice_id)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
