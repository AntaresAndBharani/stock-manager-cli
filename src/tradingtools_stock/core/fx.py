"""
Currency conversion to EUR.

Stocks trade in their own currency (USD, GBP, CHF, …) but the account is in
euros, so the buy view shows the EUR value of each order. Only the *display*
is converted — what we send to the broker stays a share count / fraction.

Rates are fetched from Yahoo Finance via the ``{CCY}EUR=X`` pairs (1 unit of
CCY = rate EUR).
"""

import pandas as pd


def to_eur(amount, currency: str | None, rates: dict[str, float | None]):
    """
    Convert ``amount`` (in ``currency``) to EUR using ``rates`` (currency ->
    EUR multiplier). Returns ``None`` when the amount is missing or no rate is
    available for the currency.
    """
    if amount is None or not pd.notna(amount):
        return None
    rate = 1.0 if currency == "EUR" else rates.get(currency or "")
    if rate is None:
        return None
    return float(amount) * float(rate)


def get_eur_rates(currencies) -> dict[str, float | None]:
    """
    Fetch EUR conversion rates for ``currencies``.

    Returns a mapping ``currency -> rate`` where ``rate`` is the EUR value of
    one unit of the currency (EUR itself is 1.0). A currency whose rate could
    not be fetched maps to ``None``.
    """
    rates: dict[str, float | None] = {"EUR": 1.0}
    needed = sorted({c for c in currencies if c and c != "EUR"})
    if not needed:
        return rates

    from tradingtools_stock.core import fetcher

    pairs = {c: f"{c}EUR=X" for c in needed}
    try:
        data = fetcher.yq.Ticker(list(pairs.values())).price
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    for ccy, sym in pairs.items():
        info = data.get(sym)
        price = info.get("regularMarketPrice") if isinstance(info, dict) else None
        rates[ccy] = float(price) if price else None
    return rates
