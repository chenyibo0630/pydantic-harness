"""Tavily CLI — search and extract, called by agent via bash_execute.

Usage:
    python tavily.py search "query" [--max-results 5] [--search-depth basic|advanced] [--topic general|news|finance] [--include-answer]
    python tavily.py extract "https://url" [--format markdown|text]

Requires TAVILY_API_KEY env var (injected per-skill from config.yaml).
"""

import argparse
import os
import subprocess
import sys

try:
    from tavily import TavilyClient
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "tavily-python", "-q"], check=True)
    from tavily import TavilyClient


def _get_client() -> TavilyClient:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        print("Error: TAVILY_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return TavilyClient(api_key=api_key)


def cmd_search(args: argparse.Namespace) -> None:
    client = _get_client()
    kwargs = {
        "query": args.query,
        "max_results": args.max_results,
        "search_depth": args.search_depth,
    }
    if args.topic:
        kwargs["topic"] = args.topic
    if args.include_answer:
        kwargs["include_answer"] = True

    response = client.search(**kwargs)

    if response.get("answer"):
        print(f"Answer: {response['answer']}\n")

    results = response.get("results", [])
    if not results:
        print("No results found.")
        return

    for i, r in enumerate(results, 1):
        print(f"[{i}] {r.get('title', '')}")
        print(f"    URL: {r.get('url', '')}")
        print(f"    {r.get('content', '')}")
        print()


def cmd_extract(args: argparse.Namespace) -> None:
    client = _get_client()
    kwargs = {"urls": args.url}
    if args.format:
        kwargs["format"] = args.format

    response = client.extract(**kwargs)
    results = response.get("results", [])
    if not results:
        print("No content extracted.")
        return

    for r in results:
        url = r.get("url", "")
        content = r.get("raw_content", "") or r.get("content", "")
        print(f"URL: {url}\n")
        print(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tavily web search & extract")
    sub = parser.add_subparsers(dest="command", required=True)

    # search
    sp = sub.add_parser("search", help="Search the web")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--max-results", type=int, default=5)
    sp.add_argument("--search-depth", choices=["basic", "advanced"], default="basic")
    sp.add_argument("--topic", choices=["general", "news", "finance"])
    sp.add_argument("--include-answer", action="store_true")

    # extract
    ep = sub.add_parser("extract", help="Extract content from URL")
    ep.add_argument("url", help="URL to extract")
    ep.add_argument("--format", choices=["markdown", "text"], default="markdown")

    args = parser.parse_args()
    if args.command == "search":
        cmd_search(args)
    elif args.command == "extract":
        cmd_extract(args)


if __name__ == "__main__":
    main()
