import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET(request: Request) {
  const days = new URL(request.url).searchParams.get("days") ?? "7";
  return proxyFastApi(`/dashboard/pilot/shadow-summary?days=${encodeURIComponent(days)}`);
}
