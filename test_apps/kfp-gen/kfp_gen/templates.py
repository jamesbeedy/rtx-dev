PYPROJECT_TOML = """\
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "0.1.0"
description = "Kubeflow Pipeline project"
requires-python = ">=3.12"
dependencies = ["kfp>=2.7"]

[project.optional-dependencies]
dev = ["ruff>=0.5.0"]
"""

README_MD = """\
# {name}

This project was generated using `kfp-gen`.

## Setup

```bash
uv pip install -e .
```

## Compile pipeline

```bash
python -m {name}.compile_pipeline
```

## Submit

```python
import kfp
client = kfp.Client()
client.create_run_from_pipeline_package(
    pipeline_package_path="pipeline.yaml",
    experiment_name="default",
)
```

A KFP v2-compatible cluster is required for submission.
"""

MAKEFILE = """\
.PHONY: install compile submit clean

install:
\tuv pip install -e .

compile: install
\tpython -m {name}.compile_pipeline

submit: compile
\t@echo "Submit pipeline.yaml to your KFP cluster"

clean:
\trm -rf dist/ build/ *.egg-info/ pipeline.yaml
"""

GITIGNORE = """\
__pycache__/
*.pyc
.env
.venv/
*.egg-info/
.pytest_cache/
pipeline.yaml
"""

INIT_PY = """\
"""

COMPONENTS_PY = """\
from kfp import dsl
from kfp.dsl import Input, Output, Dataset, Model, Metrics


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["pandas"],
)
def data_load(output_dataset: Output[Dataset]):
    import pandas as pd
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    df.to_parquet(output_dataset.path)


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["pandas", "scikit-learn"],
)
def train(input_dataset: Input[Dataset], output_model: Output[Model]):
    import pandas as pd
    import pickle
    from sklearn.linear_model import LinearRegression
    df = pd.read_parquet(input_dataset.path)
    model = LinearRegression().fit(df[["a"]], df[["b"]])
    with open(output_model.path, "wb") as f:
        pickle.dump(model, f)


@dsl.component(
    base_image="python:3.12-slim",
    packages_to_install=["pandas", "scikit-learn"],
)
def evaluate(input_model: Input[Model], output_metrics: Output[Metrics]):
    import pandas as pd
    import pickle
    import json
    from sklearn.linear_model import LinearRegression  # noqa: F401
    with open(input_model.path, "rb") as f:
        model = pickle.load(f)
    df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
    score = float(model.score(df[["a"]], df[["b"]]))
    with open(output_metrics.path, "w") as f:
        json.dump({"score": score}, f)
"""

PIPELINE_PY = """\
from kfp import dsl

from .components import data_load, train, evaluate


@dsl.pipeline(name="{name}", description="A sample pipeline scaffolded by kfp-gen")
def {name}():
    data_load_task = data_load()
    train_task = train(input_dataset=data_load_task.outputs["output_dataset"])
    evaluate(input_model=train_task.outputs["output_model"])
"""

COMPILE_PIPELINE_PY = """\
from kfp import compiler
from .pipeline import {name}


def main() -> None:
    compiler.Compiler().compile(pipeline_func={name}, package_path="pipeline.yaml")
    print("Pipeline compiled to pipeline.yaml")


if __name__ == "__main__":
    main()
"""
