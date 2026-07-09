import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET(request: Request) {
  const limit = new URL(request.url).searchParams.get("limit") ?? "50";
  return proxyFastApi(`/dashboard/opendental/runs?limit=${encodeURIComponent(limit)}`);
}
