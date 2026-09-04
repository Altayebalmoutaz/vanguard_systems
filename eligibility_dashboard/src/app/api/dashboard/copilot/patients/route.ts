import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const q = url.searchParams.get("q") ?? undefined;
  return proxyFastApi("/dashboard/copilot/patients", {
    searchParams: { q },
    timeoutMs: 30_000,
  });
}
