"""Runtime dependencies passed to conversation-aware tools via pydantic-ai's
``RunContext``.

Tools that need access to the per-conversation ``Conversation`` store (e.g.
``recall_tool_result``) take ``ctx: RunContext[ConversationDeps]`` as their
first parameter. The gateway constructs a fresh ``ConversationDeps`` per
request and hands it to ``Agent.run_stream_events(deps=...)``.

Why a dedicated dataclass instead of a module-level singleton: the
``conversation_id`` differs per request, and the same ``Agent`` instance
serves multiple concurrent SSE streams. ``RunContext`` is async-safe and
travels through pydantic-ai's tool dispatch correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.core.conversation.base import Conversation


@dataclass(frozen=True)
class ConversationDeps:
    """Per-request injection point for conversation-aware tools.

    ``system_prompt`` is the frozen snapshot for this conversation; the
    gateway resolves it (load-or-lock) before each turn. Agents that pass a
    callable as ``Agent(instructions=...)`` typically just return
    ``ctx.deps.system_prompt`` so the model sees a byte-identical system
    message for every LLM call within the conversation.
    """

    store: Conversation
    conversation_id: str
    system_prompt: str = ""
