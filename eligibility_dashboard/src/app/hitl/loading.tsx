import { Loader2 } from "lucide-react";

export default function HitlLoading() {
  return (
    <div className="ml-[64px] flex min-h-screen items-center justify-center text-[13px] text-slate-500">
      <Loader2 size={18} className="mr-2 animate-spin" />
      Loading HITL inbox…
    </div>
  );
}
