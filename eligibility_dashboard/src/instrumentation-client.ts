import * as Sentry from "@sentry/nextjs";

// Browser-side Sentry init. No-op when NEXT_PUBLIC_SENTRY_DSN is unset.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
    tracesSampleRate: 0.1,
    // PHI safety: never attach user PII; dashboard payloads can contain patient data.
    sendDefaultPii: false,
    beforeSend(event) {
      // Strip URLs' query strings defensively (may embed identifiers).
      if (event.request?.url) {
        event.request.url = event.request.url.split("?")[0];
      }
      return event;
    },
  });
}

export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
