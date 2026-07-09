import * as Sentry from "@sentry/nextjs";

// Server/edge-side Sentry init. No-op when NEXT_PUBLIC_SENTRY_DSN is unset.
export async function register() {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn) return;
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
    tracesSampleRate: 0.1,
    // PHI safety: never attach request bodies or user PII.
    sendDefaultPii: false,
  });
}

export const onRequestError = Sentry.captureRequestError;
