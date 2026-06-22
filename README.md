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

## Database Configuration

Before running commands that require a database connection (like the dashboard), you need to set up your environment variables. 

In PowerShell, you can set them for your current session like this:

```powershell
$env:DB_NAME="fractanomicsdb"
$env:DB_USER="postgres"
$env:DB_PASS="postgres"
$env:DB_HOST="localhost"
$env:DB_PORT="5433"
```

### Verify your environment

Run the `setup` command to check everything in one pass — required environment
variables, the database connection and schema, IBKR connectivity, and the
external tools the app needs (a local PostgreSQL server, the IBKR Gateway/TWS
client, Python dependencies):

```bash
tradingtools-stock setup            # report what is configured vs. missing
tradingtools-stock setup --install  # additionally provision missing pieces
```

`setup` exits non-zero if any required check fails and prints how to fix each
one. `--install` (a.k.a. `--fix`) is opt-in and never runs silently — it can
create the database and tables and, where a package manager is available,
install a local PostgreSQL server.

## IBKR Portfolio (IB Gateway)

The dashboard's **IBKR Portfolio** tab reads your live account (positions, P&L, account summary) through the TWS API. One-time setup:

1. **Install IB Gateway** (stable) from
   https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
   (default install location `C:\Jts` is auto-detected; otherwise set `IB_GATEWAY_PATH`).
2. **Log in** and enable the API: *Configure > Settings > API > Settings >* check
   *Enable ActiveX and Socket Clients*. Note the socket port.

Connection settings (defaults shown):

```powershell
$env:IB_HOST="127.0.0.1"
$env:IB_PORT="4002"        # IB Gateway paper; 4001 live, 7497 TWS paper, 7496 TWS live
$env:IB_CLIENT_ID="11"
```

Daily usage:

```bash
tradingtools-stock ibkr gateway          # start IB Gateway (log in within its window)
tradingtools-stock ibkr status           # verify the API connection
tradingtools-stock dashboard start -g    # start dashboard + IB Gateway together
```

## Try the CLI locally
Once installed, you can invoke the CLI from anywhere in your terminal!
```bash
tradingtools-stock --help
tradingtools-stock example hello World
```
