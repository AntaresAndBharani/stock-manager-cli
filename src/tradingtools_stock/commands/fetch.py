import logging
from datetime import datetime, timedelta

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from tradingtools_stock.core.fetcher import (
    create_tables_if_not_exist,
    fetch_stock_data,
    get_active_tickers_with_markets,
    format_yahoo_ticker,
    get_db_connection,
    get_existing_data_range,
    psycopg2,
    upsert_stock_data,
)

app = typer.Typer(help="Fetch and save stock data.")
console = Console()


@app.command()
def update(
    start_date: str | None = typer.Option(
        None,
        "--start-date",
        "-s",
        help="Start date in YYYY-MM-DD format (default: 30 days ago).",
    ),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        "-e",
        help="End date in YYYY-MM-DD format (default: today).",
    ),
    include_fundamentals: bool = typer.Option(
        False,
        "--include-fundamentals/--no-fundamentals",
        help="Include quarterly fundamentals.",
    ),
    tickers: list[str] | None = typer.Option(
        None,
        "--tickers",
        "-t",
        help="Specific tickers to update (e.g. -t AAPL -t GOOGL). "
        "If omitted, active DB tickers are used.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable debug logging.",
    ),
):
    """
    Update stock data in the database over a given date range.
    """
    log_level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=log_level, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    # Suppress verbose logging from external libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    default_start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
    start_dt = start_date or default_start
    end_dt = end_date or datetime.today().strftime("%Y-%m-%d")

    console.print(f"[bold cyan]Date range:[/] {start_dt} to {end_dt}")
    console.print(f"[bold cyan]Include fundamentals:[/] {include_fundamentals}")

    conn = None
    try:
        conn = get_db_connection()
        console.print("[green]Connected to database.[/]")

        create_tables_if_not_exist(conn)

        tickers_with_markets = get_active_tickers_with_markets(conn, tickers)
        if not tickers_with_markets:
            console.print(
                "[red]No active tickers found in the database. "
                "Provide via --tickers.[/]"
            )
            raise typer.Exit(1)

        console.print(
            f"Processing {len(tickers_with_markets)} tickers."
        )

        total_records = 0
        successful_tickers: list[str] = []
        failed_tickers: list[str] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                "[cyan]Fetching stock data...", total=len(tickers_with_markets)
            )

            for ticker_info in tickers_with_markets:
                ticker = ticker_info["symbol"]
                market = ticker_info["market"]
                yahoo_ticker = format_yahoo_ticker(ticker, market)
                
                progress.update(task, description=f"[cyan]Processing {ticker}...")

                db_min, db_max = get_existing_data_range(conn, ticker)
                req_start = datetime.strptime(start_dt, "%Y-%m-%d").date()
                req_end = datetime.strptime(end_dt, "%Y-%m-%d").date()

                fetch_ranges = []
                if db_min is None or db_max is None:
                    fetch_ranges.append((req_start, req_end))
                else:
                    if req_start < db_min:
                        gap_before = min(req_end, db_min - timedelta(days=1))
                        fetch_ranges.append((req_start, gap_before))

                    if req_end > db_max:
                        gap_after = max(req_start, db_max + timedelta(days=1))
                        fetch_ranges.append((gap_after, req_end))

                if not fetch_ranges:
                    progress.console.print(f"[dim]{ticker} is already up to date.[/]")
                    successful_tickers.append(ticker)
                    progress.advance(task)
                    continue

                ticker_data_accumulated = []

                for f_start, f_end in fetch_ranges:
                    data = fetch_stock_data(
                        ticker,
                        f_start.strftime("%Y-%m-%d"),
                        f_end.strftime("%Y-%m-%d"),
                        include_fundamentals,
                        yahoo_ticker,
                    )

                    if not data.empty:
                        ticker_data_accumulated.append(data)

                if ticker_data_accumulated:
                    import pandas as pd

                    combined_data = pd.concat(ticker_data_accumulated)
                    records = upsert_stock_data(
                        conn, combined_data, include_fundamentals
                    )
                    total_records += records
                    successful_tickers.append(ticker)
                    progress.console.print(
                        f"[green]✓ Saved {records} new records for {ticker}[/]"
                    )
                else:
                    successful_tickers.append(ticker)
                    progress.console.print(
                        f"[yellow]⚠ No new data returned for {ticker}[/]"
                    )

                progress.advance(task)

        console.print("\n[bold]Summary[/bold]")
        console.print(f"Total Records Inserted: {total_records}")
        console.print(f"Successful Tickers: {len(successful_tickers)}")
        if failed_tickers:
            console.print(f"Failed Tickers: {len(failed_tickers)}")

    except psycopg2.Error as e:
        console.print(f"[red]Database error: {e}[/]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/]")
        raise typer.Exit(1) from e
    finally:
        if conn:
            conn.close()
