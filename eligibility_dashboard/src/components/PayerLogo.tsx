"use client";

import { useState } from "react";
import { findPayer } from "@/lib/payerCatalog";

export function PayerLogo({ label, showName = true }: { label: string; showName?: boolean }) {
  const payer = findPayer(label);
  const isKnown = payer.slug !== "unknown";
  const [errored, setErrored] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const showImage = isKnown && !errored;

  return (
    <div className="group flex items-center gap-2.5">
      <div
        className="relative flex h-8 w-8 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[var(--border-soft)] bg-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.6),0_1px_2px_rgba(15,23,42,0.04)] transition-shadow duration-200 group-hover:shadow-[inset_0_0_0_1px_rgba(255,255,255,0.7),0_2px_8px_-2px_rgba(15,23,42,0.12)]"
        aria-hidden
      >
        {showImage ? (
          <>
            {!loaded && (
              <span className="shimmer absolute inset-0 rounded-lg" aria-hidden />
            )}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`/payers/${payer.slug}.svg`}
              alt=""
              width={32}
              height={32}
              loading="lazy"
              decoding="async"
              onLoad={() => setLoaded(true)}
              onError={() => setErrored(true)}
              className={`h-8 w-8 object-contain transition-opacity duration-300 ${
                loaded ? "opacity-100" : "opacity-0"
              }`}
            />
          </>
        ) : (
          <div
            className="flex h-full w-full items-center justify-center text-[10px] font-semibold tracking-wide text-white"
            style={{
              background: `linear-gradient(135deg, ${payer.brandColor} 0%, ${shade(payer.brandColor, -12)} 100%)`,
            }}
          >
            {initialsOf(payer.shortName)}
          </div>
        )}
      </div>
      {showName ? (
        <span className="text-[13px] font-medium tracking-[-0.005em] text-slate-800">
          {payer.displayName}
        </span>
      ) : null}
    </div>
  );
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (parts[0] ?? "?").slice(0, 2).toUpperCase();
}

// Darken/lighten a hex color by a percent (-100..100). Used for the gradient end-stop.
function shade(hex: string, percent: number): string {
  const m = hex.replace("#", "");
  if (m.length !== 6) return hex;
  const num = parseInt(m, 16);
  const amt = Math.round((percent / 100) * 255);
  const r = clamp(((num >> 16) & 0xff) + amt);
  const g = clamp(((num >> 8) & 0xff) + amt);
  const b = clamp((num & 0xff) + amt);
  return `#${((r << 16) | (g << 8) | b).toString(16).padStart(6, "0")}`;
}

function clamp(n: number): number {
  return Math.max(0, Math.min(255, n));
}
