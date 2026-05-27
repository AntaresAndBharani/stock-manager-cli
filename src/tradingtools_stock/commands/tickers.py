import typer
from rich.console import Console
from rich.table import Table

from tradingtools_stock.core.fetcher import get_db_connection

app = typer.Typer(help="Manage stock tickers in the database.")
console = Console()

@app.command("add")
def add_ticker(
    symbol: str = typer.Argument(..., help="The stock ticker symbol (e.g., AAPL)"),
    name: str = typer.Option("", "--name", "-n", help="Optional name of the company"),
):
    """
    Add a new ticker to the database to be tracked.
    """
    symbol = symbol.upper()
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tickers (symbol, name, active)
                VALUES (%s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET active = true
                """,
                (symbol, name, True)
            )
            conn.commit()
        console.print(f"[green]Successfully added/activated ticker:[/] {symbol}")
    except Exception as e:
        console.print(f"[bold red]Error adding ticker:[/] {e}")
        raise typer.Exit(1)
    finally:
        if 'conn' in locals() and conn:
            conn.close()

@app.command("list")
def list_tickers(
    all_tickers: bool = typer.Option(False, "--all", "-a", help="List all tickers, including inactive ones"),
):
    """
    List tickers currently in the database.
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            if all_tickers:
                cur.execute("SELECT symbol, name, active, created_at FROM tickers ORDER BY symbol")
            else:
                cur.execute("SELECT symbol, name, active, created_at FROM tickers WHERE active = true ORDER BY symbol")
            rows = cur.fetchall()
            
            if not rows:
                console.print("No tickers found in the database.")
                return

            table = Table(title="Tracked Tickers")
            table.add_column("Symbol", style="cyan", no_wrap=True)
            table.add_column("Name", style="magenta")
            table.add_column("Status", style="green")
            table.add_column("Added On", style="dim")

            for row in rows:
                status = "[green]Active[/]" if row[2] else "[red]Inactive[/]"
                added_date = row[3].strftime("%Y-%m-%d") if row[3] else "Unknown"
                table.add_row(row[0], row[1] or "", status, added_date)
            
            console.print(table)
    except Exception as e:
        console.print(f"[bold red]Error listing tickers:[/] {e}")
        raise typer.Exit(1)
    finally:
        if 'conn' in locals() and conn:
            conn.close()

@app.command("deactivate")
def deactivate_ticker(
    symbol: str = typer.Argument(..., help="The stock ticker symbol to deactivate"),
):
    """
    Deactivate a ticker so it won't be updated during batch fetches.
    """
    symbol = symbol.upper()
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("UPDATE tickers SET active = false WHERE symbol = %s", (symbol,))
            if cur.rowcount > 0:
                conn.commit()
                console.print(f"[yellow]Deactivated ticker:[/] {symbol}")
            else:
                console.print(f"[red]Ticker not found in the database:[/] {symbol}")
    except Exception as e:
        console.print(f"[bold red]Error deactivating ticker:[/] {e}")
        raise typer.Exit(1)
    finally:
        if 'conn' in locals() and conn:
            conn.close()

@app.command("remove")
def remove_ticker(
    symbol: str = typer.Argument(..., help="The stock ticker symbol to remove completely"),
    force: bool = typer.Option(False, "--force", "-f", help="Force removal without prompting"),
):
    """
    Remove a ticker and all its associated historical data from the database.
    """
    symbol = symbol.upper()
    if not force:
        confirm = typer.confirm(f"Are you sure you want to permanently delete {symbol} and all its historical data?")
        if not confirm:
            console.print("Operation cancelled.")
            raise typer.Exit()
            
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            # First, delete from stock_prices to respect foreign key constraints
            cur.execute("DELETE FROM stock_prices WHERE symbol = %s", (symbol,))
            deleted_prices = cur.rowcount
            
            # Then, delete the ticker itself
            cur.execute("DELETE FROM tickers WHERE symbol = %s", (symbol,))
            if cur.rowcount > 0:
                conn.commit()
                console.print(f"[green]Successfully removed ticker:[/] {symbol}")
                console.print(f"Deleted {deleted_prices} historical price records.")
            else:
                console.print(f"[yellow]Ticker not found in the database:[/] {symbol}")
    except Exception as e:
        console.print(f"[bold red]Error removing ticker:[/] {e}")
        raise typer.Exit(1)
    finally:
        if 'conn' in locals() and conn:
            conn.close()
