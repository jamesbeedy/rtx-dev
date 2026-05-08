# Multi-Exec

Execute multiple shell commands in parallel with real-time status reporting.

## Features

- Parallel execution using `ThreadPoolExecutor` for improved performance
- Real-time status table with color-coded output and duration tracking
- Support for command lists via CLI arguments or file input
- Configurable concurrency limits and per-command timeouts
- JSON output mode for programmatic consumption
- ANSI color support with opt-out for plain text output

## Installation

Install using `uv`:

```bash
uv tool install .
```

Or via `pip` for development:

```bash
pip install -e .
```

## Quickstart

Run multiple commands in parallel:

```bash
multi-exec "ls -la" "echo hi" "sleep 1"
```

## Usage

The `multi-exec` tool accepts shell commands as arguments and executes them in parallel. It supports various configuration options through command-line flags.

### CLI Flags

| Flag | Description |
|------|-------------|
| `COMMANDS...` | One or more shell commands to execute |
| `--file FILE` | Read commands from a file (one per line) |
| `--max-parallel N` | Limit maximum concurrent executions (default: CPU count) |
| `--json` | Output results in JSON format instead of tabular view |
| `--no-color` | Disable ANSI color output |
| `--timeout SECONDS` | Set timeout per command in seconds |

### Examples

**Default table output:**

```bash
$ multi-exec "ls -la" "echo hi" "sleep 1"
CMD             STATUS   EXIT   DURATION
ls -la          ✓        0      0.01s
echo hi         ✓        0      0.00s
sleep 1         ✓        0      1.00s
```

**JSON output:**

```bash
$ multi-exec --json "ls -la" "echo hi"
[
  {
    "cmd": "ls -la",
    "status": "✓",
    "exit": 0,
    "duration": "0.01s"
  },
  {
    "cmd": "echo hi",
    "status": "✓",
    "exit": 0,
    "duration": "0.00s"
  }
]
```

**Using file input:**

```bash
$ echo -e "ls -la\nsleep 2" > commands.txt
$ multi-exec --file commands.txt
CMD             STATUS   EXIT   DURATION
ls -la          ✓        0      0.01s
sleep 2         ✓        0      2.00s
```

## Configuration

No configuration files or environment variables are required. All settings are controlled via command-line flags.

## Architecture

The tool is structured into several core modules:

- **`main()`**: Entry point that parses CLI arguments and orchestrates execution.
- **`process_commands()`**: Manages parallel execution of commands using `ThreadPoolExecutor`.
- **`run_command()`**: Executes individual commands using `subprocess.run` with safe argument parsing.
- **`format_status()` / `format_duration()`**: Utility functions for consistent output formatting.
- **`print_table()`**: Renders results in a formatted table with optional colorization.

## Development

To set up a development environment:

1. Create a virtual environment with `uv venv`
2. Activate the environment
3. Install dependencies with `uv pip install -e .`
4. Run tests with `python -m pytest`
5. Follow PEP 8 style guidelines for code formatting

## Troubleshooting

- **Commands not executing**: Ensure all commands are valid shell syntax and executable.
- **Timeouts not respected**: Verify timeout values are numeric and greater than zero.
- **No output when using `--json`**: Confirm that commands complete successfully before JSON output is generated.
- **High memory usage**: Reduce `--max-parallel` to limit concurrent processes.

## License

MIT License