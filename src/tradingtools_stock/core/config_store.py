"""
Persistent application configuration stored in the database.

Settings are kept as simple key/value strings in the ``app_config`` table so
that values changed from the dashboard's Admin section survive process
restarts and are picked up on the next execution.
"""

from tradingtools_stock.core.strategies import SMA_1000_TOUCH_LOOKBACK_DAYS

# Config keys
KEY_SMA_1000_TOUCH_LOOKBACK = "sma_1000_touch_lookback_days"

# Fallback used when no value has been persisted yet. Mirrors the module-level
# default in ``strategies`` so behaviour is identical before any admin change.
DEFAULT_SMA_1000_TOUCH_LOOKBACK_DAYS = SMA_1000_TOUCH_LOOKBACK_DAYS


def ensure_config_table(conn):
    """Create the ``app_config`` table if it does not yet exist (idempotent)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_config (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()


def get_config(conn, key, default=None):
    """Return the stored string value for ``key`` or ``default`` if unset."""
    ensure_config_table(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM app_config WHERE key = %s", (key,))
        row = cur.fetchone()
    return row[0] if row and row[0] is not None else default


def set_config(conn, key, value):
    """Persist ``value`` (stored as text) for ``key`` (insert or update)."""
    ensure_config_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO app_config (key, value, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (key)
            DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, str(value)),
        )
        conn.commit()


def get_sma_1000_touch_lookback(conn):
    """Return the configured 1000-day SMA touch lookback in trading days.

    Falls back to the default when no (or an invalid) value is stored.
    """
    raw = get_config(conn, KEY_SMA_1000_TOUCH_LOOKBACK)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SMA_1000_TOUCH_LOOKBACK_DAYS
    return value if value > 0 else DEFAULT_SMA_1000_TOUCH_LOOKBACK_DAYS


def set_sma_1000_touch_lookback(conn, days):
    """Persist the 1000-day SMA touch lookback (positive integer days)."""
    set_config(conn, KEY_SMA_1000_TOUCH_LOOKBACK, int(days))
