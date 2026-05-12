"""recall_tool_result tool — reloads a previously evicted tool result.

Lives here (not in the conversation module) so all agent-facing tools are
gathered under a single location. Runtime state — the active
``Conversation`` store and the current ``conversation_id`` — is injected via
pydantic-ai's ``RunContext`` rather than a process-global singleton, so
concurrent SSE streams stay isolated.
"""

from __future__ import annotations

import logging

from pydantic_ai import RunContext

from backend.core.conversation import ConversationDeps

logger = logging.getLogger(__name__)


async def recall_tool_result(
    ctx: RunContext[ConversationDeps], call_id: str
) -> str:
    """Reload a tool result that was previously evicted from this
    conversation's history to save context.

    When an old tool output is too large to keep inline, the message history
    shows a placeholder like
    ``[evicted-tool-result] tool=read_file call_id=call_a3f size=8421chars
    preview: ...`` in place of the original content. Pass that ``call_id``
    here to retrieve the exact original bytes.

    WHEN TO USE:
    - You need to re-examine the specific content captured at the time
      of that earlier call (e.g. continuing analysis of a code snippet
      already read).

    WHEN NOT TO USE:
    - You need *current* state of a file, search, or command — call the
      original tool again. recall returns a snapshot; the underlying source
      may have changed since.
    - The 120-char ``preview`` in the placeholder is enough to remind you
      of the content. Don't recall unnecessarily; it refills context.
    - Multiple placeholders exist and you'd recall them all "just in case".
      Recall only the one(s) you actually need to inspect again.
    - The content is already in the recent (non-evicted) part of the
      conversation. Recall would duplicate it.

    On error (unknown ``call_id``, no cache for this conversation, etc.) the
    tool returns an ``[error] ...`` string listing the available ``call_id``
    values so you can self-correct.

    Args:
        call_id: The ``tool_call_id`` from the placeholder message
            (e.g. ``"call_a3f..."``). Copy it verbatim.
    """
    try:
        deps = ctx.deps
        if deps is None:
            return "[error] recall_tool_result requires ConversationDeps but none was provided."

        content = await deps.store.get_tool_result(
            deps.conversation_id, call_id
        )
        if content is None:
            available = await deps.store.list_tool_results(deps.conversation_id)
            if not available:
                return (
                    f"[error] No cached tool results in this conversation. "
                    f"call_id={call_id!r} was never evicted."
                )
            ids = ", ".join(e.call_id for e in available[:10])
            return (
                f"[error] No cached tool result for call_id={call_id!r}. "
                f"Available call_ids: {ids}"
            )
        return content
    except Exception as e:
        logger.exception(
            "recall_tool_result(call_id=%r) failed: %s", call_id, e
        )
        return f"[error] {type(e).__name__}: {e}"
