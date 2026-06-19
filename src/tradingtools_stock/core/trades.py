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


PLAN_COLUMNS = [
    "Symbol",
    "Market",
    "Currency",
    "Signal",
    "Source",
    "1000 SMA Touch Days",
    "Price",
    "Shares",
    "Est. Cost",
]


def default_share_quantity(
    price: float | None, budget: float = DEFAULT_BUDGET
) -> float:
    """
    Default number of shares for spending ~``budget`` (stock currency) at
    ``price``.

    Whole shares when at least one is affordable (``floor(budget / price)``);
    otherwise a **fractional** share (``budget / price``) so an expensive stock
    — one share dearer than the budget — can still be bought as a partial
    position. The user can override this per row in the buy table.
    """
    if price is None or not pd.notna(price) or price <= 0 or budget <= 0:
        return 0.0
    raw = budget / float(price)
    if raw >= 1:
        return float(math.floor(raw))
    return round(raw, 4)


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

    ``Shares`` seeds a per-row, user-editable quantity (whole when affordable,
    fractional/partial when one share exceeds the budget); ``Est. Cost`` is
    ``Shares * Price``.

    Columns: see :data:`PLAN_COLUMNS`.
    """

    from tradingtools_stock.core.ibkr import MARKET_TO_IB

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
                "Touch": row.get("1000 SMA Touch Days"),
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
        info = cur.get(sym) or aso.get(sym) or {}
        price = info.get("Price")
        price = float(price) if price is not None and pd.notna(price) else None
        shares = default_share_quantity(price, budget)
        market = markets.get(sym)
        currency = MARKET_TO_IB.get((market or "").upper(), ("USD", ""))[0]
        rows.append(
            {
                "Symbol": sym,
                "Market": market,
                "Currency": currency,
                "Signal": info.get("Signal"),
                "Source": source,
                "1000 SMA Touch Days": info.get("Touch"),
                "Price": price,
                "Shares": shares,
                "Est. Cost": (shares * price) if price is not None else None,
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
                ib_exec_id VARCHAR(64),
                status VARCHAR(20),
                source VARCHAR(10) DEFAULT 'CLI',
                placed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (symbol) REFERENCES tickers(symbol)
            );
            """
        )
        # Migrate tables that predate cash-quantity orders / execution
        # reconciliation: add the new columns and relax quantity (NULL for cash
        # orders, where shares aren't known up front).
        cur.execute(
            """
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS cash_amount DECIMAL(18, 6);
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS method VARCHAR(10);
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS ib_exec_id VARCHAR(64);
            ALTER TABLE trades ALTER COLUMN quantity DROP NOT NULL;
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_placed_at "
            "ON trades(placed_at);"
        )
        # One row per IBKR execution: dedupe reconciled fills (NULLs allowed for
        # CLI placement records, which have no execution id).
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_exec_id "
            "ON trades(ib_exec_id) WHERE ib_exec_id IS NOT NULL;"
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
    ib_exec_id: str | None = None,
    order_ref: str | None = ORDER_REF,
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
                 order_ref, ib_order_id, ib_perm_id, ib_exec_id, status, source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                symbol,
                action,
                quantity,
                cash_amount,
                method,
                price,
                currency,
                order_ref,
                ib_order_id,
                ib_perm_id,
                ib_exec_id,
                status,
                source,
            ),
        )
        conn.commit()


def fetch_trades(conn, start=None, end=None) -> pd.DataFrame:
    """
    Read recorded trades, optionally filtered to a [start, end] date range
    (inclusive). Includes CLI placements and any reconciled IBKR executions
    (tagged ``Manual``). Returns columns suitable for display in the dashboard.
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
            quantity AS "Quantity",
            price AS "Price",
            currency AS "Currency",
            status AS "Status",
            source AS "Source",
            ib_exec_id AS "Exec Id"
        FROM trades
        {where}
        ORDER BY placed_at DESC
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql_query(query, conn, params=tuple(params) or None)


def select_new_executions(
    executions: pd.DataFrame, existing_exec_ids: set[str]
) -> pd.DataFrame:
    """
    From an executions frame (as returned by ``ibkr.fetch_executions``), pick the
    rows worth importing into the ``trades`` table: **Manual** fills (CLI buys are
    already recorded at placement time) with an ``Exec Id`` we haven't stored yet.

    Pure helper, no DB — keeps the dedup/filter logic unit-testable.
    """
    if executions is None or executions.empty:
        return executions if executions is not None else pd.DataFrame()
    mask = (
        (executions["Source"] == SOURCE_MANUAL)
        & executions["Exec Id"].notna()
        & ~executions["Exec Id"].isin(existing_exec_ids)
    )
    return executions[mask].reset_index(drop=True)


def reconcile_executions(conn, executions: pd.DataFrame) -> int:
    """
    Import new Manual IBKR executions into the ``trades`` table so they feed the
    unified history and the "already bought this month" check. Deduped by IBKR
    ``execId``. Returns the number of rows inserted.

    CLI executions are intentionally skipped — those are recorded when the order
    is placed, so importing them again would double-count.
    """
    ensure_trades_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT ib_exec_id FROM trades WHERE ib_exec_id IS NOT NULL")
        existing = {row[0] for row in cur.fetchall()}

    new_rows = select_new_executions(executions, existing)
    if new_rows.empty:
        return 0

    inserted = 0
    for _, row in new_rows.iterrows():
        # Ensure the symbol exists in tickers (manual buys may include symbols
        # not in our active universe) to satisfy the FK.
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tickers (symbol, name, active) VALUES (%s, %s, %s) "
                "ON CONFLICT (symbol) DO NOTHING",
                (row["Symbol"], row["Symbol"], False),
            )
        record_trade(
            conn,
            row["Symbol"],
            str(row["Action"]).upper()[:4],
            quantity=float(row["Quantity"]) if pd.notna(row["Quantity"]) else None,
            method=None,
            price=float(row["Price"]) if pd.notna(row["Price"]) else None,
            currency=row.get("Currency"),
            ib_exec_id=row["Exec Id"],
            order_ref=row.get("Order Ref") or None,
            status="Filled",
            source=SOURCE_MANUAL,
        )
        inserted += 1
    return inserted
