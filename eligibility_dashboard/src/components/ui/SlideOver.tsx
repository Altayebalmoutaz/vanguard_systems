"use client";

import { X } from "lucide-react";
import { useEffect, type ReactNode } from "react";

export function SlideOver({
  open,
  onClose,
  children,
  width = 440,
}: {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  width?: number;
}) {
  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fade-in fixed inset-0 z-40 bg-slate-900/25 backdrop-blur-sm"
      onClick={onClose}
    >
      <aside
        className="slide-in-right absolute right-0 top-0 flex h-full flex-col overflow-y-auto border-l border-slate-200 bg-white shadow-[-12px_0px_32px_-12px_rgba(15,23,42,0.18)]"
        style={{ animationDuration: "0.32s", width }}
        onClick={(event) => event.stopPropagation()}
      >
        <button
          className="absolute right-5 top-5 z-10 text-slate-500 transition hover:text-indigo-600"
          onClick={onClose}
          aria-label="Close panel"
        >
          <X size={18} />
        </button>
        <div className="flex h-full flex-col px-6 pb-6 pt-16">{children}</div>
      </aside>
    </div>
  );
}
