"""Memory abstraction — stores conversation history per session.

Implementations must support get/set/delete for message lists.
pydantic-ai uses list[ModelMessage] as its history format.
"""

from abc import ABC, abstractmethod

from pydantic_ai.messages import ModelMessage


class Memory(ABC):
    """Abstract conversation memory store."""

    @abstractmethod
    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        """Retrieve message history for a conversation. None if not found."""

    @abstractmethod
    async def set(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        """Store message history for a conversation."""

    @abstractmethod
    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation's history."""

    @abstractmethod
    async def list_conversations(self) -> list[str]:
        """List all conversation IDs."""
