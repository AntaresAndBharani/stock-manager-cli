import pytest

from tradingtools_stock.core import fx


def test_to_eur_converts_with_rate():
    rates = {"EUR": 1.0, "USD": 0.9, "GBP": 1.15}
    assert fx.to_eur(100, "EUR", rates) == 100.0
    assert fx.to_eur(100, "USD", rates) == pytest.approx(90.0)
    assert fx.to_eur(200, "GBP", rates) == pytest.approx(230.0)


def test_to_eur_missing_or_none():
    rates = {"USD": 0.9}
    assert fx.to_eur(None, "USD", rates) is None  # no amount
    assert fx.to_eur(100, "JPY", rates) is None  # no rate for currency
    assert fx.to_eur(100, None, rates) is None  # no currency


def test_to_eur_eur_needs_no_rate():
    # EUR always converts 1:1 even if absent from the rates map.
    assert fx.to_eur(50, "EUR", {}) == 50.0


def test_get_eur_rates_short_circuits_without_network():
    # Only EUR / blanks -> no lookup, returns the identity map.
    assert fx.get_eur_rates(["EUR", None, ""]) == {"EUR": 1.0}
