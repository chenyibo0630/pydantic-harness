"""Memory abstraction — owns every piece of per-conversation persistent state.

A ``Memory`` implementation manages three related stores keyed by
``conversation_id``:

- **Message history**: ``list[ModelMessage]``. The conversation transcript
  in pydantic-ai's format.
- **Tool result cache**: bytes of large tool outputs that ``EvictingMemory``
  moved out of the message history. Keyed by ``(conversation_id, call_id)``.
- **System prompt snapshot**: the prompt text frozen at the first turn of
  this conversation. Subsequent turns reuse this snapshot so the system
  message stays byte-identical across the session, even if the on-disk
  prompt files change.

All three share a backing tier (in-memory ↔ in-memory, file ↔ file). Mixing
tiers would leave orphans after a restart, so the ABC keeps them together.

``delete(conversation_id)`` is responsible for clearing **all three** stores
for the conversation in a single call.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage


@dataclass(frozen=True)
class EvictedEntry:
    """Metadata for a cached tool result, returned by ``list_tool_results``."""

    call_id: str
    tool_name: str
    size: int


class Memory(ABC):
    """Abstract conversation memory store + tool result cache."""

    # ── Message history ───────────────────────────────────────────

    @abstractmethod
    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        """Retrieve message history for a conversation. None if not found."""

    @abstractmethod
    async def set(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        """Store message history for a conversation."""

    @abstractmethod
    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation's history **and** its cached tool results."""

    @abstractmethod
    async def list_conversations(self) -> list[str]:
        """List all conversation IDs (messages or tool results present)."""

    # ── Tool result cache ─────────────────────────────────────────

    @abstractmethod
    async def put_tool_result(
        self,
        conversation_id: str,
        call_id: str,
        tool_name: str,
        content: str,
    ) -> None:
        """Cache a single evicted tool result. Overwrites any existing entry."""

    @abstractmethod
    async def get_tool_result(
        self, conversation_id: str, call_id: str
    ) -> str | None:
        """Return the cached tool result content, or None if absent."""

    @abstractmethod
    async def list_tool_results(
        self, conversation_id: str
    ) -> list[EvictedEntry]:
        """List metadata for every cached tool result in the conversation."""

    # ── System prompt snapshot ────────────────────────────────────

    @abstractmethod
    async def put_system_prompt(
        self, conversation_id: str, content: str
    ) -> None:
        """Freeze the system prompt for this conversation. Subsequent calls
        for the same ``conversation_id`` overwrite — callers should only
        write once, on the first turn."""

    @abstractmethod
    async def get_system_prompt(self, conversation_id: str) -> str | None:
        """Return the frozen system prompt for this conversation, or None
        if it hasn't been locked yet (i.e. brand-new conversation)."""
