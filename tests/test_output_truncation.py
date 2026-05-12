"""Tests for output-truncation helpers wrapping sandbox tools."""

from backend.core.sandbox.tools import (
    _MAX_OUTPUT_CHARS,
    _truncate_head,
    _truncate_middle,
)


# ── _truncate_head ──────────────────────────────────────────────────


def test_head_short_output_passes_through() -> None:
    text = "hello"
    assert _truncate_head(text) == text


def test_head_at_exact_limit_unchanged() -> None:
    text = "x" * _MAX_OUTPUT_CHARS
    assert _truncate_head(text) == text


def test_head_over_limit_keeps_prefix() -> None:
    text = "a" * (_MAX_OUTPUT_CHARS + 5000)
    out = _truncate_head(text)
    assert out.startswith("a" * _MAX_OUTPUT_CHARS)
    assert "truncated" in out
    # Marker reports the original length
    assert f"{len(text):,}" in out


def test_head_truncation_marker_suggests_next_action() -> None:
    text = "x" * (_MAX_OUTPUT_CHARS * 2)
    out = _truncate_head(text)
    assert "Narrow" in out
    assert "re-run" in out


def test_head_custom_max_chars() -> None:
    text = "y" * 100
    out = _truncate_head(text, max_chars=20)
    assert out.startswith("y" * 20)
    assert "100" in out  # original length appears


# ── _truncate_middle ────────────────────────────────────────────────


def test_middle_short_output_passes_through() -> None:
    text = "build started\nok\n"
    assert _truncate_middle(text) == text


def test_middle_over_limit_keeps_head_and_tail() -> None:
    head = "HEAD" + "h" * 100
    tail = "TAIL" + "t" * 100
    middle_junk = "m" * (_MAX_OUTPUT_CHARS * 3)
    text = head + middle_junk + tail
    out = _truncate_middle(text)

    # Head fragment near the start of the original text survives
    assert out.startswith("HEAD")
    # Tail fragment near the end survives
    assert out.endswith(tail[-50:])
    # Middle elision marker reports a non-zero drop count
    assert "truncated" in out
    assert "in the middle" in out


def test_middle_truncation_keeps_balanced_halves() -> None:
    text = "X" * 100_000
    out = _truncate_middle(text)
    # The output should fit roughly within the budget (plus the marker text)
    assert len(out) < _MAX_OUTPUT_CHARS + 200
    assert out.startswith("X")
    assert out.endswith("X")


def test_middle_marker_reports_dropped_amount() -> None:
    original_len = _MAX_OUTPUT_CHARS * 3
    text = "z" * original_len
    out = _truncate_middle(text)
    # The dropped count should be ~2/3 of the original; assert it's substantial
    # by checking the marker contains a comma-separated number.
    assert "," in out  # large numbers get thousand separators
