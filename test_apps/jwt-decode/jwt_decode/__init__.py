"""JWT decoder module for parsing and displaying JWT tokens.

This module provides functionality to decode JWT (JSON Web Token) strings
and display their contents in a human-readable format. It supports parsing
of JWT headers and payloads, formatting of timestamps, and colored output
for improved readability.
"""

import base64
import json
import os
import sys
from datetime import datetime
from typing import Dict, Any, Optional, Tuple


def _base64url_decode(s: str) -> bytes:
    """Decode a base64url-encoded string, handling padding.

    Args:
        s: The base64url-encoded string to decode.

    Returns:
        The decoded bytes object.
    """
    s = s.strip()
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def _parse_jwt(token: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Parse a JWT into header and payload dicts.

    Args:
        token: The JWT string to parse.

    Returns:
        A tuple containing (header_dict, payload_dict).

    Raises:
        ValueError: If the JWT is invalid (does not have exactly 3 segments)
                   or if decoding fails.
    """
    segments = token.split(".")
    if len(segments) != 3:
        raise ValueError("Invalid JWT: must have exactly 3 segments")

    header_b64 = segments[0]
    payload_b64 = segments[1]

    try:
        header_bytes = _base64url_decode(header_b64)
        payload_bytes = _base64url_decode(payload_b64)
    except Exception as e:
        raise ValueError(f"Failed to decode JWT: {e}")

    header = json.loads(header_bytes)
    payload = json.loads(payload_bytes)

    return header, payload


def _format_timestamp(ts: int) -> Tuple[int, str]:
    """Return (unix timestamp, iso8601 string).

    Args:
        ts: Unix timestamp to format.

    Returns:
        A tuple containing (unix_timestamp, iso8601_string).
    """
    return ts, datetime.utcfromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def _format_expiry(exp: int) -> str:
    """Format expiration time into human-readable string.

    Args:
        exp: Unix timestamp representing expiration time.

    Returns:
        Formatted string showing time until expiration or "EXPIRED".
    """
    now = datetime.utcnow()
    exp_dt = datetime.utcfromtimestamp(exp)
    diff = exp_dt - now

    if diff.total_seconds() < 0:
        return "EXPIRED"

    days = diff.days
    hours, remainder = divmod(diff.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        y, d = divmod(days, 365)
        if y > 0:
            parts.append(f"{y}y")
        m, d = divmod(d, 30)
        if m > 0:
            parts.append(f"{m}mo")
        if d > 0:
            parts.append(f"{d}d")
    else:
        if hours > 0:
            parts.append(f"{hours}h")
        if minutes > 0:
            parts.append(f"{minutes}m")
        if seconds > 0:
            parts.append(f"{seconds}s")

    return f"valid ({' '.join(parts)})"


CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


def _colorize(text: str, color_code: str) -> str:
    """Wrap text with ANSI color code if enabled.

    Args:
        text: The text to colorize.
        color_code: The ANSI color code to apply.

    Returns:
        The colorized text string.
    """
    return f"{color_code}{text}{RESET}"


def _print_json(obj: Dict[str, Any], colorize: bool = True) -> None:
    """Pretty-print a dict as JSON, optionally with cyan keys.

    Args:
        obj: The dictionary to print as JSON.
        colorize: If True, colorize keys with cyan.
    """
    lines = json.dumps(obj, indent=2).splitlines()
    for line in lines:
        if colorize and ":" in line:
            key_part, _, value_part = line.partition(":")
            key_stripped = key_part.rstrip()
            leading_ws = key_part[: len(key_part) - len(key_part.lstrip())]
            print(f"{leading_ws}{_colorize(key_stripped.lstrip(), CYAN)}:{value_part}")
        else:
            print(line)


def _print_summary(payload: Dict[str, Any], colorize: bool = True) -> None:
    """Print a human-readable summary of the JWT payload.

    Args:
        payload: The JWT payload dictionary.
        colorize: If True, colorize output with ANSI codes.
    """
    exp = payload.get("exp")
    iss = payload.get("iss")
    aud = payload.get("aud")
    sub = payload.get("sub")

    print("\nSummary:")
    if iss:
        print(f"  Issuer:   {_colorize(str(iss), YELLOW) if colorize else iss}")
    if aud:
        print(f"  Audience: {_colorize(str(aud), YELLOW) if colorize else aud}")
    if sub:
        print(f"  Subject:  {_colorize(str(sub), YELLOW) if colorize else sub}")

    if exp:
        exp_unix, exp_iso = _format_timestamp(exp)
        status = _format_expiry(exp)
        exp_str = f"{exp_unix} ({exp_iso})"
        if colorize:
            exp_str = _colorize(exp_str, YELLOW)
            if status == "EXPIRED":
                status = _colorize(status, RED)
            else:
                status = _colorize(status, YELLOW)
        print(f"  Expiry:   {exp_str}")
        print(f"  Status:   {status}")


def decode(token: str, raw: bool = False, colorize: bool = True) -> None:
    """Decode a JWT and print it.

    Args:
        token: The JWT string.
        raw: If True, only print the payload as compact JSON.
        colorize: If True, use ANSI colors.
    """
    try:
        header, payload = _parse_jwt(token)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)

    if raw:
        print(json.dumps(payload, separators=(",", ":")))
        return

    _print_json(header, colorize)
    _print_json(payload, colorize)
    _print_summary(payload, colorize)


def main() -> None:
    """Main entry point for the JWT decoder CLI tool.

    Parses command-line arguments and decodes JWT from file.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Decode a JWT token.")
    parser.add_argument(
        "--token",
        default=os.path.expanduser("~/.vantage-cli/token_cache/dev/access.token"),
        help="Path to the JWT file (default: ~/.vantage-cli/token_cache/dev/access.token)",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print only the decoded payload as compact JSON",
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Suppress ANSI color codes"
    )

    args = parser.parse_args()

    try:
        with open(args.token, "r") as f:
            token = f.read().strip()
    except FileNotFoundError:
        print(f"Error: Token file not found: {args.token}", file=sys.stderr)
        sys.exit(2)

    colorize = not args.no_color and sys.stdout.isatty()
    decode(token, raw=args.raw, colorize=colorize)