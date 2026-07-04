"use client";

import { useSyncExternalStore } from "react";

const noopSubscribe = () => () => {};

/**
 * Returns `serverFallback` during SSR and the first client paint, then the real
 * `getClient()` value once hydrated. This keeps time/locale-dependent values
 * (clocks, greetings) from causing hydration mismatches without calling
 * `setState` inside an effect. `getClient` must return a primitive (compared
 * with Object.is) so repeated calls don't trigger render loops.
 */
export function useClientValue<T>(getClient: () => T, serverFallback: T): T {
  return useSyncExternalStore(noopSubscribe, getClient, () => serverFallback);
}
