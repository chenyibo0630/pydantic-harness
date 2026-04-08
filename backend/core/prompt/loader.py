"""Prompt loader — concatenate all .md files from a prompts directory.

Usage:
    load_prompts("main_agent/prompts", main_file="SYSTEM.md")
    # Returns: SYSTEM.md content + all other .md files sorted by name
"""

from pathlib import Path


def load_prompts(prompts_dir: str | Path, main_file: str = "SYSTEM.md") -> str:
    """Load and concatenate all .md prompt files from a directory.

    The main_file is loaded first, then all remaining .md files in sorted
    order. This ensures a predictable prompt structure across agents.

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
        if p.name == main_file:
            continue
        parts.append(p.read_text(encoding="utf-8").strip())

    return "\n\n".join(parts)
