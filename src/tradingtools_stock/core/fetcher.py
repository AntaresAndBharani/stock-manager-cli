import logging
import os
import time

import pandas as pd
import psycopg2
import requests  # type: ignore
from psycopg2.extras import execute_values

# Patch requests to handle encoding before importing yahooquery
original_text_property = requests.models.Response.text.fget  # type: ignore
original_content_property = requests.models.Response.content.fget  # type: ignore


def patched_text_property(self):
    """Patched text property that handles encoding errors."""
    try:
        return original_text_property(self)
    except UnicodeDecodeError:
        # Fallback to latin-1 encoding with error replacement
        return self.content.decode("latin-1", errors="replace")


def patched_content_property(self):
    """Patched content property that ensures proper encoding."""
    return original_content_property(self)


# Override the apparent_encoding to prevent auto-detection issues
original_init = requests.models.Response.__init__


def patched_init(self, *args, **kwargs):
    original_init(self, *args, **kwargs)
    # Set encoding explicitly to avoid utf-8 codec errors
    if self.encoding is None or self.encoding == "ISO-8859-1":
        self._encoding = "latin-1"


requests.models.Response.__init__ = patched_init  # type: ignore
requests.models.Response.text = property(patched_text_property)  # type: ignore

# Now import yahooquery after patching
import yahooquery as yq  # noqa: E402


def get_db_connection():
    """Create and return a database connection using environment variables."""
    try:
        return psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            database=os.environ.get("DB_NAME"),
            user=os.environ.get("DB_USER"),
            password=os.environ.get("DB_PASS"),
            port=os.environ.get("DB_PORT", "5432"),
        )
    except UnicodeDecodeError as err:
        # libpq may return localized Windows errors encoded with the ANSI codepage
        raw_message = ""
        if isinstance(err.object, (bytes, bytearray)):
            raw_message = err.object.decode("latin-1", errors="replace")
        friendly = (
            "Database connection failed due to a non UTF-8 error message. "
            "Original message: "
            f"{raw_message or err}"
        )
        raise psycopg2.OperationalError(friendly) from err


def create_tables_if_not_exist(conn):
    """Create the stock data tables if they don't exist."""
    logging.info("Checking/creating database tables...")
    with conn.cursor() as cur:
        # Create tickers table
        logging.debug("Ensuring 'tickers' table exists")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS tickers (
                symbol VARCHAR(10) PRIMARY KEY,
                name VARCHAR(255),
                active BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Add market column if it doesn't exist
        logging.debug(
            "Ensuring 'market', 'sector', and 'industry' columns exist on tickers table"
        )
        cur.execute(
            """
            ALTER TABLE tickers ADD COLUMN IF NOT EXISTS market VARCHAR(50);
            ALTER TABLE tickers ADD COLUMN IF NOT EXISTS sector VARCHAR(100);
            ALTER TABLE tickers ADD COLUMN IF NOT EXISTS industry VARCHAR(100);
            """
        )

        # Create stock_prices table
        logging.debug("Ensuring 'stock_prices' table exists")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_prices (
                date DATE NOT NULL,
                symbol VARCHAR(10) NOT NULL,
                open DECIMAL(18, 6),
                high DECIMAL(18, 6),
                low DECIMAL(18, 6),
                close DECIMAL(18, 6),
                volume BIGINT,
                quarterly_eps DECIMAL(18, 6),
                quarterly_revenue DECIMAL(24, 2),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (date, symbol),
                FOREIGN KEY (symbol) REFERENCES tickers(symbol)
            );
            """
        )

        # Create dashboard_cache table
        logging.debug("Ensuring 'dashboard_cache' table exists")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dashboard_cache (
                symbol VARCHAR(10) PRIMARY KEY,
                calculation_date DATE,
                signal VARCHAR(50),
                trend_1m VARCHAR(50),
                trend_3m VARCHAR(50),
                price DECIMAL(18, 6),
                ema_21 DECIMAL(18, 6),
                sma_50 DECIMAL(18, 6),
                sma_100 DECIMAL(18, 6),
                sma_200 DECIMAL(18, 6),
                sma_1000 DECIMAL(18, 6),
                sma_1000_touch_days INT,
                FOREIGN KEY (symbol) REFERENCES tickers(symbol)
            );
            """
        )

        # Migrate dashboard_cache: replace sma_1000_touch (VARCHAR) with two
        # typed columns sma_1000 (DECIMAL) and sma_1000_touch_days (INT).
        cur.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'dashboard_cache'
                      AND column_name = 'sma_1000_touch'
                ) THEN
                    ALTER TABLE dashboard_cache DROP COLUMN sma_1000_touch;
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'dashboard_cache'
                      AND column_name = 'sma_1000'
                ) THEN
                    ALTER TABLE dashboard_cache ADD COLUMN sma_1000 DECIMAL(18, 6);
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_name = 'dashboard_cache'
                      AND column_name = 'sma_1000_touch_days'
                ) THEN
                    ALTER TABLE dashboard_cache ADD COLUMN sma_1000_touch_days INT;
                END IF;
            END $$;
            """
        )

        # Create app_config table (persistent key/value settings, e.g. the
        # admin-configurable 1000 SMA touch lookback).
        logging.debug("Ensuring 'app_config' table exists")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS app_config (
                key VARCHAR(100) PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # Create valuation_history table (quarterly valuation multiples sourced
        # from Yahoo Finance; populated by `fetch valuation`, consumed by the
        # Valuation dashboard tab).
        logging.debug("Ensuring 'valuation_history' table exists")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS valuation_history (
                symbol VARCHAR(10) NOT NULL,
                as_of_date DATE NOT NULL,
                period_type VARCHAR(10) NOT NULL DEFAULT '',
                trailing_pe DECIMAL(18, 6),
                forward_pe DECIMAL(18, 6),
                pb DECIMAL(18, 6),
                ps DECIMAL(18, 6),
                peg DECIMAL(18, 6),
                ev_ebitda DECIMAL(18, 6),
                ev_revenue DECIMAL(18, 6),
                market_cap DECIMAL(24, 2),
                enterprise_value DECIMAL(24, 2),
                dividend_yield DECIMAL(10, 6),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (symbol, as_of_date, period_type),
                FOREIGN KEY (symbol) REFERENCES tickers(symbol)
            );
            """
        )

        # Create trades table (CLI-placed orders, recorded so they outlive
        # IBKR's short execution-retention window and stay tagged as 'CLI';
        # consumed by the IBKR Portfolio dashboard tab's Trades view).
        logging.debug("Ensuring 'trades' table exists")
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
        # Migrate trades tables created before cash-quantity order support.
        cur.execute(
            """
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS cash_amount DECIMAL(18, 6);
            ALTER TABLE trades ADD COLUMN IF NOT EXISTS method VARCHAR(10);
            ALTER TABLE trades ALTER COLUMN quantity DROP NOT NULL;
            """
        )

        # Create indexes
        logging.debug("Ensuring indexes exist")
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stock_prices_symbol ON stock_prices(symbol);
            CREATE INDEX IF NOT EXISTS idx_stock_prices_date ON stock_prices(date);
            CREATE INDEX IF NOT EXISTS idx_valuation_symbol
                ON valuation_history(symbol);
            CREATE INDEX IF NOT EXISTS idx_trades_placed_at ON trades(placed_at);
            """
        )

        conn.commit()
    logging.info("Database table initialization complete")


def get_active_tickers(conn):
    """
    Fetch active tickers from the database.

    Args:
        conn: Database connection

    Returns:
        list: List of active ticker symbols (uppercase)
    """
    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM tickers WHERE active = %s", (True,))
        rows = cur.fetchall()
        return [row[0].upper() for row in rows]


def get_active_tickers_with_markets(conn, symbols=None):
    """
    Fetch active tickers and their markets from the database.

    Args:
        conn: Database connection
        symbols: Optional list of symbols to filter by

    Returns:
        list: List of dicts with 'symbol' and 'market'
    """
    with conn.cursor() as cur:
        if symbols:
            cur.execute(
                "SELECT symbol, market FROM tickers WHERE symbol = ANY(%s) AND active = %s",
                (list(symbols), True),
            )
        else:
            cur.execute("SELECT symbol, market FROM tickers WHERE active = %s", (True,))
        rows = cur.fetchall()
        return [
            {"symbol": row[0].upper(), "market": row[1].upper() if row[1] else None}
            for row in rows
        ]


def format_yahoo_ticker(symbol: str, market: str) -> str:
    """Format the ticker symbol for Yahoo Finance based on the market."""
    if not market:
        return symbol

    market = market.upper()
    mapping = {
        "LSE": ".L",
        "BME": ".MC",
        "XETR": ".DE",
        "GETTEX": ".DE",
        "MIL": ".MI",
        "SIX": ".SW",
        "TSX": ".TO",
        "OMXSTO": ".ST",
        "EURONEXT": ".PA",
        "LSIN": ".IL",
        "TSE": ".T",
        "HKEX": ".HK",
        "NSE": ".NS",
        "BSE": ".BO",
        "SSE": ".SS",
        "SZSE": ".SZ",
    }

    suffix = mapping.get(market, "")
    return f"{symbol}{suffix}"


def get_existing_data_range(conn, symbol):
    """
    Get the min and max dates for which we have data for a ticker.

    Args:
        conn: Database connection
        symbol: Ticker symbol

    Returns:
        tuple: (min_date, max_date) or (None, None) if no data
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MIN(date), MAX(date) FROM stock_prices WHERE symbol = %s",
            (symbol.upper(),),
        )
        return cur.fetchone()


def get_global_max_date(conn):
    """
    Get the overall maximum date for which we have any stock data.

    Args:
        conn: Database connection

    Returns:
        datetime.date: The maximum date, or None if no data exists.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(date) FROM stock_prices")
        result = cur.fetchone()
        return result[0] if result and result[0] else None


def get_all_existing_data_ranges(conn, symbols):
    """
    Get the min and max dates for which we have data for multiple tickers.

    Args:
        conn: Database connection
        symbols: List of ticker symbols

    Returns:
        dict: Mapping of symbol to (min_date, max_date)
    """
    if not symbols:
        return {}

    symbols_upper = [s.upper() for s in symbols]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, MIN(date), MAX(date) FROM stock_prices WHERE symbol = ANY(%s) GROUP BY symbol",
            (symbols_upper,),
        )
        rows = cur.fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}


def fetch_stock_data(
    ticker, start_date, end_date, include_fundamentals=False, yahoo_ticker=None
):
    """
    Fetch daily OHLC data for a single ticker with retry logic.
    Optionally include historical fundamentals.

    Args:
        ticker: Stock ticker symbol
        start_date: Start date string (YYYY-MM-DD)
        end_date: End date string (YYYY-MM-DD)
        include_fundamentals: Whether to include quarterly fundamentals

    Returns:
        pandas DataFrame with stock data
    """
    max_retries = 5
    for attempt in range(max_retries):
        try:
            logging.debug(
                f"Downloading data for {ticker} from {start_date} to {end_date} "
                f"(attempt {attempt + 1})"
            )

            # Adjust end_date to be inclusive by adding one day
            start_dt = pd.to_datetime(start_date)
            end_dt = pd.to_datetime(end_date)

            # If dates are the same, yahooquery might return no data if we don't extend
            if start_dt == end_dt:
                end_dt = end_dt + pd.Timedelta(days=1)

            start_str = start_dt.strftime("%Y-%m-%d")

            query_ticker = yahoo_ticker if yahoo_ticker else ticker
            stock = yq.Ticker(query_ticker)

            # Fetch data with encoding error handling
            try:
                # yahooquery end is exclusive, so we usually want end_dt + 1 day
                # but if we already handled start==end above, we can just use end_str
                # for the general case let's ensure end is strictly after start
                query_end = (end_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                data = stock.history(start=start_str, end=query_end, interval="1d")
            except UnicodeDecodeError as ude:
                logging.warning(f"UTF-8 decoding error for {ticker}: {ude}")
                raise

            # Handle potential encoding errors in the response
            if isinstance(data, str):
                logging.warning(
                    f"Received string response for {ticker}, attempting to parse"
                )
                data = pd.DataFrame()

            logging.debug(f"Data shape: {data.shape}, columns: {list(data.columns)}")

            if data.empty:
                logging.warning(f"No data found for {ticker}")
                return pd.DataFrame()

            # Reset index to get 'date' as column
            data = data.reset_index()
            data["symbol"] = ticker

            # Standardize column names (yahooquery uses lowercase)
            data = data.rename(
                columns={
                    "date": "Date",
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume",
                }
            )

            # Fetch historical fundamentals if requested
            if include_fundamentals:
                try:
                    logging.debug(f"Fetching historical fundamentals for {ticker}")

                    # Get quarterly earnings (historical EPS, revenue)
                    quarterly_earnings = stock.earning_history
                    if (
                        isinstance(quarterly_earnings, pd.DataFrame)
                        and not quarterly_earnings.empty
                    ):
                        quarterly_earnings = quarterly_earnings.reset_index()
                        if "quarter" in quarterly_earnings.columns:
                            quarterly_earnings = quarterly_earnings.rename(
                                columns={
                                    "epsActual": "Quarterly_EPS",
                                    "quarter": "Quarter_Date",
                                }
                            )
                            # Convert quarter to datetime if needed
                            if "Quarter_Date" in quarterly_earnings.columns:
                                quarterly_earnings["Quarter_Date"] = pd.to_datetime(
                                    quarterly_earnings["Quarter_Date"], errors="coerce"
                                )

                                # Keep only necessary columns
                                cols_to_keep = ["Quarter_Date", "Quarterly_EPS"]
                                available_cols = [
                                    c
                                    for c in cols_to_keep
                                    if c in quarterly_earnings.columns
                                ]
                                if available_cols:
                                    quarterly_earnings = quarterly_earnings[
                                        available_cols
                                    ].dropna(subset=["Quarter_Date"])

                                    # Merge quarterly data with daily data
                                    data = pd.merge_asof(
                                        data.sort_values("Date"),
                                        quarterly_earnings.sort_values("Quarter_Date"),
                                        left_on="Date",
                                        right_on="Quarter_Date",
                                        direction="backward",
                                    )
                                    if "Quarter_Date" in data.columns:
                                        data = data.drop(columns=["Quarter_Date"])
                    else:
                        logging.warning(f"No quarterly earnings data for {ticker}")
                        data["Quarterly_EPS"] = None

                    # Try to get revenue from financial data
                    financials = stock.financial_data
                    if isinstance(financials, dict) and ticker in financials:
                        fin_data = financials[ticker]
                        if isinstance(fin_data, dict):
                            data["Quarterly_Revenue"] = fin_data.get(
                                "totalRevenue", None
                            )
                        else:
                            data["Quarterly_Revenue"] = None
                    else:
                        data["Quarterly_Revenue"] = None

                except Exception as e:
                    logging.warning(
                        f"Failed to fetch historical fundamentals for {ticker}: {e}"
                    )
                    data["Quarterly_EPS"] = None
                    data["Quarterly_Revenue"] = None

            logging.debug(f"Successfully fetched {len(data)} rows for {ticker}")

            # Select and return relevant columns
            columns = ["Date", "symbol", "Open", "High", "Low", "Close", "Volume"]
            if include_fundamentals:
                columns.extend(["Quarterly_EPS", "Quarterly_Revenue"])

            # Only keep columns that exist
            available_columns = [c for c in columns if c in data.columns]
            return data[available_columns]

        except UnicodeDecodeError as e:
            logging.warning(
                f"UTF-8 decoding error for {ticker} (attempt {attempt + 1}): {e}"
            )
            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logging.info(f"Retrying {ticker} in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logging.error(
                    f"Failed to fetch data for {ticker} after {max_retries} attempts "
                    "due to encoding error"
                )
                return pd.DataFrame()
        except Exception as e:
            logging.warning(f"Attempt {attempt + 1} failed for {ticker}: {e}")
            if attempt < max_retries - 1:
                # Exponential backoff: 1, 2, 4, 8, 16 seconds
                wait_time = 2**attempt
                logging.info(f"Retrying {ticker} in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logging.error(
                    f"Failed to fetch data for {ticker} after {max_retries} attempts"
                )
                return pd.DataFrame()

    return pd.DataFrame()


def upsert_stock_data(conn, data, include_fundamentals=False):
    """
    Upsert stock data into the database to avoid duplicates.

    Args:
        conn: Database connection
        data: pandas DataFrame with stock data
        include_fundamentals: Whether fundamentals columns are included
    """
    if data.empty:
        logging.warning("No data to insert")
        return 0

    with conn.cursor() as cur:
        # Ensure all unique symbols in data are in the tickers table
        unique_symbols = data["symbol"].unique()
        for sym in unique_symbols:
            cur.execute(
                """
                INSERT INTO tickers (symbol, name, active)
                VALUES (%s, %s, %s)
                ON CONFLICT (symbol) DO NOTHING
                """,
                (sym, sym, True),
            )

        # Prepare data for insertion
        records = []
        for _, row in data.iterrows():
            record = (
                row["Date"].date() if hasattr(row["Date"], "date") else row["Date"],
                row["symbol"],
                float(row["Open"]) if pd.notna(row["Open"]) else None,
                float(row["High"]) if pd.notna(row["High"]) else None,
                float(row["Low"]) if pd.notna(row["Low"]) else None,
                float(row["Close"]) if pd.notna(row["Close"]) else None,
                int(row["Volume"]) if pd.notna(row["Volume"]) else None,
                (
                    float(row.get("Quarterly_EPS"))
                    if include_fundamentals and pd.notna(row.get("Quarterly_EPS"))
                    else None
                ),
                (
                    float(row.get("Quarterly_Revenue"))
                    if include_fundamentals and pd.notna(row.get("Quarterly_Revenue"))
                    else None
                ),
            )
            records.append(record)

        # Upsert using ON CONFLICT
        insert_sql = """
            INSERT INTO stock_prices 
                (date, symbol, open, high, low, close, volume, 
                quarterly_eps, quarterly_revenue)
            VALUES %s
            ON CONFLICT (date, symbol) 
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                quarterly_eps = EXCLUDED.quarterly_eps,
                quarterly_revenue = EXCLUDED.quarterly_revenue,
                updated_at = CURRENT_TIMESTAMP
        """

        execute_values(cur, insert_sql, records)
        conn.commit()

        logging.info(f"Upserted {len(records)} records to database")
        return len(records)


def update_tickers_metadata(conn, workers=5, progress_callback=None):
    """
    Update sector and industry metadata for all tickers missing this information.
    Uses ThreadPoolExecutor for concurrent fetching to optimize time.
    """
    import concurrent.futures

    logging.info("Updating tickers metadata...")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, market FROM tickers WHERE active = true AND (sector IS NULL OR industry IS NULL)"
        )
        rows = cur.fetchall()

    if not rows:
        logging.info("No tickers need metadata updates.")
        return

    def fetch_metadata(row):
        symbol, market = row
        sector = "Unknown"
        industry = "Unknown"
        try:
            query_ticker = format_yahoo_ticker(symbol, market)
            stock = yq.Ticker(query_ticker)
            profile = stock.asset_profile

            if (
                isinstance(profile, dict)
                and query_ticker in profile
                and isinstance(profile[query_ticker], dict)
            ):
                data = profile[query_ticker]
                sector = data.get("sector", "Unknown")
                industry = data.get("industry", "Unknown")

            return symbol, sector, industry, None
        except Exception as e:
            return symbol, sector, industry, e

    updated_count = 0
    total_tickers = len(rows)

    # We fetch concurrently, but update the database synchronously to avoid DB connection issues.
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        # Submit all tasks
        future_to_symbol = {
            executor.submit(fetch_metadata, row): row[0] for row in rows
        }

        for future in concurrent.futures.as_completed(future_to_symbol):
            symbol, sector, industry, error = future.result()

            if error:
                logging.warning(f"Failed to fetch metadata for {symbol}: {error}")
            else:
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE tickers SET sector = %s, industry = %s WHERE symbol = %s",
                            (sector, industry, symbol),
                        )
                        conn.commit()
                    updated_count += 1
                    logging.info(
                        f"Updated metadata for {symbol}: Sector={sector}, Industry={industry}"
                    )
                except Exception as e:
                    logging.warning(f"Database error while updating {symbol}: {e}")

            # Fire progress callback if provided
            if progress_callback:
                progress_callback(symbol, sector, industry, error)

    logging.info(
        f"Metadata update complete. Updated {updated_count}/{total_tickers} tickers."
    )
