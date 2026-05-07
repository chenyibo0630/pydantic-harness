"""SummarizingMemory — wraps any Memory, compresses history in background after response.

Inspired by LangChain's ConversationSummaryBufferMemory:
- On set(): save full history immediately, then fire background summarization
- On get(): return the (possibly already summarized) history
- Summarization never blocks the user-facing response

Timeline:
    User sends msg → Agent responds → SSE done → memory.set(full_history)
                                                    ├─ save immediately (user sees response)
                                                    └─ background task: summarize & overwrite
    Next request → memory.get() → returns summarized history (if ready)
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from backend.core.memory.base import Memory

logger = logging.getLogger("memory.summarizing")

_SUMMARIZE_PROMPT = """\
Summarize the following conversation concisely. \
Preserve key facts, decisions, tool results, and context the assistant needs to continue helpfully. \
Output a single paragraph in the same language as the conversation.

{previous}Conversation:
{conversation}"""


_TOOL_RESULT_PREVIEW = 400  # cap per tool result so summaries stay short


def _extract_text(msg: ModelMessage) -> str:
    """Extract readable text from a ModelMessage for summarization.

    Includes tool calls + (truncated) results so the summary preserves
    the agent's reasoning chain, not just plain chat turns.
    """
    parts: list[str] = []
    if isinstance(msg, ModelRequest):
        for p in msg.parts:
            if isinstance(p, UserPromptPart) and isinstance(p.content, str):
                parts.append(f"User: {p.content}")
            elif isinstance(p, ToolReturnPart):
                content = p.content if isinstance(p.content, str) else str(p.content)
                if len(content) > _TOOL_RESULT_PREVIEW:
                    content = content[:_TOOL_RESULT_PREVIEW] + "...(truncated)"
                parts.append(f"Tool[{p.tool_name}] result: {content}")
    elif isinstance(msg, ModelResponse):
        for p in msg.parts:
            if isinstance(p, TextPart):
                parts.append(f"Assistant: {p.content}")
            elif isinstance(p, ToolCallPart):
                args = p.args if isinstance(p.args, str) else str(p.args)
                parts.append(f"Assistant called tool[{p.tool_name}]({args})")
    return "\n".join(parts)


def _sanitize_history(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Drop orphaned tool messages that would make OpenAI/DeepSeek reject the request.

    Walks forward, tracking the set of tool_call_ids announced by the most recent
    assistant ModelResponse. Drops any ToolReturnPart whose tool_call_id is not in
    that set. Drops a request-message entirely if all of its parts get filtered out.
    """
    cleaned: list[ModelMessage] = []
    open_call_ids: set[str] = set()

    for msg in messages:
        if isinstance(msg, ModelResponse):
            ids_in_msg = {
                p.tool_call_id for p in msg.parts if isinstance(p, ToolCallPart)
            }
            if ids_in_msg:
                open_call_ids = ids_in_msg
            cleaned.append(msg)
            continue

        if isinstance(msg, ModelRequest):
            kept_parts = []
            for p in msg.parts:
                if isinstance(p, ToolReturnPart):
                    if p.tool_call_id in open_call_ids:
                        kept_parts.append(p)
                        open_call_ids.discard(p.tool_call_id)
                    # else: orphan — drop silently
                else:
                    kept_parts.append(p)
            if kept_parts:
                cleaned.append(ModelRequest(parts=kept_parts))
            continue

        cleaned.append(msg)

    return cleaned


def _find_safe_split(messages: list[ModelMessage], target_keep: int) -> int:
    """Find a slice index so messages[idx:] preserves tool_call ↔ tool_result pairs.

    Returns the smallest index >= len(messages) - target_keep at which the kept
    suffix starts with a user-turn (ModelRequest containing a UserPromptPart).
    A user turn is always a safe boundary because it cannot be the result-half
    of a tool pair. Returns len(messages) when no safe boundary exists in range.
    """
    target = max(0, len(messages) - target_keep)
    for idx in range(target, len(messages)):
        msg = messages[idx]
        if isinstance(msg, ModelRequest) and any(
            isinstance(p, UserPromptPart) for p in msg.parts
        ):
            return idx
    return len(messages)


class SummarizingMemory(Memory):
    """Memory wrapper that summarizes old messages in the background.

    Args:
        store: The underlying Memory to delegate storage to.
        model: pydantic-ai Model instance or model name string for summarization.
        threshold: Summarize when message count exceeds this.
        keep_recent: Number of recent messages to keep verbatim.
    """

    def __init__(
        self,
        store: Memory,
        *,
        model: "str | None" = None,
        threshold: int = 20,
        keep_recent: int = 10,
    ) -> None:
        self._store = store
        self._model = model
        self._threshold = threshold
        self._keep_recent = keep_recent
        self._prev_summaries: dict[str, str] = {}
        self._pending: set[str] = set()  # conversation IDs currently being summarized

    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        messages = await self._store.get(conversation_id)
        if not messages:
            return messages
        sanitized = _sanitize_history(messages)
        if len(sanitized) != len(messages):
            logger.warning(
                "conv=%s: sanitized history (%d → %d msgs, dropped orphaned tool messages)",
                conversation_id[:8], len(messages), len(sanitized),
            )
            await self._store.set(conversation_id, sanitized)
        return sanitized

    async def set(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        # Save immediately — user already has the response
        await self._store.set(conversation_id, messages)

        # Fire background summarization if needed
        if len(messages) > self._threshold and conversation_id not in self._pending:
            asyncio.create_task(self._summarize_and_save(conversation_id, messages))

    async def delete(self, conversation_id: str) -> None:
        self._prev_summaries.pop(conversation_id, None)
        self._pending.discard(conversation_id)
        await self._store.delete(conversation_id)

    async def list_conversations(self) -> list[str]:
        return await self._store.list_conversations()

    async def _summarize_and_save(self, conversation_id: str, messages: list[ModelMessage]) -> None:
        self._pending.add(conversation_id)
        try:
            split = _find_safe_split(messages, self._keep_recent)
            if split <= 0 or split >= len(messages):
                logger.warning(
                    "conv=%s: no safe split point (keep_recent=%d, total=%d), skipping",
                    conversation_id[:8], self._keep_recent, len(messages),
                )
                return

            old = messages[:split]
            recent = messages[split:]

            conv_lines = [_extract_text(m) for m in old]
            conversation = "\n".join(line for line in conv_lines if line)

            if not conversation.strip():
                logger.warning("conv=%s: no extractable text, skipping", conversation_id[:8])
                return

            previous = ""
            prev = self._prev_summaries.get(conversation_id, "")
            if prev:
                previous = f"Previous summary: {prev}\n\n"
                logger.debug("conv=%s: chaining with previous summary (%d chars)", conversation_id[:8], len(prev))

            prompt = _SUMMARIZE_PROMPT.format(previous=previous, conversation=conversation)

            logger.info(
                "conv=%s: summarizing %d messages (%d chars conversation)",
                conversation_id[:8], len(old), len(conversation),
            )

            from pydantic_ai import Agent

            summarizer = Agent(
                self._model,
                instructions="You are a conversation summarizer. Be concise but preserve all key information.",
                defer_model_check=True,
            )
            result = await summarizer.run(prompt)
            summary = result.output
            usage = result.usage()

            logger.info(
                "conv=%s: summarized %d→%d chars (tokens: %d in, %d out)",
                conversation_id[:8], len(conversation), len(summary),
                usage.input_tokens or 0, usage.output_tokens or 0,
            )

            self._prev_summaries[conversation_id] = summary

            summary_msg = ModelRequest(
                parts=[SystemPromptPart(content=f"[Conversation summary]: {summary}")]
            )

            # Overwrite stored history with compressed version
            await self._store.set(conversation_id, [summary_msg, *recent])

        except Exception:
            logger.exception("conv=%s: summarization failed, keeping original history", conversation_id[:8])
        finally:
            self._pending.discard(conversation_id)
