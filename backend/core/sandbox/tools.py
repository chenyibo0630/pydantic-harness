"""Tool shell functions — thin wrappers that delegate to the active sandbox.

These are the functions registered as pydantic-ai tools.
LLM sees their docstrings; execution goes through Sandbox.

All wrappers catch sandbox exceptions and return them as strings, so a
single tool failure never crashes the SSE stream — the LLM sees the
error and can adjust (retry with bigger timeout, fix the path, etc.).
"""

import logging
from collections.abc import Callable
from functools import wraps
from typing import TypeVar

from backend.core.sandbox.base import Sandbox
from backend.core.sandbox.exceptions import (
    CommandError,
    FileNotFoundError_,
    PathDeniedError,
    ToolError,
)
from backend.core.sandbox.local import LocalSandbox
from backend.core.sandbox.remote import RemoteSandbox

logger = logging.getLogger(__name__)

_sandbox: Sandbox | None = None

F = TypeVar("F", bound=Callable[..., str])

# ── Output truncation ────────────────────────────────────────────
#
# A single tool result is fed straight into the model's context. Without a
# hard ceiling, one rogue `bash_execute` or sprawling `grep_search` can blow
# the window or starve the rest of the turn. We cap each tool's output at
# 30,000 characters — the same limit Claude Code applies to its Bash tool —
# and let the model re-narrow if it cared about the dropped tail.

_MAX_OUTPUT_CHARS = 30_000


def _truncate_head(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Keep the first ``max_chars`` characters; drop the rest.

    Use for results where relevance is front-loaded: grep matches (file
    order), glob paths (mtime-sorted), list_dir (alphabetical), read_file
    (sequential bytes — caller can re-read later lines).
    """
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n\n[truncated: output was {len(text):,} chars, kept first "
        f"{max_chars:,}. Narrow the query (pattern, glob, range) and re-run "
        "to see more.]"
    )


def _truncate_middle(text: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    """Keep both ends, drop the middle.

    Use for bash output where the interesting bits (the command echo / first
    error, plus the final result / stack trace) cluster at the head and tail;
    long build-log middles are usually repetitive noise.
    """
    if len(text) <= max_chars:
        return text
    half = (max_chars - 80) // 2
    dropped = len(text) - 2 * half
    return (
        text[:half]
        + f"\n\n[... truncated {dropped:,} chars in the middle ...]\n\n"
        + text[-half:]
    )


def init_sandbox(
    workspace: str,
    *,
    skills_dir: str | None = None,
    skills: list | None = None,
    python_path: str | None = None,
    sandbox_type: str = "local",
    remote_url: str = "",
    remote_token: str = "",
    remote_timeout: int = 60,
) -> None:
    """Initialize the global sandbox. Called at agent startup.

    Args:
        workspace: Workspace directory path (used by local sandbox).
        sandbox_type: "local" or "remote".
        remote_url: Sandbox service URL (when sandbox_type is "remote").
        remote_token: Bearer token for sandbox service auth.
        remote_timeout: HTTP timeout in seconds for remote calls.
    """
    global _sandbox
    if sandbox_type == "remote":
        _sandbox = RemoteSandbox(
            base_url=remote_url,
            token=remote_token,
            timeout=remote_timeout,
        )
    else:
        _sandbox = LocalSandbox(
            workspace,
            skills_dir=skills_dir,
            skills=skills,
            python_path=python_path,
        )


def get_sandbox() -> Sandbox:
    """Get the active sandbox instance."""
    if _sandbox is None:
        raise RuntimeError("Sandbox not initialized. Call init_sandbox() first.")
    return _sandbox


def _safe_tool(func: F) -> F:
    """Catch sandbox exceptions, log details, return error string to LLM.

    Tool errors are expected: bad paths, slow commands, missing files. They
    must be visible to the LLM (so it can adjust) and visible in server logs
    (so operators can debug), but must NOT crash the SSE stream.
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> str:
        tool_name = func.__name__
        try:
            return func(*args, **kwargs)
        except CommandError as e:
            logger.warning(
                "Tool %s CommandError (exit=%s): %s | args=%s kwargs=%s",
                tool_name, getattr(e, "exit_code", "?"), e, args, kwargs,
            )
            return f"[error] CommandError: {e}"
        except PathDeniedError as e:
            logger.warning("Tool %s PathDeniedError: %s | args=%s kwargs=%s",
                           tool_name, e, args, kwargs)
            return f"[error] PathDeniedError: {e}"
        except FileNotFoundError_ as e:
            logger.info("Tool %s FileNotFound: %s | args=%s kwargs=%s",
                        tool_name, e, args, kwargs)
            return f"[error] FileNotFound: {e}"
        except ToolError as e:
            logger.warning("Tool %s ToolError (code=%s): %s | args=%s kwargs=%s",
                           tool_name, e.code, e, args, kwargs)
            return f"[error] {e.code}: {e}"
        except Exception as e:
            logger.exception(
                "Tool %s unexpected error: %s | args=%s kwargs=%s",
                tool_name, e, args, kwargs,
            )
            return f"[error] {type(e).__name__}: {e}"
    return wrapper  # type: ignore[return-value]


# ── Tool functions (registered with pydantic-ai Agent) ───────────


@_safe_tool
def bash_execute(command: str, workdir: str = ".", timeout: int = 120) -> str:
    """Execute a shell command and return its output.

    All paths are relative to the workspace root. Use "." for the workspace
    root itself. For slow commands (pandoc, pip install, font scans, large
    builds) pass timeout >= 180. Default is 120s; absolute max is 300s.

    Output longer than 30,000 characters is truncated in the **middle**
    (head + tail kept, middle elided) so both the command echo / setup and
    the final result / error message survive. Pipe through `head`, `tail`,
    or `grep` if you need to see the dropped part.

    Args:
        command: The shell command to execute.
        workdir: Working directory relative to workspace. Default ".".
        timeout: Timeout in seconds. Default 120.
    """
    return _truncate_middle(get_sandbox().execute_command(command, workdir, timeout))


@_safe_tool
def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read a file's contents.

    Paths are relative to the workspace root (e.g. "README.md",
    "docs/intro.md"). Single call returns at most 200 lines — use
    start_line/end_line to paginate larger files.

    Args:
        path: File path relative to workspace.
        start_line: First line (1-based). 0 means start of file.
        end_line: Last line (1-based inclusive). 0 means end of file.
    """
    return _truncate_head(get_sandbox().read_file(path, start_line, end_line))


@_safe_tool
def write_file(path: str, content: str, append: bool = False) -> str:
    """Write content to a file, creating parent directories if needed.

    Paths are relative to the workspace root.

    Args:
        path: File path relative to workspace.
        content: Text content to write.
        append: If True, append instead of overwriting.
    """
    return get_sandbox().write_file(path, content, append)


@_safe_tool
def str_replace(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Replace a string in a file in-place.

    Paths are relative to the workspace root. When replace_all is False,
    old_str must appear exactly once.

    Args:
        path: File path relative to workspace.
        old_str: The exact string to replace.
        new_str: The replacement string.
        replace_all: If True, replace all occurrences.
    """
    return get_sandbox().str_replace(path, old_str, new_str, replace_all)


@_safe_tool
def list_dir(path: str = ".", max_depth: int = 2) -> str:
    """List directory contents in tree format.

    Paths are relative to the workspace root. Use "." (default) to list the
    workspace root. Automatically filters noise directories (.git,
    node_modules, __pycache__, etc).

    Args:
        path: Directory path relative to workspace. Default ".".
        max_depth: Maximum depth to traverse. Default 2.
    """
    return get_sandbox().list_dir(path, max_depth)


@_safe_tool
def glob_files(pattern: str, path: str = ".") -> str:
    """Find files matching a glob pattern.

    Paths are relative to the workspace root. Supports patterns like
    "**/*.py", "src/**/*.ts", "*.md".

    Args:
        pattern: Glob pattern to match files against.
        path: Directory to search in (relative to workspace). Default ".".
    """
    return get_sandbox().glob_files(pattern, path)


@_safe_tool
def grep_search(pattern: str, path: str = ".", glob: str = "", context: int = 0) -> str:
    """Search file contents using a regex pattern.

    Paths are relative to the workspace root.

    Args:
        pattern: Regular expression pattern to search for.
        path: File or directory to search in (relative to workspace).
            Default ".".
        glob: Glob pattern to filter files, e.g. "*.py".
        context: Number of context lines before and after each match.
    """
    return _truncate_head(get_sandbox().grep_search(pattern, path, glob, context))
