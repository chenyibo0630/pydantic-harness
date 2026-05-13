"""FileConversation — disk-backed conversation store.

Persists message history, the evicted tool result cache, and the per-session
system prompt snapshot to the filesystem. Each ``conversation_id`` gets its
own subdirectory under ``base_dir``. State survives a process restart, so
re-deploying the backend container doesn't drop in-flight conversations.

Layout under ``base_dir``::

    base_dir/
    └── {conv_id}/
        ├── messages.jsonl    ← one ModelMessage per line, append-only
        │                       (Claude Code-style session log)
        ├── prompt.txt        ← system prompt snapshot (raw UTF-8 text)
        └── tool_results/
            └── {call_id}.json  ← one file per evicted tool result

Why JSONL for messages: every normal turn appends 1–4 new messages to a
growing history. Rewriting the whole array each turn is wasteful and makes
the file unreadable (everything on one line). With JSONL we just append
the new tail; the on-disk file is human-inspectable (``tail -f``,
``wc -l``, ``jq -c`` all work), and compaction (which produces a shorter
history) falls back to a full atomic rewrite.

Append-vs-rewrite decision (in ``set()``):
- ``len(new) > existing_line_count``  → append the tail
- otherwise                            → atomic full rewrite

The trigger for compaction (``SummarizingConversation`` replaces the old
prefix with a single summary message) and history sanitization both
produce shorter lists, so they naturally take the rewrite path.

Concurrency model: same as ``backend.core.memory.MemoryStore`` — in-process
``threading.Lock`` per ``conversation_id``. The backend runs as a single
Python process; pydantic-ai dispatches sync tool calls to a worker thread
pool, so concurrent reads/writes to the same conv come in on different
threads. A process-wide lock per conv is sufficient and avoids the
complexity of file-level locking. If the deployment ever scales to
multiple gunicorn workers sharing this directory, swap to fcntl/msvcrt
file locks here.

Atomic writes: tempfile + ``os.replace`` for the rewrite path; append
with ``os.fsync`` for the append path. Readers always see either the
previous complete state or the new one, never a half-write.

Sanitization: ``conversation_id`` (usually uuid hex from the gateway) and
``call_id`` (provider-supplied) are mapped through ``_sanitize_key`` before
being used as filenames. Two unsanitized IDs that map to the same safe
key will collide — uuid hex never does, but very long or special-character
call_ids could in theory.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

import pydantic
from pydantic_ai.messages import ModelMessage, ModelRequest

from backend.core.conversation.base import Conversation, EvictedEntry

logger = logging.getLogger("conversation.file")

_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.-]")
_MAX_KEY_LEN = 96

# Single-message TypeAdapter for JSONL (one ModelMessage per line). Inherits
# pydantic-ai's bytes-as-base64 convention so multi-modal content survives
# round-trip.
_MSG_ADAPTER: pydantic.TypeAdapter[ModelMessage] = pydantic.TypeAdapter(
    ModelMessage,
    config=pydantic.ConfigDict(
        ser_json_bytes="base64", val_json_bytes="base64"
    ),
)


def _sanitize_key(value: str) -> str:
    """Map an arbitrary id to a filesystem-safe token. Never empty."""
    cleaned = _SAFE_KEY.sub("_", value).strip("._")[:_MAX_KEY_LEN]
    return cleaned or "_"


class FileConversation(Conversation):
    """Disk-backed implementation of ``Conversation`` ABC."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)
        # Per-conv locks created lazily. ``_locks_guard`` serializes the
        # lazy-allocate step itself so two threads can't both create
        # different Lock instances for the same conv.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # ── Internal helpers ───────────────────────────────────────────

    def _lock(self, conv_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(conv_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[conv_id] = lock
            return lock

    def _conv_dir(self, conv_id: str) -> Path:
        return self._base / _sanitize_key(conv_id)

    def _messages_path(self, conv_id: str) -> Path:
        return self._conv_dir(conv_id) / "messages.jsonl"

    def _prompt_path(self, conv_id: str) -> Path:
        return self._conv_dir(conv_id) / "prompt.txt"

    def _tool_results_dir(self, conv_id: str) -> Path:
        return self._conv_dir(conv_id) / "tool_results"

    def _tool_result_path(self, conv_id: str, call_id: str) -> Path:
        return self._tool_results_dir(conv_id) / f"{_sanitize_key(call_id)}.json"

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        """Atomic temp-file + rename. ``os.fsync`` to flush the page cache
        so a process crash doesn't leave a zero-byte file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_str = tempfile.mkstemp(
            dir=str(path.parent), prefix=".", suffix=".tmp"
        )
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise

    # ── JSONL helpers (one ModelMessage per line) ─────────────────

    @staticmethod
    def _strip_instructions(messages: list[ModelMessage]) -> list[ModelMessage]:
        """Remove the duplicated ``instructions`` field from every
        ``ModelRequest`` before persisting.

        Why: pydantic-ai sets ``request.instructions`` to the **full**
        resolved system prompt on every LLM call (see
        ``_agent_graph.py:_set_instructions``), and ``all_messages()``
        keeps it on the persisted request. With our setup the system
        prompt is locked per session and lives canonically in
        ``prompt.txt`` — repeating it on every ModelRequest line in
        the jsonl is waste (5-10 KB × turn-count).

        Safe to drop because pydantic-ai only consults ``instructions``
        on the **current** request being built (line 792 of
        ``_agent_graph.py``), not on historical messages. Historical
        ``instructions`` is metadata used only for request-merging
        equality checks, which still work with ``None``.
        """
        out: list[ModelMessage] = []
        for msg in messages:
            if isinstance(msg, ModelRequest) and msg.instructions:
                out.append(dataclasses.replace(msg, instructions=None))
            else:
                out.append(msg)
        return out

    @staticmethod
    def _count_jsonl_lines(path: Path) -> int:
        """Count non-empty lines on disk — the line count IS the message
        count because we always write exactly one message per line with a
        trailing newline."""
        if not path.exists():
            return 0
        try:
            with open(path, "rb") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def _read_jsonl(
        self, path: Path, conversation_id: str
    ) -> list[ModelMessage] | None:
        """Parse every line as a ModelMessage. Bad lines are logged and
        skipped; a fully unreadable file returns None so the caller
        starts a fresh history."""
        if not path.exists():
            return None
        messages: list[ModelMessage] = []
        try:
            with open(path, "rb") as f:
                for lineno, raw in enumerate(f, start=1):
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        messages.append(_MSG_ADAPTER.validate_json(line))
                    except Exception:
                        logger.exception(
                            "conv=%s: messages.jsonl line %d failed to parse, skipping",
                            conversation_id[:8],
                            lineno,
                        )
        except OSError:
            logger.exception(
                "conv=%s: messages.jsonl read failed", conversation_id[:8]
            )
            return None
        return messages

    def _append_jsonl(
        self, path: Path, tail: list[ModelMessage]
    ) -> None:
        """Append ``len(tail)`` lines, fsync, close. Atomic at the
        single-write level for typical message sizes; the surrounding
        ``threading.Lock`` makes the multi-line append atomic against
        other in-process writers."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "ab") as f:
            for msg in tail:
                f.write(_MSG_ADAPTER.dump_json(msg))
                f.write(b"\n")
            f.flush()
            os.fsync(f.fileno())

    def _rewrite_jsonl(
        self, path: Path, messages: list[ModelMessage]
    ) -> None:
        """Atomic full rewrite for compaction / sanitize / first write."""
        chunks = [_MSG_ADAPTER.dump_json(m) + b"\n" for m in messages]
        self._atomic_write_bytes(path, b"".join(chunks))

    # ── Message history ───────────────────────────────────────────

    async def get(self, conversation_id: str) -> list[ModelMessage] | None:
        path = self._messages_path(conversation_id)
        with self._lock(conversation_id):
            return self._read_jsonl(path, conversation_id)

    async def set(
        self, conversation_id: str, messages: list[ModelMessage]
    ) -> None:
        """Append the tail when ``messages`` strictly extends what's on
        disk; otherwise atomically rewrite the whole file.

        Compaction (``SummarizingConversation``) and history sanitization
        both produce a shorter list than what was last persisted, so they
        naturally hit the rewrite path. Normal turns add one or more
        messages and hit the cheap append path.
        """
        path = self._messages_path(conversation_id)
        slim = self._strip_instructions(messages)
        with self._lock(conversation_id):
            existing_count = self._count_jsonl_lines(path)
            if existing_count > 0 and len(slim) > existing_count:
                tail = slim[existing_count:]
                self._append_jsonl(path, tail)
            else:
                # First-write (empty existing), same-length, or shorter
                # (compaction). Rewrite atomically.
                self._rewrite_jsonl(path, slim)

    async def delete(self, conversation_id: str) -> None:
        conv_dir = self._conv_dir(conversation_id)
        with self._lock(conversation_id):
            if conv_dir.exists():
                shutil.rmtree(conv_dir, ignore_errors=True)
        # Best-effort cleanup of the per-conv lock entry. ``_locks_guard``
        # makes the dict mutation safe; if another thread re-creates a
        # lock for the same conv right after we drop it, that's fine —
        # a fresh lock for a fresh conv is the correct outcome.
        with self._locks_guard:
            self._locks.pop(conversation_id, None)

    async def list_conversations(self) -> list[str]:
        if not self._base.exists():
            return []
        # Note: sanitization is one-way. The returned strings are the
        # **sanitized** IDs (safe filenames). Callers that need to map
        # back to the original UUIDs should rely on the fact that uuid
        # hex characters survive _sanitize_key unchanged.
        return [p.name for p in self._base.iterdir() if p.is_dir()]

    # ── Tool result cache ─────────────────────────────────────────

    async def put_tool_result(
        self,
        conversation_id: str,
        call_id: str,
        tool_name: str,
        content: str,
    ) -> None:
        payload = {
            "call_id": call_id,
            "tool_name": tool_name,
            "content": content,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self._lock(conversation_id):
            self._atomic_write_bytes(
                self._tool_result_path(conversation_id, call_id), data
            )

    async def get_tool_result(
        self, conversation_id: str, call_id: str
    ) -> str | None:
        path = self._tool_result_path(conversation_id, call_id)
        with self._lock(conversation_id):
            if not path.exists():
                return None
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        content = payload.get("content")
        return content if isinstance(content, str) else None

    async def list_tool_results(
        self, conversation_id: str
    ) -> list[EvictedEntry]:
        results_dir = self._tool_results_dir(conversation_id)
        entries: list[EvictedEntry] = []
        with self._lock(conversation_id):
            if not results_dir.exists():
                return entries
            for path in results_dir.glob("*.json"):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                content = payload.get("content", "")
                entries.append(
                    EvictedEntry(
                        call_id=payload.get("call_id", path.stem),
                        tool_name=payload.get("tool_name", "unknown"),
                        size=len(content) if isinstance(content, str) else 0,
                    )
                )
        return entries

    # ── System prompt snapshot ────────────────────────────────────

    async def put_system_prompt(
        self, conversation_id: str, content: str
    ) -> None:
        with self._lock(conversation_id):
            self._atomic_write_bytes(
                self._prompt_path(conversation_id),
                content.encode("utf-8"),
            )

    async def get_system_prompt(self, conversation_id: str) -> str | None:
        path = self._prompt_path(conversation_id)
        with self._lock(conversation_id):
            if not path.exists():
                return None
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return None
