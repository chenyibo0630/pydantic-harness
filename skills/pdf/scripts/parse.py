"""PDF parsing CLI — called by agent via bash_execute.

Subcommands: text | markdown | metadata | tables | images | search

Backed by PyMuPDF (pymupdf) + pymupdf4llm. Auto-installs on first run.
"""

from __future__ import annotations

import argparse
import json
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


def _parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    if "-" in spec:
        start_s, end_s = spec.split("-", 1)
        start, end = int(start_s), int(end_s)
        if start > end:
            raise ValueError(f"Invalid page range: {spec}")
        return list(range(start, end + 1))
    return [int(spec)]


def _open_doc(path: str):
    import pymupdf

    p = Path(path)
    if not p.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    return pymupdf.open(p)


def cmd_text(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    doc = _open_doc(args.path)
    pages = _parse_pages(args.pages)
    indices = pages if pages is not None else range(len(doc))
    total = len(doc)
    for i in indices:
        if i < 0 or i >= total:
            continue
        print(f"\n--- Page {i + 1}/{total} ---\n")
        print(doc[i].get_text())


def cmd_markdown(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    _ensure("pymupdf4llm")
    import pymupdf4llm

    pages = _parse_pages(args.pages)
    md = pymupdf4llm.to_markdown(args.path, pages=pages)

    if args.save:
        Path(args.save).write_text(md, encoding="utf-8")
        print(f"Wrote markdown ({len(md)} chars) to {args.save}")
    else:
        print(md)


def cmd_metadata(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    doc = _open_doc(args.path)
    meta = doc.metadata or {}
    out = {
        "pages": len(doc),
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "subject": meta.get("subject", ""),
        "creator": meta.get("creator", ""),
        "producer": meta.get("producer", ""),
        "format": meta.get("format", ""),
        "encrypted": doc.is_encrypted,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))


def cmd_tables(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    doc = _open_doc(args.path)
    pages = _parse_pages(args.pages)
    indices = pages if pages is not None else range(len(doc))

    found = 0
    for i in indices:
        if i < 0 or i >= len(doc):
            continue
        page = doc[i]
        try:
            tables = page.find_tables()
        except Exception as e:
            print(f"Page {i + 1}: table detection failed ({e})", file=sys.stderr)
            continue
        for j, table in enumerate(tables.tables, 1):
            found += 1
            print(f"\n--- Page {i + 1}, Table {j} ---\n")
            try:
                _ensure("pandas")
                df = table.to_pandas()
                print(df.to_markdown(index=False))
            except Exception:
                rows = table.extract()
                for row in rows:
                    print("| " + " | ".join("" if c is None else str(c) for c in row) + " |")
    if found == 0:
        print("No tables detected.")


def cmd_images(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    import pymupdf

    doc = _open_doc(args.path)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for i, page in enumerate(doc, 1):
        for img_idx, img in enumerate(page.get_images(full=True), 1):
            xref = img[0]
            pix = pymupdf.Pixmap(doc, xref)
            if pix.n >= 5:  # CMYK / alpha → convert to RGB
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            target = out_dir / f"page{i}_img{img_idx}.png"
            pix.save(str(target))
            count += 1
    print(f"Extracted {count} image(s) to {out_dir}/")


def cmd_search(args: argparse.Namespace) -> None:
    _ensure("pymupdf")
    doc = _open_doc(args.path)
    query = args.query
    ctx = max(0, args.context)

    total_hits = 0
    for i, page in enumerate(doc, 1):
        try:
            hits = page.search_for(query)
        except Exception:
            hits = []
        if not hits:
            continue
        text = page.get_text()
        lower_text = text.lower()
        lower_query = query.lower()
        offsets: list[int] = []
        start = 0
        while True:
            idx = lower_text.find(lower_query, start)
            if idx == -1:
                break
            offsets.append(idx)
            start = idx + len(lower_query)

        total_hits += len(hits)
        print(f"\n--- Page {i}: {len(hits)} match(es) ---")
        for off in offsets[: len(hits)]:
            left = max(0, off - ctx)
            right = min(len(text), off + len(query) + ctx)
            snippet = text[left:right].replace("\n", " ").strip()
            print(f"  ...{snippet}...")

    if total_hits == 0:
        print(f"No matches for {query!r}.")
    else:
        print(f"\nTotal: {total_hits} match(es).")


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF parsing (PyMuPDF)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_text = sub.add_parser("text", help="Plain text extraction")
    p_text.add_argument("path")
    p_text.add_argument("--pages", help="Page range, e.g. 0-4 or 3")

    p_md = sub.add_parser("markdown", help="Markdown conversion (LLM-friendly)")
    p_md.add_argument("path")
    p_md.add_argument("--pages", help="Page range, e.g. 0-9 or 3")
    p_md.add_argument("--save", help="Write markdown to file instead of stdout")

    p_meta = sub.add_parser("metadata", help="Title, author, page count")
    p_meta.add_argument("path")

    p_tab = sub.add_parser("tables", help="Extract tables")
    p_tab.add_argument("path")
    p_tab.add_argument("--pages", help="Page range, e.g. 2-5")

    p_img = sub.add_parser("images", help="Extract embedded images")
    p_img.add_argument("path")
    p_img.add_argument("--output-dir", required=True, help="Output directory for images")

    p_search = sub.add_parser("search", help="Search text across pages")
    p_search.add_argument("path")
    p_search.add_argument("query")
    p_search.add_argument("--context", type=int, default=120, help="Chars of context per match")

    args = parser.parse_args()
    handlers = {
        "text": cmd_text,
        "markdown": cmd_markdown,
        "metadata": cmd_metadata,
        "tables": cmd_tables,
        "images": cmd_images,
        "search": cmd_search,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
