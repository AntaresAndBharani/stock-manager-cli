"""
Environment health checks and provisioning for the ``setup`` command.

This module verifies that a machine is actually ready to run the dashboard,
fetchers and IBKR integration: required environment variables, the database
connection, the database/schema, IBKR connectivity, and the external tools the
app depends on (a local PostgreSQL server, the IBKR Gateway/TWS client, and the
Python runtime dependencies).

Checks are pure-ish and return :class:`CheckResult` objects so the CLI layer can
render them and so they can be unit-tested without touching the network. Actual
provisioning (creating the database/tables, installing a package via the
platform package manager) lives in the ``provision_*`` helpers and is only run
when the user explicitly opts in.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field

# Status values used by CheckResult.
OK = "ok"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"

# Required database environment variables. DB_HOST/DB_PORT have safe defaults in
# get_db_connection, so they are reported but never fatal when missing.
REQUIRED_DB_VARS = ("DB_NAME", "DB_USER", "DB_PASS")
DEFAULTED_DB_VARS = {"DB_HOST": "localhost", "DB_PORT": "5432"}

# Optional IBKR environment variables (all have code defaults).
OPTIONAL_IB_VARS = {
    "IB_HOST": "127.0.0.1",
    "IB_PORT": "4002",
    "IB_CLIENT_ID": "11",
    "IB_GATEWAY_PATH": "",
}

# Variables whose values must never be printed.
SECRET_VARS = {"DB_PASS"}

# Expected tables mapped to the columns we most care about (migrated columns
# that older databases may be missing). Existence of the table is required;
# missing columns are reported as warnings since create_tables_if_not_exist can
# add them.
EXPECTED_SCHEMA: dict[str, tuple[str, ...]] = {
    "tickers": ("market", "sector", "industry"),
    "stock_prices": (),
    "dashboard_cache": ("sma_1000", "sma_1000_touch_days"),
    "app_config": (),
    "valuation_history": (),
    "trades": ("cash_amount", "method", "ib_exec_id"),
}

# Core tables to report row counts for as a quick sanity signal.
ROW_COUNT_TABLES = ("tickers", "stock_prices", "trades")

# Python runtime dependencies that must be importable.
RUNTIME_DEPS = ("psycopg2", "ib_async", "yahooquery", "streamlit", "pandas")


@dataclass
class CheckResult:
    """The outcome of a single health check."""

    name: str
    status: str
    detail: str = ""
    remediation: str = ""
    required: bool = False
    # An optional callable that provisions/fixes this check when --install is
    # passed. Returns a human-readable message describing what it did.
    fix: object = field(default=None, repr=False)


def _mask(value: str) -> str:
    """Mask a secret value, keeping only a hint of its length."""
    if not value:
        return ""
    return "*" * 8


def is_port_open(host: str, port: int | str, timeout: float = 1.0) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def is_local_host(host: str | None) -> bool:
    """Return True when host refers to the local machine."""
    return (host or "").strip().lower() in {"", "localhost", "127.0.0.1", "::1"}


def _db_settings() -> dict[str, str]:
    """Resolve effective DB connection settings (env + defaults)."""
    return {
        "host": os.environ.get("DB_HOST", DEFAULTED_DB_VARS["DB_HOST"]),
        "port": os.environ.get("DB_PORT", DEFAULTED_DB_VARS["DB_PORT"]),
        "name": os.environ.get("DB_NAME", ""),
        "user": os.environ.get("DB_USER", ""),
        "password": os.environ.get("DB_PASS", ""),
    }


# --------------------------------------------------------------------------- #
# 1. Environment variables
# --------------------------------------------------------------------------- #
def check_environment() -> list[CheckResult]:
    """Check required and optional environment variables."""
    results: list[CheckResult] = []

    for var in REQUIRED_DB_VARS:
        value = os.environ.get(var)
        if value:
            shown = _mask(value) if var in SECRET_VARS else value
            results.append(CheckResult(var, OK, detail=shown, required=True))
        else:
            results.append(
                CheckResult(
                    var,
                    FAIL,
                    detail="not set",
                    remediation=f"Set the {var} environment variable.",
                    required=True,
                )
            )

    for var, default in DEFAULTED_DB_VARS.items():
        value = os.environ.get(var)
        if value:
            results.append(CheckResult(var, OK, detail=value))
        else:
            results.append(CheckResult(var, WARN, detail=f"using default '{default}'"))

    for var, default in OPTIONAL_IB_VARS.items():
        value = os.environ.get(var)
        if value:
            results.append(CheckResult(var, OK, detail=value))
        elif default:
            results.append(CheckResult(var, SKIP, detail=f"using default '{default}'"))
        else:
            results.append(CheckResult(var, SKIP, detail="not set (optional)"))

    return results


# --------------------------------------------------------------------------- #
# 2 & 3. Database connection and existence
# --------------------------------------------------------------------------- #
def check_database():
    """
    Check the database connection and existence.

    Returns a tuple ``(results, conn)`` where ``conn`` is an open connection to
    the target database (or ``None`` if it could not be opened). Callers are
    responsible for closing ``conn``.
    """
    from tradingtools_stock.core.fetcher import get_db_connection

    results: list[CheckResult] = []
    settings = _db_settings()

    if not (settings["name"] and settings["user"]):
        results.append(
            CheckResult(
                "DB connection",
                SKIP,
                detail="DB_NAME/DB_USER not set",
                remediation="Set the database environment variables first.",
                required=True,
            )
        )
        return results, None

    conn = None
    try:
        conn = get_db_connection()
        version = ""
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), version();")
            row = cur.fetchone()
            if row:
                server = row[1].split(",")[0] if row[1] else ""
                version = f"{row[0]} ({server})"
        results.append(CheckResult("DB connection", OK, detail=version, required=True))
        # If we connected to the target database, it obviously exists.
        results.append(CheckResult("DB exists", OK, detail=settings["name"]))
        return results, conn
    except Exception as exc:  # noqa: BLE001 - report any connection failure
        results.append(
            CheckResult(
                "DB connection",
                FAIL,
                detail=str(exc).strip().splitlines()[0] if str(exc) else "failed",
                remediation=(
                    "Ensure PostgreSQL is running and the DB_* settings are "
                    "correct. Run with --install to create the database/tables."
                ),
                required=True,
                fix=_make_provision_database(),
            )
        )
        results.append(_check_database_exists(settings))
        return results, None


def _check_database_exists(settings: dict[str, str]) -> CheckResult:
    """Connect to the default 'postgres' database to see if the target exists."""
    try:
        import psycopg2

        conn = psycopg2.connect(
            dbname="postgres",
            user=settings["user"],
            password=settings["password"],
            host=settings["host"],
            port=settings["port"],
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                    (settings["name"],),
                )
                exists = cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            "DB exists",
            WARN,
            detail=f"could not verify ({str(exc).strip().splitlines()[0]})",
        )

    if exists:
        return CheckResult("DB exists", OK, detail=settings["name"])
    return CheckResult(
        "DB exists",
        FAIL,
        detail=f"database '{settings['name']}' not found",
        remediation="Run with --install (or `db setup`) to create it.",
        required=True,
        fix=_make_provision_database(),
    )


# --------------------------------------------------------------------------- #
# 4. Schema & tables
# --------------------------------------------------------------------------- #
def check_schema(conn) -> list[CheckResult]:
    """Verify expected tables/columns exist and report core row counts."""
    results: list[CheckResult] = []
    if conn is None:
        results.append(
            CheckResult(
                "Schema",
                SKIP,
                detail="no database connection",
                required=True,
            )
        )
        return results

    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public';"
        )
        present_tables = {row[0] for row in cur.fetchall()}

        missing_tables = [t for t in EXPECTED_SCHEMA if t not in present_tables]
        if missing_tables:
            results.append(
                CheckResult(
                    "Tables",
                    FAIL,
                    detail=f"missing: {', '.join(missing_tables)}",
                    remediation="Run with --install to create the tables.",
                    required=True,
                    fix=_make_provision_tables(),
                )
            )
        else:
            results.append(
                CheckResult("Tables", OK, detail=f"{len(EXPECTED_SCHEMA)} present")
            )

        # Column checks only for tables that exist.
        cur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public';"
        )
        cols_by_table: dict[str, set[str]] = {}
        for table, column in cur.fetchall():
            cols_by_table.setdefault(table, set()).add(column)

        missing_cols: list[str] = []
        for table, columns in EXPECTED_SCHEMA.items():
            if table not in present_tables:
                continue
            for column in columns:
                if column not in cols_by_table.get(table, set()):
                    missing_cols.append(f"{table}.{column}")
        if missing_cols:
            results.append(
                CheckResult(
                    "Columns",
                    WARN,
                    detail=f"missing: {', '.join(missing_cols)}",
                    remediation="Run with --install to apply migrations.",
                    fix=_make_provision_tables(),
                )
            )

        # Row counts for core tables that exist.
        for table in ROW_COUNT_TABLES:
            if table not in present_tables:
                continue
            cur.execute(f"SELECT COUNT(*) FROM {table};")  # noqa: S608 - fixed names
            count = cur.fetchone()[0]
            status = OK if count else WARN
            detail = f"{count} rows"
            if not count and table == "tickers":
                detail += " (none imported yet)"
            results.append(CheckResult(f"rows: {table}", status, detail=detail))

    return results


# --------------------------------------------------------------------------- #
# 5. IBKR connectivity
# --------------------------------------------------------------------------- #
def check_ibkr() -> list[CheckResult]:
    """Probe IBKR Gateway/TWS reachability (optional, never fatal)."""
    from tradingtools_stock.core import ibkr as ibkr_core

    host, port, _ = ibkr_core.get_ib_settings()
    if is_port_open(host, port):
        return [CheckResult("IBKR API", OK, detail=f"reachable on {host}:{port}")]
    return [
        CheckResult(
            "IBKR API",
            WARN,
            detail=f"not reachable on {host}:{port}",
            remediation="Start it with `ibkr gateway` (optional).",
        )
    ]


# --------------------------------------------------------------------------- #
# 6. External tools
# --------------------------------------------------------------------------- #
def _package_manager() -> str | None:
    """Return an available package manager command, or None."""
    if sys.platform == "win32":
        for mgr in ("winget", "choco"):
            if shutil.which(mgr):
                return mgr
        return None
    if sys.platform == "darwin":
        return "brew" if shutil.which("brew") else None
    for mgr in ("apt-get", "dnf", "yum"):
        if shutil.which(mgr):
            return mgr
    return None


def check_tools() -> list[CheckResult]:
    """Detect required external tools and Python dependencies."""
    results: list[CheckResult] = []
    settings = _db_settings()

    # PostgreSQL server — only relevant for a local database.
    if is_local_host(settings["host"]):
        running = is_port_open(settings["host"], settings["port"])
        client_tools = (
            shutil.which("psql") or shutil.which("pg_ctl") or shutil.which("postgres")
        )
        if running:
            results.append(
                CheckResult(
                    "PostgreSQL server",
                    OK,
                    detail=f"running on {settings['host']}:{settings['port']}",
                )
            )
        elif client_tools:
            results.append(
                CheckResult(
                    "PostgreSQL server",
                    WARN,
                    detail="installed but not listening on DB_PORT",
                    remediation="Start the PostgreSQL service.",
                )
            )
        else:
            results.append(
                CheckResult(
                    "PostgreSQL server",
                    FAIL,
                    detail="not installed",
                    remediation=(
                        "Install PostgreSQL (run with --install to attempt it)."
                    ),
                    required=True,
                    fix=_make_install_postgres(),
                )
            )
    else:
        results.append(
            CheckResult(
                "PostgreSQL server",
                SKIP,
                detail=f"remote host {settings['host']}",
            )
        )

    # IBKR client (optional).
    from tradingtools_stock.core import ibkr as ibkr_core

    exe = ibkr_core.find_gateway_executable()
    if exe is not None:
        results.append(CheckResult("IBKR client", OK, detail=str(exe)))
    else:
        results.append(
            CheckResult(
                "IBKR client",
                WARN,
                detail="IB Gateway/TWS not found",
                remediation=(
                    f"Download from {ibkr_core.GATEWAY_DOWNLOAD_URL} "
                    "or set IB_GATEWAY_PATH (optional)."
                ),
            )
        )

    # Python runtime dependencies.
    missing_deps = [
        dep for dep in RUNTIME_DEPS if importlib.util.find_spec(dep) is None
    ]
    if missing_deps:
        results.append(
            CheckResult(
                "Python deps",
                FAIL,
                detail=f"missing: {', '.join(missing_deps)}",
                remediation="Run `pip install -e .` in the project root.",
                required=True,
            )
        )
    else:
        results.append(
            CheckResult("Python deps", OK, detail=f"{len(RUNTIME_DEPS)} importable")
        )

    return results


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_all_checks() -> list[CheckResult]:
    """Run every check group and return the combined, ordered results."""
    results: list[CheckResult] = []
    results.extend(check_environment())
    db_results, conn = check_database()
    results.extend(db_results)
    try:
        results.extend(check_schema(conn))
    finally:
        if conn is not None:
            conn.close()
    results.extend(check_ibkr())
    results.extend(check_tools())
    return results


def has_required_failures(results: list[CheckResult]) -> bool:
    """Return True when any required check failed."""
    return any(r.required and r.status == FAIL for r in results)


# --------------------------------------------------------------------------- #
# Provisioning (only invoked with --install)
# --------------------------------------------------------------------------- #
def _make_provision_database():
    def _provision() -> str:
        return provision_database()

    return _provision


def _make_provision_tables():
    def _provision() -> str:
        return provision_tables()

    return _provision


def _make_install_postgres():
    def _provision() -> str:
        return install_postgres()

    return _provision


def provision_database() -> str:
    """Create the target database (if missing) and all tables."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    settings = _db_settings()
    if not (settings["name"] and settings["user"]):
        raise RuntimeError("DB_NAME/DB_USER must be set before provisioning.")

    conn = psycopg2.connect(
        dbname="postgres",
        user=settings["user"],
        password=settings["password"],
        host=settings["host"],
        port=settings["port"],
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    created = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                (settings["name"],),
            )
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{settings["name"]}"')
                created = True
    finally:
        conn.close()

    provision_tables()
    return (
        f"created database '{settings['name']}' and tables"
        if created
        else "database already existed; ensured tables"
    )


def provision_tables() -> str:
    """Create/migrate all tables in the target database."""
    from tradingtools_stock.core.fetcher import (
        create_tables_if_not_exist,
        get_db_connection,
    )

    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
    finally:
        conn.close()
    return "ensured all tables exist"


def install_postgres() -> str:
    """Install PostgreSQL via the platform package manager (best effort)."""
    mgr = _package_manager()
    if mgr is None:
        raise RuntimeError(
            "No supported package manager found. Install PostgreSQL manually "
            "from https://www.postgresql.org/download/."
        )
    commands = {
        "winget": ["winget", "install", "-e", "--id", "PostgreSQL.PostgreSQL"],
        "choco": ["choco", "install", "postgresql", "-y"],
        "brew": ["brew", "install", "postgresql"],
        "apt-get": ["sudo", "apt-get", "install", "-y", "postgresql"],
        "dnf": ["sudo", "dnf", "install", "-y", "postgresql-server"],
        "yum": ["sudo", "yum", "install", "-y", "postgresql-server"],
    }
    cmd = commands[mgr]
    subprocess.run(cmd, check=True)
    return f"installed PostgreSQL via {mgr}"
