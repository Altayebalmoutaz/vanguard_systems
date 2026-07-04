import { proxyFastApi } from "@/lib/bff/fastapiProxy";

export async function GET() {
  return proxyFastApi("/auth/me");
}
