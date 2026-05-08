# Crawl

A fast, parallel HTTP crawler for fetching and extracting content from web pages.

## Features

- Fetches multiple URLs concurrently using `httpx` and `asyncio`
- Extracts plain text from HTML pages with `BeautifulSoup`
- Supports input via command-line arguments or file lists
- Configurable concurrency, timeouts, and user agents
- Colorized table output or JSON summary mode
- Sanitized filename generation for safe storage

## Installation

Install using `uv`:

```bash
uv tool install .
```

Or install in development mode using `pip`:

```bash
pip install -e .
```

## Quickstart

Fetch and save HTML from two URLs:

```bash
crawl https://example.com https://python.org
```

## Usage

The `crawl` command supports the following flags:

| Flag | Description |
|------|-------------|
| `URL...` | URLs to crawl (positional arguments) |
| `--file FILE` | File containing URLs (one per line) |
| `--output DIR` | Output directory (default: `./crawled`) |
| `--concurrency N` | Max concurrent requests (default: 8) |
| `--extract-text` | Save extracted text instead of raw HTML |
| `--timeout SECONDS` | Per-request timeout (default: 30) |
| `--user-agent STRING` | Custom user agent (default: `crawl/0.1`) |
| `--json` | Print JSON summary instead of table |

### Examples

Fetch and save HTML:

```bash
crawl https://example.com https://python.org
```

Fetch and extract text:

```bash
crawl --extract-text https://example.com
```

Print JSON summary instead of table:

```bash
crawl --json https://example.com
```

## Configuration

No configuration files are supported. All settings can be controlled via command-line flags or environment variables where applicable.

## Architecture

The `crawl` tool is built around three core modules:
- `main`: Handles CLI argument parsing and orchestration
- `utils`: Provides helper functions like `sanitize_filename`
- `output`: Manages formatted output and result printing

## Development

Set up a development virtual environment using `uv`:

```bash
uv venv
source .venv/bin/activate
uv pip install -e .
```

Run tests with:

```bash
python -m pytest
```

Code follows PEP 8 style guidelines with black formatting.

## Troubleshooting

**No output files created**
Ensure the output directory exists or use the default `./crawled`.

**Rate limiting errors**
Reduce concurrency with `--concurrency N` or add delays between requests.

**Timeout failures**
Increase timeout value with `--timeout SECONDS`.

**Missing dependencies**
Reinstall with `pip install -e .` to ensure all dependencies are installed.

## License

MIT License