import os

import psycopg2
import typer
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from rich.console import Console

from tradingtools_stock.core.fetcher import (
    create_tables_if_not_exist,
    get_db_connection,
)

app = typer.Typer(help="Database management commands.")
console = Console()


@app.command("setup")
def setup(
    db_name: str = typer.Option("youtube_db", help="Name of the database to create"),
    user: str = typer.Option("postgres", help="Database user"),
    password: str = typer.Option("postgres", help="Database password"),
    host: str = typer.Option("localhost", help="Database host"),
    port: str = typer.Option("5432", help="Database port"),
):
    """
    Setup the database and initialize tables.
    """
    console.print(f"Setting up database [bold cyan]{db_name}[/]...")
    try:
        # Connect to the default 'postgres' database to create the new one
        conn = psycopg2.connect(
            dbname="postgres", user=user, password=password, host=host, port=port
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{db_name}'"
            )
            exists = cur.fetchone()
            if not exists:
                cur.execute(f"CREATE DATABASE {db_name}")
                console.print(f"[green]Database {db_name} created successfully.[/]")
            else:
                console.print(f"[yellow]Database {db_name} already exists.[/]")
        conn.close()

        # Connect to the new database to create tables
        os.environ["DB_NAME"] = db_name
        os.environ["DB_USER"] = user
        os.environ["DB_PASS"] = password
        os.environ["DB_HOST"] = host
        os.environ["DB_PORT"] = port

        new_conn = get_db_connection()
        create_tables_if_not_exist(new_conn)
        new_conn.close()

        console.print("[bold green]Database setup complete![/]")
    except Exception as e:
        console.print(f"[bold red]Error setting up database: {e}[/]")
        raise typer.Exit(1) from e
