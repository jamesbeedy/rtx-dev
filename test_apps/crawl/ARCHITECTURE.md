# ARCHITECTURE

## Overview

The `crawl` CLI tool is a parallel HTTP crawler that processes URLs concurrently using `httpx` and `asyncio`. It supports fetching HTML content or extracting text from web pages, with configurable concurrency limits and request timeouts. The tool accepts URLs as command-line arguments or from a file, and can output results in either HTML format or extracted text, storing results in a specified directory.

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Input     │───▶│  Processing │───▶│   Output    │
│             │    │             │    │             │
│  URLs       │    │  Async      │    │  Files      │
│  (CLI/file) │    │  Requests   │    │  (HTML/text)│
└─────────────┘    └─────────────┘    └─────────────┘
```

## Module Layout

| Module Path | Role | Key Public Symbols |
|-------------|------|-------------------|
| `crawl.py` | Main application logic | `sanitize_filename`, `print_table`, `main` |

## Data Flow

1. Command-line arguments are parsed using `argparse`
2. URLs are collected from CLI arguments or file input
3. For each URL, an async HTTP request is made using `httpx.AsyncClient`
4. Response data is processed (HTML fetch or text extraction with BeautifulSoup)
5. Results are saved to disk in the specified output directory
6. Summary table or JSON output is displayed based on flags
7. All operations are performed with configurable concurrency limits

## Concurrency Model

The application uses `asyncio` with `httpx.AsyncClient` for concurrent HTTP requests. A semaphore is used to limit maximum concurrent requests to the value specified by `--concurrency` (default 8). Each URL is processed in an asynchronous task within the event loop, allowing up to N simultaneous requests where N is the concurrency limit.

## Error Handling Strategy

The application uses exit codes 1 for general failures and 2 for invalid input. Exception types include `httpx.RequestError` for network issues, `httpx.TimeoutException` for timeouts, and `ValueError` for invalid configuration. All exceptions are caught at the main level and result in appropriate error messages and exit codes.

## Extension Points

- Add new content extraction methods by implementing additional parsing functions
- Extend URL validation by adding custom URL sanitization logic
- Support additional output formats by implementing new file writing functions
- Add proxy support by extending the `httpx.AsyncClient` configuration
- Implement retry logic by wrapping HTTP requests in retry mechanisms

## Performance Notes

The tool's performance scales with the `--concurrency` parameter, with optimal values depending on network conditions and target server capabilities. Memory usage is bounded by the number of concurrent requests. Text extraction adds overhead but reduces storage requirements. Default timeout of 30 seconds balances responsiveness with reliability.

## Testing Strategy

Testing should cover URL validation, concurrent request handling, file I/O operations, text extraction functionality, and error scenarios including timeouts and invalid URLs. Unit tests should mock HTTP responses and file system operations. Integration tests should validate end-to-end crawling behavior with real HTTP endpoints.