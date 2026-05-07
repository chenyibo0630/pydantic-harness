"""PDF generation CLI — called by agent via bash_execute.

Subcommands: create | merge | split | watermark

Backed by PyMuPDF (pymupdf). Optional: 'markdown' lib for markdown rendering.
"""

from __future__ import annotations

import argparse
import html
import subprocess
import sys
from pathlib import Path


def _ensure(package: str, import_name: str | None = None) -> None:
    name = import_name or package
    try:
        __import__(name)
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", package, "-q"],
            check=True,
        )


def _read_input(value: str, from_file: bool) -> str:
    if from_file:
        p = Path(value)
        if not p.exists():
            print(f"Error: file not found: {value}", file=sys.stderr)
            sys.exit(1)
        return p.read_text(encoding="utf-8")
    return value


def _render_html_to_pdf(html_content: str, output: str, size: str) -> None:
    """Render HTML to PDF using PyMuPDF Story + DocumentWriter (handles paging)."""
    import pymupdf

    page_rect = pymupdf.paper_rect(size)
    margin = 50
    where = page_rect + (margin, margin, -margin, -margin)

    story = pymupdf.Story(html=html_content)
    writer = pymupdf.DocumentWriter(output)
    more = 1
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()


def cmd_create(args: argparse.Namespace) -> None:
    _ensure("pymupdf")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    if args.format == "markdown":
        _ensure("markdown")
        import markdown as md_lib

        source = _read_input(args.content, from_file=True)
        html_body = md_lib.markdown(source, extensions=["tables", "fenced_code"])
        html_doc = (
            "<html><body style='font-family: sans-serif; font-size: 11pt;'>"
            f"{html_body}</body></html>"
        )
    else:  # text
        source = _read_input(args.content, from_file=args.from_file)
        escaped = html.escape(source)
        html_doc = (
            "<html><body><pre style='font-family: monospace; font-size: 10pt; "
            f"white-space: pre-wrap;'>{escaped}</pre></body></html>"
        )

    _render_html_to_pdf(html_doc, str(out), args.size)
    print(f"Created PDF: {out}")


def cmd_merge(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    import pymupdf

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    result = pymupdf.open()
    for path in args.inputs:
        if not Path(path).exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)
        with pymupdf.open(path) as doc:
            result.insert_pdf(doc)
    result.save(str(out))
    result.close()
    print(f"Merged {len(args.inputs)} file(s) into {out}")


def cmd_split(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    import pymupdf

    src = Path(args.path)
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        sys.exit(1)

    if "-" in args.pages:
        start_s, end_s = args.pages.split("-", 1)
        start, end = int(start_s), int(end_s)
    else:
        start = end = int(args.pages)
    if start > end:
        print(f"Error: invalid page range {args.pages}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    with pymupdf.open(src) as doc:
        total = len(doc)
        if start < 0 or end >= total:
            print(
                f"Error: pages {args.pages} out of range (doc has {total} pages, 0-indexed)",
                file=sys.stderr,
            )
            sys.exit(1)
        new = pymupdf.open()
        new.insert_pdf(doc, from_page=start, to_page=end)
        new.save(str(out))
        new.close()
    print(f"Extracted pages {start}-{end} ({end - start + 1} page(s)) to {out}")


def cmd_watermark(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    import pymupdf

    src = Path(args.path)
    if not src.exists():
        print(f"Error: file not found: {src}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    color = (args.opacity, args.opacity, args.opacity)
    angle = args.angle

    with pymupdf.open(src) as doc:
        for page in doc:
            rect = page.rect
            cx, cy = rect.width / 2, rect.height / 2
            if angle == 0:
                page.insert_textbox(
                    rect,
                    args.text,
                    fontsize=args.fontsize,
                    color=color,
                    align=pymupdf.TEXT_ALIGN_CENTER,
                )
            else:
                tw = pymupdf.TextWriter(rect, color=color)
                # Place text near origin; morph centers + rotates around (cx, cy)
                tw.append((0, 0), args.text, fontsize=args.fontsize)
                text_w = tw.text_rect.width
                text_h = tw.text_rect.height
                origin = pymupdf.Point(cx - text_w / 2, cy + text_h / 4)
                # Reset writer with corrected origin
                tw = pymupdf.TextWriter(rect, color=color)
                tw.append(origin, args.text, fontsize=args.fontsize)
                pivot = pymupdf.Point(cx, cy)
                matrix = pymupdf.Matrix(1, 1).prerotate(angle)
                tw.write_text(page, morph=(pivot, matrix))
        doc.save(str(out), incremental=False, garbage=3, deflate=True)
    print(f"Watermarked {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF generation (PyMuPDF)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="Create a PDF from text or markdown")
    p_create.add_argument("content", help="Inline content OR a file path (with --from-file or markdown format)")
    p_create.add_argument("--output", required=True, help="Output PDF path")
    p_create.add_argument("--format", choices=["text", "markdown"], default="text")
    p_create.add_argument("--from-file", action="store_true", help="Treat 'content' as a path (text format only; markdown is always a file)")
    p_create.add_argument("--size", default="A4", help="Page size: A4, letter, legal, etc. (default A4)")

    p_merge = sub.add_parser("merge", help="Merge multiple PDFs into one")
    p_merge.add_argument("inputs", nargs="+", help="Input PDF paths (in order)")
    p_merge.add_argument("--output", required=True, help="Output PDF path")

    p_split = sub.add_parser("split", help="Extract a page range to a new PDF")
    p_split.add_argument("path", help="Source PDF")
    p_split.add_argument("pages", help="Page range, e.g. 0-4 or 3 (0-indexed, inclusive)")
    p_split.add_argument("--output", required=True, help="Output PDF path")

    p_wm = sub.add_parser("watermark", help="Overlay a text watermark on every page")
    p_wm.add_argument("path", help="Source PDF")
    p_wm.add_argument("text", help="Watermark text")
    p_wm.add_argument("--output", required=True, help="Output PDF path")
    p_wm.add_argument("--fontsize", type=int, default=60)
    p_wm.add_argument("--angle", type=float, default=45.0, help="Rotation in degrees (0 = horizontal)")
    p_wm.add_argument("--opacity", type=float, default=0.8, help="Gray level 0-1 (lower = darker)")

    args = parser.parse_args()
    handlers = {
        "create": cmd_create,
        "merge": cmd_merge,
        "split": cmd_split,
        "watermark": cmd_watermark,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
