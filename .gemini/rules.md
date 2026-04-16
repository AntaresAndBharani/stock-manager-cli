# Antigravity IDE & AI Rules for Python CLI Template

This file contains explicit instructions for any AI coding assistant (like Antigravity) working in this workspace.

## 1. Architectural Philosophy
- **Framework:** This project uses `Typer` (not `Click` or `Argparse` directly). All CLI entry points must use Typer decorators and patterns.
- **Project Structure:** We use a `src/` layout. Commands and core logic should be placed in `src/cli_app/commands/` and `src/cli_app/core/` respectively.
- **Dependencies:** This project defaults to `hatchling` as the build system defined in `pyproject.toml`. Managing dependencies should ideally be done via standard `pip` into a virtual environment, or newer tools like `uv`. 

## 2. Coding Standards
- **Typing:** Strict type hinting is enforced via `Mypy`. All new functions, arguments, and return types MUST have type hints.
- **Linting & Formatting:** `Ruff` is the sole linter and formatter. Do NOT use `black`, `flake8`, or `isort`. Always format output with `ruff check --fix .` and `ruff format .` via the `make format` command.
- **Logging & Output:** Avoid using standard `print()` for final CLI output. Use `rich.console.Console()` to ensure beautiful, styled terminal output instead.

## 3. Workflow & Tool Usage
- Whenever making changes across multiple logic chains or debugging complex tracebacks, activate the **sequential-thinking** MCP tool to reason through the problem iteratively.
- For Git state changes or releases, wrap them through a GitHub MCP rule or standard Git commands.
- Run `make check` before concluding any major feature task to ensure tests and linting constraints pass.
