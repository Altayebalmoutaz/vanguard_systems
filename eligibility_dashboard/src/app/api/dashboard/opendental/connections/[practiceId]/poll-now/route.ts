import { proxyFastApi } from "@/lib/bff/fastapiProxy";

type RouteParams = { params: Promise<{ practiceId: string }> };

export async function POST(_request: Request, { params }: RouteParams) {
  const { practiceId } = await params;
  return proxyFastApi(
    `/dashboard/opendental/connections/${encodeURIComponent(practiceId)}/poll-now`,
    { method: "POST", body: {} },
  );
}
