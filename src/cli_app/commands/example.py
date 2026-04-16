import typer
from rich.console import Console

app = typer.Typer(help="Example commands to demonstrate structure.")
console = Console()


@app.command()
def hello(name: str = typer.Argument(..., help="Name of the person to greet.")) -> None:
    """
    Greet a user with their name.
    """
    console.print(f"Hello, [bold green]{name}[/]!")


@app.command()
def info() -> None:
    """
    Print information about this environment.
    """
    console.print("This is a world-class CLI template environment. [green]Ready![/]")
