import { Suspense } from "react";
import { Loader2 } from "lucide-react";
import EligibilityDashboard from "@/features/eligibility/EligibilityDashboard";

export default function EligibilityPage() {
  return (
    <Suspense
      fallback={
        <div className="ml-[60px] flex min-h-screen items-center justify-center text-[13px] text-slate-500">
          <Loader2 size={18} className="mr-2 animate-spin" />
          Loading eligibility dashboard…
        </div>
      }
    >
      <EligibilityDashboard />
    </Suspense>
  );
}
