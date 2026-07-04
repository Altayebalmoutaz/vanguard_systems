import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const limit = searchParams.get("limit") ?? "25";
  return proxyFastApi("/dashboard/eligibility/activity", { searchParams: { limit } });
}
