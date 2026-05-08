"""
A module for crawling URLs in parallel with support for text extraction and
structured output formatting.

This module provides functionality to asynchronously fetch URLs, optionally
extract text from HTML pages, and save results to disk with proper
sanitization of filenames. It supports various configuration options including
concurrency limits, timeouts, and custom user agents.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


def sanitize_filename(url: str) -> str:
    """Sanitizes a URL into a valid filesystem filename.

    This function takes a URL and converts it into a filename by:
    1. Extracting the netloc and path components
    2. Including query parameters if present
    3. Replacing problematic characters with underscores
    4. Truncating to 200 characters maximum

    Args:
        url: The URL to sanitize.

    Returns:
        A sanitized filename string safe for filesystem use.
    """
    parsed = urlparse(url)
    base = (parsed.netloc or "") + (parsed.path or "/")
    if parsed.query:
        base += "?" + parsed.query
    sanitized = re.sub(r"[/:?&=#]", "_", base)
    sanitized = sanitized.strip("_") or "index"
    return sanitized[:200]


async def fetch_url(
    client: httpx.AsyncClient,
    url: str,
    extract_text: bool,
    timeout: int,
    user_agent: str,
    output_dir: str,
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    """Fetches a single URL and saves its content to disk.

    This function performs an HTTP GET request to the specified URL using
    the provided client and saves the result to a file in the output directory.
    If extract_text is True, only the text content will be saved.

    Args:
        client: An initialized AsyncClient instance for making requests.
        url: The URL to fetch.
        extract_text: Whether to extract text content instead of saving HTML.
        timeout: Request timeout in seconds.
        user_agent: User agent string to use for requests.
        output_dir: Directory where files should be saved.
        semaphore: Semaphore to limit concurrent requests.

    Returns:
        A dictionary containing metadata about the fetch operation including:
        - url: The requested URL
        - status: HTTP status code or None on error
        - bytes_written: Number of bytes written to file
        - duration_s: Time taken for the operation in seconds
        - filepath: Path to saved file or None if not saved
        - error: Error message if operation failed, otherwise None
    """
    async with semaphore:
        start = time.time()
        record: Dict[str, Any] = {
            "url": url,
            "status": None,
            "bytes_written": 0,
            "duration_s": 0.0,
            "filepath": None,
            "error": None,
        }
        try:
            r = await client.get(
                url,
                timeout=timeout,
                headers={"User-Agent": user_agent},
                follow_redirects=True,
            )
            record["status"] = r.status_code
            if r.status_code < 400:
                ext = ".txt" if extract_text else ".html"
                filename = sanitize_filename(url) + ext
                filepath = os.path.join(output_dir, filename)
                content = r.text
                if extract_text:
                    soup = BeautifulSoup(content, "html.parser")
                    content = soup.get_text(separator="\n", strip=True)
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                record["bytes_written"] = len(content.encode("utf-8"))
                record["filepath"] = filepath
            else:
                record["error"] = f"HTTP {r.status_code}"
        except Exception as e:
            record["error"] = str(e)
        record["duration_s"] = round(time.time() - start, 3)
        return record


def print_table(results: List[Dict[str, Any]], use_color: bool = True) -> None:
    """Prints a formatted table of crawl results to stdout.

    This function displays a tabular view of crawl results with status colors
    when supported by the terminal. It shows URL, status code, bytes transferred,
    and duration for each crawled item.

    Args:
        results: List of dictionaries containing crawl result data.
        use_color: Whether to use ANSI color codes in output (default: True).
    """
    if not results:
        return
    url_w = min(60, max(len(r["url"]) for r in results))
    print(f"{'URL':<{url_w}}  {'STATUS':<7}  {'BYTES':>10}  {'TIME':>8}")
    print("-" * (url_w + 30))
    for r in results:
        url_disp = r["url"]
        if len(url_disp) > url_w:
            url_disp = url_disp[: url_w - 1] + "…"
        status = str(r["status"]) if r["status"] is not None else "ERR"
        if use_color:
            if r["error"] is None and r["status"] and 200 <= r["status"] < 300:
                status = f"\033[32m{status:<7}\033[0m"
            else:
                status = f"\033[31m{status:<7}\033[0m"
        else:
            status = f"{status:<7}"
        print(f"{url_disp:<{url_w}}  {status}  {r['bytes_written']:>10}  {r['duration_s']:>7.2f}s")
        if r["error"]:
            print(f"  └─ error: {r['error']}")


async def run_crawl(
    urls: List[str],
    output_dir: str,
    concurrency: int,
    extract_text: bool,
    timeout: int,
    user_agent: str,
    json_output: bool,
) -> int:
    """Runs the URL crawling process with the given configuration.

    This function orchestrates the crawling of multiple URLs in parallel,
    handling file creation and result reporting.

    Args:
        urls: List of URLs to crawl.
        output_dir: Directory where crawled files should be saved.
        concurrency: Maximum number of concurrent HTTP requests.
        extract_text: Whether to extract text content instead of saving HTML.
        timeout: Per-request timeout in seconds.
        user_agent: Custom user agent string for HTTP requests.
        json_output: Whether to output results as JSON instead of formatted table.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    os.makedirs(output_dir, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        tasks = [
            fetch_url(client, u, extract_text, timeout, user_agent, output_dir, semaphore)
            for u in urls
        ]
        results = await asyncio.gather(*tasks)
    success = all(r["error"] is None for r in results)
    if json_output:
        print(json.dumps(results, indent=2))
    else:
        print_table(results, use_color=sys.stdout.isatty())
    return 0 if success else 1


def main() -> int:
    """Main entry point for the crawler CLI application.

    Parses command-line arguments and initiates the crawling process.
    Supports reading URLs from both command line and file inputs.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = argparse.ArgumentParser(description="Crawl URLs in parallel.")
    parser.add_argument("urls", nargs="*", help="URLs to crawl")
    parser.add_argument("--file", help="File with URLs (one per line)")
    parser.add_argument("--output", default="./crawled", help="Output directory")
    parser.add_argument("--concurrency", type=int, default=8, help="Max concurrent requests")
    parser.add_argument("--extract-text", action="store_true", help="Extract text instead of saving HTML")
    parser.add_argument("--timeout", type=int, default=30, help="Per-request timeout in seconds")
    parser.add_argument("--user-agent", default="crawl/0.1", help="Custom user agent string")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.file:
        with open(args.file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
    if not urls:
        print("No URLs provided.", file=sys.stderr)
        return 1
    return asyncio.run(
        run_crawl(
            urls,
            args.output,
            args.concurrency,
            args.extract_text,
            args.timeout,
            args.user_agent,
            args.json,
        )
    )