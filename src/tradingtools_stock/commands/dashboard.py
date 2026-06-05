import subprocess
from pathlib import Path

import typer

app = typer.Typer(help="Analytics and web dashboards.")


@app.command("start")
def start():
    """
    Launch the Streamlit web dashboard.
    """
    import tradingtools_stock

    # Find the path to app.py
    pkg_dir = Path(tradingtools_stock.__file__).parent
    app_path = pkg_dir / "dashboard" / "app.py"

    if not app_path.exists():
        typer.secho(f"Dashboard app not found at {app_path}", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.secho("Starting Streamlit Dashboard...", fg=typer.colors.GREEN)

    # Run streamlit
    try:
        subprocess.run(["streamlit", "run", str(app_path)])
    except KeyboardInterrupt:
        typer.secho("\nDashboard stopped.", fg=typer.colors.YELLOW)
