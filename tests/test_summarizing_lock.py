"""Tests for SummarizingConversation's synchronous-lock semantics.

Synchronous compaction means: when ``set()`` triggers a summary, the next
turn's ``get()`` must block until the summary has been written to the
store. The pre-lock design was fire-and-forget, which could race with
new turns or with ``delete()``.

These tests use a stub Conversation that lets us slow down the inner
``set()`` so we can interleave operations deterministically.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from backend.core.conversation import (
    InMemoryConversation,
    SummarizingConversation,
)
from backend.core.conversation.base import Conversation, EvictedEntry
from backend.core.conversation.summarizing import SUMMARY_PREFIX


# ── Stub a slow summarizer ─────────────────────────────────────────


class _StubAgent:
    """Stand-in for pydantic_ai.Agent. Returns a fixed summary after a
    configurable async delay so tests can interleave concurrent ops."""

    def __init__(self, *args, **kwargs) -> None:
        pass  # ignore model / instructions / defer_model_check

    async def run(self, prompt: str) -> Any:
        # Yield control so other coroutines can race.
        await _StubAgent._delay()

        class _Result:
            output = "## Active Task\nNone.\n\n## Completed Actions\n[stub]"

            def usage(self) -> Any:  # noqa: D401
                class _U:
                    input_tokens = 100
                    output_tokens = 50

                return _U()

        return _Result()

    _delay_seconds: float = 0.0

    @classmethod
    async def _delay(cls) -> None:
        if cls._delay_seconds > 0:
            await asyncio.sleep(cls._delay_seconds)


@pytest.fixture(autouse=True)
def _patch_agent(monkeypatch):
    """Replace pydantic_ai.Agent with _StubAgent inside summarizing.py for
    every test in this file."""
    import pydantic_ai

    monkeypatch.setattr(pydantic_ai, "Agent", _StubAgent)
    yield
    _StubAgent._delay_seconds = 0.0


# ── Helpers ────────────────────────────────────────────────────────


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _build_long_history(n: int) -> list[ModelMessage]:
    """``n`` alternating user/assistant pairs, length 2n."""
    msgs: list[ModelMessage] = []
    for i in range(n):
        msgs.append(_user(f"u{i}"))
        msgs.append(_assistant(f"a{i}"))
    return msgs


# ── Tests ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_below_threshold_skips_summary() -> None:
    """Small histories don't trigger compaction at all."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)

    msgs = _build_long_history(5)  # 10 messages — below threshold
    await mem.set("conv-1", msgs)

    stored = await mem.get("conv-1")
    assert len(stored) == 10  # untouched


@pytest.mark.asyncio
async def test_set_above_threshold_compacts_inline() -> None:
    """Once threshold is passed, set() must return only after the summary
    has been written to the store (no fire-and-forget)."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)

    msgs = _build_long_history(15)  # 30 messages — above threshold
    await mem.set("conv-1", msgs)

    stored = await mem.get("conv-1")
    # First element should be the summary (SystemPromptPart with SUMMARY_PREFIX).
    head = stored[0]
    assert isinstance(head, ModelRequest)
    assert isinstance(head.parts[0], SystemPromptPart)
    assert head.parts[0].content.startswith(SUMMARY_PREFIX)
    # Remaining messages should equal the recent-keep slice.
    assert len(stored) == 1 + 10  # one summary + last 10 user-turns kept


@pytest.mark.asyncio
async def test_get_blocks_on_in_flight_compaction() -> None:
    """While set() is busy summarizing, a concurrent get() must wait — not
    see the uncompacted intermediate state."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)
    msgs = _build_long_history(15)

    # Make the summarizer slow so we have time to race.
    _StubAgent._delay_seconds = 0.20

    set_task = asyncio.create_task(mem.set("conv-1", msgs))
    # Yield so set() acquires the lock before we attempt get().
    await asyncio.sleep(0.01)
    assert not set_task.done()

    get_task = asyncio.create_task(mem.get("conv-1"))
    # get() should still be pending — lock is held.
    await asyncio.sleep(0.05)
    assert not get_task.done()

    # Once set() completes, get() proceeds.
    await set_task
    stored = await get_task
    # The state get() returns must be the post-compaction state, not the
    # 30-message intermediate.
    head = stored[0]
    assert head.parts[0].content.startswith(SUMMARY_PREFIX)
    assert len(stored) == 11


@pytest.mark.asyncio
async def test_delete_blocks_on_in_flight_compaction() -> None:
    """delete() must wait for an in-flight compaction so the post-compaction
    state can't accidentally resurrect the conversation."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)
    msgs = _build_long_history(15)

    _StubAgent._delay_seconds = 0.20

    set_task = asyncio.create_task(mem.set("conv-1", msgs))
    await asyncio.sleep(0.01)
    assert not set_task.done()

    del_task = asyncio.create_task(mem.delete("conv-1"))
    await asyncio.sleep(0.05)
    assert not del_task.done()

    await set_task
    await del_task
    assert await mem.get("conv-1") is None


@pytest.mark.asyncio
async def test_back_to_back_sets_serialize() -> None:
    """Two set() calls for the same conv must serialize — the second one
    sees the post-compaction state, not the pre-compaction one."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)

    _StubAgent._delay_seconds = 0.10

    msgs1 = _build_long_history(15)  # 30 msgs → triggers compaction
    msgs2 = _build_long_history(15) + [_user("u_new"), _assistant("a_new")]

    t1 = asyncio.create_task(mem.set("conv-1", msgs1))
    # Schedule second set before first completes.
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(mem.set("conv-1", msgs2))

    await asyncio.gather(t1, t2)

    # End state must reflect the SECOND set(): its messages were saved
    # (then compacted again). Either way the SUMMARY_PREFIX dominates head;
    # the key requirement is that the conversation didn't get clobbered.
    stored = await mem.get("conv-1")
    assert stored is not None
    head = stored[0]
    assert head.parts[0].content.startswith(SUMMARY_PREFIX)


@pytest.mark.asyncio
async def test_summarizer_failure_keeps_original_history() -> None:
    """If the summarizer raises, the inner store keeps the uncompacted
    history that set() saved first — never half-written state."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)
    msgs = _build_long_history(15)

    class _BrokenAgent(_StubAgent):
        async def run(self, prompt: str) -> Any:
            raise RuntimeError("LLM exploded")

    import pydantic_ai

    # Swap in a broken agent ONLY for this test.
    original = pydantic_ai.Agent
    pydantic_ai.Agent = _BrokenAgent
    try:
        # set() must NOT propagate — it catches the exception.
        await mem.set("conv-1", msgs)
    finally:
        pydantic_ai.Agent = original

    stored = await mem.get("conv-1")
    # No summary head — the original 30 messages are preserved verbatim.
    assert len(stored) == 30
    assert isinstance(stored[0].parts[0], UserPromptPart)


@pytest.mark.asyncio
async def test_different_conversations_dont_block_each_other() -> None:
    """Each conv has its own lock — slow compaction in conv-A must not
    stall ops on conv-B."""
    base = InMemoryConversation()
    mem = SummarizingConversation(base, model="stub", threshold=20, keep_recent=10)

    _StubAgent._delay_seconds = 0.30

    msgs = _build_long_history(15)
    # Start a slow compaction on conv-A.
    slow_task = asyncio.create_task(mem.set("conv-A", msgs))

    # Meanwhile conv-B should complete its operations without waiting.
    await asyncio.sleep(0.01)
    await mem.set("conv-B", _build_long_history(2))  # below threshold, fast
    assert not slow_task.done()  # A still summarizing
    stored_b = await mem.get("conv-B")
    assert stored_b is not None
    assert not slow_task.done()  # A still summarizing — proves no cross-conv block

    await slow_task
