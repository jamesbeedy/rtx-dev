# ARCHITECTURE

## Overview

The `multi-exec` CLI tool executes multiple shell commands in parallel using Python's `ThreadPoolExecutor`. It processes command inputs through a structured pipeline that handles execution, timing, and result formatting. The tool supports both direct command arguments and file-based command lists, with configurable parallelism limits and timeouts.

```
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│   Inputs    │───▶│   Processing │───▶│   Output     │
│  Commands   │    │   Pipeline   │    │   Format     │
└─────────────┘    └──────────────┘    └──────────────┘
     │                   │                    │
     ▼                   ▼                    ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│ Command     │    │ Thread Pool  │    │ Table/JSON   │
│ Arguments   │    │ Executor     │    │ Formatting   │
│ File Input  │    │              │    │              │
└─────────────┘    └──────────────┘    └──────────────┘
```

## Module Layout

| Module Path | Role | Key Public Symbols |
|-------------|------|-------------------|
| `multi_exec.py` | Main application logic | `run_command`, `print_table`, `process_commands`, `main` |
| `__main__.py` | Entry point | N/A |

## Data Flow

1. CLI arguments are parsed using `argparse` to collect commands, file input, and configuration options
2. Commands are validated and prepared for execution
3. Each command is submitted to a `ThreadPoolExecutor` with optional timeout
4. Execution results are collected with exit codes, runtime, and timeout status
5. Results are formatted into either tabular or JSON output based on user preference
6. Formatted output is displayed to console with optional colorization

## Concurrency Model

The application uses `concurrent.futures.ThreadPoolExecutor` to manage parallel command execution. By default, it creates a thread pool with a worker count equal to the number of available CPU cores. Users can override this via `--max-parallel` argument. Each command execution is wrapped in a timeout mechanism using `concurrent.futures.wait()` with `return_when=FIRST_EXCEPTION`. A semaphore ensures that the maximum parallel execution limit is respected when specified.

## Error Handling Strategy

The tool captures exceptions during command execution including `subprocess.TimeoutExpired` for timeouts and general `subprocess.CalledProcessError` for non-zero exit codes. Exit codes from commands are preserved and used for status reporting. Timeout exceptions are specifically handled to mark commands as "timed out" rather than failed. All errors are logged and propagated through the result structure for consistent display. The application exits with code 0 on successful completion, or non-zero if critical failures occur during setup or execution.

## Extension Points

- **New Output Formats**: Add new formatter functions that accept `results` list and return formatted strings
- **New Command Sources**: Implement additional input handlers for different data sources
- **Alternative Executors**: Replace `ThreadPoolExecutor` with custom executor implementations
- **New Status Indicators**: Extend `format_status` function to handle additional result states
- **Custom Timeouts**: Implement alternative timeout strategies or configurations

## Performance Notes

The tool scales linearly with available CPU cores up to the system limit. Memory usage grows proportionally to the number of concurrent commands. I/O operations are optimized by using asynchronous waiting mechanisms. Command execution time is measured precisely using `time.time()` for accurate duration reporting. The default thread pool size is set to `min(32, os.cpu_count() + 4)` to balance resource utilization with overhead.

## Testing Strategy

Unit tests cover individual functions (`run_command`, `format_duration`, `format_status`) with mocked subprocess behavior and various edge cases. Integration tests validate end-to-end command execution with real subprocess calls. Test scenarios include normal execution, timeout conditions, non-zero exit codes, and error handling paths. Parallel execution is tested with varying thread counts and timeout values. Output formatting is verified against expected table and JSON structures. System tests validate CLI argument parsing and configuration option behavior.