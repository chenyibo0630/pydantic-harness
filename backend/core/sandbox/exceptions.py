"""Sandbox exceptions — structured errors for tool operations."""


class ToolError(Exception):
    """Base exception for all tool/sandbox errors."""

    def __init__(self, message: str, code: str = "TOOL_ERROR") -> None:
        self.code = code
        super().__init__(message)


class PathDeniedError(ToolError):
    """Path is outside the allowed workspace or uses traversal."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="PATH_DENIED")


class FileNotFoundError_(ToolError):
    """Target file or directory does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"File not found: {path}", code="FILE_NOT_FOUND")


class CommandError(ToolError):
    """Shell command execution failed."""

    def __init__(self, message: str, exit_code: int = -1) -> None:
        self.exit_code = exit_code
        super().__init__(message, code="COMMAND_ERROR")
