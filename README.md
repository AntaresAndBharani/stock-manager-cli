# World-Class Python CLI Template

This repository is a state-of-the-art starting point for building Python Command Line Interfaces (CLIs). It's optimized for both human developers and the Antigravity AI pair-programming agent.

## Core Technologies
- **Framework:** [Typer](https://typer.tiangolo.com/) with [Rich](https://rich.readthedocs.io/)
- **Packaging:** Standard `pyproject.toml` (PEP 621) using `hatchling`
- **Linting & Formatting:** [Ruff](https://beta.ruff.rs/docs/)
- **Type Checking:** [Mypy](https://mypy.readthedocs.io/en/stable/)
- **Testing:** [Pytest](https://docs.pytest.org/en/7.4.x/)
- **Git Hooks:** [pre-commit](https://pre-commit.com/)

## Antigravity (AI) Integration
This repository is configured out-of-the-box with `.gemini/rules.md`. These rules instruct Antigravity automatically on architectural patterns (like enforcing standard `Typer` usage over other frameworks and maintaining `ruff` standards). 
See `.gemini/mcp_instructions.md` for tips on how to push the AI even further using MCP tools like `sequential-thinking`.

## How to Work on this project

1. **Install tools and dependencies:**
We highly recommend using `uv` or creating a virtual environment first.
```bash
# Using standard pip in a venv
pip install -e ".[dev]"
```

2. **Setup Git hooks:**
```bash
make setup
```

3. **Useful Commands:**
- `make format`: Auto-fix linting issues and auto-format code using Ruff.
- `make lint`: Run strict Ruff and Mypy checks.
- `make test`: Run pytest suite.
- `make check`: Run format, lint, and tests all at once.

## Try the CLI locally
Once installed, you can invoke the CLI from anywhere in your terminal!
```bash
tradingtools-stock --help
tradingtools-stock example hello World
```
