import argparse
import re
import sys
from pathlib import Path

from .templates import (
    PYPROJECT_TOML,
    README_MD,
    MAKEFILE,
    GITIGNORE,
    INIT_PY,
    COMPONENTS_PY,
    PIPELINE_PY,
    COMPILE_PIPELINE_PY,
)


def validate_name(name: str) -> bool:
    """Validate that a name is suitable for use as a project identifier.

    Args:
        name (str): The name to validate.

    Returns:
        bool: True if the name is valid, False otherwise.
    """
    return re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", name) is not None


def render(template: str, name: str) -> str:
    """Render a template string with the given name.

    Args:
        template (str): The template string to render.
        name (str): The name to substitute into the template.

    Returns:
        str: The rendered template string.
    """
    return template.format(name=name)


def create_project(name: str, output_dir: Path, force: bool = False) -> None:
    """Create a new Kubeflow Pipelines v2 project structure.

    Args:
        name (str): The name of the project.
        output_dir (Path): The directory where the project will be created.
        force (bool, optional): Whether to overwrite an existing directory. Defaults to False.

    Raises:
        ValueError: If the project name is invalid.
        FileExistsError: If the project directory already exists and force is False.
    """
    if not validate_name(name):
        raise ValueError(f"Invalid project name: {name}")
    project_dir = output_dir / name
    if project_dir.exists() and not force:
        raise FileExistsError(
            f"Directory {project_dir} already exists. Use --force to overwrite."
        )
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / name).mkdir(parents=True, exist_ok=True)

    files = [
        (project_dir / "pyproject.toml", render(PYPROJECT_TOML, name)),
        (project_dir / "README.md", render(README_MD, name)),
        (project_dir / "Makefile", render(MAKEFILE, name)),
        (project_dir / ".gitignore", GITIGNORE),
        (project_dir / name / "__init__.py", INIT_PY),
        (project_dir / name / "components.py", COMPONENTS_PY),
        (project_dir / name / "pipeline.py", render(PIPELINE_PY, name)),
        (project_dir / name / "compile_pipeline.py", render(COMPILE_PIPELINE_PY, name)),
    ]
    for path, content in files:
        path.write_text(content)
        print(f"Created {path}")


def main() -> int:
    """Main entry point for the kfp-gen command-line tool.

    Returns:
        int: Exit code (0 for success, 1 for error).
    """
    parser = argparse.ArgumentParser(description="Scaffold a Kubeflow Pipelines v2 project")
    parser.add_argument("name", help="Name of the pipeline project")
    parser.add_argument("--dir", default=Path.cwd(), type=Path, help="Output directory")
    parser.add_argument("--force", action="store_true", help="Overwrite existing directory")
    args = parser.parse_args()
    try:
        create_project(args.name, args.dir, args.force)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1