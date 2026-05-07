"""Security-focused tests for LocalSandbox.

Covers P0 patches:
  - _resolve rejects symlinks in path segments
  - glob_files / grep_search skip symlinks and symlinked directories
"""

import sys
from pathlib import Path

import pytest

from backend.core.sandbox.exceptions import PathDeniedError
from backend.core.sandbox.local import LocalSandbox


_posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="symlink tests require POSIX or admin privileges"
)


@pytest.fixture
def sandbox(tmp_path: Path) -> LocalSandbox:
    ws = tmp_path / "ws"
    ws.mkdir()
    return LocalSandbox(str(ws))


def test_resolve_allows_regular_nested_paths(sandbox: LocalSandbox) -> None:
    """Sanity: the new symlink check must not reject normal nested directories."""
    ws = Path(sandbox._workspace)
    (ws / "a" / "b").mkdir(parents=True)
    (ws / "a" / "b" / "x.txt").write_text("hi")
    assert sandbox.read_file("/workspace/a/b/x.txt").startswith("hi")


def test_glob_returns_regular_files(sandbox: LocalSandbox) -> None:
    ws = Path(sandbox._workspace)
    (ws / "a.py").write_text("x")
    (ws / "nested").mkdir()
    (ws / "nested" / "b.py").write_text("y")
    out = sandbox.glob_files("**/*.py")
    assert "a.py" in out
    assert "b.py" in out


@_posix_only
def test_resolve_rejects_symlink_pointing_outside_workspace(tmp_path: Path, sandbox: LocalSandbox) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    link = Path(sandbox._workspace) / "link"
    link.symlink_to(outside)

    with pytest.raises(PathDeniedError):
        sandbox.read_file("/workspace/link/secret.txt")


@_posix_only
def test_resolve_rejects_symlink_pointing_inside_workspace(sandbox: LocalSandbox) -> None:
    """Strict mode: any symlink in path is rejected, even if target stays in workspace."""
    real_dir = Path(sandbox._workspace) / "real"
    real_dir.mkdir()
    (real_dir / "x.txt").write_text("data")

    link = Path(sandbox._workspace) / "shortcut"
    link.symlink_to(real_dir)

    with pytest.raises(PathDeniedError):
        sandbox.read_file("/workspace/shortcut/x.txt")


@_posix_only
def test_resolve_rejects_symlinked_leaf_file(sandbox: LocalSandbox) -> None:
    real = Path(sandbox._workspace) / "real.txt"
    real.write_text("data")
    link = Path(sandbox._workspace) / "link.txt"
    link.symlink_to(real)

    with pytest.raises(PathDeniedError):
        sandbox.read_file("/workspace/link.txt")


@_posix_only
def test_glob_skips_symlinked_files(sandbox: LocalSandbox) -> None:
    ws = Path(sandbox._workspace)
    (ws / "real.py").write_text("x")
    (ws / "link.py").symlink_to(ws / "real.py")

    out = sandbox.glob_files("*.py")
    assert "real.py" in out
    assert "link.py" not in out


@_posix_only
def test_glob_skips_files_under_symlinked_dir(tmp_path: Path, sandbox: LocalSandbox) -> None:
    """Files reachable only through a symlinked directory must not be enumerated."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("leak")

    (Path(sandbox._workspace) / "linkdir").symlink_to(outside)

    out = sandbox.glob_files("**/*.py")
    assert "leak.py" not in out


@_posix_only
def test_grep_skips_symlinked_dirs(tmp_path: Path, sandbox: LocalSandbox) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.py").write_text("SECRET_MARKER")

    (Path(sandbox._workspace) / "linkdir").symlink_to(outside)

    out = sandbox.grep_search("SECRET_MARKER")
    assert "SECRET_MARKER" not in out
    assert "leak.py" not in out
