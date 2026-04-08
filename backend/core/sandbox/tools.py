"""Tool shell functions — thin wrappers that delegate to the active sandbox.

These are the functions registered as pydantic-ai tools.
LLM sees their docstrings; execution goes through Sandbox.
"""

from backend.core.sandbox.base import Sandbox
from backend.core.sandbox.local import LocalSandbox

_sandbox: Sandbox | None = None


def init_sandbox(workspace: str) -> None:
    """Initialize the global sandbox. Called at agent startup."""
    global _sandbox
    _sandbox = LocalSandbox(workspace)


def get_sandbox() -> Sandbox:
    """Get the active sandbox instance."""
    if _sandbox is None:
        raise RuntimeError("Sandbox not initialized. Call init_sandbox() first.")
    return _sandbox


# ── Tool functions (registered with pydantic-ai Agent) ───────────


def bash_execute(command: str, workdir: str = "/workspace", timeout: int = 30) -> str:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to execute.
        workdir: Working directory. Default /workspace.
        timeout: Timeout in seconds. Default 30.
    """
    return get_sandbox().execute_command(command, workdir, timeout)


def read_file(path: str, start_line: int = 0, end_line: int = 0) -> str:
    """Read a file's contents.

    Single call returns at most 200 lines. Use start_line/end_line to paginate.

    Args:
        path: File path to read.
        start_line: First line (1-based). 0 means start of file.
        end_line: Last line (1-based inclusive). 0 means end of file.
    """
    return get_sandbox().read_file(path, start_line, end_line)


def write_file(path: str, content: str, append: bool = False) -> str:
    """Write content to a file, creating parent directories if needed.

    Args:
        path: File path to write.
        content: Text content to write.
        append: If True, append instead of overwriting.
    """
    return get_sandbox().write_file(path, content, append)


def str_replace(path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
    """Replace a string in a file in-place.

    When replace_all is False, old_str must appear exactly once.

    Args:
        path: File path to modify.
        old_str: The exact string to replace.
        new_str: The replacement string.
        replace_all: If True, replace all occurrences.
    """
    return get_sandbox().str_replace(path, old_str, new_str, replace_all)


def list_dir(path: str, max_depth: int = 2) -> str:
    """List directory contents in tree format.

    Automatically filters noise directories (.git, node_modules, __pycache__, etc).

    Args:
        path: Directory path to list.
        max_depth: Maximum depth to traverse. Default 2.
    """
    return get_sandbox().list_dir(path, max_depth)


def glob_files(pattern: str, path: str = "/workspace") -> str:
    """Find files matching a glob pattern.

    Supports patterns like "**/*.py", "src/**/*.ts", "*.md".

    Args:
        pattern: Glob pattern to match files against.
        path: Directory to search in. Default /workspace.
    """
    return get_sandbox().glob_files(pattern, path)


def grep_search(pattern: str, path: str = "/workspace", glob: str = "", context: int = 0) -> str:
    """Search file contents using a regex pattern.

    Args:
        pattern: Regular expression pattern to search for.
        path: File or directory to search in. Default /workspace.
        glob: Glob pattern to filter files, e.g. "*.py".
        context: Number of context lines before and after each match.
    """
    return get_sandbox().grep_search(pattern, path, glob, context)
