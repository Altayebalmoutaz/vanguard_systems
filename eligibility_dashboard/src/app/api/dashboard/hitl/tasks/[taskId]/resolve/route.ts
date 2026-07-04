import { proxyFastApi } from "@/lib/bff/fastapiProxy";

type RouteParams = { params: Promise<{ taskId: string }> };

export async function POST(request: Request, { params }: RouteParams) {
  const { taskId } = await params;
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }
  return proxyFastApi(`/dashboard/hitl/tasks/${encodeURIComponent(taskId)}/resolve`, {
    method: "POST",
    body,
  });
}
