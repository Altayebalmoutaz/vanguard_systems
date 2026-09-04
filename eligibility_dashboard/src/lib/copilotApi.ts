export type CopilotChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type CopilotToolTrace = {
  name: string;
  args?: Record<string, unknown>;
};

export type CopilotDirectoryPatient = {
  patient_id: string;
  od_pat_num: number | null;
  name: string;
  subtitle: string;
  sources: string[];
};

export type CopilotDirectoryResponse = {
  ok: boolean;
  opendentalConnected: boolean;
  patients: CopilotDirectoryPatient[];
  message?: string;
};

export type CopilotChatResponse = {
  ok: boolean;
  reply?: string;
  toolTrace?: CopilotToolTrace[];
  model?: string;
  message?: string;
};

async function parseJson<T>(resp: Response): Promise<T> {
  return (await resp.json().catch(() => ({}))) as T;
}

function errorMessage(payload: { error?: unknown; detail?: unknown }, fallback: string): string {
  if (typeof payload.error === "string") return payload.error;
  if (typeof payload.detail === "string") return payload.detail;
  if (
    payload.detail &&
    typeof payload.detail === "object" &&
    "message" in payload.detail &&
    typeof payload.detail.message === "string"
  ) {
    return payload.detail.message;
  }
  return fallback;
}

export async function fetchCopilotPatients(query?: string): Promise<CopilotDirectoryResponse> {
  const params = new URLSearchParams();
  if (query && query.trim()) params.set("q", query.trim());
  const qs = params.toString();
  const resp = await fetch(`/api/dashboard/copilot/patients${qs ? `?${qs}` : ""}`, {
    cache: "no-store",
  });
  const payload = await parseJson<{
    opendental_connected?: boolean;
    patients?: CopilotDirectoryPatient[];
    error?: string;
    detail?: string;
  }>(resp);
  if (!resp.ok) {
    return {
      ok: false,
      opendentalConnected: false,
      patients: [],
      message: errorMessage(payload, "Unable to load patients."),
    };
  }
  return {
    ok: true,
    opendentalConnected: Boolean(payload.opendental_connected),
    patients: payload.patients ?? [],
  };
}

export async function postCopilotChat(
  patientId: string,
  messages: CopilotChatMessage[],
  odPatNum?: number | null,
): Promise<CopilotChatResponse> {
  const resp = await fetch("/api/dashboard/copilot/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      patient_id: patientId,
      messages,
      od_pat_num: odPatNum ?? undefined,
    }),
  });
  const payload = await parseJson<{
    reply?: string;
    tool_trace?: CopilotToolTrace[];
    model?: string;
    error?: string;
    detail?: string | { message?: string };
  }>(resp);
  if (!resp.ok) {
    return { ok: false, message: errorMessage(payload, "Copilot request failed") };
  }
  return {
    ok: true,
    reply: payload.reply ?? "",
    toolTrace: payload.tool_trace ?? [],
    model: payload.model,
  };
}
