"""Bash execution tool — cross-platform shell command runner.

Windows → cmd.exe (shell=True)
Unix    → /bin/sh
"""

import locale
import subprocess
import sys
from pathlib import Path

_MAX_OUTPUT = 8000
_DEFAULT_TIMEOUT = 30


def _system_encoding() -> str:
    if sys.platform == "win32":
        return locale.getpreferredencoding(False) or "utf-8"
    return "utf-8"


def bash_execute(
    command: str,
    workdir: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Execute a shell command and return its output.

    Args:
        command: The shell command to execute.
        workdir: Working directory. Defaults to current directory.
        timeout: Timeout in seconds. Default 30.
    """
    cwd = Path(workdir) if workdir else Path.cwd()

    if not cwd.exists():
        return f"Error: working directory not found: {cwd}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            encoding=_system_encoding(),
            errors="replace",
        )

        parts: list[str] = []
        if result.returncode != 0:
            parts.append(f"[exit {result.returncode}]")
        if (result.stdout or "").strip():
            parts.append(result.stdout)
        if (result.stderr or "").strip():
            parts.append(f"[stderr]\n{result.stderr}")

        output = "\n".join(parts).strip() or "(no output)"

        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + f"\n... (truncated, {len(output)} total chars)"

        return output

    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"
