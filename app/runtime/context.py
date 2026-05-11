from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from supabase import Client


@dataclass
class AgentContext:
    """Per-request context passed into the agent runtime (like claw's session + deps)."""

    settings: Settings
    supabase: Client | None = None
    practice_id: str | None = None
    # Append-only trace of tool calls for this run (good for learning / debugging)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)

    def trace_tool(self, name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        self.tool_trace.append({"tool": name, "arguments": arguments, "result": result})
