"""Memory abstraction — stores conversation history per session **and** the
side-channel cache of evicted tool results.

A ``Memory`` implementation owns two related stores:

- **Message history**: ``list[ModelMessage]`` keyed by ``conversation_id``.
  pydantic-ai uses this format for the conversation transcript.
- **Tool result cache**: bytes of large tool outputs that ``EvictingMemory``
  moved out of the message history. Keyed by ``(conversation_id, call_id)``.

Both stores share a backing tier (in-memory ↔ in-memory, file ↔ file). Mixing
tiers would leave orphans after a restart, so the ABC keeps them together.

``delete(conversation_id)`` is responsible for clearing **both** stores for
the conversation in a single call.
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
