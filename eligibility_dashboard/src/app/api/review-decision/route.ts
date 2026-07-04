import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function POST(request: Request) {
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }
  return proxyFastApi("/review-decision", {
    method: "POST",
    body,
  });
}
