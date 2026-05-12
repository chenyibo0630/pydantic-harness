"""``memory`` tool — save durable notes that survive across conversations.

Ported behaviour from hermes-agent: a single tool with an ``action`` enum
(``add`` / ``replace`` / ``remove``) and a ``target`` enum
(``memory`` / ``user``). Content lives in two markdown files
(``MEMORY.md`` and ``USER.md``) on disk, separated by ``§``.

The store is a process-global singleton initialized at server start (the
``main_agent/server.py`` lifespan calls ``init_memory_store(...)`` with the
configured directory). All conversations share it — this is intentional:
"the user prefers concise answers" applies across every conversation, not
just the one where you learned it.

Mid-conversation writes are visible on disk **immediately**, but they do
NOT change the system prompt of the in-flight conversation — that one
locked its snapshot at first turn. New conversations pick up the updated
notes when they build their own snapshot.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Literal

from backend.core.memory import MemoryStore

logger = logging.getLogger(__name__)

_memory_store: MemoryStore | None = None


def init_memory_store(memory_dir: str | Path) -> None:
    """Construct and install the process-global memory store. Idempotent —
    later calls overwrite the previous instance (useful for tests)."""
    global _memory_store
    _memory_store = MemoryStore(memory_dir)
    logger.info("memory store initialized at %s", _memory_store._dir)


def get_memory_store() -> MemoryStore | None:
    """Return the active memory store, or None if the server didn't init
    one (e.g. tests). Callers must handle the None case explicitly."""
    return _memory_store


def memory(
    action: Literal["add", "replace", "remove"],
    target: Literal["memory", "user"] = "memory",
    content: str | None = None,
    old_text: str | None = None,
) -> str:
    """Save / update / remove durable notes that survive across sessions.

    The **when** and **how to phrase** rules (proactive saving, declarative
    vs imperative wording, what NOT to save) are spelled out in the system
    prompt section "Memory 工具使用规则". This docstring covers only the
    call mechanics.

    TWO TARGETS:
    - ``user`` — who the user is: name, role, preferences, communication
      style, pet peeves.
    - ``memory`` — your notes: environment facts, project conventions,
      tool quirks, lessons learned.

    ACTIONS:
    - ``add`` — append a new entry. Required: ``content``.
    - ``replace`` — update an existing entry. Required: ``old_text``
      (short unique substring identifying the entry) and ``content``
      (the new full entry text).
    - ``remove`` — delete an existing entry. Required: ``old_text``.

    Returns a JSON string with ``success``, current ``entries`` (after
    the change), and ``usage`` (``%`` of char budget consumed). On
    failure: ``success=false`` and ``error`` explains why; for ambiguous
    ``old_text`` the response includes a ``matches`` array of previews
    so you can refine.
    """
    store = get_memory_store()
    if store is None:
        return json.dumps(
            {
                "success": False,
                "error": "Memory store not initialized on this server.",
            },
            ensure_ascii=False,
        )

    if target not in ("memory", "user"):
        return json.dumps(
            {"success": False, "error": f"Invalid target {target!r}. Use 'memory' or 'user'."},
            ensure_ascii=False,
        )

    if action == "add":
        if not content:
            return json.dumps(
                {"success": False, "error": "content is required for action='add'."},
                ensure_ascii=False,
            )
        result = store.add(target, content)
    elif action == "replace":
        if not old_text:
            return json.dumps(
                {"success": False, "error": "old_text is required for action='replace'."},
                ensure_ascii=False,
            )
        if not content:
            return json.dumps(
                {"success": False, "error": "content is required for action='replace'."},
                ensure_ascii=False,
            )
        result = store.replace(target, old_text, content)
    elif action == "remove":
        if not old_text:
            return json.dumps(
                {"success": False, "error": "old_text is required for action='remove'."},
                ensure_ascii=False,
            )
        result = store.remove(target, old_text)
    else:
        return json.dumps(
            {"success": False, "error": f"Unknown action {action!r}."},
            ensure_ascii=False,
        )

    return json.dumps(result, ensure_ascii=False)
