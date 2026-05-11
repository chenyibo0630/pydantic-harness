"""EvictingMemory — moves old tool results out of the message history.

Tool results (file reads, bash logs, search dumps) typically dominate token
usage in long conversations. ``EvictingMemory`` wraps another ``Memory`` and,
on every ``set()``, walks the history looking for old ``ToolReturnPart``
entries large enough to be worth evicting. For each one it:

1. Persists the full content to the **same** ``Memory``'s tool result cache,
   keyed by ``(conversation_id, tool_call_id)``.
2. Replaces the part's ``content`` in-history with a short, structured
   placeholder that names the tool, points at the cache, and tells the model
   how to recall the original.

Critically, the ``tool_call_id`` and the surrounding ``ToolCallPart`` are kept
intact, so the OpenAI/Anthropic ``tool_call ↔ tool_result`` pairing the
``_sanitize_history`` logic depends on is preserved.

Placeholder format intentionally avoids the raw original content so re-eviction
on the next turn becomes a no-op (idempotent).
"""

from __future__ import annotations

import logging
from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ToolReturnPart,
)

from backend.core.memory.base import EvictedEntry, Memory

logger = logging.getLogger("memory.evicting")

PLACEHOLDER_MARKER = "[evicted-tool-result]"


def _content_text(value: object) -> str:
    """Best-effort string view of a ToolReturnPart.content for size accounting."""
    return value if isinstance(value, str) else str(value)


def _is_placeholder(content: object) -> bool:
    return isinstance(content, str) and content.startswith(PLACEHOLDER_MARKER)


def _make_placeholder(part: ToolReturnPart, raw: str) -> str:
    """Compact summary keeping enough metadata for the model to decide whether
    to recall."""
    size = len(raw)
    lines = raw.count("\n") + 1
    preview = raw.replace("\r", " ").replace("\n", " ")[:120].strip()
    if not preview:
        preview = "(empty)"
    return (
        f"{PLACEHOLDER_MARKER} tool={part.tool_name} "
        f"call_id={part.tool_call_id} size={size}chars lines={lines}\n"
        f"preview: {preview}\n"
        f"Original tool output was moved to the cache to save context. "
        f"NOTE: this is a snapshot of a past call, NOT current state. "
        f"For fresh data, call the original tool again. "
        f"To reload this exact snapshot, call recall_tool_result(call_id=\"{part.tool_call_id}\")."
    )


class EvictingMemory(Memory):
    """Memory decorator that evicts large old tool results into the same
    underlying ``Memory``'s tool result cache.

    Args:
        store: Underlying memory (handles both message history and tool
            result cache — same backing tier for both).
        keep_recent: Messages within the last ``keep_recent`` are never
            touched (the live working window).
        min_size: Only evict tool results whose content is at least this many
            characters. Small results aren't worth a placeholder.
    """

    def __init__(
        self,
        store: Memory,
        *,
        keep_recent: int = 10,
        min_size: int = 2000,
    ) -> None:
        if keep_recent < 0:
            raise ValueError("keep_recent must be >= 0")
        if min_size <= 0:
            raise ValueError("min_size must be > 0")

        self._store = store
        self._keep_recent = keep_recent
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

    # ── Eviction ──────────────────────────────────────────────────

    async def _evict(
        self, conversation_id: str, messages: list[ModelMessage]
    ) -> list[ModelMessage]:
        if len(messages) <= self._keep_recent:
            return messages

        cutoff = len(messages) - self._keep_recent
        result: list[ModelMessage] = []
        evicted_count = 0
        freed_chars = 0

        for idx, msg in enumerate(messages):
            if idx >= cutoff or not isinstance(msg, ModelRequest):
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
