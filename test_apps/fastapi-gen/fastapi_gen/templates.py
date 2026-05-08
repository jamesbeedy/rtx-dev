PYPROJECT_TOML_TEMPLATE = """\
[project]
name = "$name"
version = "0.1.0"
description = "FastAPI application"
authors = []
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "pydantic-settings",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "httpx",
]

[build-system]
requires = ["setuptools", "wheel"]
build-backend = "setuptools.build_meta"
"""

README_MD_TEMPLATE = """\
# $name

This is a FastAPI application scaffolded with `fastapi-gen`.

## Running

```bash
uv run uvicorn $name.main:app --reload
```

## Development

Install dependencies:

```bash
uv sync --all-extras
```

Run tests:

```bash
uv run pytest
```
"""

DOCKERFILE_TEMPLATE = """\
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir uv && uv pip install --system .

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /usr/local /usr/local
COPY . .
EXPOSE 8000
CMD ["uvicorn", "$name.main:app", "--host", "0.0.0.0", "--port", "8000"]
"""

GITIGNORE_TEMPLATE = """\
__pycache__/
*.pyc
.venv/
*.egg-info/
.pytest_cache/
.mypy_cache/
.coverage
.env
"""

MAIN_INIT_TEMPLATE = """\
__version__ = "0.1.0"
"""

MAIN_PY_TEMPLATE = """\
from fastapi import FastAPI
from . import __version__
from .config import settings

app = FastAPI(title=settings.app_name)


@app.get("/health")
async def health_check():
    return {"status": "ok"}


@app.get("/version")
async def version():
    return {"version": __version__}
"""

CONFIG_PY_TEMPLATE = """\
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    app_name: str = "$name"
    log_level: str = "INFO"


settings = Settings()
"""

TEST_HEALTH_PY_TEMPLATE = """\
from fastapi.testclient import TestClient
from $name.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
"""
