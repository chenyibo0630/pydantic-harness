"""Tests for EvictingConversation (always-evict) + InMemoryConversation tool result cache."""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from backend.core.conversation import EvictingConversation, InMemoryConversation
from backend.core.conversation.base import EvictedEntry
from backend.core.conversation.evicting import PLACEHOLDER_MARKER


@pytest.fixture
def base_store() -> InMemoryConversation:
    return InMemoryConversation()


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_text(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _assistant_call(tool_name: str, call_id: str, args: str = "{}") -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id)]
    )


def _tool_return(tool_name: str, call_id: str, content: str) -> ModelRequest:
    return ModelRequest(
        parts=[
            ToolReturnPart(
                tool_name=tool_name, tool_call_id=call_id, content=content
            )
        ]
    )


# ── InMemoryConversation: tool result cache ────────────────────────────────


@pytest.mark.asyncio
async def test_in_memory_put_then_get(base_store: InMemoryConversation) -> None:
    await base_store.put_tool_result("conv-1", "call-a", "read_file", "data")
    assert await base_store.get_tool_result("conv-1", "call-a") == "data"


@pytest.mark.asyncio
async def test_in_memory_get_missing_returns_none(base_store: InMemoryConversation) -> None:
    assert await base_store.get_tool_result("conv-1", "missing") is None


@pytest.mark.asyncio
async def test_in_memory_list_tool_results(base_store: InMemoryConversation) -> None:
    await base_store.put_tool_result("conv-1", "call-a", "read_file", "x" * 100)
    await base_store.put_tool_result("conv-1", "call-b", "bash_execute", "y" * 50)

    entries = await base_store.list_tool_results("conv-1")
    by_id = {e.call_id: e for e in entries}
    assert isinstance(entries[0], EvictedEntry)
    assert by_id["call-a"].tool_name == "read_file"
    assert by_id["call-a"].size == 100
    assert by_id["call-b"].size == 50


@pytest.mark.asyncio
async def test_in_memory_delete_clears_both(base_store: InMemoryConversation) -> None:
    """delete() must clear messages AND tool result cache atomically."""
    await base_store.set("conv-1", [_user("hi")])
    await base_store.put_tool_result("conv-1", "call-a", "tool", "data")

    await base_store.delete("conv-1")

    assert await base_store.get("conv-1") is None
    assert await base_store.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_in_memory_list_conversations_includes_cache_only(
    base_store: InMemoryConversation,
) -> None:
    """A conversation present only in the tool cache still surfaces."""
    await base_store.put_tool_result("conv-only-cache", "call-a", "tool", "data")
    convs = await base_store.list_conversations()
    assert "conv-only-cache" in convs


# ── EvictingConversation: always-evict ────────────────────────────────────


@pytest.mark.asyncio
async def test_no_tool_returns_means_no_changes(base_store: InMemoryConversation) -> None:
    """A history with no tool returns must pass through unchanged."""
    mem = EvictingConversation(base_store)
    messages = [_user("hi"), _assistant_text("hello")]

    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")
    assert stored == messages


@pytest.mark.asyncio
async def test_every_large_tool_result_is_evicted(
    base_store: InMemoryConversation,
) -> None:
    """No 'recent' window — every ToolReturnPart >= min_size is evicted,
    including the very last one written in the same set() call."""
    mem = EvictingConversation(base_store, min_size=100)
    big_a = "a" * 5000
    big_b = "b" * 5000
    big_c = "c" * 5000
    messages = [
        _user("q1"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big_a),
        _user("q2"),
        _assistant_call("read_file", "call-b"),
        _tool_return("read_file", "call-b", big_b),
        _user("q3"),
        _assistant_call("read_file", "call-c"),
        _tool_return("read_file", "call-c", big_c),
        _assistant_text("done"),
    ]
    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")

    # All three tool returns are placeholders, including the newest.
    for idx, call_id, original in (
        (2, "call-a", big_a),
        (5, "call-b", big_b),
        (8, "call-c", big_c),
    ):
        part = stored[idx].parts[0]
        assert part.content.startswith(PLACEHOLDER_MARKER), f"call_id={call_id}"
        assert part.tool_call_id == call_id
        assert await mem.get_tool_result("conv-1", call_id) == original


@pytest.mark.asyncio
async def test_small_tool_result_below_min_size_is_kept(
    base_store: InMemoryConversation,
) -> None:
    """Tiny results aren't worth a placeholder — keep them inline."""
    mem = EvictingConversation(base_store, min_size=500)
    small = "ok"
    messages = [
        _user("q"),
        _assistant_call("noop", "call-a"),
        _tool_return("noop", "call-a", small),
        _assistant_text("done"),
    ]
    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")
    assert stored[2].parts[0].content == small
    assert await mem.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_placeholder_format_is_byte_stable_across_runs(
    base_store: InMemoryConversation,
) -> None:
    """The placeholder must be deterministic given the same tool/call/content
    — this is what makes the stored prefix byte-stable for prompt caching."""
    big = "z" * 2000
    msgs = [
        _user("q"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big),
    ]

    mem_a = EvictingConversation(InMemoryConversation(), min_size=100)
    mem_b = EvictingConversation(InMemoryConversation(), min_size=100)
    await mem_a.set("conv-1", list(msgs))
    await mem_b.set("conv-1", list(msgs))

    placeholder_a = (await mem_a.get("conv-1"))[2].parts[0].content
    placeholder_b = (await mem_b.get("conv-1"))[2].parts[0].content
    assert placeholder_a == placeholder_b


@pytest.mark.asyncio
async def test_idempotent_on_repeated_set(base_store: InMemoryConversation) -> None:
    """Calling set() with the same conversation twice yields byte-identical
    storage — placeholders are already placeholders the second time."""
    mem = EvictingConversation(base_store, min_size=10)
    big = "x" * 200
    msgs = [
        _user("q"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big),
    ]
    await mem.set("conv-1", msgs)
    first = await mem.get("conv-1")
    first_placeholder = first[2].parts[0].content

    await mem.set("conv-1", first)
    second = await mem.get("conv-1")
    assert second[2].parts[0].content == first_placeholder
    assert await mem.get_tool_result("conv-1", "call-a") == big


@pytest.mark.asyncio
async def test_tool_call_pairing_preserved(base_store: InMemoryConversation) -> None:
    """tool_call_id on the evicted ToolReturnPart must still match its
    upstream ToolCallPart, otherwise OpenAI/Anthropic reject the history."""
    mem = EvictingConversation(base_store, min_size=10)
    big = "w" * 300
    messages = [
        _user("q"),
        _assistant_call("read_file", "call-xyz"),
        _tool_return("read_file", "call-xyz", big),
    ]
    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")

    call_part = stored[1].parts[0]
    return_part = stored[2].parts[0]
    assert call_part.tool_call_id == return_part.tool_call_id == "call-xyz"


@pytest.mark.asyncio
async def test_delete_clears_cache_via_inner_store(
    base_store: InMemoryConversation,
) -> None:
    mem = EvictingConversation(base_store, min_size=10)
    big = "x" * 200
    await mem.set(
        "conv-1",
        [
            _user("q"),
            _assistant_call("read_file", "call-a"),
            _tool_return("read_file", "call-a", big),
        ],
    )
    assert await mem.get_tool_result("conv-1", "call-a") == big

    await mem.delete("conv-1")
    assert await mem.get("conv-1") is None
    assert await mem.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_invalid_min_size_rejected(base_store: InMemoryConversation) -> None:
    with pytest.raises(ValueError):
        EvictingConversation(base_store, min_size=0)
