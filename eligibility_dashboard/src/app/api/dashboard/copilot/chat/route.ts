import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }
  return proxyFastApi("/dashboard/copilot/chat", {
    method: "POST",
    body,
    timeoutMs: 120_000,
  });
}
