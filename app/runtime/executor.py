from typing import Any

from app.runtime.context import AgentContext
from app.tools.registry import get_tool_registry


class UnknownAgentError(KeyError):
    pass


async def invoke_tool(ctx: AgentContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    reg = get_tool_registry()
    fn = reg.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown_tool:{name}"}
    result = await fn(ctx, arguments)
    ctx.trace_tool(name, arguments, result)
    return result


async def run_agent(agent_id: str, payload: dict[str, Any], ctx: AgentContext) -> dict[str, Any]:
    """
    Agent entrypoint. Today this is deterministic (no LLM): each agent is a small workflow.
    Later, an LLM can choose tools; this function becomes the outer loop.
    """
    if agent_id == "prior_auth":
        return await _agent_prior_auth(ctx, payload)

    if agent_id == "coding_agent_demo":
        return await _agent_coding_demo(ctx, payload)

    raise UnknownAgentError(agent_id)


async def _agent_prior_auth(ctx: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
    await invoke_tool(
        ctx,
        "log_agent_event",
        {
            "agent_id": "prior_auth",
            "step": "start",
            "payload": {
                "patient_id": payload.get("patient_id"),
                "cpt_code": payload.get("cpt_code"),
            },
            "practice_id": payload.get("practice_id"),
        },
    )

    eligibility = {"eligible": True, "payer": "Aetna"}
    auth_result = {"status": "approved", "auth_id": "AUTH123"}

    await invoke_tool(
        ctx,
        "log_agent_event",
        {
            "agent_id": "prior_auth",
            "step": "completed",
            "payload": {"eligibility": eligibility, "auth": auth_result},
            "practice_id": payload.get("practice_id"),
        },
    )

    return {
        "agent_id": "prior_auth",
        "step": "completed",
        "eligibility": eligibility,
        "auth": auth_result,
        "tool_trace": ctx.tool_trace,
    }


async def _agent_coding_demo(ctx: AgentContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Minimal harness demo: ping + list tools + optional DB log."""
    message = payload.get("message", "hello")
    await invoke_tool(ctx, "ping", {"message": message})
    tools = await invoke_tool(ctx, "list_registered_tools", {})
    await invoke_tool(
        ctx,
        "log_agent_event",
        {
            "agent_id": "coding_agent_demo",
            "step": "demo_completed",
            "payload": {"message": message, "tools": tools.get("tools")},
            "practice_id": payload.get("practice_id"),
        },
    )
    return {
        "agent_id": "coding_agent_demo",
        "message": message,
        "registered_tools": tools.get("tools"),
        "tool_trace": ctx.tool_trace,
    }
