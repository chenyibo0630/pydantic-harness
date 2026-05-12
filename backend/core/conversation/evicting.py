"""EvictingConversation — moves every large tool result out of the message history.

Tool results (file reads, bash logs, search dumps) typically dominate token
usage in long conversations. ``EvictingConversation`` wraps another
``Conversation`` and, on every ``set()``, walks the **entire** history and
replaces every ``ToolReturnPart`` whose content is at least ``min_size``
characters with a short, structured placeholder. The original bytes go into
the wrapped ``Conversation``'s tool result cache, keyed by
``(conversation_id, tool_call_id)``.

**Always-evict** semantics — not a sliding window:

We deliberately do **not** keep "recent" results inline. A sliding window
would mean the prefix bytes drift every turn as the boundary advances,
breaking Anthropic prompt cache repeatedly. With always-evict, the stored
prefix becomes byte-stable from turn 2 onwards: once every tool result is a
placeholder, the next turn's history bytes match this turn's exactly, and
prompt cache reads through.

The cost: the model only sees real bytes during the turn the call happened.
On any subsequent turn it must call ``recall_tool_result(call_id=...)`` to
re-load the original content. The placeholder includes a preview and call_id
hint to make that decision easy.

Critically, the ``tool_call_id`` and the surrounding ``ToolCallPart`` are
kept intact, so the OpenAI/Anthropic ``tool_call ↔ tool_result`` pairing the
``_sanitize_history`` logic depends on is preserved.

Placeholder format intentionally derives only from stable inputs (tool name,
call_id, content size, preview), so re-eviction on the next turn is a no-op
(idempotent).
"""

from __future__ import annotations

import logging
from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
)

from backend.core.conversation.base import Conversation, EvictedEntry

logger = logging.getLogger("conversation.evicting")

PLACEHOLDER_MARKER = "[evicted-tool-result]"


def _content_text(value: object) -> str:
    """Best-effort string view of a ToolReturnPart.content for size accounting."""
    return value if isinstance(value, str) else str(value)


def _is_placeholder(content: object) -> bool:
    return isinstance(content, str) and content.startswith(PLACEHOLDER_MARKER)


def _make_placeholder(part: ToolReturnPart, raw: str) -> str:
    """Compact, action-first summary. Lead with what was evicted, then surface
    the two possible follow-ups (recall vs re-invoke) so the model can pick
    without parsing prose."""
    size = len(raw)
    lines = raw.count("\n") + 1
    preview = raw.replace("\r", " ").replace("\n", " ")[:120].strip()
    if not preview:
        preview = "(empty)"
    return (
        f"{PLACEHOLDER_MARKER} tool={part.tool_name} "
        f"call_id={part.tool_call_id} size={size}chars lines={lines}\n"
        f"preview: {preview}\n"
        f"To reload the original bytes (past snapshot):  "
        f"recall_tool_result(call_id=\"{part.tool_call_id}\")\n"
        f"For current state of the underlying source:    "
        f"call {part.tool_name} again with the same arguments."
    )


class EvictingConversation(Conversation):
    """Conversation decorator that evicts **every** large tool result on
    every ``set()`` into the same underlying ``Conversation``'s tool result
    cache.

    Args:
        store: Underlying conversation store (handles message history and
            tool result cache on the same backing tier).
        min_size: Only evict tool results whose content is at least this many
            characters. Smaller results aren't worth a placeholder (the
            placeholder itself is ~250 chars; evicting a 100-char result
            would inflate, not shrink).
    """

    def __init__(
        self,
        store: Conversation,
        *,
        min_size: int = 256,
    ) -> None:
        if min_size <= 0:
            raise ValueError("min_size must be > 0")

        self._store = store
        self._min_size = min_size

    # ── Message history ───────────────────────────────────────────

    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        return await self._store.get(conversation_id)

    async def set(
        self, conversation_id: str, messages: list[ModelMessage]
    ) -> None:
        evicted = await self._evict(conversation_id, messages)
        await self._store.set(conversation_id, evicted)

    async def delete(self, conversation_id: str) -> None:
        # Inner store is responsible for clearing both messages and tool cache.
        await self._store.delete(conversation_id)

    async def list_conversations(self) -> list[str]:
        return await self._store.list_conversations()

    # ── Tool result cache (pure forwarding) ───────────────────────

    async def put_tool_result(
        self,
        conversation_id: str,
        call_id: str,
        tool_name: str,
        content: str,
    ) -> None:
        await self._store.put_tool_result(
            conversation_id, call_id, tool_name, content
        )

    async def get_tool_result(
        self, conversation_id: str, call_id: str
    ) -> str | None:
        return await self._store.get_tool_result(conversation_id, call_id)

    async def list_tool_results(
        self, conversation_id: str
    ) -> list[EvictedEntry]:
        return await self._store.list_tool_results(conversation_id)

    # ── System prompt snapshot (pure forwarding) ─────────────────

    async def put_system_prompt(
        self, conversation_id: str, content: str
    ) -> None:
        await self._store.put_system_prompt(conversation_id, content)

    async def get_system_prompt(self, conversation_id: str) -> str | None:
        return await self._store.get_system_prompt(conversation_id)

    # ── Eviction ──────────────────────────────────────────────────

    async def _evict(
        self, conversation_id: str, messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        result: list[ModelMessage] = []
        evicted_count = 0
        freed_chars = 0

        for msg in messages:
            if not isinstance(msg, ModelRequest):
                result.append(msg)
                continue

            new_parts = list(msg.parts)
            mutated = False
            for i, part in enumerate(msg.parts):
                if not isinstance(part, ToolReturnPart):
                    continue
                if _is_placeholder(part.content):
                    continue
                raw = _content_text(part.content)
                if len(raw) < self._min_size:
                    continue

                try:
                    await self._store.put_tool_result(
                        conversation_id,
                        part.tool_call_id,
                        part.tool_name,
                        raw,
                    )
                except Exception:
                    logger.exception(
                        "conv=%s call_id=%s: cache write failed, keeping inline",
                        conversation_id[:8],
                        part.tool_call_id,
                    )
                    continue

                placeholder = _make_placeholder(part, raw)
                new_parts[i] = replace(part, content=placeholder)
                mutated = True
                evicted_count += 1
                freed_chars += len(raw) - len(placeholder)

            result.append(ModelRequest(parts=new_parts) if mutated else msg)

        if evicted_count:
            logger.info(
                "conv=%s: evicted %d tool result(s), freed ~%d chars",
                conversation_id[:8],
                evicted_count,
                freed_chars,
            )
        return result
