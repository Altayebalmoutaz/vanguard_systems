import { proxyFastApi } from "@/lib/bff/fastapiProxy";

type RouteParams = { params: Promise<{ requestId: string }> };

export async function POST(_request: Request, { params }: RouteParams) {
  const { requestId } = await params;
  return proxyFastApi(
    `/dashboard/eligibility/requests/${encodeURIComponent(requestId)}/voice/queue`,
    { method: "POST" },
  );
}
