"""Tests for EvictingMemory + InMemoryStore tool result cache."""

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from backend.core.memory import EvictingMemory, InMemoryStore
from backend.core.memory.base import EvictedEntry
from backend.core.memory.evicting import PLACEHOLDER_MARKER


@pytest.fixture
def base_store() -> InMemoryStore:
    return InMemoryStore()


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


# ── InMemoryStore: tool result cache ────────────────────────────────


@pytest.mark.asyncio
async def test_in_memory_put_then_get(base_store: InMemoryStore) -> None:
    await base_store.put_tool_result("conv-1", "call-a", "read_file", "data")
    assert await base_store.get_tool_result("conv-1", "call-a") == "data"


@pytest.mark.asyncio
async def test_in_memory_get_missing_returns_none(base_store: InMemoryStore) -> None:
    assert await base_store.get_tool_result("conv-1", "missing") is None


@pytest.mark.asyncio
async def test_in_memory_list_tool_results(base_store: InMemoryStore) -> None:
    await base_store.put_tool_result("conv-1", "call-a", "read_file", "x" * 100)
    await base_store.put_tool_result("conv-1", "call-b", "bash_execute", "y" * 50)

    entries = await base_store.list_tool_results("conv-1")
    by_id = {e.call_id: e for e in entries}
    assert isinstance(entries[0], EvictedEntry)
    assert by_id["call-a"].tool_name == "read_file"
    assert by_id["call-a"].size == 100
    assert by_id["call-b"].size == 50


@pytest.mark.asyncio
async def test_in_memory_delete_clears_both(base_store: InMemoryStore) -> None:
    """delete() must clear messages AND tool result cache atomically."""
    await base_store.set("conv-1", [_user("hi")])
    await base_store.put_tool_result("conv-1", "call-a", "tool", "data")

    await base_store.delete("conv-1")

    assert await base_store.get("conv-1") is None
    assert await base_store.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_in_memory_list_conversations_includes_cache_only(
    base_store: InMemoryStore,
) -> None:
    """A conversation present only in the tool cache still surfaces."""
    await base_store.put_tool_result("conv-only-cache", "call-a", "tool", "data")
    convs = await base_store.list_conversations()
    assert "conv-only-cache" in convs


# ── EvictingMemory ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_short_history_passes_through_unchanged(
    base_store: InMemoryStore,
) -> None:
    mem = EvictingMemory(base_store, keep_recent=10)
    messages = [_user("hi"), _assistant_text("hello")]

    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")
    assert stored == messages


@pytest.mark.asyncio
async def test_large_old_tool_result_is_evicted(
    base_store: InMemoryStore,
) -> None:
    mem = EvictingMemory(base_store, keep_recent=2, min_size=100)
    big_content = "x" * 5000
    messages = [
        _user("read README"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big_content),
        _assistant_text("done"),
        _user("now what"),
        _assistant_text("..."),
    ]

    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")

    part = stored[2].parts[0]
    assert part.content.startswith(PLACEHOLDER_MARKER)
    assert part.tool_call_id == "call-a"
    assert "5000" in part.content

    # Original content is now in the same store's tool result cache.
    assert await mem.get_tool_result("conv-1", "call-a") == big_content


@pytest.mark.asyncio
async def test_recent_tool_result_is_not_evicted(
    base_store: InMemoryStore,
) -> None:
    mem = EvictingMemory(base_store, keep_recent=2, min_size=100)
    big = "y" * 500
    messages = [
        _user("q"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big),
        _assistant_text("done"),
    ]
    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")
    assert stored[2].parts[0].content == big
    assert await mem.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_small_tool_result_below_min_size_is_kept(
    base_store: InMemoryStore,
) -> None:
    mem = EvictingMemory(base_store, keep_recent=1, min_size=5000)
    small = "tiny"
    messages = [
        _user("q1"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", small),
        _user("q2"),
        _assistant_text("a2"),
    ]
    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")
    assert stored[2].parts[0].content == small
    assert await mem.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_placeholder_is_idempotent_on_second_eviction(
    base_store: InMemoryStore,
) -> None:
    mem = EvictingMemory(base_store, keep_recent=1, min_size=10)
    big = "z" * 200
    messages = [
        _user("q"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big),
        _user("q2"),
    ]
    await mem.set("conv-1", messages)
    first = await mem.get("conv-1")
    first_placeholder = first[2].parts[0].content

    await mem.set("conv-1", first)
    second = await mem.get("conv-1")
    assert second[2].parts[0].content == first_placeholder
    assert await mem.get_tool_result("conv-1", "call-a") == big


@pytest.mark.asyncio
async def test_tool_call_pairing_preserved(base_store: InMemoryStore) -> None:
    """tool_call_id on the evicted ToolReturnPart must still match its
    upstream ToolCallPart, otherwise OpenAI/Anthropic reject the history."""
    mem = EvictingMemory(base_store, keep_recent=1, min_size=10)
    big = "w" * 300
    messages = [
        _user("q"),
        _assistant_call("read_file", "call-xyz"),
        _tool_return("read_file", "call-xyz", big),
        _user("q2"),
    ]
    await mem.set("conv-1", messages)
    stored = await mem.get("conv-1")

    call_part = stored[1].parts[0]
    return_part = stored[2].parts[0]
    assert call_part.tool_call_id == return_part.tool_call_id == "call-xyz"


@pytest.mark.asyncio
async def test_delete_clears_cache_via_inner_store(
    base_store: InMemoryStore,
) -> None:
    mem = EvictingMemory(base_store, keep_recent=1, min_size=10)
    big = "x" * 200
    await mem.set(
        "conv-1",
        [
            _user("q"),
            _assistant_call("read_file", "call-a"),
            _tool_return("read_file", "call-a", big),
            _user("q2"),
        ],
    )
    assert await mem.get_tool_result("conv-1", "call-a") == big

    await mem.delete("conv-1")
    assert await mem.get("conv-1") is None
    assert await mem.get_tool_result("conv-1", "call-a") is None


@pytest.mark.asyncio
async def test_invalid_keep_recent_rejected(base_store: InMemoryStore) -> None:
    with pytest.raises(ValueError):
        EvictingMemory(base_store, keep_recent=-1)


@pytest.mark.asyncio
async def test_invalid_min_size_rejected(base_store: InMemoryStore) -> None:
    with pytest.raises(ValueError):
        EvictingMemory(base_store, min_size=0)
