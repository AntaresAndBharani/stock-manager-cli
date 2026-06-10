import typer
from rich.console import Console

from tradingtools_stock.commands import dashboard, db, example, fetch, ibkr, tickers
from tradingtools_stock.core.config import __version__

app = typer.Typer(
    name="tradingtools-stock",
    help="A world-class Python CLI template.",
    add_completion=False,
)
console = Console()

# Add subcommands
app.add_typer(example.app, name="example")
app.add_typer(fetch.app, name="fetch")
app.add_typer(db.app, name="db")
app.add_typer(tickers.app, name="tickers")
app.add_typer(dashboard.app, name="dashboard")
app.add_typer(ibkr.app, name="ibkr")


def version_callback(value: bool) -> None:
    if value:
        console.print(f"tradingtools-stock version: [bold cyan]{__version__}[/]")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Print version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """
    World-Class CLI App Root.
    """
    pass
