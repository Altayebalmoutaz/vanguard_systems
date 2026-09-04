"use client";

import { Building2, Check, ChevronDown } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { fetchAuthMe } from "@/lib/dashboardApi";
import { practiceLabel } from "@/lib/practice";

type PracticeRole = { practice_id: string; role: string };

export function ClinicSwitcher() {
  const [roles, setRoles] = useState<PracticeRole[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

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
    });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, []);

  const onSelect = async (practiceId: string) => {
    if (!practiceId || practiceId === activeId || saving) {
      setOpen(false);
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
      setOpen(false);
      return;
    }
    window.location.reload();
  };

  if (!activeId && roles.length === 0) {
    return null;
  }

  const canSwitch = roles.length > 1;

  return (
    <div ref={rootRef} className="relative px-2 pb-2">
      <button
        type="button"
        disabled={!canSwitch || saving}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => {
          if (canSwitch) {
            setOpen((value) => !value);
          }
        }}
        className="flex h-9 w-full items-center gap-2 rounded-md border border-slate-200 bg-slate-50 px-2.5 text-left hover:bg-slate-100 disabled:cursor-default disabled:hover:bg-slate-50"
      >
        <Building2 size={15} className="shrink-0 text-slate-500" />
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-slate-900">
          {activeId ? practiceLabel(activeId) : "Select clinic"}
        </span>
        {canSwitch ? <ChevronDown size={14} className="shrink-0 text-slate-400" /> : null}
      </button>
      {open && canSwitch ? (
        <ul
          role="listbox"
          className="absolute inset-x-2 top-full z-40 mt-1 overflow-hidden rounded-md border border-slate-200 bg-white py-1 shadow-lg"
        >
          {roles.map((row) => {
            const selected = row.practice_id === activeId;
            return (
              <li key={row.practice_id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  disabled={saving}
                  onClick={() => {
                    void onSelect(row.practice_id);
                  }}
                  className="flex w-full items-center gap-2 px-2.5 py-2 text-left text-[13px] font-semibold text-slate-800 hover:bg-slate-50"
                >
                  <span className="min-w-0 flex-1 truncate">{practiceLabel(row.practice_id)}</span>
                  {selected ? <Check size={14} className="shrink-0 text-[var(--accent-primary)]" /> : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
