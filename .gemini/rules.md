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

## Agentic SDLC Pipeline
Headless Architect (Claude) and PR Review (Claude) run automatically on
issue/PR label changes and events (`.github/workflows/architect.yml`,
`pr-review.yml`, `merge.yml`). Three Amigos and Dev & Test run instead via
Antigravity Scheduled Tasks (`.antigravity/tasks/three-amigos.md`,
`.antigravity/tasks/dev-test.md`) polling this repo's GitHub state directly
— their GitHub Actions equivalents (`three-amigos.yml`, `dev-test.yml`)
exist in this repo too but stay **disabled** as a documented fallback, not
the active path. Full design and rationale live in the
`AntaresAndBharani/graph-engineering` repo (`docs/definition-node.md`,
`docs/three-amigos-node.md`, `docs/dev-test-node.md`,
`docs/antigravity-scheduled-tasks.md`, `README.md`) — this is the
quick-reference for using it here, not a copy of that design.

- **As PO, draft a User Story** with the `user-story.yml` issue template.
  When ready, relabel it `status:ready-for-architect` to hand off.
- **Label meanings:**
  - `status:definition` — still drafting
  - `status:ready-for-architect` — PO says go (on a story: decompose; on a
    subtask: incorporate my answer to a prior `status:needs-po-input`)
  - `status:needs-po-input` — Architect needs your decision; read the
    comment, answer, then relabel `status:ready-for-architect`
  - `status:review` — Architect handed this subtask batch to Three Amigos
  - `status:needs-revision` / `status:needs-clarification` — Three Amigos
    bounced it back to Architect; no action needed from you unless it
    escalates to `status:needs-po-input`
  - `status:awaiting-approval` — Three Amigos' own internal marker on a
    subtask it's cleared for pickup; not something you act on
  - `status:ready` — Three Amigos sets this on the story automatically on
    a READY batch verdict. Dev & Test picks it up from here on its own.
  - `status:done` — set automatically once every subtask under a story is
    closed; the story itself is also closed at that point
- **Nothing gets implemented until Three Amigos clears the batch.** Past
  that point the whole loop — Dev & Test's implementation, PR Review,
  fix-up rounds, and Merge — runs without you. You still get pulled in for
  `status:needs-po-input` escalations (Architect conflicts, round-cap
  hits) at any stage.
