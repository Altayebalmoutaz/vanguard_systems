"use client";

import {
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
  useCallback,
  useRef,
  useState,
} from "react";

type SpotlightCardProps = {
  children: ReactNode;
  className?: string;
  /** Soft spotlight tint — muted indigo by default for enterprise tone. */
  glowColor?: string;
};

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return true;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Cursor-follow radial spotlight overlay. Falls back to a static soft sheen
 * when reduced-motion is preferred or the pointer leaves.
 * Uses a dedicated overlay child (not ::before) so it coexists with `.card::before`.
 */
export function SpotlightCard({
  children,
  className = "",
  glowColor = "rgba(99, 102, 241, 0.12)",
}: SpotlightCardProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x: "50%", y: "50%" });
  const [active, setActive] = useState(false);

  const onMove = useCallback((e: MouseEvent<HTMLDivElement>) => {
    if (prefersReducedMotion()) return;
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    setPos({ x: `${x}%`, y: `${y}%` });
    setActive(true);
  }, []);

  const onLeave = useCallback(() => {
    setActive(false);
    setPos({ x: "50%", y: "50%" });
  }, []);

  const style = {
    "--mx": pos.x,
    "--my": pos.y,
    "--spotlight-color": glowColor,
  } as CSSProperties;

  return (
    <div
      ref={ref}
      className={`spotlight-card ${active ? "spotlight-card--active" : ""} ${className}`}
      style={style}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
    >
      <span className="spotlight-card__glow" aria-hidden />
      {children}
    </div>
  );
}
