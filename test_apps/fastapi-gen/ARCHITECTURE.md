# Architecture

## Overview

The `fastapi-gen` CLI tool is a lightweight scaffolding utility that generates new FastAPI projects using Python's built-in `string.Template` system instead of Jinja2. The tool accepts a project name and optional configuration parameters, then creates a complete project structure with pre-defined templates.

```
┌─────────────────┐
│   Input         │
│                 │
│  project_name   │
│  --dir          │
│  --no-docker    │
│  --force        │
└─────────┬───────┘
          │
┌─────────▼────────┐
│   Processing     │
│                  │
│  validate_project_name() │
│  create_directory()     │
│  write_file()           │
│  generate_project()     │
└─────────┬────────┘
          │
┌─────────▼────────┐
│   Output         │
│                  │
│  Project directory with files │
└──────────────────┘
```

## Module Layout

| Module Path | Role | Key Public Symbols |
|-------------|------|-------------------|
| `fastapi_gen/main.py` | Entry point and CLI argument parsing | `main`, `parser` |
| `fastapi_gen/generator.py` | Core project generation logic | `generate_project` |
| `fastapi_gen/utils.py` | Utility functions for file operations | `validate_project_name`, `create_directory`, `write_file` |

## Data Flow

1. CLI arguments are parsed via argparse, including project name, target directory, Docker flag, and force overwrite option
2. Project name validation occurs through `validate_project_name()` function
3. Target directory creation happens via `create_directory()` if it doesn't exist
4. Template-based project generation occurs in `generate_project()` which:
   - Reads template files from embedded resources
   - Processes each template using `string.Template` substitution
   - Writes generated files to the target directory
5. Files are written using `write_file()` with proper error handling
6. Generated project structure includes standard FastAPI components like `main.py`, `requirements.txt`, `.gitignore`, and optionally `Dockerfile`

## Concurrency Model

No concurrency is used. The tool operates synchronously with a single-threaded execution model. All file I/O operations occur sequentially without parallelization or async processing.

## Error Handling Strategy

Exit codes:
- 0: Successful completion
- 1: Invalid project name
- 2: Directory already exists and --force not specified
- 3: File system errors during creation/writing
- 4: Template processing failures

Exception types:
- `ValueError`: For invalid project names
- `FileExistsError`: When directory exists and --force not specified
- `IOError`: For file system access issues
- `TemplateError`: For template processing failures (from string.Template)

## Extension Points

- Template system: Add new template files to the resource bundle
- Project structure: Extend `generate_project()` to include additional directories or files
- Validation rules: Modify `validate_project_name()` to add custom naming constraints
- CLI options: Extend `parser` in main module to support new flags
- File generation: Add new file handlers in `generate_project()` function

## Performance Notes

The tool has minimal performance overhead due to its synchronous nature and simple file operations. Template processing is fast as it uses Python's optimized `string.Template` implementation. Memory usage remains constant regardless of project size since templates are processed in-place rather than loaded entirely into memory.

## Testing Strategy

Unit tests cover:
- `validate_project_name()` with valid/invalid project names
- `create_directory()` with existing/non-existing paths
- `write_file()` with various content types and permissions
- `generate_project()` with different configuration combinations
- Edge cases like empty project names, special characters, and long names

Integration tests verify:
- Complete project generation workflow
- File content correctness against expected templates
- Directory structure integrity
- Error conditions and appropriate exit codes
- Dockerfile generation toggle behavior
- Force overwrite functionality