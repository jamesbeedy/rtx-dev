"""
A module for executing multiple shell commands in parallel with various output formats.

This module provides functionality to run shell commands concurrently, with options
for controlling parallelism, timeouts, output formatting, and colorization.
"""

import argparse
import concurrent.futures
import json
import os
import shlex
import subprocess
import sys
import time
from typing import List, Optional, Dict, Any


def run_command(cmd: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    """Run a single command and return its result.

    Args:
        cmd: The shell command to execute as a string
        timeout: Optional timeout in seconds for the command execution

    Returns:
        Dictionary containing command execution results with keys:
        - cmd: The original command string
        - exit_code: The exit code of the command (or -1 on error/timeout)
        - duration_s: Execution duration in seconds
        - stdout: Standard output from the command
        - stderr: Standard error from the command
        - timed_out: Boolean indicating if the command timed out

    """
    start_time = time.time()
    try:
        args = shlex.split(cmd)
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout if timeout else None,
        )
        duration = time.time() - start_time
        return {
            "cmd": cmd,
            "exit_code": result.returncode,
            "duration_s": round(duration, 2),
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return {
            "cmd": cmd,
            "exit_code": -1,
            "duration_s": round(duration, 2),
            "stdout": "",
            "stderr": "",
            "timed_out": True,
        }
    except Exception as e:
        duration = time.time() - start_time
        return {
            "cmd": cmd,
            "exit_code": -1,
            "duration_s": round(duration, 2),
            "stdout": "",
            "stderr": str(e),
            "timed_out": False,
        }


def format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string with 2 decimal places followed by 's'
    """
    return f"{seconds:.2f}s"


def format_status(exit_code: int, timed_out: bool) -> str:
    """Format command execution status for display.

    Args:
        exit_code: The exit code of the command
        timed_out: Whether the command timed out

    Returns:
        Status string: ✓ for success, ✗ for failure, or ✗ TIMEOUT for timeout
    """
    if timed_out:
        return "✗ TIMEOUT"
    elif exit_code == 0:
        return "✓"
    else:
        return "✗"


def print_table(results: List[Dict[str, Any]], use_color: bool = True) -> None:
    """Print command execution results in a formatted table.

    Args:
        results: List of command execution result dictionaries
        use_color: Whether to apply ANSI color codes to output

    """
    if not results:
        return
    max_cmd_width = max(len(r["cmd"]) for r in results)
    cmd_width = max(10, max_cmd_width)
    header = f"{'CMD':<{cmd_width}} {'STATUS':<10} {'EXIT':<5} {'DURATION':<10}"
    print(header)
    for r in results:
        cmd_display = r["cmd"]
        status = format_status(r["exit_code"], r["timed_out"])
        exit_code = r["exit_code"] if not r["timed_out"] else "TIMEOUT"
        duration = format_duration(r["duration_s"])
        if use_color:
            if r["timed_out"] or r["exit_code"] != 0:
                cmd_display = f"\033[31m{cmd_display}\033[0m"
                status = f"\033[31m{status}\033[0m"
            else:
                cmd_display = f"\033[32m{cmd_display}\033[0m"
                status = f"\033[32m{status}\033[0m"
        print(f"{cmd_display:<{cmd_width}} {status:<10} {exit_code!s:<5} {duration:<10}")


def process_commands(
    commands: List[str],
    max_parallel: Optional[int] = None,
    timeout: Optional[float] = None,
    json_output: bool = False,
    no_color: bool = False,
) -> int:
    """Process and execute multiple commands in parallel.

    Args:
        commands: List of shell commands to execute
        max_parallel: Maximum number of parallel command executions (default: CPU count)
        timeout: Per-command timeout in seconds
        json_output: Whether to output results as JSON instead of table format
        no_color: Whether to disable ANSI color codes in output

    Returns:
        Maximum exit code among all executed commands

    """
    if not commands:
        return 0
    if max_parallel is None:
        max_parallel = os.cpu_count() or 4
    results: List[Dict[str, Any]] = []
    max_exit_code = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel) as executor:
        futures = [executor.submit(run_command, cmd, timeout) for cmd in commands]
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            max_exit_code = max(max_exit_code, result["exit_code"])
    if json_output:
        print(json.dumps(results, indent=2))
    else:
        print_table(results, use_color=(not no_color) and sys.stdout.isatty())
    return max_exit_code


def main() -> None:
    """Main entry point for the multi-exec command-line tool.

    Parses command-line arguments and executes the specified commands.
    
    Command-line arguments:
        - commands: Shell commands to execute (positional arguments)
        - --file: File containing commands (one per line)
        - --max-parallel: Maximum parallel executions
        - --json: Output JSON instead of table
        - --no-color: Disable ANSI colors
        - --timeout: Per-command timeout in seconds
    
    Exits with the maximum exit code from all executed commands.
    """
    parser = argparse.ArgumentParser(description="Run multiple shell commands in parallel")
    parser.add_argument("commands", nargs="*", help="Commands to execute")
    parser.add_argument("--file", type=str, help="File containing commands (one per line)")
    parser.add_argument("--max-parallel", type=int, help="Maximum parallel executions")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of table")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    parser.add_argument("--timeout", type=float, help="Per-command timeout in seconds")
    args = parser.parse_args()

    commands = list(args.commands)
    if args.file:
        try:
            with open(args.file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        commands.append(line)
        except FileNotFoundError:
            print(f"Error: File '{args.file}' not found.", file=sys.stderr)
            sys.exit(1)

    if not commands:
        print("No commands provided.", file=sys.stderr)
        sys.exit(1)

    exit_code = process_commands(
        commands,
        max_parallel=args.max_parallel,
        timeout=args.timeout,
        json_output=args.json,
        no_color=args.no_color,
    )
    sys.exit(exit_code)