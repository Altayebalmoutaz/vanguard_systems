"use client";

import { useEffect, useState } from "react";
import type { User } from "@supabase/supabase-js";

import { fetchAuthMe } from "@/lib/dashboardApi";
import { getSupabaseBrowserClient } from "@/lib/supabase";

export type StaffRole = "admin" | "billing_lead" | "front_office" | "read_only";

const STAFF_ROLES = new Set<StaffRole>(["admin", "billing_lead", "front_office", "read_only"]);

export function useStaffSession(): User | null {
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const client = getSupabaseBrowserClient();
    if (!client) {
      return;
    }

    let active = true;

    client.auth.getUser().then(({ data }) => {
      if (active) {
        setUser(data.user);
      }
    });

    const {
      data: { subscription },
    } = client.auth.onAuthStateChange((_event, session) => {
      if (active) {
        setUser(session?.user ?? null);
      }
    });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, []);

  return user;
}

export function useStaffProfile(): {
  role: StaffRole | null;
  practiceId: string | null;
  practiceRoles: { practice_id: string; role: StaffRole }[];
  loading: boolean;
} {
  const user = useStaffSession();
  const [role, setRole] = useState<StaffRole | null>(null);
  const [practiceId, setPracticeId] = useState<string | null>(null);
  const [practiceRoles, setPracticeRoles] = useState<{ practice_id: string; role: StaffRole }[]>(
    [],
  );
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) {
      setRole(null);
      setPracticeId(null);
      setPracticeRoles([]);
      setLoading(false);
      return;
    }

    let active = true;
    void fetchAuthMe().then((profile) => {
      if (!active) return;
      const roles = profile?.practice_roles ?? [];
      if (roles.length) {
        const extra = profile as { active_practice_id?: string };
        const match =
          roles.find((row) => row.practice_id === extra.active_practice_id) ?? roles[0];
        setPracticeRoles(roles);
        setPracticeId(match.practice_id);
        setRole(coerceStaffRole(match.role));
      } else {
        setPracticeRoles([]);
        setPracticeId(null);
        setRole(staffRoleFromMetadata(user));
      }
      setLoading(false);
    });

    return () => {
      active = false;
    };
  }, [user]);

  return { role, practiceId, practiceRoles, loading };
}

export function staffDisplayName(user: User | null, fallback: string): string {
  if (!user) {
    return fallback;
  }
  const meta = user.user_metadata ?? {};
  const fromMeta =
    (typeof meta.full_name === "string" && meta.full_name) ||
    (typeof meta.name === "string" && meta.name) ||
    "";
  if (fromMeta) {
    return fromMeta;
  }
  return user.email ?? fallback;
}

export function staffInitials(displayName: string): string {
  return displayName
    .replace(/^(Dr\.?|Mr\.?|Mrs\.?|Ms\.?)\s+/i, "")
    .split(/\s+/)
    .map((part) => part.charAt(0))
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

function coerceStaffRole(value: unknown): StaffRole | null {
  if (typeof value !== "string") {
    return null;
  }
  return STAFF_ROLES.has(value as StaffRole) ? (value as StaffRole) : null;
}

function staffRoleFromMetadata(user: User): StaffRole | null {
  const appMeta = user.app_metadata ?? {};
  const userMeta = user.user_metadata ?? {};
  const directRole =
    coerceStaffRole(appMeta.role) ??
    coerceStaffRole(appMeta.practice_role) ??
    coerceStaffRole(userMeta.role) ??
    coerceStaffRole(userMeta.practice_role);
  if (directRole) {
    return directRole;
  }

  const practiceRoles = appMeta.practice_roles ?? userMeta.practice_roles;
  if (Array.isArray(practiceRoles)) {
    for (const item of practiceRoles) {
      if (typeof item === "object" && item !== null && "role" in item) {
        const role = coerceStaffRole((item as { role?: unknown }).role);
        if (role) {
          return role;
        }
      }
    }
  }

  if (typeof practiceRoles === "object" && practiceRoles !== null) {
    for (const role of Object.values(practiceRoles)) {
      const coerced = coerceStaffRole(role);
      if (coerced) {
        return coerced;
      }
    }
  }

  return null;
}

export function staffRole(user: User | null): StaffRole | null {
  if (!user) {
    return null;
  }
  return staffRoleFromMetadata(user);
}

export function canAccessWithRole(role: StaffRole | null, allowed?: readonly StaffRole[]): boolean {
  if (!allowed || allowed.length === 0) {
    return true;
  }
  // Local/dev sessions often have no role claim yet — show nav rather than a blank shell.
  if (!role) {
    return true;
  }
  return allowed.includes(role);
}
