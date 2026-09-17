"""Web search and page reading.

No search API key: results come from DuckDuckGo's HTML endpoint, which needs no
account. Scraped HTML is inherently brittle, so every failure here is reported
as a tool error the model can work around rather than an exception.
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote_plus, unquote, urlparse

from ..backends._http import HTTPError, fetch_text
from . import Tool, ToolResult

SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"

_RESULT = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="(?P<url>[^"]+)"[^>]*>'
    r"(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(?P<text>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SCRIPTS = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")
_BLANKS = re.compile(r"\n{3,}")


def strip_html(markup: str) -> str:
    """Turn a page into something worth putting in a prompt."""
    text = _SCRIPTS.sub(" ", markup)
    text = re.sub(r"<(br|/p|/div|/h\d|/li)[^>]*>", "\n", text, flags=re.I)
    text = _TAGS.sub(" ", text)
    text = html.unescape(text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return _BLANKS.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def _clean_link(raw: str) -> str:
    """DuckDuckGo wraps results in a redirect; unwrap it."""
    link = html.unescape(raw)
    if "uddg=" in link:
        link = unquote(link.split("uddg=", 1)[1].split("&", 1)[0])
    if link.startswith("//"):
        link = "https:" + link
    return link


def parse_results(markup: str, limit: int = 5) -> list[dict]:
    snippets = [strip_html(m.group("text")) for m in _SNIPPET.finditer(markup)]
    results = []
    for index, match in enumerate(_RESULT.finditer(markup)):
        if len(results) >= limit:
            break
        results.append(
            {
                "title": strip_html(match.group("title")),
                "url": _clean_link(match.group("url")),
                "snippet": snippets[index] if index < len(snippets) else "",
            }
        )
    return results


def tools(config) -> list[Tool]:
    def web_search(query: str, limit: int = 5) -> ToolResult:
        count = max(1, min(int(limit), 8))
        try:
            markup = fetch_text(SEARCH_URL.format(query=quote_plus(query)))
        except HTTPError as exc:
            return ToolResult(f"Search failed: {exc}", is_error=True)
        results = parse_results(markup, count)
        if not results:
            return ToolResult(f"No results for {query!r}.", is_error=True)
        lines = []
        for item in results:
            lines.append(f"{item['title']}\n{item['url']}\n{item['snippet']}".strip())
        return ToolResult(
            "\n\n".join(lines), display=f"searched the web for {query!r}"
        )

    def fetch_url(url: str, max_characters: int = 6000) -> ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return ToolResult("Only http and https URLs can be fetched.", is_error=True)
        try:
            markup = fetch_text(url)
        except HTTPError as exc:
            return ToolResult(f"Could not fetch that page: {exc}", is_error=True)
        text = strip_html(markup)
        limit = max(500, min(int(max_characters), 20_000))
        if len(text) > limit:
            text = text[:limit] + "\n... (truncated)"
        return ToolResult(
            f"{url}\n\n{text}", display=f"read {parsed.netloc}"
        )

    return [
        Tool(
            name="web_search",
            description=(
                "Search the web and get titles, links and snippets. Use this "
                "for anything current -- news, prices, opening times, "
                "documentation -- rather than answering from memory."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for."},
                    "limit": {
                        "type": "integer",
                        "description": "How many results (1-8, default 5).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            handler=web_search,
            summarize=lambda a: f"search the web for {a.get('query')!r}",
        ),
        Tool(
            name="fetch_url",
            description=(
                "Fetch a web page and read its text. Use it after a search when "
                "the snippet is not enough to answer properly."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The page to read."},
                    "max_characters": {
                        "type": "integer",
                        "description": "How much text to keep (default 6000).",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=fetch_url,
            summarize=lambda a: f"read {a.get('url')}",
        ),
    ]
