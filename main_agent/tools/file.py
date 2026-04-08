"""File tools — read, write, list, replace.

Write operations are restricted to WRITE_ALLOWED_ROOTS directories
configured in the agent's config.yaml.
"""

from pathlib import Path

_READ_MAX_LINES = 200
_WRITE_ROOTS: list[Path] = []


def set_write_roots(roots: list[str]) -> None:
    """Configure allowed write roots. Called at agent startup."""
    _WRITE_ROOTS.clear()
    _WRITE_ROOTS.extend(Path(p).resolve() for p in roots)


def _check_write_allowed(path: Path) -> str | None:
    if not _WRITE_ROOTS:
        return "Write denied: write_allowed_roots is not configured"
    resolved = path.resolve()
    for root in _WRITE_ROOTS:
        try:
            resolved.relative_to(root)
            return None
        except ValueError:
            continue
    allowed = ", ".join(str(r) for r in _WRITE_ROOTS)
    return f"Write denied: {resolved} is not under allowed roots [{allowed}]"


def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read a file's contents.

    Single call returns at most 200 lines. Use start_line/end_line to paginate.

    Args:
        path: File path to read.
        start_line: First line (1-based). 0 means start of file.
        end_line: Last line (1-based inclusive). 0 means end of file.
    """
    p = Path(path)
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"

    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    total = len(lines)

    start = (start_line - 1) if start_line > 0 else 0
    end = end_line if end_line > 0 else total
    actual_end = min(end, start + _READ_MAX_LINES)
    selected = lines[start:actual_end]
    content = "".join(selected) or "(empty)"

    if actual_end < end:
        content += (
            f"\n[truncated: showing lines {start + 1}-{actual_end} of {total}. "
            f"Use start_line/end_line to read more.]"
        )
    return content


def list_dir(path: str, max_depth: int = 2) -> str:
    """List directory contents in tree format.

    Args:
        path: Directory path to list.
        max_depth: Maximum depth to traverse. Default 2.
    """
    p = Path(path)
    if not p.exists():
        return f"Error: path not found: {path}"
    if not p.is_dir():
        return f"Error: not a directory: {path}"

    result: list[str] = [str(p)]
    _walk(p, "", max_depth, 1, result)
    return "\n".join(result)


def _walk(directory: Path, prefix: str, max_depth: int, depth: int, lines: list[str]) -> None:
    try:
        entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except PermissionError:
        lines.append(f"{prefix}[permission denied]")
        return

    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
        if entry.is_dir() and depth < max_depth:
            extension = "    " if is_last else "│   "
            _walk(entry, prefix + extension, max_depth, depth + 1, lines)


def str_replace(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Replace a string in a file in-place.

    When replace_all is False, old_str must appear exactly once.

    Args:
        path: File path to modify.
        old_str: The exact string to replace.
        new_str: The replacement string.
        replace_all: If True, replace all occurrences.
    """
    p = Path(path)
    if not p.exists():
        return f"Error: file not found: {path}"
    if not p.is_file():
        return f"Error: not a file: {path}"

    err = _check_write_allowed(p)
    if err:
        return err

    content = p.read_text(encoding="utf-8")
    count = content.count(old_str)
    if count == 0:
        return f"Error: string not found in {path}"
    if not replace_all and count > 1:
        return f"Error: string appears {count} times; pass replace_all=True or use a more specific string"

    new_content = content.replace(old_str, new_str) if replace_all else content.replace(old_str, new_str, 1)
    p.write_text(new_content, encoding="utf-8")
    replaced = count if replace_all else 1
    return f"Replaced {replaced} occurrence(s) in {path}"


def write_file(path: str, content: str, append: bool = False) -> str:
    """Write content to a file, creating parent directories if needed.

    Args:
        path: File path to write.
        content: Text content to write.
        append: If True, append instead of overwriting.
    """
    p = Path(path)
    err = _check_write_allowed(p)
    if err:
        return err

    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with open(p, mode, encoding="utf-8") as f:
        f.write(content)
    return f"Written {len(content)} chars to {path}"
