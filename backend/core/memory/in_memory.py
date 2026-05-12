"""In-memory conversation store — simple dict-based, lost on restart.

Holds message history, the evicted tool result cache, and the per-session
system prompt snapshot. Same tier (RAM) for all three keeps restart
semantics consistent: a process restart clears everything atomically, no
orphan entries.
"""

from pydantic_ai.messages import ModelMessage

from backend.core.memory.base import EvictedEntry, Memory


class InMemoryStore(Memory):
    def __init__(self) -> None:
        self._messages: dict[str, list[ModelMessage]] = {}
        self._tool_results: dict[str, dict[str, dict[str, str]]] = {}
        self._system_prompts: dict[str, str] = {}

    # ── Message history ───────────────────────────────────────────

    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        return self._messages.get(conversation_id)

    async def set(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        self._messages[conversation_id] = messages

    async def delete(self, conversation_id: str) -> None:
        self._messages.pop(conversation_id, None)
        self._tool_results.pop(conversation_id, None)
        self._system_prompts.pop(conversation_id, None)

    async def list_conversations(self) -> list[str]:
        return list(
            {
                *self._messages.keys(),
                *self._tool_results.keys(),
                *self._system_prompts.keys(),
            }
        )

    # ── Tool result cache ─────────────────────────────────────────

    async def put_tool_result(
        self,
        conversation_id: str,
        call_id: str,
        tool_name: str,
        content: str,
    ) -> None:
        bucket = self._tool_results.setdefault(conversation_id, {})
        bucket[call_id] = {"tool_name": tool_name, "content": content}

    async def get_tool_result(
        self, conversation_id: str, call_id: str
    ) -> str | None:
        entry = self._tool_results.get(conversation_id, {}).get(call_id)
        return entry["content"] if entry else None

    async def list_tool_results(
        self, conversation_id: str
    ) -> list[EvictedEntry]:
        bucket = self._tool_results.get(conversation_id, {})
        return [
            EvictedEntry(
                call_id=cid,
                tool_name=entry["tool_name"],
                size=len(entry["content"]),
            )
            for cid, entry in bucket.items()
        ]

    # ── System prompt snapshot ────────────────────────────────────

    async def put_system_prompt(
        self, conversation_id: str, content: str
    ) -> None:
        self._system_prompts[conversation_id] = content

    async def get_system_prompt(self, conversation_id: str) -> str | None:
        return self._system_prompts.get(conversation_id)
