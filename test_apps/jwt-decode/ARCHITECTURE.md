# Architecture

## Overview

The `jwt-decode` CLI tool is designed to read a JWT token from a specific file path (`~/.vantage-cli/token_cache/dev/access.token`) and display its decoded components in a human-readable format. The tool processes the JWT by parsing its header and payload, formatting timestamps, and presenting a summary of key claims.

```
┌─────────────────────────────────────┐
│ ~/.vantage-cli/token_cache/dev/access.token │
└─────────────┬─────────────────────┘
              │
              ▼
┌─────────────────────────────────────┐
│         jwt-decode CLI              │
│        ┌───────────┐                │
│        │  decode() │                │
│        └───────────┘                │
│              │                      │
│              ▼                      │
│     ┌─────────────────────────┐     │
│     │   _parse_jwt()          │     │
│     │   _format_timestamp()   │     │
│     │   _format_expiry()      │     │
│     └─────────────────────────┘     │
│              │                      │
│              ▼                      │
│     ┌─────────────────────────┐     │
│     │   _print_json()         │     │
│     │   _print_summary()      │     │
│     └─────────────────────────┘     │
│              │                      │
│              ▼                      │
│     ┌─────────────────────────┐     │
│     │   Pretty printed output │     │
│     └─────────────────────────┘     │
└─────────────────────────────────────┘
```

## Module Layout

| Module Path | Role | Key Public Symbols |
|-------------|------|-------------------|
| `jwt_decode.py` | Main application logic | `decode`, `main` |

## Data Flow

1. CLI tool reads JWT from `~/.vantage-cli/token_cache/dev/access.token`
2. Input token is passed to `decode()` function
3. `decode()` calls `_parse_jwt()` to split token into header and payload components
4. Timestamps within payload are formatted using `_format_timestamp()`
5. Expiry timestamp is formatted using `_format_expiry()`
6. Formatted data structures are passed to `_print_json()` for JSON display
7. Summary information is extracted and displayed via `_print_summary()`
8. Final output includes header, payload, and summary sections with optional colorization

## Concurrency Model

No concurrency mechanisms are used. The application operates synchronously with a single-threaded execution model.

## Error Handling Strategy

The tool uses standard Python exception handling with exit codes:
- Exit code 1: File not found or invalid JWT format
- Exit code 2: Permission denied accessing token file
- Exit code 3: Invalid base64url encoding in JWT
- Standard library exceptions are caught and converted to appropriate exit codes
- All errors are logged to stderr before program termination

## Extension Points

- New formatting functions can be added to `_print_summary()` to handle additional claim types
- Color scheme can be extended by modifying `_colorize()` function
- Additional output formats can be implemented by adding new print functions
- New token sources can be supported by extending the file path resolution logic in `main()`

## Performance Notes

The tool has minimal performance requirements as it processes only a single JWT token. All operations are CPU-bound with negligible I/O overhead. Base64 decoding and JSON parsing are optimized through standard library usage. Memory usage scales linearly with token size.

## Testing Strategy

Testing should cover:
- Valid JWT token parsing with various claim sets
- Invalid token format handling with appropriate error messages
- Missing token file scenarios
- Permission denied scenarios
- Edge case timestamp values
- Colorized vs non-colorized output consistency
- Command-line argument validation
- Cross-platform path handling for token file location