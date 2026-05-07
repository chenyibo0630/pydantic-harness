"""Sandbox ABC — defines the unified interface for all sandbox implementations."""

from abc import ABC, abstractmethod


class Sandbox(ABC):
    """Abstract sandbox environment.

    All file, command, and search operations go through this interface.
    Implementations handle path resolution, security, and execution.

    Path convention: paths are relative to the workspace root. Use
    "." for the workspace root itself, "foo.txt" for a file in the root,
    "subdir/bar.txt" for nested files. Skill scripts use "/skills/<name>/...".
    """

    @abstractmethod
    def execute_command(self, command: str, workdir: str = ".", timeout: int = 120) -> str:
        """Execute a shell command.

        Args:
            command: The shell command to execute.
            workdir: Working directory (relative to workspace). Default ".".
            timeout: Timeout in seconds.

        Returns:
            Command output (stdout + stderr).
        """

    @abstractmethod
    def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> str:
        """Read a file's contents.

        Args:
            path: File path (relative to workspace).
            start_line: First line (1-based). 0 = start of file.
            end_line: Last line (1-based inclusive). 0 = end of file.

        Returns:
            File content (at most 200 lines per call).
        """

    @abstractmethod
    def write_file(self, path: str, content: str, append: bool = False) -> str:
        """Write content to a file.

        Args:
            path: File path (relative to workspace).
            content: Text content to write.
            append: If True, append instead of overwriting.

        Returns:
            Confirmation message.
        """

    @abstractmethod
    def str_replace(self, path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
        """Replace a string in a file in-place.

        Args:
            path: File path (relative to workspace).
            old_str: The exact string to replace.
            new_str: The replacement string.
            replace_all: If True, replace all occurrences.

        Returns:
            Confirmation message.
        """

    @abstractmethod
    def list_dir(self, path: str, max_depth: int = 2) -> str:
        """List directory contents in tree format.

        Args:
            path: Directory path (relative to workspace).
            max_depth: Maximum depth to traverse.

        Returns:
            Tree-formatted directory listing.
        """

    @abstractmethod
    def glob_files(self, pattern: str, path: str = ".") -> str:
        """Find files matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g. "**/*.py").
            path: Directory to search in (relative to workspace). Default ".".

        Returns:
            Matched file paths, one per line.
        """

    @abstractmethod
    def grep_search(self, pattern: str, path: str = ".", glob: str = "", context: int = 0) -> str:
        """Search file contents using a regex pattern.

        Args:
            pattern: Regular expression pattern.
            path: File or directory to search in (relative to workspace).
            glob: Optional glob to filter files (e.g. "*.py").
            context: Number of context lines around each match.

        Returns:
            Matched lines with file paths and line numbers.
        """
