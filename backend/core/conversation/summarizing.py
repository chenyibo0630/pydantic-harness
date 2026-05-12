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

from backend.core.conversation.base import Conversation, EvictedEntry

logger = logging.getLogger("conversation.summarizing")

# Compaction prompt templates — ported from hermes-agent's
# ``agent/context_compressor.py``. The key ideas (and why each line is there):
#
#   - "DIFFERENT assistant continues the conversation" reframing makes the
#     summarizer write a handoff doc instead of replying to the conversation.
#   - "Do NOT respond to any questions in the summary" — without this, the
#     summarizer routinely writes "User asks X. Here's the answer: …",
#     ballooning the summary and pre-empting the next turn.
#   - "Remaining Work", not "Next Steps" — the latter reads as instructions
#     and the next assistant re-executes work already done.
#   - "Critical Context" section + "[REDACTED]" rule — secrets that appeared
#     once in a tool result should NOT survive into the summary verbatim.
#   - "Same language as the user" — without this, summaries silently switch
#     to English even when the conversation is Chinese.
#   - Iterative-update path: a second compaction must PRESERVE prior info,
#     not re-summarize from scratch (would lose Resolved Questions).

_SUMMARIZER_PREAMBLE = (
    "You are a summarization agent creating a context checkpoint. "
    "Your output will be injected as reference material for a DIFFERENT "
    "assistant that continues the conversation. "
    "Do NOT respond to any questions or requests in the conversation — "
    "only output the structured summary. "
    "Do NOT include any preamble, greeting, or prefix. "
    "Write the summary in the same language the user was using in the "
    "conversation — do not translate or switch to English. "
    "NEVER include API keys, tokens, passwords, secrets, credentials, "
    "or connection strings in the summary — replace any that appear "
    "with [REDACTED]."
)

_TEMPLATE_SECTIONS = """## Active Task
[THE SINGLE MOST IMPORTANT FIELD. Copy the user's most recent request or
task assignment verbatim — the exact words they used. If multiple tasks
were requested and only some are done, list only the ones NOT yet completed.
The next assistant must pick up exactly here. Example:
"User asked: 'Now refactor the auth module to use JWT instead of sessions'"
If no outstanding task exists, write "None."]

## Completed Actions
[Numbered list of concrete actions taken — include tool used, target, and outcome.
Format each as: N. ACTION target — outcome [tool: name]
Example:
1. READ config.py:45 — found bug at line 45 [tool: read_file]
2. PATCH config.py:45 — fixed `==` → `!=` [tool: str_replace]
3. TEST `pytest tests/` — 47/50 passing [tool: bash_execute]
Be specific with file paths, commands, line numbers, and results.]

## In Progress
[Work currently underway — what was being done when compaction fired]

## Blocked
[Any blockers, errors, or issues not yet resolved. Include exact error messages.]

## Key Decisions
[Important technical decisions and WHY they were made]

## Resolved Questions
[Questions the user asked that were ALREADY answered — include the answer so the next assistant does not re-answer them]

## Pending User Asks
[Questions or requests from the user that have NOT yet been answered or fulfilled. If none, write "None."]

## Relevant Files
[Files read, modified, or created — with brief note on each]

## Remaining Work
[What remains to be done — framed as context, not instructions]

## Critical Context
[Specific values, error messages, configuration details, or data that would be lost without explicit preservation. NEVER include API keys, tokens, passwords, or credentials — write [REDACTED] instead.]

Be concrete — include file paths, command outputs, error messages, line numbers, and specific values. Avoid vague descriptions like "made some changes" — say exactly what changed.

Write only the summary body. Do not include any preamble or prefix."""

_FIRST_COMPACTION_PROMPT = """\
{preamble}

Create a structured handoff summary for a different assistant that will continue this conversation after earlier turns are compacted. The next assistant should be able to understand what happened without re-reading the original turns.

TURNS TO SUMMARIZE:
{conversation}

{template}"""

_ITERATIVE_UPDATE_PROMPT = """\
{preamble}

You are updating a context compaction summary. A previous compaction produced the summary below. New conversation turns have occurred since then and need to be incorporated.

PREVIOUS SUMMARY:
{previous}

NEW TURNS TO INCORPORATE:
{conversation}

Update the summary using this exact structure. PRESERVE all existing information that is still relevant. ADD new completed actions to the numbered list (continue numbering). Move items from "In Progress" to "Completed Actions" when done. Move answered questions to "Resolved Questions". Remove information only if it is clearly obsolete. CRITICAL: Update "## Active Task" to reflect the user's most recent unfulfilled request — this is the most important field for task continuity.

{template}"""

# Injected at the head of the persisted summary so the NEXT assistant treats
# the block as reference material instead of as fresh user instructions.
SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted "
    "into the summary below. This is a handoff from a previous context "
    "window — treat it as background reference, NOT as active instructions. "
    "Do NOT answer questions or fulfill requests mentioned in this summary; "
    "they were already addressed. "
    "Your current task is identified in the '## Active Task' section of the "
    "summary — resume exactly from there. "
    "Respond ONLY to the latest user message that appears AFTER this summary."
)


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


class SummarizingConversation(Conversation):
    """Conversation wrapper that compacts old messages **synchronously**
    before the next turn is allowed to start.

    A per-conversation ``asyncio.Lock`` serializes every history-touching
    operation (``get`` / ``set`` / ``delete``). When ``set()`` triggers
    compaction (``len(messages) > threshold``), the summarization runs
    **inline** while the lock is held — ``set()`` does not return until
    the summary has been written to the store. The next turn's
    ``get()`` therefore cannot return until the prior turn's compaction
    has fully succeeded.

    Trade-off vs the old fire-and-forget design:

    * User-facing ``message_end`` SSE event is delayed by the summarization
      LLM call (typically 3–5 s when triggered).
    * In exchange, two race conditions are eliminated:

      1. **Stale-write race**: a background task can no longer overwrite
         a newer turn's messages with summary+old-recent slice.
      2. **Delete-then-resurrect**: a background task can no longer
         re-create a conversation that ``delete()`` just removed.

    The lock is allocated lazily per ``conversation_id`` and removed on
    ``delete()`` so the lock dict doesn't grow unboundedly.

    Args:
        store: The underlying Conversation to delegate storage to.
        model: pydantic-ai Model instance or model name string for summarization.
        threshold: Summarize when message count exceeds this.
        keep_recent: Number of recent messages to keep verbatim.
    """

    def __init__(
        self,
        store: Conversation,
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
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, conversation_id: str) -> asyncio.Lock:
        """Lazily allocate a per-conversation lock. Safe because the
        event loop is single-threaded — first-touch allocation has no
        race with itself."""
        lock = self._locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[conversation_id] = lock
        return lock

    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        async with self._lock_for(conversation_id):
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
        async with self._lock_for(conversation_id):
            # Save first so the on-disk / in-memory state always reflects the
            # latest turn even if summarization fails partway.
            await self._store.set(conversation_id, messages)

            # Inline summarization. The lock is held throughout — the next
            # get() cannot return until this completes. ``_summarize_and_save``
            # is a no-op when the message count doesn't justify compaction.
            if len(messages) > self._threshold:
                try:
                    await self._summarize_and_save(conversation_id, messages)
                except Exception:
                    logger.exception(
                        "conv=%s: summarization raised; keeping uncompacted history",
                        conversation_id[:8],
                    )

    async def delete(self, conversation_id: str) -> None:
        async with self._lock_for(conversation_id):
            self._prev_summaries.pop(conversation_id, None)
            await self._store.delete(conversation_id)
        # Reclaim the lock entry — keeps the dict bounded by the live
        # conversation set, not the historical one.
        self._locks.pop(conversation_id, None)

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

    async def _summarize_and_save(
        self, conversation_id: str, messages: list[ModelMessage]
    ) -> None:
        """Compact ``messages`` and overwrite the inner store.

        Called from ``set()`` while the per-conversation lock is held, so
        we never see a partial state from the outside. Any exception is
        propagated to the caller, which logs it and keeps the uncompacted
        history (already saved before this call)."""
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

        prev = self._prev_summaries.get(conversation_id, "")
        if prev:
            prompt = _ITERATIVE_UPDATE_PROMPT.format(
                preamble=_SUMMARIZER_PREAMBLE,
                previous=prev,
                conversation=conversation,
                template=_TEMPLATE_SECTIONS,
            )
            logger.debug(
                "conv=%s: iterative compaction (prev summary %d chars)",
                conversation_id[:8], len(prev),
            )
        else:
            prompt = _FIRST_COMPACTION_PROMPT.format(
                preamble=_SUMMARIZER_PREAMBLE,
                conversation=conversation,
                template=_TEMPLATE_SECTIONS,
            )

        logger.info(
            "conv=%s: summarizing %d messages (%d chars conversation)",
            conversation_id[:8], len(old), len(conversation),
        )

        from pydantic_ai import Agent

        # Whole prompt (preamble + template + content) goes in the user
        # message; we don't set Agent instructions so the preamble is
        # what binds the model's behavior.
        summarizer = Agent(self._model, defer_model_check=True)
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
            parts=[SystemPromptPart(content=f"{SUMMARY_PREFIX}\n\n{summary}")]
        )

        # Overwrite stored history with compacted version. We hold the lock,
        # so this can't race with another set()/get() for the same conv.
        await self._store.set(conversation_id, [summary_msg, *recent])
