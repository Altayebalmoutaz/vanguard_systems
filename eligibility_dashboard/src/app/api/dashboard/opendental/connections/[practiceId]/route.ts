import { proxyFastApi } from "@/lib/bff/fastapiProxy";

type RouteParams = { params: Promise<{ practiceId: string }> };

export async function PUT(request: Request, { params }: RouteParams) {
  const { practiceId } = await params;
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }
  return proxyFastApi(`/dashboard/opendental/connections/${encodeURIComponent(practiceId)}`, {
    method: "PUT",
    body,
  });
}
