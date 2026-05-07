---
name: pdf
description: Use this skill to read and create local PDF files. Reading defaults to fast plain-text extraction (`text`); also supports metadata, tables, images, search, and slow-but-structured markdown. Creating: text/markdown to PDF, merge, split, watermark. Backed by PyMuPDF — fast, no models, ~25MB.
---

# PDF Skill

Read and create PDFs using PyMuPDF (`pymupdf` + `pymupdf4llm`). Lightweight, instant, no GPU needed.

For scanned PDFs requiring OCR, equations, or complex layouts, this skill is **not** the right choice — note the limitation back to the user instead of producing low-quality output.

Two scripts:
- `parse.py` — read existing PDFs (text, markdown, metadata, tables, images, search)
- `generate.py` — create / modify PDFs (create, merge, split, watermark)

## Reading commands (parse.py)

### Text — plain text extraction (DEFAULT — use this first)

```bash
python {SCRIPTS_DIR}/parse.py text /workspace/report.pdf
```

Options:
- `--pages 0-4` — page range (0-based, inclusive). Single page: `--pages 3`. Omit for full doc.

**This is the right tool for almost every reading task** (summarize, Q&A, search, quote). Speed: ~0.1 sec/page. Output is plain text with page markers; LLMs handle it fine without markdown structure.

### Markdown — convert to markdown (SLOW, use only when structure matters)

```bash
python {SCRIPTS_DIR}/parse.py markdown /workspace/paper.pdf
```

Options:
- `--pages 0-9` — page range
- `--save /workspace/paper.md` — write output to file instead of stdout

**WARNING**: 10–50× slower than `text` because pymupdf4llm runs `find_tables()` on every page (geometric table detection). On a 20-page math-heavy PDF this can take 90+ seconds. Only use when you genuinely need:
- Real markdown tables (use `tables` subcommand instead if that's your only need)
- Inferred headings (`#`, `##`) for outline navigation
- Code-block / list structure preserved

For everything else, prefer `text` — it's faster and usually enough.

### Metadata — title, author, page count

```bash
python {SCRIPTS_DIR}/parse.py metadata /workspace/contract.pdf
```

### Tables — extract tables as markdown

```bash
python {SCRIPTS_DIR}/parse.py tables /workspace/financial.pdf
```

Options:
- `--pages 2-5` — limit to specific pages

### Images — dump embedded images to a directory

```bash
python {SCRIPTS_DIR}/parse.py images /workspace/slides.pdf --output-dir /workspace/slides_images
```

### Search — find text occurrences across pages

```bash
python {SCRIPTS_DIR}/parse.py search /workspace/report.pdf "revenue"
```

Options:
- `--context 200` — chars of surrounding context per match (default 120)

## Generation commands (generate.py)

### Create — text or markdown → PDF

```bash
# Inline text
python {SCRIPTS_DIR}/generate.py create "Hello World" --output /workspace/hello.pdf

# Text from file
python {SCRIPTS_DIR}/generate.py create /workspace/notes.txt --from-file --output /workspace/notes.pdf

# Markdown file → styled PDF (tables, code blocks, headings)
python {SCRIPTS_DIR}/generate.py create /workspace/report.md --format markdown --output /workspace/report.pdf
```

Options:
- `--format text|markdown` — default `text`
- `--from-file` — treat positional arg as file path (text format only; markdown is always a file)
- `--size A4|letter|legal` — page size (default `A4`)

### Merge — concat multiple PDFs

```bash
python {SCRIPTS_DIR}/generate.py merge /workspace/a.pdf /workspace/b.pdf /workspace/c.pdf --output /workspace/merged.pdf
```

### Split — extract a page range to a new PDF

```bash
python {SCRIPTS_DIR}/generate.py split /workspace/big.pdf 0-4 --output /workspace/first5.pdf
python {SCRIPTS_DIR}/generate.py split /workspace/big.pdf 7   --output /workspace/page8.pdf
```

Pages are 0-indexed and inclusive.

### Watermark — overlay text on every page

```bash
python {SCRIPTS_DIR}/generate.py watermark /workspace/report.pdf "DRAFT" --output /workspace/report_draft.pdf
```

Options:
- `--fontsize 60` — default 60
- `--angle 45` — rotation degrees, 0 = horizontal (default 45)
- `--opacity 0.8` — gray level 0–1, lower = darker (default 0.8 for subtle)

## When to Use

**Reading**:
- User uploads or references a local PDF and wants its contents read, summarized, or quoted
- User asks about specific pages, sections, tables, or figures in a PDF
- Document is text-based (digital PDF) — for **scanned** PDFs, OCR is needed (out of scope here)
- Remote URLs: prefer downloading first via `bash_execute` (curl/wget) into `/workspace/`, then run this skill

**Generation**:
- User wants a PDF report from text/markdown content (e.g., "save this as PDF")
- User asks to combine, split, or extract pages from existing PDFs
- User wants a watermark / "DRAFT" / "CONFIDENTIAL" overlay
- For complex layouts (multi-column, precise CSS) the markdown renderer is best-effort — for production-grade typography, fall back to LaTeX or HTML+browser-print outside this skill

## Workflow

1. **Inspect first** — run `metadata` to learn page count and title
2. **Choose extraction(picking the cheap option first)**:
   - **For overview / Q&A / summarize → `text`** ← default
   - For specific term lookup → `search` (avoid dumping whole doc)
   - For numerical data only → `tables`
   - For visual content → `images`
   - For headings/outline preservation → `markdown` (slow, see warning above)
3. **Use `--pages`** to limit extraction on large PDFs (>20 pages) and stay within the context budget

## Notes

- All paths are **inside the sandbox** — typically `/workspace/<file>.pdf`
- Output is printed to stdout; redirect or use `--save` (markdown only) for large output
- First run auto-installs `pymupdf` (~25MB), `pymupdf4llm` (markdown reading), and `markdown` (markdown→PDF). Total <30MB, no models
- Tables: detection is heuristic — verify against the source for critical data
- Generated PDFs use sans-serif body / monospace for `text` format; markdown supports headings, lists, fenced code, tables
