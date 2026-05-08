"""Web search via DuckDuckGo HTML. Ported from mcp-server/vllm_mcp.py."""
from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from . import Tool, ToolContext, register

_BROWSER_UAS = [
    "Mozilla/5.0 (X11; Linux x86_64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]
_DDG_MIN_INTERVAL = float(os.environ.get("DDG_MIN_INTERVAL_S", "1.5"))
_ddg_last_call: float = 0.0
_ddg_lock: asyncio.Lock | None = None


def _headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(_BROWSER_UAS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    }


async def _throttle() -> None:
    global _ddg_last_call, _ddg_lock
    if _ddg_lock is None:
        _ddg_lock = asyncio.Lock()
    async with _ddg_lock:
        elapsed = time.monotonic() - _ddg_last_call
        wait = _DDG_MIN_INTERVAL - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        _ddg_last_call = time.monotonic()


async def _web_search(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    query = args.get("query", "")
    max_results = int(args.get("max_results", 5))
    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query, "kl": "us-en"},
                headers=_headers(),
            )
            r.raise_for_status()
            html = r.text
    except httpx.HTTPError as e:
        return {"error": f"DDG request failed: {type(e).__name__}: {e}"}
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, str]] = []
    for div in soup.select("div.result")[:max_results]:
        a = div.select_one("a.result__a")
        snip = div.select_one("a.result__snippet, div.result__snippet")
        if a is None:
            continue
        url = a.get("href") or ""
        if url.startswith("/l/?") or url.startswith("//duckduckgo.com/l/"):
            qs = parse_qs(urlparse(url).query)
            if "uddg" in qs:
                url = unquote(qs["uddg"][0])
        results.append({
            "title": a.get_text(strip=True),
            "url": url,
            "snippet": snip.get_text(strip=True) if snip else "",
        })
    return {"results": results}


web_search_tool = register(Tool(
    name="web_search",
    schema={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the public web via DuckDuckGo and return up to "
                           "`max_results` snippets. Use for current events, exact "
                           "API details, version numbers, recent docs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    execute=_web_search,
))
