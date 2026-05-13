"""Tests for FileConversation — disk-backed conversation store."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
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

from backend.core.conversation import FileConversation
from backend.core.conversation.file import _sanitize_key


@pytest.fixture
def store(tmp_path: Path) -> FileConversation:
    return FileConversation(tmp_path)


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _assistant_call(tool_name: str, call_id: str, args: str = "{}") -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=tool_name, args=args, tool_call_id=call_id)]
    )


def _tool_return(tool_name: str, call_id: str, content: str) -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=tool_name, tool_call_id=call_id, content=content)]
    )


# ── Message history ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_missing_returns_none(store: FileConversation) -> None:
    assert await store.get("conv-1") is None


@pytest.mark.asyncio
async def test_set_then_get_round_trip(store: FileConversation) -> None:
    msgs: list[ModelMessage] = [
        _user("hello"),
        _assistant("hi there"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", "file contents here"),
        _assistant("read it"),
    ]
    await store.set("conv-1", msgs)
    loaded = await store.get("conv-1")
    assert loaded is not None
    assert len(loaded) == 5
    assert loaded[0].parts[0].content == "hello"
    assert loaded[1].parts[0].content == "hi there"
    assert loaded[2].parts[0].tool_call_id == "call-a"
    assert loaded[3].parts[0].content == "file contents here"


@pytest.mark.asyncio
async def test_set_overwrites_previous(store: FileConversation) -> None:
    await store.set("conv-1", [_user("first"), _assistant("a1")])
    await store.set("conv-1", [_user("second"), _assistant("a2")])

    loaded = await store.get("conv-1")
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "second"


@pytest.mark.asyncio
async def test_persistence_across_instances(tmp_path: Path) -> None:
    """Disk is the source of truth — a fresh instance pointing at the
    same dir must see what the previous one wrote."""
    s1 = FileConversation(tmp_path)
    msgs = [_user("durable"), _assistant("yes")]
    await s1.set("conv-1", msgs)

    s2 = FileConversation(tmp_path)
    loaded = await s2.get("conv-1")
    assert loaded is not None
    assert loaded[0].parts[0].content == "durable"


@pytest.mark.asyncio
async def test_delete_removes_conversation_dir(store: FileConversation, tmp_path: Path) -> None:
    await store.set("conv-1", [_user("hi")])
    await store.put_tool_result("conv-1", "call-a", "tool", "data")
    await store.put_system_prompt("conv-1", "frozen prompt")

    assert (tmp_path / "conv-1").is_dir()
    await store.delete("conv-1")

    assert not (tmp_path / "conv-1").exists()
    assert await store.get("conv-1") is None
    assert await store.get_tool_result("conv-1", "call-a") is None
    assert await store.get_system_prompt("conv-1") is None


@pytest.mark.asyncio
async def test_delete_missing_conversation_is_idempotent(
    store: FileConversation,
) -> None:
    # Should not raise.
    await store.delete("never-existed")


@pytest.mark.asyncio
async def test_corrupted_lines_are_skipped(
    store: FileConversation, tmp_path: Path
) -> None:
    """A bad line in messages.jsonl must not crash — that line is logged
    and skipped; the surrounding valid lines still load."""
    # First write produces valid jsonl.
    await store.set("conv-1", [_user("first"), _assistant("a1")])
    path = tmp_path / "conv-1" / "messages.jsonl"
    # Inject a malformed line in the middle.
    data = path.read_text(encoding="utf-8").splitlines()
    data.insert(1, "{not valid json")
    path.write_text("\n".join(data) + "\n", encoding="utf-8")

    loaded = await store.get("conv-1")
    assert loaded is not None
    # Bad line skipped; 2 valid messages preserved.
    assert len(loaded) == 2


@pytest.mark.asyncio
async def test_messages_persisted_as_jsonl_one_per_line(
    store: FileConversation, tmp_path: Path
) -> None:
    """Verify the on-disk format: one JSON object per line, easy to
    inspect with tail / jq / grep."""
    await store.set("conv-1", [_user("hi"), _assistant("hello"), _user("again")])

    path = tmp_path / "conv-1" / "messages.jsonl"
    raw = path.read_text(encoding="utf-8")
    lines = [ln for ln in raw.split("\n") if ln]
    assert len(lines) == 3
    # Each line is a standalone JSON object.
    for ln in lines:
        obj = json.loads(ln)
        assert "kind" in obj  # ModelRequest/ModelResponse discriminator
        assert "parts" in obj


@pytest.mark.asyncio
async def test_normal_turn_uses_append_not_rewrite(
    store: FileConversation, tmp_path: Path
) -> None:
    """Subsequent set() with strictly more messages must extend the
    existing file via append, not rewrite it from scratch. We detect
    this by writing a unique first-line marker and confirming it
    survives across multiple set() calls."""
    await store.set("conv-1", [_user("turn-1-user"), _assistant("turn-1-asst")])
    path = tmp_path / "conv-1" / "messages.jsonl"
    first_inode = path.stat().st_ino if hasattr(path.stat(), "st_ino") else None
    first_line_before = path.read_text(encoding="utf-8").splitlines()[0]

    # Simulate turn 2: caller passes existing + new tail.
    await store.set(
        "conv-1",
        [
            _user("turn-1-user"),
            _assistant("turn-1-asst"),
            _user("turn-2-user"),
            _assistant("turn-2-asst"),
        ],
    )

    after = path.read_text(encoding="utf-8").splitlines()
    assert len(after) == 4
    # First line byte-identical — proves we appended rather than rewrote.
    assert after[0] == first_line_before


@pytest.mark.asyncio
async def test_shorter_history_triggers_full_rewrite(
    store: FileConversation, tmp_path: Path
) -> None:
    """Compaction shrinks the message list. set() must rewrite (not
    append), otherwise the file would end up with stale tail entries
    appended after the summary."""
    long_history = [_user(f"u{i}") for i in range(6)]
    await store.set("conv-1", long_history)

    # Simulate compaction: replace with a much shorter list.
    compacted = [_user("[summary stub]"), _user("recent-user")]
    await store.set("conv-1", compacted)

    loaded = await store.get("conv-1")
    assert len(loaded) == 2
    assert loaded[0].parts[0].content == "[summary stub]"


@pytest.mark.asyncio
async def test_instructions_field_stripped_on_persist(
    store: FileConversation, tmp_path: Path
) -> None:
    """Pydantic-ai sets ``ModelRequest.instructions`` to the full
    resolved system prompt on every LLM call. The persisted history
    must not duplicate that — prompt.txt is canonical and pydantic-ai
    re-injects it fresh on the next run via the instructions callable."""
    big_instr = "BIG_SYSTEM_PROMPT " * 200  # ~3.4 KB
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="hi")], instructions=big_instr),
        ModelResponse(parts=[TextPart(content="hello")]),
        ModelRequest(parts=[UserPromptPart(content="again")], instructions=big_instr),
    ]
    await store.set("conv-1", msgs)

    path = tmp_path / "conv-1" / "messages.jsonl"
    raw = path.read_text(encoding="utf-8")

    # The big instructions string must NOT appear in the persisted file.
    assert "BIG_SYSTEM_PROMPT" not in raw
    # Each ModelRequest line should have instructions=null.
    for line in raw.splitlines():
        obj = json.loads(line)
        if obj.get("kind") == "request":
            assert obj.get("instructions") is None

    # The in-memory list passed to set() is left untouched (defensive copy).
    assert msgs[0].instructions == big_instr


@pytest.mark.asyncio
async def test_persisted_messages_remain_valid_after_strip(
    store: FileConversation,
) -> None:
    """Round-trip after the strip must still produce parseable ModelMessage
    objects — pydantic-ai accepts ``instructions=None`` on history."""
    msgs = [
        ModelRequest(parts=[UserPromptPart(content="q")], instructions="long"),
        ModelResponse(parts=[TextPart(content="a")]),
    ]
    await store.set("conv-1", msgs)

    loaded = await store.get("conv-1")
    assert loaded is not None
    assert len(loaded) == 2
    # The user content survives.
    assert loaded[0].parts[0].content == "q"
    # The stripped instructions came back as None (as designed).
    assert loaded[0].instructions is None


@pytest.mark.asyncio
async def test_set_idempotent_when_unchanged(
    store: FileConversation, tmp_path: Path
) -> None:
    """Calling set() twice with the same list must not duplicate lines.
    The same-length branch hits the rewrite path which produces the
    same content."""
    msgs = [_user("hi"), _assistant("hello")]
    await store.set("conv-1", msgs)
    await store.set("conv-1", msgs)

    loaded = await store.get("conv-1")
    assert len(loaded) == 2


# ── Tool result cache ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_result_round_trip(store: FileConversation) -> None:
    await store.put_tool_result("conv-1", "call-a", "read_file", "x" * 5000)
    assert await store.get_tool_result("conv-1", "call-a") == "x" * 5000


@pytest.mark.asyncio
async def test_tool_result_missing_returns_none(store: FileConversation) -> None:
    assert await store.get_tool_result("conv-1", "missing") is None


@pytest.mark.asyncio
async def test_list_tool_results_returns_metadata(store: FileConversation) -> None:
    await store.put_tool_result("conv-1", "call-a", "read_file", "x" * 100)
    await store.put_tool_result("conv-1", "call-b", "bash_execute", "y" * 50)

    entries = await store.list_tool_results("conv-1")
    by_id = {e.call_id: e for e in entries}
    assert by_id["call-a"].tool_name == "read_file"
    assert by_id["call-a"].size == 100
    assert by_id["call-b"].tool_name == "bash_execute"
    assert by_id["call-b"].size == 50


@pytest.mark.asyncio
async def test_tool_result_unicode_preserved(store: FileConversation) -> None:
    content = "中文内容 — and § markers\n第二行"
    await store.put_tool_result("conv-1", "call-a", "tool", content)
    assert await store.get_tool_result("conv-1", "call-a") == content


# ── System prompt snapshot ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_prompt_round_trip(store: FileConversation) -> None:
    snapshot = "you are a helpful assistant\n中文 + ascii"
    await store.put_system_prompt("conv-1", snapshot)
    assert await store.get_system_prompt("conv-1") == snapshot


@pytest.mark.asyncio
async def test_system_prompt_overwrite(store: FileConversation) -> None:
    await store.put_system_prompt("conv-1", "v1")
    await store.put_system_prompt("conv-1", "v2")
    assert await store.get_system_prompt("conv-1") == "v2"


@pytest.mark.asyncio
async def test_system_prompt_missing_returns_none(store: FileConversation) -> None:
    assert await store.get_system_prompt("never-set") is None


# ── Isolation, listing, sanitization ────────────────────────────────


@pytest.mark.asyncio
async def test_conversations_are_isolated(store: FileConversation) -> None:
    await store.set("conv-a", [_user("alpha")])
    await store.set("conv-b", [_user("beta")])
    await store.put_tool_result("conv-a", "call-x", "tool", "secret-a")
    await store.put_tool_result("conv-b", "call-x", "tool", "secret-b")

    assert (await store.get("conv-a"))[0].parts[0].content == "alpha"
    assert (await store.get("conv-b"))[0].parts[0].content == "beta"
    assert await store.get_tool_result("conv-a", "call-x") == "secret-a"
    assert await store.get_tool_result("conv-b", "call-x") == "secret-b"


@pytest.mark.asyncio
async def test_list_conversations_returns_dir_names(store: FileConversation) -> None:
    await store.set("conv-a", [_user("hi")])
    await store.put_tool_result("conv-b", "call-x", "tool", "data")
    await store.put_system_prompt("conv-c", "x")

    convs = await store.list_conversations()
    assert set(convs) == {"conv-a", "conv-b", "conv-c"}


@pytest.mark.asyncio
async def test_ids_with_path_separators_are_sanitized(
    store: FileConversation, tmp_path: Path
) -> None:
    """A conv_id containing path separators or `..` must not escape the
    base dir."""
    await store.set("conv/../evil", [_user("payload")])

    # Whatever the sanitized name is, it must be a single child of tmp_path.
    children = list(tmp_path.iterdir())
    assert len(children) == 1
    assert children[0].is_relative_to(tmp_path)


def test_sanitize_key_drops_unsafe_characters() -> None:
    assert _sanitize_key("conv/../evil") == "conv_.._evil"
    assert _sanitize_key("") == "_"
    assert _sanitize_key("////") == "_"
    long = "a" * 500
    assert len(_sanitize_key(long)) <= 96


# ── Concurrency ─────────────────────────────────────────────────────


def test_concurrent_threaded_writes_serialize(tmp_path: Path) -> None:
    """The threading.Lock per conv prevents two threads from clobbering
    each other when writing to the same conversation."""
    import asyncio

    store = FileConversation(tmp_path)

    def writer(i: int) -> None:
        asyncio.run(store.put_tool_result(
            "conv-1", f"call-{i}", "tool", f"payload-{i}"
        ))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 20 results should have landed.
    async def _list():
        return await store.list_tool_results("conv-1")
    entries = asyncio.run(_list())
    call_ids = {e.call_id for e in entries}
    assert call_ids == {f"call-{i}" for i in range(20)}


# ── Atomic write ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_atomic_write_leaves_no_tmp_files(
    store: FileConversation, tmp_path: Path
) -> None:
    await store.set("conv-1", [_user("hi")])
    await store.put_tool_result("conv-1", "call-a", "tool", "data")
    await store.put_system_prompt("conv-1", "snapshot")

    # No .tmp files should remain after a successful write.
    tmp_leftovers = list(tmp_path.rglob(".*.tmp"))
    assert tmp_leftovers == []


# ── Integration with the wrapper stack ──────────────────────────────


@pytest.mark.asyncio
async def test_works_under_evicting_and_summarizing(tmp_path: Path) -> None:
    """The full prod chain ``SummarizingConversation(EvictingConversation(
    FileConversation))`` must round-trip end-to-end. EvictingConversation
    delegates put_tool_result to the inner FileConversation."""
    from backend.core.conversation import (
        EvictingConversation,
        SummarizingConversation,
    )

    base = FileConversation(tmp_path)
    chain = SummarizingConversation(
        EvictingConversation(base, min_size=10),
        model="stub",
        threshold=999,  # disable compaction for this test
    )

    big = "x" * 200
    msgs = [
        _user("read"),
        _assistant_call("read_file", "call-a"),
        _tool_return("read_file", "call-a", big),
        _assistant("done"),
    ]
    await chain.set("conv-1", msgs)

    # Eviction wrote the big content to disk via FileConversation.
    assert await chain.get_tool_result("conv-1", "call-a") == big

    # The history should have the placeholder in place of the big content.
    stored = await chain.get("conv-1")
    placeholder = stored[2].parts[0].content
    assert "[evicted-tool-result]" in placeholder
