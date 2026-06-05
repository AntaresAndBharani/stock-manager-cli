import logging
from datetime import datetime, timedelta
import concurrent.futures

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from tradingtools_stock.core.fetcher import (
    create_tables_if_not_exist,
    fetch_stock_data,
    get_active_tickers_with_markets,
    format_yahoo_ticker,
    get_db_connection,
    get_existing_data_range,
    get_all_existing_data_ranges,
    get_global_max_date,
    psycopg2,
    upsert_stock_data,
)

app = typer.Typer(help="Fetch and save stock data.")
console = Console()


def _run_fetch_logic(
    start_date: str | None,
    end_date: str | None,
    include_fundamentals: bool,
    tickers: list[str] | None,
    debug: bool,
    workers: int,
    is_catch_up: bool = False,
):
    log_level = logging.DEBUG if debug else logging.INFO
    # Avoid duplicate handlers if basicConfig is called multiple times
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(
        level=log_level, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    # Suppress verbose logging from external libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    conn = None
    try:
        conn = get_db_connection()
        console.print("[green]Connected to database.[/]")

        create_tables_if_not_exist(conn)

        if is_catch_up:
            max_dt = get_global_max_date(conn)
            if max_dt:
                start_dt = max_dt.strftime("%Y-%m-%d")
                console.print(
                    f"[bold cyan]Catch-up mode:[/] Found last global fetch date: {start_dt}"
                )
            else:
                start_dt = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
                console.print(
                    "[yellow]No existing data found for catch-up. Using default 30 days.[/]"
                )
        else:
            default_start = (datetime.today() - timedelta(days=30)).strftime("%Y-%m-%d")
            start_dt = start_date or default_start

        end_dt = end_date or datetime.today().strftime("%Y-%m-%d")

        console.print(f"[bold cyan]Date range:[/] {start_dt} to {end_dt}")
        console.print(f"[bold cyan]Include fundamentals:[/] {include_fundamentals}")

        tickers_with_markets = get_active_tickers_with_markets(conn, tickers)
        if not tickers_with_markets:
            console.print(
                "[red]No active tickers found in the database. "
                "Provide via --tickers.[/]"
            )
            raise typer.Exit(1)

        console.print(f"Processing {len(tickers_with_markets)} tickers.")

        total_records = 0
        successful_tickers: list[str] = []
        failed_tickers: list[str] = []
        stale_tickers: list[dict] = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                "[cyan]Fetching stock data...", total=len(tickers_with_markets)
            )

            # Pre-calculate fetch tasks
            db_ranges = get_all_existing_data_ranges(
                conn, [t["symbol"] for t in tickers_with_markets]
            )

            fetch_tasks = []

            for ticker_info in tickers_with_markets:
                ticker = ticker_info["symbol"]
                market = ticker_info["market"]
                yahoo_ticker = format_yahoo_ticker(ticker, market)

                db_min, db_max = db_ranges.get(ticker, (None, None))
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

                fetch_tasks.append(
                    {
                        "ticker": ticker,
                        "market": market,
                        "yahoo_ticker": yahoo_ticker,
                        "fetch_ranges": fetch_ranges,
                    }
                )

            def fetch_for_ticker(task_info):
                ticker = task_info["ticker"]
                yahoo_ticker = task_info["yahoo_ticker"]
                fetch_ranges = task_info["fetch_ranges"]

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

                import pandas as pd

                if ticker_data_accumulated:
                    return ticker, pd.concat(ticker_data_accumulated)
                else:
                    return ticker, pd.DataFrame()

            if fetch_tasks:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=workers
                ) as executor:
                    future_to_ticker = {
                        executor.submit(fetch_for_ticker, task_info): task_info[
                            "ticker"
                        ]
                        for task_info in fetch_tasks
                    }

                    for future in concurrent.futures.as_completed(future_to_ticker):
                        ticker = future_to_ticker[future]
                        try:
                            returned_ticker, combined_data = future.result()
                            if not combined_data.empty:
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
                                db_min, db_max = db_ranges.get(ticker, (None, None))
                                yesterday = (
                                    datetime.today() - timedelta(days=1)
                                ).date()
                                if (
                                    db_max is None
                                    or (
                                        isinstance(db_max, datetime)
                                        and db_max.date() < yesterday
                                    )
                                    or (
                                        not isinstance(db_max, datetime)
                                        and db_max < yesterday
                                    )
                                ):
                                    issue_msg = f"⚠ No new data returned for {ticker}, and last DB date ({db_max}) is before yesterday!"
                                    progress.console.print(f"[red]{issue_msg}[/]")
                                    stale_tickers.append(
                                        {"Ticker": ticker, "Last DB Date": str(db_max)}
                                    )
                                else:
                                    progress.console.print(
                                        f"[yellow]⚠ No new data returned for {ticker}[/]"
                                    )
                        except Exception as exc:
                            failed_tickers.append(ticker)
                            progress.console.print(
                                f"[red]✗ Error processing {ticker}: {exc}[/]"
                            )
                        finally:
                            progress.advance(task)

        console.print("\n[bold]Summary[/bold]")
        console.print(f"Total Records Inserted: {total_records}")
        console.print(f"Successful Tickers: {len(successful_tickers)}")
        if failed_tickers:
            console.print(f"Failed Tickers: {len(failed_tickers)}")

        if stale_tickers:
            console.print(
                "\n[bold red]Stale Tickers (No new data and last DB date before yesterday)[/bold red]"
            )
            table = Table(show_header=True, header_style="bold magenta")
            table.add_column("Ticker")
            table.add_column("Last DB Date")
            for item in stale_tickers:
                table.add_row(item["Ticker"], item["Last DB Date"])
            console.print(table)

    except psycopg2.Error as e:
        console.print(f"[red]Database error: {e}[/]")
        raise typer.Exit(1) from e
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/]")
        raise typer.Exit(1) from e
    finally:
        if conn:
            conn.close()


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
    workers: int = typer.Option(
        10,
        "--workers",
        "-w",
        help="Number of concurrent workers for fetching data.",
    ),
):
    """
    Update stock data in the database over a given date range.
    """
    _run_fetch_logic(
        start_date=start_date,
        end_date=end_date,
        include_fundamentals=include_fundamentals,
        tickers=tickers,
        debug=debug,
        workers=workers,
        is_catch_up=False,
    )


@app.command("catch-up")
def catch_up(
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
    workers: int = typer.Option(
        10,
        "--workers",
        "-w",
        help="Number of concurrent workers for fetching data.",
    ),
):
    """
    Update stock data starting from the last global fetch date in the database.
    """
    _run_fetch_logic(
        start_date=None,
        end_date=end_date,
        include_fundamentals=include_fundamentals,
        tickers=tickers,
        debug=debug,
        workers=workers,
        is_catch_up=True,
    )
