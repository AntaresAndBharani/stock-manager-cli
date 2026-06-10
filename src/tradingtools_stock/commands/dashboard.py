import subprocess
from pathlib import Path

import typer

app = typer.Typer(help="Analytics and web dashboards.")


@app.command("start")
def start(
    gateway: bool = typer.Option(
        False,
        "--gateway",
        "-g",
        help="Also start IB Gateway (if installed and not already running).",
    ),
):
    """
    Launch the Streamlit web dashboard.
    """
    import tradingtools_stock

    if gateway:
        from tradingtools_stock.core import ibkr as ibkr_core

        host, port, _ = ibkr_core.get_ib_settings()
        if ibkr_core.is_api_port_open(host, port):
            typer.secho(
                f"IB Gateway API already reachable on {host}:{port}.",
                fg=typer.colors.GREEN,
            )
        else:
            exe = ibkr_core.find_gateway_executable()
            if exe is None:
                typer.secho(
                    "IB Gateway not found - install it from "
                    f"{ibkr_core.GATEWAY_DOWNLOAD_URL} or set IB_GATEWAY_PATH. "
                    "Continuing without it.",
                    fg=typer.colors.YELLOW,
                )
            else:
                ibkr_core.launch_gateway(exe)
                typer.secho(
                    "IB Gateway launched - log in within its window to enable "
                    "the IBKR Portfolio tab.",
                    fg=typer.colors.GREEN,
                )

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
