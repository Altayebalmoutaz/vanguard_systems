import { Loader2 } from "lucide-react";

export default function PatientLoading() {
  return (
    <div className="ml-[60px] flex min-h-screen items-center justify-center text-[13px] text-slate-500">
      <Loader2 size={18} className="mr-2 animate-spin" />
      Loading patient profile…
    </div>
  );
}
