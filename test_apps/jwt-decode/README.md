# Jwt-Decode

A command-line utility for decoding and analyzing JSON Web Tokens with detailed header, payload, and expiration summaries.

## Features

- Decodes JWT tokens and displays structured header and payload data
- Provides human-readable expiration time summaries
- Supports custom token file paths via CLI argument
- Outputs compact JSON when using `--raw` flag
- Colorized terminal output with optional disabling via `--no-color`
- Parses and formats standard JWT timestamp fields (exp, iat, nbf)

## Installation

Install using `uv`:

```bash
uv tool install .
```

Alternatively, install in development mode using pip:

```bash
pip install -e .
```

## Quickstart

```bash
jwt-decode
```

## Usage

| Flag         | Description                                                                 |
|--------------|-----------------------------------------------------------------------------|
| `--token`    | Path to the JWT file (default: `~/.vantage-cli/token_cache/dev/access.token`) |
| `--raw`      | Print only the decoded payload as compact JSON                              |
| `--no-color` | Suppress ANSI color codes                                                   |

### Example Sessions

```bash
$ jwt-decode --token ./fake_token.txt
{
  "alg": "RS256"
}
{
  "sub": "example",
  "iss": "https://issuer.example.com",
  "aud": "https://audience.example.com",
  "exp": 1700000000,
  "iat": 1690000000,
  "nbf": 1690000000
}
VALID (expires in 1y 2mo 3d)
```

```bash
$ jwt-decode --raw --token ./fake_token.txt
{"sub":"example","iss":"https://issuer.example.com","aud":"https://audience.example.com","exp":1700000000,"iat":1690000000,"nbf":1690000000}
```

### Security Note

Decoding a JWT **does not** verify its signature. It only parses the header and payload. To verify signatures, use a proper JWT library with key validation.

## Configuration

No configuration files or environment variables required. Default token path is hardcoded to `~/.vantage-cli/token_cache/dev/access.token`.

## Architecture

The tool is organized into modular components handling core functionality. The `_base64url_decode` function decodes base64url-encoded segments, `_parse_jwt` extracts and validates header and payload, and helper functions format timestamps and output. The `main` function orchestrates CLI argument parsing and execution flow.

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

Code follows PEP 8 style guidelines with consistent naming and type hints.

## Troubleshooting

- **Invalid token format**: Ensure input is a valid JWT string with three base64url-encoded segments.
- **File not found**: Verify the specified token path exists and is readable.
- **Missing default token**: The default path `~/.vantage-cli/token_cache/dev/access.token` must exist if no `--token` is provided.
- **Color output issues**: Use `--no-color` to disable ANSI escape sequences if terminal does not support them.

## License

MIT License