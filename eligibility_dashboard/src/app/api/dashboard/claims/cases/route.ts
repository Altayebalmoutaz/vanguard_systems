import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  return proxyFastApi("/dashboard/claims/cases", {
    searchParams: {
      status: searchParams.get("status") ?? undefined,
      limit: searchParams.get("limit") ?? undefined,
    },
  });
}
