"""RemoteSandbox — Sandbox ABC implementation that delegates to the sandbox service via HTTP."""

import httpx

from backend.core.sandbox.base import Sandbox
from backend.core.sandbox.exceptions import (
    CommandError,
    FileNotFoundError_,
    PathDeniedError,
    ToolError,
)

_ERROR_MAP = {
    "PATH_DENIED": PathDeniedError,
    "FILE_NOT_FOUND": FileNotFoundError_,
    "COMMAND_ERROR": CommandError,
}


class RemoteSandbox(Sandbox):
    """Sandbox implementation that calls a remote sandbox service over HTTP."""

    def __init__(self, base_url: str, token: str = "", timeout: int = 60) -> None:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
        )

    def _call(self, endpoint: str, payload: dict) -> str:
        """POST to sandbox service and return output or raise appropriate error."""
        try:
            resp = self._client.post(endpoint, json=payload)
        except httpx.ConnectError as exc:
            raise CommandError(f"Sandbox service unreachable: {exc}")
        except httpx.TimeoutException as exc:
            raise CommandError(f"Sandbox service timeout: {exc}")

        data = resp.json()

        if resp.status_code == 200:
            return data.get("output", "")

        # Map error code to exception type
        code = data.get("code", "TOOL_ERROR")
        message = data.get("error", "Unknown sandbox error")
        exc_cls = _ERROR_MAP.get(code, ToolError)

        if exc_cls == FileNotFoundError_:
            raise FileNotFoundError_(message.removeprefix("File not found: "))
        elif exc_cls == CommandError:
            raise CommandError(message)
        elif exc_cls == PathDeniedError:
            raise PathDeniedError(message)
        else:
            raise ToolError(message, code=code)

    def execute_command(self, command: str, workdir: str = "/workspace", timeout: int = 30) -> str:
        return self._call("/sandbox/execute_command", {
            "command": command, "workdir": workdir, "timeout": timeout,
        })

    def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> str:
        return self._call("/sandbox/read_file", {
            "path": path, "start_line": start_line, "end_line": end_line,
        })

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        return self._call("/sandbox/write_file", {
            "path": path, "content": content, "append": append,
        })

    def str_replace(self, path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
        return self._call("/sandbox/str_replace", {
            "path": path, "old_str": old_str, "new_str": new_str, "replace_all": replace_all,
        })

    def list_dir(self, path: str, max_depth: int = 2) -> str:
        return self._call("/sandbox/list_dir", {
            "path": path, "max_depth": max_depth,
        })

    def glob_files(self, pattern: str, path: str = "/workspace") -> str:
        return self._call("/sandbox/glob_files", {
            "pattern": pattern, "path": path,
        })

    def grep_search(self, pattern: str, path: str = "/workspace", glob: str = "", context: int = 0) -> str:
        return self._call("/sandbox/grep_search", {
            "pattern": pattern, "path": path, "glob": glob, "context": context,
        })
