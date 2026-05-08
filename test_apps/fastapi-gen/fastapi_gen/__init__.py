import re
from pathlib import Path
from string import Template

from .templates import (
    PYPROJECT_TOML_TEMPLATE,
    README_MD_TEMPLATE,
    DOCKERFILE_TEMPLATE,
    GITIGNORE_TEMPLATE,
    MAIN_INIT_TEMPLATE,
    MAIN_PY_TEMPLATE,
    CONFIG_PY_TEMPLATE,
    TEST_HEALTH_PY_TEMPLATE,
)


"""FastAPI project scaffolding utilities.

This module provides functions to generate a new FastAPI project structure
with standard files like pyproject.toml, Dockerfile, README.md, and more.
It includes validation for project names and handles file creation with
template substitution.
"""


def validate_project_name(name: str) -> bool:
    """Validate that a project name follows the required naming convention.

    Args:
        name (str): The project name to validate.

    Returns:
        bool: True if the name is valid, False otherwise.
    """
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", name))


def create_directory(path: Path) -> None:
    """Create a directory and all parent directories if they don't exist.

    Args:
        path (Path): The path to the directory to create.
    """
    path.mkdir(parents=True, exist_ok=True)


def write_file(path: Path, content: str) -> None:
    """Write content to a file and print a confirmation message.

    Args:
        path (Path): The path to the file to write.
        content (str): The content to write to the file.
    """
    print(f"Creating {path}")
    path.write_text(content)


def generate_project(
    project_name: str,
    target_dir: Path = Path("."),
    no_docker: bool = False,
    force: bool = False,
) -> None:
    """Generate a new FastAPI project structure.

    This function creates all necessary files and directories for a FastAPI
    project including pyproject.toml, Dockerfile, README.md, and test files.

    Args:
        project_name (str): The name of the project to create.
        target_dir (Path): The target directory where the project will be created.
        no_docker (bool): If True, skip Dockerfile generation.
        force (bool): If True, overwrite existing directories.

    Raises:
        ValueError: If the project name is invalid or the target directory
            already exists and force is False.
    """
    if not validate_project_name(project_name):
        raise ValueError("Invalid project name. Must match [a-zA-Z][a-zA-Z0-9_-]*")
    full_path = target_dir / project_name
    if full_path.exists() and not force:
        raise ValueError(f"Directory '{full_path}' already exists. Use --force to overwrite.")

    create_directory(full_path)
    create_directory(full_path / project_name)
    create_directory(full_path / "tests")

    data = {"name": project_name, "name_upper": project_name.upper()}

    write_file(full_path / "pyproject.toml", Template(PYPROJECT_TOML_TEMPLATE).substitute(data))
    write_file(full_path / "README.md", Template(README_MD_TEMPLATE).substitute(data))
    if not no_docker:
        write_file(full_path / "Dockerfile", Template(DOCKERFILE_TEMPLATE).substitute(data))
    write_file(full_path / ".gitignore", GITIGNORE_TEMPLATE)
    write_file(full_path / project_name / "__init__.py", Template(MAIN_INIT_TEMPLATE).substitute(data))
    write_file(full_path / project_name / "main.py", Template(MAIN_PY_TEMPLATE).substitute(data))
    write_file(full_path / project_name / "config.py", Template(CONFIG_PY_TEMPLATE).substitute(data))
    write_file(full_path / "tests" / "__init__.py", "")
    write_file(full_path / "tests" / "test_health.py", Template(TEST_HEALTH_PY_TEMPLATE).substitute(data))


def main() -> None:
    """Main entry point for the FastAPI project generator CLI.

    Parses command line arguments and generates a new FastAPI project
    based on the provided parameters.
    
    Exits with code 1 if an error occurs during project generation.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Scaffold a new FastAPI application")
    parser.add_argument("project_name", help="Name of the project to create")
    parser.add_argument("--dir", default=".", type=Path, help="Target directory for the project")
    parser.add_argument("--no-docker", action="store_true", help="Skip Dockerfile generation")
    parser.add_argument("--force", action="store_true", help="Overwrite existing directories")
    args = parser.parse_args()

    try:
        generate_project(
            project_name=args.project_name,
            target_dir=args.dir,
            no_docker=args.no_docker,
            force=args.force,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)