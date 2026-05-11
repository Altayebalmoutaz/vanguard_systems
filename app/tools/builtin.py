import json
from typing import Any

from app.integrations.db_tables import CODING_LOG
from app.runtime.context import AgentContext
from app.tools.registry import get_tool_registry

registry = get_tool_registry()


@registry.register("ping")
async def tool_ping(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    """Health-style tool: proves the tool loop works."""
    msg = args.get("message", "pong")
    return {"ok": True, "echo": msg, "has_database": ctx.supabase is not None}


@registry.register("log_agent_event")
async def tool_log_agent_event(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    """Persist a trace row to `coding_log` (hosted schema has no `agent_run_log`)."""
    if ctx.supabase is None:
        return {"ok": False, "skipped": True, "reason": "supabase_not_configured"}

    agent_id = args.get("agent_id", "unknown")
    envelope = {
        "agent_id": agent_id,
        "step": args.get("step"),
        "payload": args.get("payload"),
        "practice_id": args.get("practice_id") or ctx.practice_id,
    }
    row = {
        "department": "agent_harness",
        "coder_name": str(agent_id)[:120],
        "clinical_note": json.dumps(envelope, default=str),
        "status": str(args.get("step") or "event")[:120],
    }
    res = ctx.supabase.table(CODING_LOG).insert(row).execute()
    return {"ok": True, "inserted": getattr(res, "data", res)}


@registry.register("list_registered_tools")
async def tool_list_registered_tools(ctx: AgentContext, args: dict[str, Any]) -> dict[str, Any]:
    _ = args
    return {"tools": registry.list_names()}
