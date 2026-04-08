"""In-memory conversation store — simple dict-based, lost on restart."""

from pydantic_ai.messages import ModelMessage

from backend.core.memory.base import Memory


class InMemoryStore(Memory):
    def __init__(self) -> None:
        self._store: dict[str, list[ModelMessage]] = {}

    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        return self._store.get(conversation_id)

    async def set(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        self._store[conversation_id] = messages

    async def delete(self, conversation_id: str) -> None:
        self._store.pop(conversation_id, None)

    async def list_conversations(self) -> list[str]:
        return list(self._store.keys())
