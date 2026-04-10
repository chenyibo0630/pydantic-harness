---
name: tavily
description: Use this skill for web search (find information by keywords) and web fetch (extract content from specific URLs). Covers current events, documentation, news, and any web-based research.
---

# Tavily Skill

Provides two capabilities: **search** (find information) and **extract** (fetch page content).

## Commands

### Search — find information by keywords

```bash
python {SCRIPTS_DIR}/search.py search "YOUR_QUERY"
```

Options:
- `--max-results 5` — number of results (default 5)
- `--search-depth basic|advanced` — advanced for deeper results
- `--topic general|news|finance` — topic filter
- `--include-answer` — include a direct AI-generated answer

### Extract — fetch and read a specific URL

```bash
python {SCRIPTS_DIR}/search.py extract "https://example.com/page"
```

Options:
- `--format markdown|text` — output format (default markdown)

## When to Use

- **Search**: user asks about recent events, needs documentation, or asks questions you're unsure about
- **Extract**: user provides a URL to read, or you found a URL via search and need the full content

## Workflow

1. **Search** to find relevant URLs and summaries
2. If more detail is needed, **extract** the most relevant URL
3. Synthesize and cite sources
