"""
``setup`` / ``doctor`` command: verify (and optionally provision) the
environment needed to run the dashboard, fetchers and IBKR integration.
"""

import typer
from rich.console import Console
from rich.table import Table

from tradingtools_stock.core import setup as setup_core
from tradingtools_stock.core.setup import FAIL, OK, SKIP, WARN, CheckResult

console = Console()

_STATUS_LABEL = {
    OK: "[green]PASS[/]",
    FAIL: "[bold red]FAIL[/]",
    WARN: "[yellow]WARN[/]",
    SKIP: "[dim]SKIP[/]",
}


def _render(results: list[CheckResult]) -> None:
    table = Table(title="Environment health check")
    table.add_column("Status", justify="center", no_wrap=True)
    table.add_column("Check", no_wrap=True)
    table.add_column("Detail")
    for r in results:
        label = _STATUS_LABEL.get(r.status, r.status)
        req = " [dim](required)[/]" if r.required and r.status == FAIL else ""
        table.add_row(label, r.name, f"{r.detail}{req}")
    console.print(table)


def _print_remediation(results: list[CheckResult]) -> None:
    actionable = [r for r in results if r.status in (FAIL, WARN) and r.remediation]
    if not actionable:
        return
    console.print("\n[bold]How to fix:[/]")
    for r in actionable:
        console.print(f"  - [cyan]{r.name}[/]: {r.remediation}")


def setup(
    install: bool = typer.Option(
        False,
        "--install",
        "--fix",
        help="Attempt to provision missing pieces (database, tables, tools). "
        "Never runs silently without this flag.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompts when used with --install.",
    ),
) -> None:
    """
    Check environment variables, the database connection and schema, IBKR
    connectivity, and required external tools. Reports everything in one pass.

    With --install, offers to provision the missing pieces (create the
    database/tables, install a local PostgreSQL server). Installation is
    opt-in, idempotent and OS-aware; it never runs silently.
    """
    results = setup_core.run_all_checks()
    _render(results)

    if install:
        fixed_any = _run_fixes(results, assume_yes=yes)
        if fixed_any:
            console.print("\n[bold]Re-checking after provisioning...[/]")
            results = setup_core.run_all_checks()
            _render(results)

    if setup_core.has_required_failures(results):
        _print_remediation(results)
        console.print(
            "\n[bold red]Environment is not ready.[/] "
            "Fix the failures above (or run with --install)."
        )
        raise typer.Exit(1)

    _print_remediation(results)
    console.print("\n[bold green]Environment is ready.[/]")


def _run_fixes(results: list[CheckResult], assume_yes: bool) -> bool:
    """Run the fix callable for each fixable failing check. Returns True if any ran."""
    # De-duplicate: several checks may share the same provisioning action.
    seen: set[str] = set()
    ran = False
    for r in results:
        if r.status not in (FAIL, WARN) or r.fix is None:
            continue
        key = getattr(r.fix, "__qualname__", repr(r.fix))
        if key in seen:
            continue
        seen.add(key)

        if not assume_yes:
            proceed = typer.confirm(f"Provision '{r.name}'?", default=True)
            if not proceed:
                console.print(f"[yellow]Skipped {r.name}.[/]")
                continue
        try:
            message = r.fix()  # type: ignore[operator]
            console.print(f"[green]OK {r.name}: {message}[/]")
            ran = True
        except Exception as exc:  # noqa: BLE001 - surface any provisioning error
            console.print(f"[red]FAILED {r.name}: {exc}[/]")
    return ran
