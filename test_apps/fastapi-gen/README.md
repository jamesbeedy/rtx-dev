# fastapi-gen

CLI tool for scaffolding new FastAPI projects using string.Template templates.

## features

- Generates complete FastAPI project structure with minimal dependencies
- Supports custom target directories and force overwrite options
- Skips Dockerfile generation when not needed
- Uses only standard library `string.Template` for templating
- Includes comprehensive test suite and health check
- Zero external dependencies beyond Python standard library

## installation

Install using uv:

```bash
uv tool install .
```

Or install in development mode using pip:

```bash
pip install -e .
```

## quickstart

Scaffold a new FastAPI project with a single command:

```bash
fastapi-gen myapp
```

## usage

The `fastapi-gen` CLI supports the following arguments:

| Flag | Description |
|------|-------------|
| `project_name` (positional) | Name of the project to create |
| `--dir <path>` | Target directory for the project (default: current directory) |
| `--no-docker` | Skip Dockerfile generation |
| `--force` | Overwrite existing directories |

Example session:

```bash
$ fastapi-gen myapp
Creating myapp/pyproject.toml
Creating myapp/README.md
Creating myapp/Dockerfile
Creating myapp/.gitignore
Creating myapp/myapp/__init__.py
Creating myapp/myapp/main.py
Creating myapp/myapp/config.py
Creating myapp/tests/__init__.py
Creating myapp/tests/test_health.py
$ cd myapp && uv run uvicorn myapp.main:app --reload
```

Create project in a specific directory:

```bash
$ fastapi-gen --dir /home/user/projects myapp
```

Generate without Docker support:

```bash
$ fastapi-gen --no-docker myapp
```

## configuration

No configuration files or environment variables required. All settings are controlled via CLI flags.

## architecture

The tool is composed of four core modules:

- `main.py`: Entry point handling CLI argument parsing and orchestration
- `generator.py`: Core logic for validating names, creating directories, and writing files
- `templates.py`: Template definitions using `string.Template` for project scaffolding
- `utils.py`: Utility functions for path validation and file I/O operations

## development

To set up a development environment:

1. Create a virtual environment with `uv venv`
2. Activate the environment
3. Install in editable mode: `pip install -e .`
4. Run tests with `python -m pytest`

Code follows PEP 8 style guidelines with consistent formatting.

## troubleshooting

**Error: "Directory already exists"**
Resolution: Use `--force` flag to overwrite existing directories.

**Error: "Invalid project name"**
Resolution: Ensure project name contains only alphanumeric characters and underscores.

**Error: "Permission denied"**
Resolution: Check write permissions on target directory.

**Error: "Template not found"**
Resolution: Confirm all template files exist in the package resources.

## license

MIT License