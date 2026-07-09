"use client";

export default function EligibilityError({
  error,
  reset,
}: {
  error: Error;
  reset: () => void;
}) {
  return (
    <div className="ml-[60px] flex min-h-screen flex-col items-center justify-center gap-3 px-6 text-center">
      <h1 className="text-[18px] font-semibold text-slate-900">
        Eligibility dashboard failed to load
      </h1>
      <p className="max-w-md text-[13px] text-slate-600">{error.message}</p>
      <button
        type="button"
        onClick={reset}
        className="rounded-lg bg-[var(--accent-primary)] px-4 py-2 text-[13px] font-semibold text-white hover:bg-[var(--accent-primary-hover)]"
      >
        Try again
      </button>
    </div>
  );
}
