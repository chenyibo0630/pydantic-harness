"""LocalSandbox — local filesystem sandbox with virtual path mapping.

Maps /workspace/ virtual paths to a real workspace directory.
All tool operations are confined within the workspace boundary.
"""

import locale
import os
import re
import subprocess
import sys
from pathlib import Path

from backend.core.sandbox.base import Sandbox
from backend.core.sandbox.exceptions import (
    CommandError,
    FileNotFoundError_,
    PathDeniedError,
    ToolError,
)

_VIRTUAL_PREFIX = "/workspace"
_SKILLS_PREFIX = "/skills"
_READ_MAX_LINES = 200
_MAX_OUTPUT = 8000
_MAX_SEARCH_RESULTS = 200
_MAX_CONTEXT_LINES = 5

_IGNORED_NAMES: set[str] = {
    # VCS
    ".git", ".svn", ".hg",
    # Dependencies
    "node_modules", "__pycache__", ".venv", "venv", "site-packages",
    # Build output
    "dist", "build", ".next", ".nuxt", "target", "out",
    # IDE
    ".idea", ".vscode",
    # OS generated
    ".DS_Store", "Thumbs.db",
    # Cache / test artifacts
    ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    # Egg info
    ".egg-info",
}

_BINARY_EXTENSIONS: set[str] = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp",
    ".zip", ".tar", ".gz", ".whl",
    ".pdf", ".woff", ".woff2", ".ttf", ".eot",
}


class LocalSandbox(Sandbox):
    """Local filesystem sandbox confined to a workspace directory."""

    def __init__(
        self,
        workspace: str,
        skills_dir: str | None = None,
        skills: list | None = None,
        python_path: str | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        if not self._workspace.exists():
            self._workspace.mkdir(parents=True, exist_ok=True)
        self._skills_dir = Path(skills_dir).resolve() if skills_dir else None
        self._skills = skills or []
        self._python_path = python_path or sys.executable

    # ── Path resolution ──────────────────────────────────────────

    def _reject_symlink_in_path(self, raw: Path, root: Path, virtual_path: str) -> None:
        """Reject if any segment of raw (up to but excluding root) is a symlink.

        Checked on the unresolved path — .resolve() would otherwise mask the
        symlink. Prevents both out-of-workspace escape and intra-workspace
        information leaks via symlinks.
        """
        try:
            rel = raw.relative_to(root)
        except ValueError:
            return  # not under root; other checks will reject
        current = root
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise PathDeniedError(f"Symlinks are not allowed: {virtual_path}")

    def _resolve(self, virtual_path: str) -> Path:
        """Convert virtual path to real path, with security checks.

        Supported virtual prefixes:
          /workspace/...  → maps to workspace directory (read/write)
          /skills/...     → maps to skills directory (read-only)
        """
        vp = virtual_path.replace("\\", "/")

        # /skills/ prefix → read-only skills directory
        if vp.startswith(_SKILLS_PREFIX + "/") or vp == _SKILLS_PREFIX:
            if self._skills_dir is None:
                raise PathDeniedError("Skills directory not configured")
            rel = vp[len(_SKILLS_PREFIX):].lstrip("/")
            if ".." in Path(rel).parts:
                raise PathDeniedError(f"Path traversal not allowed: {virtual_path}")
            raw = self._skills_dir / rel
            self._reject_symlink_in_path(raw, self._skills_dir, virtual_path)
            resolved = raw.resolve()
            try:
                resolved.relative_to(self._skills_dir)
            except ValueError:
                raise PathDeniedError(f"Path escapes skills directory: {virtual_path}")
            return resolved

        # /workspace/ prefix → workspace directory
        if vp.startswith(_VIRTUAL_PREFIX + "/"):
            vp = vp[len(_VIRTUAL_PREFIX) + 1:]
        elif vp.startswith(_VIRTUAL_PREFIX):
            vp = vp[len(_VIRTUAL_PREFIX):]
        elif vp.startswith("/"):
            raise PathDeniedError(f"Absolute paths outside /workspace and /skills are not allowed: {virtual_path}")

        if ".." in Path(vp).parts:
            raise PathDeniedError(f"Path traversal not allowed: {virtual_path}")

        raw = self._workspace / vp
        self._reject_symlink_in_path(raw, self._workspace, virtual_path)
        resolved = raw.resolve()

        try:
            resolved.relative_to(self._workspace)
        except ValueError:
            raise PathDeniedError(f"Path escapes workspace: {virtual_path}")

        return resolved

    def _to_virtual(self, real_path: Path) -> str:
        """Convert real path back to virtual path for output masking."""
        resolved = real_path.resolve()
        if self._skills_dir:
            try:
                rel = resolved.relative_to(self._skills_dir)
                return f"{_SKILLS_PREFIX}/{rel.as_posix()}"
            except ValueError:
                pass
        try:
            rel = resolved.relative_to(self._workspace)
            return f"{_VIRTUAL_PREFIX}/{rel.as_posix()}"
        except ValueError:
            return str(real_path)

    def _mask_output(self, text: str) -> str:
        """Replace real paths with virtual paths in output."""
        result = text
        # Mask skills dir first (longer path takes precedence)
        if self._skills_dir:
            sd = str(self._skills_dir)
            result = result.replace(sd, _SKILLS_PREFIX)
            result = result.replace(sd.replace("\\", "/"), _SKILLS_PREFIX)
            result = result.replace(sd.replace("/", "\\"), _SKILLS_PREFIX)
        # Mask workspace
        ws = str(self._workspace)
        result = result.replace(ws, _VIRTUAL_PREFIX)
        result = result.replace(ws.replace("\\", "/"), _VIRTUAL_PREFIX)
        result = result.replace(ws.replace("/", "\\"), _VIRTUAL_PREFIX)
        return result

    # ── Command execution ────────────────────────────────────────

    @staticmethod
    def _system_encoding() -> str:
        if sys.platform == "win32":
            return locale.getpreferredencoding(False) or "utf-8"
        return "utf-8"

    def _resolve_skill_env(self, command: str) -> dict[str, str]:
        """Extract env vars from matched skill's config for subprocess injection."""
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        cmd_normalized = command.replace("\\", "/")
        for skill in self._skills:
            if f"skills/{skill.name}/" in cmd_normalized:
                for k, v in skill.config.items():
                    if isinstance(v, str):
                        env[k.upper()] = v
                break
        return env

    def _resolve_python(self, command: str) -> str:
        """Replace bare 'python ' with the venv Python path."""
        if command.startswith("python ") or command.startswith("python3 "):
            prefix = "python3 " if command.startswith("python3 ") else "python "
            return f'"{self._python_path}" {command[len(prefix):]}'
        return command

    def execute_command(self, command: str, workdir: str = "/workspace", timeout: int = 30) -> str:
        try:
            cwd = self._resolve(workdir)
        except PathDeniedError:
            cwd = self._workspace  # fallback to workspace root
        if not cwd.exists():
            cwd = self._workspace

        command = self._resolve_python(command)
        env = self._resolve_skill_env(command)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                encoding=self._system_encoding(),
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

            return self._mask_output(output)

        except subprocess.TimeoutExpired:
            raise CommandError(f"Command timed out after {timeout}s")
        except Exception as e:
            raise CommandError(str(e))

    # ── File operations ──────────────────────────────────────────

    def read_file(self, path: str, start_line: int = 0, end_line: int = 0) -> str:
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError_(path)
        if not p.is_file():
            raise ToolError(f"Not a file: {path}")

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

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        p = self._resolve(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if append else "w"
        with open(p, mode, encoding="utf-8") as f:
            f.write(content)
        return f"Written {len(content)} chars to {self._to_virtual(p)}"

    def str_replace(self, path: str, old_str: str, new_str: str, replace_all: bool = False) -> str:
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError_(path)
        if not p.is_file():
            raise ToolError(f"Not a file: {path}")

        content = p.read_text(encoding="utf-8")
        count = content.count(old_str)
        if count == 0:
            raise ToolError(f"String not found in {path}")
        if not replace_all and count > 1:
            raise ToolError(f"String appears {count} times; pass replace_all=True or use a more specific string")

        new_content = content.replace(old_str, new_str) if replace_all else content.replace(old_str, new_str, 1)
        p.write_text(new_content, encoding="utf-8")
        replaced = count if replace_all else 1
        return f"Replaced {replaced} occurrence(s) in {self._to_virtual(p)}"

    # ── Directory listing ────────────────────────────────────────

    def list_dir(self, path: str, max_depth: int = 2) -> str:
        p = self._resolve(path)
        if not p.exists():
            raise FileNotFoundError_(path)
        if not p.is_dir():
            raise ToolError(f"Not a directory: {path}")

        result: list[str] = [self._to_virtual(p)]
        self._walk(p, "", max_depth, 1, result)
        return "\n".join(result)

    def _walk(self, directory: Path, prefix: str, max_depth: int, depth: int, lines: list[str]) -> None:
        try:
            entries = sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except PermissionError:
            lines.append(f"{prefix}[permission denied]")
            return

        # Filter ignored
        entries = [e for e in entries if e.name not in _IGNORED_NAMES]

        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir() and depth < max_depth:
                extension = "    " if is_last else "│   "
                self._walk(entry, prefix + extension, max_depth, depth + 1, lines)

    # ── Search operations ────────────────────────────────────────

    def _is_skipped(self, p: Path) -> bool:
        return bool(_IGNORED_NAMES.intersection(p.parts))

    def _is_binary(self, p: Path) -> bool:
        return p.suffix.lower() in _BINARY_EXTENSIONS

    def _has_symlink_ancestor(self, p: Path, root: Path) -> bool:
        """True if any directory between root (exclusive) and p (inclusive) is a symlink."""
        try:
            rel = p.relative_to(root)
        except ValueError:
            return False
        current = root
        for part in rel.parts[:-1]:
            current = current / part
            if current.is_symlink():
                return True
        return False

    def _safe_file_filter(self, p: Path, root: Path) -> bool:
        """Common filter: regular file, not symlink, not in ignored dirs, no symlink ancestor."""
        return (
            p.is_file()
            and not p.is_symlink()
            and not self._is_skipped(p)
            and not self._has_symlink_ancestor(p, root)
        )

    def glob_files(self, pattern: str, path: str = "/workspace") -> str:
        root = self._resolve(path)
        if not root.exists():
            raise FileNotFoundError_(path)
        if not root.is_dir():
            raise ToolError(f"Not a directory: {path}")

        matches = sorted(
            (p for p in root.glob(pattern) if self._safe_file_filter(p, root)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        if not matches:
            return f"No files matching '{pattern}' in {self._to_virtual(root)}"

        total = len(matches)
        truncated = matches[:_MAX_SEARCH_RESULTS]
        lines = [self._to_virtual(p) for p in truncated]

        if total > _MAX_SEARCH_RESULTS:
            lines.append(f"\n[truncated: showing {_MAX_SEARCH_RESULTS} of {total} matches]")

        return "\n".join(lines)

    def grep_search(self, pattern: str, path: str = "/workspace", glob: str = "", context: int = 0) -> str:
        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"Invalid regex: {e}")

        root = self._resolve(path)
        if not root.exists():
            raise FileNotFoundError_(path)

        files: list[Path]
        if root.is_file():
            files = [root]
        else:
            pattern_glob = glob or "*"
            files = sorted(
                p for p in root.rglob(pattern_glob)
                if self._safe_file_filter(p, root) and not self._is_binary(p)
            )

        ctx = min(context, _MAX_CONTEXT_LINES)
        results: list[str] = []
        match_count = 0

        for fp in files:
            try:
                file_lines = fp.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            vp = self._to_virtual(fp)
            for i, line in enumerate(file_lines):
                if not regex.search(line):
                    continue

                match_count += 1
                if match_count > _MAX_SEARCH_RESULTS:
                    results.append(f"\n[truncated: {_MAX_SEARCH_RESULTS} of {match_count}+ matches shown]")
                    return "\n".join(results)

                if ctx > 0:
                    start = max(0, i - ctx)
                    end = min(len(file_lines), i + ctx + 1)
                    snippet = "\n".join(
                        f"  {j + 1}{'>' if j == i else ':'} {file_lines[j]}"
                        for j in range(start, end)
                    )
                    results.append(f"{vp}:\n{snippet}")
                else:
                    results.append(f"{vp}:{i + 1}: {line}")

        if not results:
            return f"No matches for '{pattern}'"

        return "\n".join(results)
