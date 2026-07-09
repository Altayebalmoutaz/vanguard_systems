import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET() {
  return proxyFastApi("/dashboard/eligibility/settings");
}

export async function PUT(request: Request) {
  let body: unknown = {};
  try {
    body = await request.json();
  } catch {
    return Response.json({ error: "invalid_json" }, { status: 400 });
  }
  return proxyFastApi("/dashboard/eligibility/settings", { method: "PUT", body });
}
