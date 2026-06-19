import typer
from rich.console import Console
from rich.table import Table

from tradingtools_stock.core import ibkr as ibkr_core

app = typer.Typer(help="IBKR connectivity: IB Gateway and account status.")
console = Console()


@app.command("gateway")
def gateway():
    """
    Launch IB Gateway if it is not already running.

    The Gateway window opens for you to log in; the API session then stays
    available for the dashboard and other commands.
    """
    host, port, _ = ibkr_core.get_ib_settings()
    if ibkr_core.is_api_port_open(host, port):
        console.print(f"[green]IB Gateway API already reachable on {host}:{port}.[/]")
        return

    exe = ibkr_core.find_gateway_executable()
    if exe is None:
        console.print("[bold red]IB Gateway is not installed (or not found).[/]")
        console.print(
            f"Download it from: [cyan]{ibkr_core.GATEWAY_DOWNLOAD_URL}[/]\n"
            "If it is installed in a non-default location, set IB_GATEWAY_PATH "
            "to the full path of ibgateway.exe."
        )
        raise typer.Exit(1)

    console.print(f"Launching IB Gateway: [bold cyan]{exe}[/]")
    ibkr_core.launch_gateway(exe)
    console.print(
        "[green]IB Gateway started.[/] Log in within its window, then make sure "
        "the API is enabled (Configure > Settings > API > Settings > "
        "'Enable ActiveX and Socket Clients') and the socket port matches "
        f"IB_PORT (current: {port})."
    )


@app.command("status")
def status():
    """
    Check the TWS/IB Gateway connection and print an account summary.
    """
    host, port, _ = ibkr_core.get_ib_settings()
    if not ibkr_core.is_api_port_open(host, port):
        console.print(
            f"[bold red]No TWS/IB Gateway API listening on {host}:{port}.[/]\n"
            "Start it with: [cyan]tradingtools-stock ibkr gateway[/]"
        )
        raise typer.Exit(1)

    console.print(f"Connecting to {host}:{port}...")
    try:
        data = ibkr_core.fetch_portfolio()
    except Exception as e:
        console.print(f"[bold red]Connected to socket but API handshake failed: {e}[/]")
        console.print(
            "Check that the API is enabled in the Gateway/TWS settings and that "
            "you are logged in."
        )
        raise typer.Exit(1) from e

    console.print(f"[bold green]Connected.[/] Account: [bold cyan]{data['account']}[/]")

    table = Table(title="Account Summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for tag, value in data["summary"].items():
        table.add_row(tag, f"{value:,.2f}")
    console.print(table)

    positions = data["positions"]
    console.print(f"Open positions: [bold]{len(positions)}[/]")


@app.command("reconcile")
def reconcile():
    """
    Import IBKR executions into the local trades history.

    Pulls recent fills from IB Gateway and records new **Manual** executions
    (anything not placed by this tool) so they appear in the trades history and
    count towards the dashboard's "already bought this month" check. Deduped by
    IBKR execution id; safe to run repeatedly.
    """
    from tradingtools_stock.core import trades as trades_core
    from tradingtools_stock.core.fetcher import get_db_connection

    host, port, _ = ibkr_core.get_ib_settings()
    if not ibkr_core.is_api_port_open(host, port):
        console.print(
            f"[bold red]No TWS/IB Gateway API listening on {host}:{port}.[/]\n"
            "Start it with: [cyan]tradingtools-stock ibkr gateway[/]"
        )
        raise typer.Exit(1)

    console.print(f"Fetching executions from {host}:{port}...")
    try:
        executions = ibkr_core.fetch_executions()
    except Exception as e:
        console.print(f"[bold red]Could not fetch executions: {e}[/]")
        raise typer.Exit(1) from e

    conn = get_db_connection()
    try:
        inserted = trades_core.reconcile_executions(conn, executions)
    finally:
        conn.close()

    if inserted:
        console.print(
            f"[bold green]Reconciled {inserted} new manual execution(s).[/]"
        )
    else:
        console.print("[yellow]No new manual executions to import.[/]")


@app.command("trades")
def trades(
    start: str | None = typer.Option(
        None, "--start", help="Earliest date (YYYY-MM-DD), inclusive."
    ),
    end: str | None = typer.Option(
        None, "--end", help="Latest date (YYYY-MM-DD), inclusive."
    ),
):
    """
    List trades recorded by the CLI (the 'Average buy' action), optionally
    filtered by date. These are persisted locally and always tagged 'CLI'.
    """
    from tradingtools_stock.core import trades as trades_core
    from tradingtools_stock.core.fetcher import get_db_connection

    conn = get_db_connection()
    try:
        df = trades_core.fetch_trades(conn, start, end)
    finally:
        conn.close()

    if df.empty:
        console.print("[yellow]No recorded trades for the given range.[/]")
        return

    table = Table(title="Recorded Trades")
    for col in ["Placed At", "Symbol", "Action", "Quantity", "Price", "Source"]:
        table.add_column(col)
    for _, row in df.iterrows():
        price = row["Price"]
        qty = row["Quantity"]
        table.add_row(
            str(row["Placed At"]),
            str(row["Symbol"]),
            str(row["Action"]),
            f"{float(qty):g}" if qty is not None else "-",
            f"{float(price):.2f}" if price is not None else "-",
            str(row["Source"]),
        )
    console.print(table)
