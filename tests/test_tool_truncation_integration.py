"""End-to-end tests that the truncation wrappers in tools.py actually fire
when a real sandbox returns large output."""

from pathlib import Path

import pytest

from backend.core.sandbox import init_sandbox, read_file
from backend.core.sandbox.tools import _MAX_OUTPUT_CHARS


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    init_sandbox(str(ws))
    return ws


def test_read_file_truncates_huge_single_line(workspace: Path) -> None:
    """A single line larger than the char cap still gets truncated.

    read_file paginates by lines (200 max per call), but a pathological case
    is a binary or minified file where 200 lines = millions of chars. The
    char cap is the safety net for that.
    """
    huge = "x" * (_MAX_OUTPUT_CHARS * 2)
    (workspace / "big.txt").write_text(huge)

    out = read_file("big.txt")
    assert len(out) < _MAX_OUTPUT_CHARS + 500
    assert "truncated" in out


def test_read_file_under_limit_passes_through(workspace: Path) -> None:
    """Small files come back verbatim, no marker added."""
    content = "line1\nline2\nline3\n"
    (workspace / "small.txt").write_text(content)

    out = read_file("small.txt")
    assert "truncated" not in out
    # Content should be present (LocalSandbox may add headers, so substring check)
    assert "line1" in out and "line3" in out
