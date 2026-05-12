"""Prompt loader — concatenate all .md files from a prompts directory.

Usage:
    load_prompts("main_agent/prompts", main_file="SYSTEM.md")
    # Returns: SYSTEM.md content + all other .md files sorted by name

``MEMORY.md`` and ``USER.md`` are skipped — they are managed by the
``memory`` tool / ``MemoryStore`` and injected into the system prompt with
their own headers (USER PROFILE / MEMORY) by ``build_system_prompt``. If
``load_prompts`` also included them, the model would see the same content
twice.
"""

from pathlib import Path

# Files in the prompts directory that ``MemoryStore`` owns. ``load_prompts``
# skips them to avoid double-injection into the system prompt.
_MEMORY_OWNED_FILES = frozenset({"MEMORY.md", "USER.md"})


def load_prompts(prompts_dir: str | Path, main_file: str = "SYSTEM.md") -> str:
    """Load and concatenate all .md prompt files from a directory.

    The main_file is loaded first, then all remaining .md files in sorted
    order — skipping any file owned by ``MemoryStore``.

    Args:
        prompts_dir: Path to the prompts directory.
        main_file: Name of the primary prompt file to load first.

    Returns:
        Concatenated prompt text.
    """
    directory = Path(prompts_dir)
    if not directory.is_dir():
        return ""

    main = directory / main_file
    parts: list[str] = []

    if main.exists():
        parts.append(main.read_text(encoding="utf-8").strip())

    for p in sorted(directory.glob("*.md")):
        if p.name == main_file or p.name in _MEMORY_OWNED_FILES:
            continue
        parts.append(p.read_text(encoding="utf-8").strip())

    return "\n\n".join(parts)
