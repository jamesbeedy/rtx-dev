# kfp-gen

CLI tool for scaffolding Kubeflow Pipelines v2 projects with component examples.

## Features

- Generates fully functional Kubeflow Pipelines v2 project structure
- Includes sample `@dsl.component` definitions for reusable pipeline tasks
- Supports custom output directory with optional overwrite flag
- Creates standard project files including `pyproject.toml`, `Makefile`, and `.gitignore`
- Provides ready-to-run compilation script for pipeline YAML generation
- Compatible with KFP v2 cluster deployment workflows

## Installation

Install using uv:

```bash
uv tool install .
```

Alternatively, install in development mode:

```bash
pip install -e .
```

## Quickstart

Scaffold a new pipeline project with a single command:

```bash
kfp-gen mypipeline
```

## Usage

Run `kfp-gen` with the following options:

| Flag | Description |
|------|-------------|
| `name` | Required. Name of the pipeline project |
| `--dir PATH` | Optional. Output directory (default: current working directory) |
| `--force` | Optional. Overwrite existing target directory |

Example invocations:

```bash
# Generate project in current directory
kfp-gen mypipeline

# Generate project in specific directory
kfp-gen --dir /path/to/project mypipeline

# Force overwrite if directory exists
kfp-gen --force mypipeline
```

Generated project structure:

```
mypipeline/
  pyproject.toml
  README.md
  Makefile
  .gitignore
  mypipeline/
    __init__.py
    components.py
    pipeline.py
    compile_pipeline.py
```

## Configuration

No configuration files or environment variables required. All settings are controlled through CLI arguments.

## Architecture

The tool consists of three core modules:

- `main()` handles CLI argument parsing and orchestration of project creation
- `create_project()` manages file generation and directory handling logic
- `render()` processes templates with project name substitution

## Development

Set up a development environment using uv:

```bash
uv venv
uv pip install -e .
```

Run tests with:

```bash
uv run pytest
```

Code follows PEP 8 style guidelines with consistent formatting.

## Troubleshooting

**Project directory already exists**: Use `--force` flag to overwrite existing directories.

**Invalid project name**: Ensure project name contains only alphanumeric characters and underscores.

**Missing dependencies**: Install with `uv pip install -e .` or `pip install -e .`.

**Pipeline compilation fails**: Verify KFP v2 compatible cluster is available for submission.

## License

MIT License