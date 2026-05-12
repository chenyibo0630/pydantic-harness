"""Curated, file-backed memory that survives across conversations.

Ported from hermes-agent's ``MemoryStore`` with the **same operational
guarantees**: two stores (``memory`` for agent notes / ``user`` for user
profile), ``§`` entry delimiter, atomic temp-file writes,
character-budget enforcement, and content scanning before admission.

**Concurrency model**: in-process only. We use ``threading.Lock`` per
target rather than ``fcntl`` / ``msvcrt`` file locks. The reason: this
project runs as a single backend process — the only concurrency comes
from pydantic-ai dispatching sync tools to its worker thread pool. A
process-wide Python lock is sufficient and avoids polluting the user's
prompts dir with placeholder ``.lock`` files. If the deployment ever
needs multi-process write coordination (e.g. multiple gunicorn workers
sharing a mounted volume), reintroduce file-level locking here.

The **frozen snapshot pattern** that hermes implements at process scope —
"system prompt is captured once at session start; tool writes update disk
but not the snapshot, preserving prefix cache" — is handled in this project
by the *session-scoped* system prompt lock in
``backend/core/conversation/``. Each conversation reads the disk once at
first turn via ``main_agent/agent.py:build_system_prompt`` and caches the
result on the ``Conversation`` instance; later writes from the ``memory``
tool only land on disk, never in an in-flight conversation.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.core.memory.scanner import scan_memory_content

logger = logging.getLogger("memory.store")

ENTRY_DELIMITER = "\n§\n"
_VALID_TARGETS = ("memory", "user")

DEFAULT_CHAR_LIMITS: dict[str, int] = {
    # Hermes's tuned values — small enough to keep prefix-cache hits cheap,
    # large enough to hold a few dozen short notes. Tune per deployment if
    # you have a very long-running single user.
    "memory": 2200,
    "user": 1375,
}


class MemoryStore:
    """Two-target, file-backed entry store with admission control."""

    def __init__(
        self,
        notes_dir: str | Path,
        char_limits: dict[str, int] | None = None,
    ) -> None:
        self._dir = Path(notes_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._limits = {**DEFAULT_CHAR_LIMITS, **(char_limits or {})}
        # Process-wide per-target locks. Protects against the case where
        # pydantic-ai dispatches two concurrent ``memory.add()`` tool calls
        # from different conversations to its worker thread pool, both
        # touching the same MEMORY.md / USER.md file.
        self._locks: dict[str, threading.Lock] = {
            target: threading.Lock() for target in _VALID_TARGETS
        }

    # ── Public API ─────────────────────────────────────────────────

    def read_entries(self, target: str) -> list[str]:
        """Return current on-disk entries (deduplicated, order-preserving).
        Empty list if the file doesn't exist yet or is blank."""
        self._check_target(target)
        path = self._path(target)
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("read %s failed: %s", path, e)
            return []
        if not raw.strip():
            return []
        entries = [e.strip() for e in raw.split(ENTRY_DELIMITER)]
        # Dedupe preserving insertion order
        return list(dict.fromkeys(e for e in entries if e))

    def render_system_block(self, target: str) -> str:
        """Format the on-disk entries as a single system-prompt block with
        a labeled header. Empty string if there are no entries — callers
        skip joining empty blocks into the prompt."""
        entries = self.read_entries(target)
        if not entries:
            return ""

        limit = self._limits.get(target, 0)
        content = ENTRY_DELIMITER.join(entries)
        current = len(content)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        if target == "user":
            header = (
                f"USER PROFILE (who the user is) "
                f"[{pct}% — {current:,}/{limit:,} chars]"
            )
        else:
            header = (
                f"MEMORY (your personal notes) "
                f"[{pct}% — {current:,}/{limit:,} chars]"
            )
        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    def add(self, target: str, content: str) -> dict:
        """Append a new entry. No-op (with a friendly message) if already
        present. Rejects empty content, injection patterns, and writes that
        would push the file past its character budget."""
        self._check_target(target)
        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        scan_error = scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(target):
            entries = self.read_entries(target)
            if content in entries:
                return self._success(target, "Entry already exists (no duplicate added).")

            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))
            limit = self._limits.get(target, 0)
            if new_total > limit:
                current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
                return {
                    "success": False,
                    "error": (
                        f"Memory at {current:,}/{limit:,} chars. "
                        f"Adding this entry ({len(content)} chars) would "
                        "exceed the limit. Replace or remove existing "
                        "entries first."
                    ),
                    "usage": f"{current:,}/{limit:,}",
                }

            self._write_entries(target, new_entries)

        return self._success(target, "Entry added.")

    def replace(self, target: str, old_text: str, new_content: str) -> dict:
        """Locate the unique entry containing ``old_text`` as a substring and
        replace it with ``new_content``. Ambiguous matches (multiple distinct
        entries match) return an error and show previews — caller can retry
        with a more specific ``old_text``. Identical duplicate matches resolve
        to the first."""
        self._check_target(target)
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {
                "success": False,
                "error": "new_content cannot be empty. Use 'remove' to delete entries.",
            }

        scan_error = scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(target):
            entries = self.read_entries(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1 and len({e for _, e in matches}) > 1:
                previews = [
                    e[:80] + ("..." if len(e) > 80 else "") for _, e in matches
                ]
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": previews,
                }

            idx = matches[0][0]
            limit = self._limits.get(target, 0)
            test_entries = entries.copy()
            test_entries[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(test_entries))
            if new_total > limit:
                return {
                    "success": False,
                    "error": (
                        f"Replacement would put memory at {new_total:,}/{limit:,} "
                        "chars. Shorten the new content or remove other entries first."
                    ),
                }

            entries[idx] = new_content
            self._write_entries(target, entries)
        return self._success(target, "Entry replaced.")

    def remove(self, target: str, old_text: str) -> dict:
        """Remove the unique entry containing ``old_text`` as a substring."""
        self._check_target(target)
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(target):
            entries = self.read_entries(target)
            matches = [(i, e) for i, e in enumerate(entries) if old_text in e]
            if not matches:
                return {"success": False, "error": f"No entry matched '{old_text}'."}

            if len(matches) > 1 and len({e for _, e in matches}) > 1:
                previews = [
                    e[:80] + ("..." if len(e) > 80 else "") for _, e in matches
                ]
                return {
                    "success": False,
                    "error": f"Multiple entries matched '{old_text}'. Be more specific.",
                    "matches": previews,
                }

            idx = matches[0][0]
            entries.pop(idx)
            self._write_entries(target, entries)
        return self._success(target, "Entry removed.")

    # ── Internal helpers ───────────────────────────────────────────

    def _path(self, target: str) -> Path:
        return self._dir / f"{target.upper()}.md"

    @staticmethod
    def _check_target(target: str) -> None:
        if target not in _VALID_TARGETS:
            raise ValueError(
                f"Invalid target {target!r}. Use one of {_VALID_TARGETS}."
            )

    def _success(self, target: str, message: str) -> dict:
        entries = self.read_entries(target)
        current = len(ENTRY_DELIMITER.join(entries)) if entries else 0
        limit = self._limits.get(target, 0)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        return {
            "success": True,
            "target": target,
            "message": message,
            "entries": entries,
            "entry_count": len(entries),
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
        }

    @contextmanager
    def _file_lock(self, target: str) -> Iterator[None]:
        """Acquire the per-target in-process lock. Name preserved for
        readability of call sites — the lock is in-memory, not on disk.

        ``threading.Lock`` is sufficient because:
        - the backend runs as a single Python process;
        - pydantic-ai dispatches sync tools to a worker thread pool, so
          concurrent ``memory.add()`` calls show up as multiple threads
          inside this process — exactly what threading.Lock guards;
        - we do not need to coordinate with other processes touching the
          same notes dir (and have no current deployment that does so).
        """
        with self._locks[target]:
            yield

    def _write_entries(self, target: str, entries: list[str]) -> None:
        """Atomic temp-file + rename. Readers see either the old complete
        file or the new complete file, never a half-written one."""
        path = self._path(target)
        content = ENTRY_DELIMITER.join(entries) if entries else ""

        fd, tmp_str = tempfile.mkstemp(
            dir=str(path.parent), prefix=".notes_", suffix=".tmp"
        )
        tmp = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
