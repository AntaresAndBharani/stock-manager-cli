"""
Trade sizing, buy-plan construction and persistence.

This module holds the *pure* trading logic (no IBKR connection): how a fixed
per-stock budget maps to a whole-share quantity, how the dashboard's entry
tables become a buy plan, and how executed trades are recorded to / read from
the database. Actually talking to IB Gateway lives in :mod:`core.ibkr`.

CLI-placed orders are tagged with :data:`ORDER_REF` so they can be told apart
from orders placed elsewhere (TWS, the IBKR app): a trade we recorded — or an
IBKR execution whose ``orderRef`` matches — is shown as ``CLI``; anything else
is ``Manual``.
"""

import math

import pandas as pd

# Tag written to every order's ``orderRef`` so executions placed by this tool
# are identifiable when read back from IBKR (vs. orders placed manually).
ORDER_REF = "stock-manager-cli"

# Default per-stock budget for an "average buy", in the stock's own currency.
DEFAULT_BUDGET = 150.0

SOURCE_CLI = "CLI"
SOURCE_MANUAL = "Manual"


def compute_buy_quantity(price: float | None, budget: float = DEFAULT_BUDGET) -> int:
    """
    Whole-share quantity for spending up to ``budget`` (stock currency) at
    ``price``.

    Returns 0 when the price is missing/non-positive or a single share already
    costs more than the budget — those rows are surfaced to the user but never
    auto-bought.
    """
    if price is None or not pd.notna(price) or price <= 0:
        return 0
    if budget <= 0:
        return 0
    return int(math.floor(budget / float(price)))


# Per-stock order method the user picks in the buy table.
METHOD_SHARES = "Shares"
METHOD_CASH = "Cash"
PLAN_COLUMNS = [
    "Symbol",
    "Market",
    "Signal",
    "Source",
    "Price",
    "Shares",
    "Est. Cost",
    "Cash",
    "Method",
]


def build_buy_plan(
    current_entries: pd.DataFrame,
    asof_entries: pd.DataFrame | None,
    markets: dict[str, str | None] | None = None,
    budget: float = DEFAULT_BUDGET,
    exclude_symbols: set[str] | None = None,
) -> pd.DataFrame:
    """
    Build a buy plan from the dashboard entry tables.

    ``current_entries`` and ``asof_entries`` are entry-signal frames (as produced
    by the dashboard) with at least ``Ticker``/``Symbol`` and ``Price`` columns.
    The plan is the *union* of both sets, one row per symbol, annotated with
    where the entry came from (``current`` / ``as-of`` / ``both``).

    ``markets`` optionally maps symbol -> market code so the resulting contract
    can be resolved by IBKR. ``exclude_symbols`` is dropped from the plan (used
    to hide stocks already bought this month).

    Each row offers two ways to spend the budget — ``Shares`` (whole shares,
    ``floor(budget / price)``) or ``Cash`` (a monetary cash-quantity order for
    the budget) — and a default ``Method`` the user can change per row.

    Columns: see :data:`PLAN_COLUMNS`.
    """

    def _symbols(df: pd.DataFrame | None) -> dict[str, dict]:
        out: dict[str, dict] = {}
        if df is None or df.empty:
            return out
        sym_col = "Ticker" if "Ticker" in df.columns else "Symbol"
        for _, row in df.iterrows():
            sym = row.get(sym_col)
            if not sym or pd.isna(sym):
                continue
            out[str(sym)] = {
                "Price": row.get("Price"),
                "Signal": row.get("Signal"),
            }
        return out

    cur = _symbols(current_entries)
    aso = _symbols(asof_entries)

    markets = markets or {}
    exclude_symbols = exclude_symbols or set()
    rows = []
    for sym in sorted(set(cur) | set(aso)):
        if sym in exclude_symbols:
            continue
        if sym in cur and sym in aso:
            source = "both"
        elif sym in cur:
            source = "current"
        else:
            source = "as-of"
        # Prefer the live price when the symbol is a current entry.
        info = cur.get(sym) or aso.get(sym)
        price = info.get("Price")
        price = float(price) if price is not None and pd.notna(price) else None
        shares = compute_buy_quantity(price, budget)
        rows.append(
            {
                "Symbol": sym,
                "Market": markets.get(sym),
                "Signal": info.get("Signal"),
                "Source": source,
                "Price": price,
                "Shares": shares,
                "Est. Cost": (shares * price) if price is not None else None,
                "Cash": budget,
                # Default to a cash order when a whole share is unaffordable,
                # otherwise whole shares.
                "Method": METHOD_CASH if shares <= 0 else METHOD_SHARES,
            }
        )

    return pd.DataFrame(rows, columns=PLAN_COLUMNS)


def symbols_bought_this_month(conn, today=None) -> set[str]:
    """
    Symbols with a recorded trade in the current calendar month.

    Used to hide stocks already bought this month from the buy table.
    """
    ensure_trades_table(conn)
    today = today or pd.Timestamp.today()
    month_start = pd.Timestamp(today).normalize().replace(day=1).date()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT symbol FROM trades WHERE placed_at::date >= %s",
            (month_start,),
        )
        return {row[0] for row in cur.fetchall()}


def ensure_trades_table(conn) -> None:
    """Create the ``trades`` table if it does not yet exist (idempotent)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id SERIAL PRIMARY KEY,
                symbol VARCHAR(10) NOT NULL,
                action VARCHAR(4) NOT NULL,
                quantity DECIMAL(18, 6),
                cash_amount DECIMAL(18, 6),
                method VARCHAR(10),
                price DECIMAL(18, 6),
                currency VARCHAR(10),
                order_ref VARCHAR(50),
                ib_order_id BIGINT,
                ib_perm_id BIGINT,
                status VARCHAR(20),
                source VARCHAR(10) DEFAULT 'CLI',
                placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (symbol) REFERENCES tickers(symbol)
            );
            """
        )
        # Migrate tables that predate cash-quantity orders: add the new columns
        # and relax quantity (NULL for cash orders, where shares aren't known).
        cur.execute(
            """
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS cash_amount DECIMAL(18, 6);
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS method VARCHAR(10);
            ALTER TABLE trades ALTER COLUMN quantity DROP NOT NULL;
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_placed_at "
            "ON trades(placed_at);"
        )
        conn.commit()


def record_trade(
    conn,
    symbol: str,
    action: str,
    quantity: float | None = None,
    *,
    cash_amount: float | None = None,
    method: str | None = None,
    price: float | None = None,
    currency: str | None = None,
    ib_order_id: int | None = None,
    ib_perm_id: int | None = None,
    status: str | None = None,
    source: str = SOURCE_CLI,
) -> None:
    """Persist a single CLI-placed trade so it survives IBKR's short execution
    retention window and remains tagged as ``CLI`` indefinitely.

    ``quantity`` is the whole-share count (Shares method); ``cash_amount`` is the
    monetary order size (Cash method). Exactly one is typically set.
    """
    ensure_trades_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO trades
                (symbol, action, quantity, cash_amount, method, price, currency,
                 order_ref, ib_order_id, ib_perm_id, status, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                symbol,
                action,
                quantity,
                cash_amount,
                method,
                price,
                currency,
                ORDER_REF,
                ib_order_id,
                ib_perm_id,
                status,
                source,
            ),
        )
        conn.commit()


def fetch_trades(conn, start=None, end=None) -> pd.DataFrame:
    """
    Read recorded CLI trades, optionally filtered to a [start, end] date range
    (inclusive). Returns columns suitable for display in the dashboard.
    """
    ensure_trades_table(conn)
    clauses = []
    params: list = []
    if start is not None:
        clauses.append("placed_at::date >= %s")
        params.append(start)
    if end is not None:
        clauses.append("placed_at::date <= %s")
        params.append(end)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"""
        SELECT
            placed_at AS "Placed At",
            symbol AS "Symbol",
            action AS "Action",
            method AS "Method",
            quantity AS "Quantity",
            cash_amount AS "Cash",
            price AS "Price",
            currency AS "Currency",
            status AS "Status",
            source AS "Source"
        FROM trades
        {where}
        ORDER BY placed_at DESC
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql_query(query, conn, params=tuple(params) or None)
