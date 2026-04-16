import typer
from rich.console import Console

from cli_app.commands import example
from cli_app.core.config import __version__

app = typer.Typer(
    name="cli-app",
    help="A world-class Python CLI template.",
    add_completion=False,
)
console = Console()

# Add subcommands
app.add_typer(example.app, name="example")


def version_callback(value: bool) -> None:
    if value:
        console.print(f"cli-app version: [bold cyan]{__version__}[/]")
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
