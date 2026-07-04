import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const status = searchParams.get("status") ?? "pending";
  return proxyFastApi("/dashboard/hitl/tasks", { searchParams: { status } });
}
