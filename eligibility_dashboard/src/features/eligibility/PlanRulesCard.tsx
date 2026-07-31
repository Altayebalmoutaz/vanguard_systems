"use client";

import { StatusPill } from "@/components/ui/StatusPill";
import type {
  VobDetails,
  VobFrequencyLimitation,
  VobWaitingPeriod,
} from "@/lib/types";
import { AlertTriangle, CalendarClock, ListChecks, Scale } from "lucide-react";

const CDT_LABELS: Record<string, string> = {
  D0120: "Periodic oral evaluation",
  D0140: "Limited oral evaluation",
  D0150: "Comprehensive oral evaluation",
  D0180: "Comprehensive periodontal evaluation",
  D0210: "Intraoral - complete series",
  D0220: "Intraoral - periapical first",
  D0270: "Bitewings - single image",
  D0272: "Bitewings - two images",
  D0273: "Bitewings - three images",
  D0274: "Bitewings - four images",
  D0330: "Panoramic radiographic image",
  D1110: "Prophylaxis - adult",
  D1120: "Prophylaxis - child",
  D1206: "Topical fluoride varnish",
  D2391: "Resin composite - one surface",
  D2392: "Resin composite - two surfaces",
  D2393: "Resin composite - three surfaces",
  D2740: "Crown - porcelain/ceramic",
  D2750: "Crown - porcelain fused to metal",
  D2950: "Core buildup, including pins",
  D3330: "Endodontic therapy - molar",
  D4341: "Scaling & root planing - per quadrant",
  D4355: "Full mouth debridement",
  D4910: "Periodontal maintenance",
};

export function serviceLabelFor(code: string | null | undefined): string | null {
  if (!code) return null;
  const upper = code.trim().toUpperCase();
  return CDT_LABELS[upper] ? `${upper} – ${CDT_LABELS[upper]}` : upper;
}

export function titleCaseCategory(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  if (trimmed === trimmed.toUpperCase() && /[A-Z]/.test(trimmed)) {
    return trimmed
      .toLowerCase()
      .split(/[_\s]+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }
  return trimmed;
}

/** Collapse whitespace, soften ALL-CAPS payer copy, truncate for display. */
export function humanizePayerText(
  value: string | null | undefined,
  maxLen = 120,
): { text: string; full: string } | null {
  if (!value) return null;
  const collapsed = value.replace(/\s+/g, " ").trim();
  if (!collapsed) return null;
  let text = collapsed;
  const letters = text.replace(/[^A-Za-z]/g, "");
  if (letters.length >= 8 && letters === letters.toUpperCase()) {
    text = text.charAt(0).toUpperCase() + text.slice(1).toLowerCase();
  }
  if (text.length <= maxLen) {
    return { text, full: collapsed };
  }
  return { text: `${text.slice(0, maxLen - 1).trimEnd()}…`, full: collapsed };
}

function parseIsoDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const text = value.trim();
  if (!text) return null;
  const iso = text.includes("T") ? text.split("T", 1)[0]! : text.slice(0, 10);
  const d = new Date(`${iso}T00:00:00`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatFriendlyDate(value: string | Date | null | undefined): string | null {
  const d = value instanceof Date ? value : parseIsoDate(value);
  if (!d) return null;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function addMonths(date: Date, months: number): Date {
  const next = new Date(date.getTime());
  next.setMonth(next.getMonth() + months);
  return next;
}

function formatFrequencyRule(row: VobFrequencyLimitation): string | null {
  const qty = row.quantity;
  const months = row.period_months;
  if (qty != null && months != null && months > 0) {
    const unit =
      row.quantity_qualifier && /visit/i.test(row.quantity_qualifier)
        ? qty === 1
          ? "visit"
          : "visits"
        : qty === 1
          ? "time"
          : "times";
    return `${qty} ${unit} per ${months} months`;
  }
  if (qty != null && row.time_period) {
    return `${qty} per ${row.time_period}`;
  }
  const humanized = humanizePayerText(row.description);
  return humanized?.text ?? null;
}

function subjectLabel(
  cdt: string | null | undefined,
  category: string | null | undefined,
): string {
  return serviceLabelFor(cdt) ?? titleCaseCategory(category) ?? "Benefit";
}

type FrequencyRowView = {
  key: string;
  subject: string;
  rule: string;
  detail: string | null;
  title?: string;
};

function buildFrequencyRows(vob: VobDetails): FrequencyRowView[] {
  const limits = vob.frequency_limitations ?? [];
  const lastServices = vob.last_service_dates ?? [];
  const usedLast = new Set<number>();
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const out: FrequencyRowView[] = [];

  for (let i = 0; i < limits.length; i++) {
    const limit = limits[i]!;
    const rule = formatFrequencyRule(limit);
    if (!rule) continue;
    const subject = subjectLabel(limit.cdt_code, limit.category);
    let lastIdx = -1;
    if (limit.cdt_code) {
      lastIdx = lastServices.findIndex(
        (ls, idx) =>
          !usedLast.has(idx) &&
          (ls.cdt_code || "").toUpperCase() === limit.cdt_code!.toUpperCase(),
      );
    }
    if (lastIdx < 0 && limit.category) {
      lastIdx = lastServices.findIndex(
        (ls, idx) =>
          !usedLast.has(idx) &&
          (ls.category || "").toUpperCase() === limit.category!.toUpperCase(),
      );
    }

    let detail: string | null = null;
    if (lastIdx >= 0) {
      usedLast.add(lastIdx);
      const ls = lastServices[lastIdx]!;
      const lastDate = parseIsoDate(ls.service_date);
      const lastLabel = formatFriendlyDate(lastDate);
      if (lastDate && lastLabel) {
        const months = limit.period_months;
        if (months != null && months > 0) {
          const next = addMonths(lastDate, months);
          const nextLabel = formatFriendlyDate(next);
          if (next <= today) {
            detail = `Last ${ls.cdt_code || subject}: ${lastLabel} · Eligible now`;
          } else {
            detail = `Last ${ls.cdt_code || subject}: ${lastLabel} · Next eligible ${nextLabel}`;
          }
        } else {
          detail = `Last ${ls.cdt_code || subject}: ${lastLabel}`;
        }
      }
    }

    const fallback = humanizePayerText(limit.description);
    out.push({
      key: `freq-${i}`,
      subject,
      rule,
      detail,
      title: fallback?.full,
    });
  }
  return out;
}

type WaitingRowView = {
  key: string;
  subject: string;
  statusLabel: string;
  tone: "success" | "warn" | "info" | "neutral";
  title?: string;
};

function buildWaitingRows(vob: VobDetails): WaitingRowView[] {
  const periods = vob.waiting_periods ?? [];
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const out: WaitingRowView[] = [];

  for (let i = 0; i < periods.length; i++) {
    const row: VobWaitingPeriod = periods[i]!;
    const subject = subjectLabel(row.cdt_code, row.category);
    const end = parseIsoDate(row.end_date);
    const fallback = humanizePayerText(row.description);
    if (!end && row.months == null && !fallback) continue;

    if (end) {
      const endLabel = formatFriendlyDate(end);
      if (end <= today) {
        out.push({
          key: `wait-${i}`,
          subject,
          statusLabel: "Satisfied",
          tone: "success",
          title: fallback?.full,
        });
      } else {
        out.push({
          key: `wait-${i}`,
          subject,
          statusLabel: endLabel ? `Until ${endLabel}` : "Pending",
          tone: "warn",
          title: fallback?.full,
        });
      }
      continue;
    }

    if (row.months != null) {
      out.push({
        key: `wait-${i}`,
        subject,
        statusLabel: `${row.months} month${row.months === 1 ? "" : "s"}`,
        tone: "info",
        title: fallback?.full,
      });
      continue;
    }

    out.push({
      key: `wait-${i}`,
      subject,
      statusLabel: fallback!.text,
      tone: "neutral",
      title: fallback?.full,
    });
  }
  return out;
}

function leftoverLastServiceDates(vob: VobDetails): Array<{
  key: string;
  label: string;
}> {
  const limits = vob.frequency_limitations ?? [];
  const lastServices = vob.last_service_dates ?? [];
  const matched = new Set<number>();

  for (const limit of limits) {
    if (limit.cdt_code) {
      const idx = lastServices.findIndex(
        (ls, i) =>
          !matched.has(i) &&
          (ls.cdt_code || "").toUpperCase() === limit.cdt_code!.toUpperCase(),
      );
      if (idx >= 0) matched.add(idx);
    } else if (limit.category) {
      const idx = lastServices.findIndex(
        (ls, i) =>
          !matched.has(i) &&
          (ls.category || "").toUpperCase() === limit.category!.toUpperCase(),
      );
      if (idx >= 0) matched.add(idx);
    }
  }

  return lastServices
    .map((ls, idx) => {
      if (matched.has(idx)) return null;
      const dateLabel = formatFriendlyDate(ls.service_date);
      if (!dateLabel) return null;
      const subject = subjectLabel(ls.cdt_code, ls.category);
      return { key: `lsd-${idx}`, label: `${subject}: ${dateLabel}` };
    })
    .filter((row): row is { key: string; label: string } => row != null);
}

function hasPlanRulesContent(vob: VobDetails | null | undefined): boolean {
  if (!vob) return false;
  if ((vob.frequency_limitations?.length ?? 0) > 0) return true;
  if ((vob.waiting_periods?.length ?? 0) > 0) return true;
  if (vob.missing_tooth_clause?.present) return true;
  if ((vob.age_limits?.length ?? 0) > 0) return true;
  if ((vob.downgrades?.length ?? 0) > 0) return true;
  if (vob.ortho_age_cutoff != null) return true;
  return false;
}

export function PlanRulesCard({ vob }: { vob: VobDetails | null | undefined }) {
  if (!hasPlanRulesContent(vob)) return null;
  const details = vob!;
  const frequencyRows = buildFrequencyRows(details);
  const waitingRows = buildWaitingRows(details);
  const leftoverDates = leftoverLastServiceDates(details);
  const missing = details.missing_tooth_clause;
  const ageLimits = details.age_limits ?? [];
  const downgrades = details.downgrades ?? [];

  return (
    <div className="card p-5">
      <div className="mb-3 flex items-center gap-2">
        <ListChecks size={16} className="text-indigo-600" strokeWidth={2} />
        <h4 className="text-[13px] font-semibold text-slate-900">
          Plan rules &amp; limits
        </h4>
      </div>
      <div className="space-y-4 border-t border-slate-100 pt-3">
        {missing?.present ? (
          <div
            className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-[12.5px] text-amber-900"
            title={missing.description || undefined}
          >
            <AlertTriangle size={14} className="mt-0.5 shrink-0 text-amber-600" />
            <div>
              <div className="font-semibold">Missing tooth clause applies</div>
              <div className="mt-0.5 text-[11.5px] text-amber-800/90">
                Confirm tooth history before prosthetic treatment.
              </div>
            </div>
          </div>
        ) : null}

        {frequencyRows.length ? (
          <section>
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
              <CalendarClock size={12} />
              Frequency limits
            </div>
            <ul className="space-y-2">
              {frequencyRows.map((row) => (
                <li
                  key={row.key}
                  className="rounded-lg bg-slate-50 px-3 py-2"
                  title={row.title}
                >
                  <div className="text-[12.5px] font-medium text-slate-900">
                    {row.subject}
                  </div>
                  <div className="mt-0.5 text-[12px] text-slate-700">{row.rule}</div>
                  {row.detail ? (
                    <div className="mt-1 text-[11.5px] text-slate-500">
                      {row.detail}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {waitingRows.length ? (
          <section>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
              Waiting periods
            </div>
            <ul className="space-y-2">
              {waitingRows.map((row) => (
                <li
                  key={row.key}
                  className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2"
                  title={row.title}
                >
                  <span className="min-w-0 truncate text-[12.5px] font-medium text-slate-900">
                    {row.subject}
                  </span>
                  <StatusPill tone={row.tone} label={row.statusLabel} size="sm" />
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {ageLimits.length || details.ortho_age_cutoff != null ? (
          <section>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
              Age limits
            </div>
            <ul className="space-y-1.5">
              {details.ortho_age_cutoff != null ? (
                <li className="text-[12.5px] text-slate-700">
                  Orthodontics through age {details.ortho_age_cutoff}
                </li>
              ) : null}
              {ageLimits.map((row, idx) => {
                const subject = subjectLabel(row.cdt_code, row.category);
                let label: string | null = null;
                if (row.age_min != null && row.age_max != null) {
                  label = `${subject}: ages ${row.age_min}–${row.age_max}`;
                } else if (row.age_max != null) {
                  label = `${subject}: up to age ${row.age_max}`;
                } else if (row.age_min != null) {
                  label = `${subject}: from age ${row.age_min}`;
                } else {
                  label = humanizePayerText(row.description)?.text ?? null;
                }
                if (!label) return null;
                return (
                  <li
                    key={`age-${idx}`}
                    className="text-[12.5px] text-slate-700"
                    title={row.description || undefined}
                  >
                    {label}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        {downgrades.length ? (
          <section>
            <div className="mb-2 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
              <Scale size={12} />
              Alternate benefits
            </div>
            <ul className="space-y-1.5">
              {downgrades.map((row, idx) => {
                let label: string | null = null;
                if (row.cdt_from && row.cdt_to) {
                  label = `${row.cdt_from} → ${row.cdt_to}`;
                } else {
                  label = humanizePayerText(row.description)?.text ?? null;
                }
                if (!label) return null;
                const subject = titleCaseCategory(row.category);
                return (
                  <li
                    key={`dg-${idx}`}
                    className="text-[12.5px] text-slate-700"
                    title={row.description || undefined}
                  >
                    {subject ? (
                      <span className="font-medium text-slate-900">{subject}: </span>
                    ) : null}
                    {label}
                  </li>
                );
              })}
            </ul>
          </section>
        ) : null}

        {leftoverDates.length ? (
          <section>
            <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">
              Other last service dates
            </div>
            <ul className="list-disc space-y-0.5 pl-4 text-[12.5px] text-slate-700">
              {leftoverDates.map((row) => (
                <li key={row.key}>{row.label}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </div>
  );
}
