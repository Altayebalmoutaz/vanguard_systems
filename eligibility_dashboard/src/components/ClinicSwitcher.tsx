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
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    void fetchAuthMe().then((profile) => {
      if (!alive) {
        return;
      }
      const nextRoles = profile?.practice_roles ?? [];
      const extra = profile as { active_practice_id?: string };
      setRoles(nextRoles);
      setActiveId(extra.active_practice_id ?? nextRoles[0]?.practice_id ?? "");
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

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
    <div className="fixed top-0 right-0 left-[60px] z-20 flex h-10 items-center gap-2 border-b border-slate-200 bg-white px-6">
      <Building2 size={15} className="shrink-0 text-slate-500" />
      <span className="text-[11px] font-semibold uppercase tracking-[0.04em] text-slate-500">
        Clinic
      </span>
      {!loaded ? (
        <span className="text-[13px] font-semibold text-slate-400">Loading…</span>
      ) : roles.length > 1 ? (
        <select
          id="clinic-switcher"
          value={activeId}
          disabled={saving}
          onChange={(event) => {
            void onChange(event.target.value);
          }}
          className="h-7 rounded-md border border-slate-200 bg-white px-2 text-[13px] font-semibold text-slate-900 outline-none hover:border-slate-300"
        >
          {roles.map((row) => (
            <option key={row.practice_id} value={row.practice_id}>
              {practiceLabel(row.practice_id)}
            </option>
          ))}
        </select>
      ) : (
        <span className="text-[13px] font-semibold text-slate-900">
          {activeId ? practiceLabel(activeId) : "No clinic assigned"}
        </span>
      )}
    </div>
  );
}
