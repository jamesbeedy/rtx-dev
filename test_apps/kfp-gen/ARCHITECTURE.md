# kfp-gen Architecture

## Overview

The `kfp-gen` CLI tool is a scaffolding utility that generates Kubeflow Pipelines v2 projects with standard @dsl.component examples. The tool accepts a project name as input and creates a complete project structure with appropriate Python modules, configuration files, and example components.

```
┌─────────────┐    ┌──────────────────┐    ┌─────────────────┐
│  Input      │───▶│   Processing     │───▶│   Output        │
│  Project    │    │                  │    │                 │
│  Name       │    │  - Validate      │    │  - Project      │
│             │    │  - Render        │    │    Structure    │
│             │    │  - Create        │    │    - Components │
│             │    │                  │    │    - Config     │
└─────────────┘    └──────────────────┘    └─────────────────┘
```

## Module Layout

| Module Path | Role | Key Public Symbols |
|-------------|------|-------------------|
| `kfp_gen/main.py` | Entry point and CLI interface | `main`, `create_project` |
| `kfp_gen/templates.py` | Template rendering logic | `render` |
| `kfp_gen/validation.py` | Project name validation | `validate_name` |

## Data Flow

1. CLI arguments are parsed, extracting project name, output directory, and force flag
2. Project name validation occurs using `validate_name()` function
3. Template rendering process begins with `render()` function to generate file contents
4. Directory creation and file writing operations occur in `create_project()`
5. Generated project structure includes pipeline components, configuration files, and setup scripts

## Concurrency Model

No concurrency is used in the current implementation. The tool operates synchronously with sequential execution of validation, template rendering, and file system operations.

## Error Handling Strategy

The tool uses exit codes 1-255 for different error conditions:
- Exit code 1: Invalid project name
- Exit code 2: Directory already exists without force flag
- Exit code 3: File system errors during project creation
- Exit code 4: Template rendering failures
Exception types are caught at the top level and converted to appropriate exit codes with user-friendly messages.

## Extension Points

- **Template System**: Add new template types by extending the `render()` function with additional template parameters
- **Validation Rules**: Extend `validate_name()` with additional naming constraints or domain-specific rules
- **File Generation**: Add new file types by modifying the `create_project()` function to include additional file creation logic
- **Project Scaffolding**: Implement new project layouts by adding new template directories and updating the `create_project()` logic

## Performance Notes

The tool has minimal performance requirements since it only performs file system operations and text rendering. Template rendering is optimized through string replacement operations. For large projects, consider implementing lazy loading of templates or caching mechanisms for repeated operations.

## Testing Strategy

Unit tests should cover:
- `validate_name()` with various valid/invalid inputs including edge cases
- `render()` function with different template contexts and variables
- `create_project()` with force flag scenarios and directory existence checks
- Error conditions such as invalid directory permissions and file system failures
Integration tests should verify complete project generation workflow including file content validation and proper directory structure creation.