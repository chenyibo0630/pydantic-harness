"""prepare_tools hooks — dynamically filter tools before each model request.

Usage in agent.py:
    from backend.core.hooks import safe_mode_filter

    Agent(
        prepare_tools=safe_mode_filter(blocked=["bash_execute", "write_file"]),
    )
"""

from collections.abc import Callable

from pydantic_ai.tools import RunContext, ToolDefinition


def safe_mode_filter(
    *,
    blocked: list[str] | None = None,
    allowed: list[str] | None = None,
) -> Callable:
    """Create a prepare_tools hook that filters tools by name.

    Args:
        blocked: Tool names to exclude (blocklist mode).
        allowed: Tool names to keep (allowlist mode, takes precedence).
    """

    def _filter(
        ctx: RunContext, tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        if allowed is not None:
            return [t for t in tool_defs if t.name in allowed]
        if blocked is not None:
            return [t for t in tool_defs if t.name not in blocked]
        return tool_defs

    return _filter
