from pathlib import Path

import pytest
from typer.testing import CliRunner

from tradingtools_stock.core import ibkr as ibkr_core
from tradingtools_stock.main import app


def test_ibkr_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["ibkr", "--help"])
    assert result.exit_code == 0
    assert "gateway" in result.stdout
    assert "status" in result.stdout
    assert "trades" in result.stdout


def test_make_stock_contract_us_default() -> None:
    # Unknown/None market falls back to USD on SMART with no primaryExchange.
    contract = ibkr_core._make_stock_contract("AAPL", None)
    assert contract.symbol == "AAPL"
    assert contract.currency == "USD"
    assert contract.exchange == "SMART"
    assert not contract.primaryExchange


def test_make_stock_contract_mapped_market() -> None:
    contract = ibkr_core._make_stock_contract("SAN", "BME")
    assert contract.currency == "EUR"
    assert contract.exchange == "SMART"
    assert contract.primaryExchange == "BM"


def test_get_ib_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IB_HOST", raising=False)
    monkeypatch.delenv("IB_PORT", raising=False)
    monkeypatch.delenv("IB_CLIENT_ID", raising=False)
    host, port, client_id = ibkr_core.get_ib_settings()
    assert host == ibkr_core.DEFAULT_HOST
    assert port == ibkr_core.DEFAULT_PORT
    assert client_id == ibkr_core.DEFAULT_CLIENT_ID


def test_get_ib_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_HOST", "10.0.0.5")
    monkeypatch.setenv("IB_PORT", "7497")
    monkeypatch.setenv("IB_CLIENT_ID", "42")
    assert ibkr_core.get_ib_settings() == ("10.0.0.5", 7497, 42)


def test_find_gateway_executable_env_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = tmp_path / "ibgateway.exe"
    exe.touch()
    monkeypatch.setenv("IB_GATEWAY_PATH", str(exe))
    assert ibkr_core.find_gateway_executable() == exe


def test_find_gateway_executable_env_path_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("IB_GATEWAY_PATH", str(tmp_path / "nope.exe"))
    assert ibkr_core.find_gateway_executable() is None


def test_ibkr_status_no_gateway(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ibkr_core, "is_api_port_open", lambda *a, **k: False)
    result = runner.invoke(app, ["ibkr", "status"])
    assert result.exit_code == 1
    assert "No TWS/IB Gateway API" in result.stdout


def test_ibkr_gateway_not_installed(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ibkr_core, "is_api_port_open", lambda *a, **k: False)
    monkeypatch.setattr(ibkr_core, "find_gateway_executable", lambda: None)
    result = runner.invoke(app, ["ibkr", "gateway"])
    assert result.exit_code == 1
    assert "not installed" in result.stdout


def test_ibkr_gateway_already_running(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ibkr_core, "is_api_port_open", lambda *a, **k: True)
    result = runner.invoke(app, ["ibkr", "gateway"])
    assert result.exit_code == 0
    assert "already reachable" in result.stdout


def test_ibkr_gateway_launches(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = tmp_path / "ibgateway.exe"
    exe.touch()
    launched: list[Path] = []
    monkeypatch.setattr(ibkr_core, "is_api_port_open", lambda *a, **k: False)
    monkeypatch.setattr(ibkr_core, "find_gateway_executable", lambda: exe)
    monkeypatch.setattr(ibkr_core, "launch_gateway", launched.append)
    result = runner.invoke(app, ["ibkr", "gateway"])
    assert result.exit_code == 0
    assert launched == [exe]
