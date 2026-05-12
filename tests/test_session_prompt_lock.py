"""Tests for per-conversation system prompt locking.

The InMemoryStore now keeps a snapshot dict; the gateway's
``stream_agent_response`` resolves load-or-lock semantics on each turn.
These tests cover the storage layer plus the lock-on-first-turn behavior
without spinning a real LLM.
"""

import asyncio

import pytest

from backend.core.memory import (
    EvictingMemory,
    InMemoryStore,
    SummarizingMemory,
)


# ── InMemoryStore: snapshot store ────────────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_missing_returns_none() -> None:
    store = InMemoryStore()
    assert await store.get_system_prompt("conv-1") is None


@pytest.mark.asyncio
async def test_snapshot_round_trip() -> None:
    store = InMemoryStore()
    await store.put_system_prompt("conv-1", "frozen prompt v1")
    assert await store.get_system_prompt("conv-1") == "frozen prompt v1"


@pytest.mark.asyncio
async def test_snapshot_overwrite_within_same_conv() -> None:
    """put_system_prompt must overwrite — callers are responsible for
    calling it only once per conversation."""
    store = InMemoryStore()
    await store.put_system_prompt("conv-1", "v1")
    await store.put_system_prompt("conv-1", "v2")
    assert await store.get_system_prompt("conv-1") == "v2"


@pytest.mark.asyncio
async def test_snapshot_isolated_per_conversation() -> None:
    store = InMemoryStore()
    await store.put_system_prompt("conv-a", "alpha")
    await store.put_system_prompt("conv-b", "beta")
    assert await store.get_system_prompt("conv-a") == "alpha"
    assert await store.get_system_prompt("conv-b") == "beta"


@pytest.mark.asyncio
async def test_delete_clears_snapshot() -> None:
    store = InMemoryStore()
    await store.put_system_prompt("conv-1", "frozen")
    await store.delete("conv-1")
    assert await store.get_system_prompt("conv-1") is None


@pytest.mark.asyncio
async def test_list_conversations_includes_snapshot_only_conv() -> None:
    store = InMemoryStore()
    await store.put_system_prompt("snap-only", "x")
    assert "snap-only" in await store.list_conversations()


# ── Decorator forwarding ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evicting_memory_forwards_snapshot_calls() -> None:
    base = InMemoryStore()
    mem = EvictingMemory(base)
    await mem.put_system_prompt("conv-1", "frozen")
    assert await mem.get_system_prompt("conv-1") == "frozen"
    # Verify it actually landed in the inner store, not just the wrapper.
    assert await base.get_system_prompt("conv-1") == "frozen"


@pytest.mark.asyncio
async def test_summarizing_memory_forwards_snapshot_calls() -> None:
    base = InMemoryStore()
    mem = SummarizingMemory(base, model="test")
    await mem.put_system_prompt("conv-1", "frozen")
    assert await mem.get_system_prompt("conv-1") == "frozen"
    assert await base.get_system_prompt("conv-1") == "frozen"


@pytest.mark.asyncio
async def test_decorated_chain_locks_session() -> None:
    """The full prod chain SummarizingMemory(EvictingMemory(InMemoryStore))
    keeps the snapshot at the deepest layer."""
    base = InMemoryStore()
    mem = SummarizingMemory(EvictingMemory(base), model="test")
    await mem.put_system_prompt("conv-1", "frozen at L0")
    assert await mem.get_system_prompt("conv-1") == "frozen at L0"
    assert await base.get_system_prompt("conv-1") == "frozen at L0"


# ── Lock semantics: simulate the gateway resolution ──────────────────


async def _resolve_or_lock(store, conv_id, build) -> str:
    """Reproduces the load-or-lock helper from sse.py for unit testing."""
    snapshot = await store.get_system_prompt(conv_id)
    if snapshot is None:
        snapshot = build()
        await store.put_system_prompt(conv_id, snapshot)
    return snapshot


@pytest.mark.asyncio
async def test_first_turn_locks_snapshot() -> None:
    store = InMemoryStore()
    builds: list[int] = []

    def build() -> str:
        builds.append(1)
        return f"prompt-v{len(builds)}"

    out = await _resolve_or_lock(store, "conv-1", build)
    assert out == "prompt-v1"
    assert len(builds) == 1


@pytest.mark.asyncio
async def test_subsequent_turns_reuse_snapshot_even_if_disk_changes() -> None:
    """build() is allowed to return a different value (simulating an edited
    on-disk prompt file). Subsequent turns must NOT see the new value."""
    store = InMemoryStore()
    calls = iter(["v1", "v2-disk-was-edited", "v3-edited-again"])

    def build() -> str:
        return next(calls)

    first = await _resolve_or_lock(store, "conv-1", build)
    second = await _resolve_or_lock(store, "conv-1", build)
    third = await _resolve_or_lock(store, "conv-1", build)

    assert first == second == third == "v1"


@pytest.mark.asyncio
async def test_new_conversation_re_reads_disk() -> None:
    """A brand-new conv_id must trigger a fresh build()."""
    store = InMemoryStore()
    calls = iter(["v1", "v2"])

    def build() -> str:
        return next(calls)

    a = await _resolve_or_lock(store, "conv-a", build)
    b = await _resolve_or_lock(store, "conv-b", build)
    assert a == "v1"
    assert b == "v2"


@pytest.mark.asyncio
async def test_deleting_conversation_allows_re_lock_with_fresh_content() -> None:
    """Explicit delete clears the snapshot so the next turn re-reads."""
    store = InMemoryStore()
    calls = iter(["v1", "v2"])

    def build() -> str:
        return next(calls)

    first = await _resolve_or_lock(store, "conv-1", build)
    await store.delete("conv-1")
    second = await _resolve_or_lock(store, "conv-1", build)
    assert first == "v1"
    assert second == "v2"
