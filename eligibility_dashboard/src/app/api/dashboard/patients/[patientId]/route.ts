import { proxyFastApi } from "@/lib/bff/fastapiProxy";

type RouteParams = { params: Promise<{ patientId: string }> };

export async function GET(_request: Request, { params }: RouteParams) {
  const { patientId } = await params;
  return proxyFastApi(`/dashboard/patients/${patientId}`);
}
