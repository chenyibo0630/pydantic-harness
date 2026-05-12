"""Tests for recall_tool_result tool function.

We invoke the tool directly (without going through pydantic-ai's dispatch)
by constructing a stub RunContext that exposes a ``deps`` attribute. This
keeps tests independent of any model/provider.
"""

from dataclasses import dataclass
from typing import Any

import pytest

from backend.core.conversation import InMemoryConversation, ConversationDeps
from backend.core.tools import recall_tool_result


@dataclass
class _StubCtx:
    """Minimal stand-in for pydantic_ai.RunContext used in unit tests."""

    deps: Any


@pytest.fixture
def store() -> InMemoryConversation:
    return InMemoryConversation()


@pytest.mark.asyncio
async def test_recall_returns_cached_content(store: InMemoryConversation) -> None:
    content = "some long file body" * 50
    await store.put_tool_result("conv-1", "call-a", "read_file", content)

    ctx = _StubCtx(deps=ConversationDeps(store=store, conversation_id="conv-1"))
    assert await recall_tool_result(ctx, "call-a") == content


@pytest.mark.asyncio
async def test_recall_missing_call_id_lists_available(
    store: InMemoryConversation,
) -> None:
    await store.put_tool_result("conv-1", "call-a", "read_file", "data")
    await store.put_tool_result("conv-1", "call-b", "bash_execute", "log")

    ctx = _StubCtx(deps=ConversationDeps(store=store, conversation_id="conv-1"))
    result = await recall_tool_result(ctx, "call-z")
    assert result.startswith("[error]")
    assert "call-a" in result
    assert "call-b" in result


@pytest.mark.asyncio
async def test_recall_when_conversation_has_no_cache(
    store: InMemoryConversation,
) -> None:
    ctx = _StubCtx(deps=ConversationDeps(store=store, conversation_id="conv-empty"))
    result = await recall_tool_result(ctx, "any-id")
    assert result.startswith("[error]")
    assert "never evicted" in result.lower() or "no cached" in result.lower()


@pytest.mark.asyncio
async def test_recall_isolated_per_conversation(store: InMemoryConversation) -> None:
    """Different conversations must not see each other's cached results."""
    await store.put_tool_result("conv-a", "call-x", "tool", "secret-a")
    await store.put_tool_result("conv-b", "call-x", "tool", "secret-b")

    ctx_a = _StubCtx(deps=ConversationDeps(store=store, conversation_id="conv-a"))
    ctx_b = _StubCtx(deps=ConversationDeps(store=store, conversation_id="conv-b"))

    assert await recall_tool_result(ctx_a, "call-x") == "secret-a"
    assert await recall_tool_result(ctx_b, "call-x") == "secret-b"


@pytest.mark.asyncio
async def test_recall_with_missing_deps_returns_error() -> None:
    ctx = _StubCtx(deps=None)
    result = await recall_tool_result(ctx, "call-a")
    assert result.startswith("[error]")
    assert "ConversationDeps" in result
