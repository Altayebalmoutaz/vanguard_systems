import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET() {
  return proxyFastApi("/dashboard/eligibility/queue");
}
