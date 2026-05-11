from collections.abc import Awaitable, Callable
from typing import Any

from app.runtime.context import AgentContext

ToolFn = Callable[[AgentContext, dict[str, Any]], Awaitable[dict[str, Any]]]


class ToolRegistry:
    """Maps tool name → async implementation (claw-style tool surface)."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolFn] = {}

    def register(self, name: str) -> Callable[[ToolFn], ToolFn]:
        def decorator(fn: ToolFn) -> ToolFn:
            self._tools[name] = fn
            return fn

        return decorator

    def get(self, name: str) -> ToolFn | None:
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        return sorted(self._tools.keys())


_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
        import app.tools.builtin  # noqa: F401 — registers tools on import

    return _registry
