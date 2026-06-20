import pytest
from typer.testing import CliRunner

from tradingtools_stock.core import setup as setup_core
from tradingtools_stock.core.setup import FAIL, OK, WARN, CheckResult
from tradingtools_stock.main import app


@pytest.fixture(autouse=True)
def _clear_db_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test from a clean DB environment."""
    for var in ("DB_NAME", "DB_USER", "DB_PASS", "DB_HOST", "DB_PORT"):
        monkeypatch.delenv(var, raising=False)


def test_setup_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["setup", "--help"])
    assert result.exit_code == 0
    assert "--install" in result.stdout


def test_check_environment_missing_required() -> None:
    results = setup_core.check_environment()
    by_name = {r.name: r for r in results}
    assert by_name["DB_NAME"].status == FAIL
    assert by_name["DB_NAME"].required is True
    # Defaulted vars warn rather than fail.
    assert by_name["DB_HOST"].status == WARN


def test_check_environment_masks_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_NAME", "db")
    monkeypatch.setenv("DB_USER", "user")
    monkeypatch.setenv("DB_PASS", "supersecret")
    by_name = {r.name: r for r in setup_core.check_environment()}
    assert by_name["DB_PASS"].status == OK
    assert "supersecret" not in by_name["DB_PASS"].detail


def test_check_database_skips_without_env() -> None:
    results, conn = setup_core.check_database()
    assert conn is None
    assert results[0].name == "DB connection"
    assert results[0].status == "skip"


def test_check_database_reports_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DB_NAME", "db")
    monkeypatch.setenv("DB_USER", "user")
    monkeypatch.setenv("DB_PASS", "pw")

    def _boom():
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr("tradingtools_stock.core.fetcher.get_db_connection", _boom)
    # _check_database_exists also tries to connect; make it a clean WARN.
    monkeypatch.setattr(
        setup_core,
        "_check_database_exists",
        lambda settings: CheckResult("DB exists", WARN, detail="x"),
    )
    results, conn = setup_core.check_database()
    assert conn is None
    assert results[0].name == "DB connection"
    assert results[0].status == FAIL
    assert results[0].required is True


def test_check_schema_skips_without_conn() -> None:
    results = setup_core.check_schema(None)
    assert results[0].status == "skip"


def test_check_schema_detects_missing_table() -> None:
    class FakeCursor:
        def __init__(self) -> None:
            self._last = ""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, *args):
            self._last = sql

        def fetchall(self):
            if "information_schema.tables" in self._last:
                # Only one of the expected tables exists.
                return [("tickers",)]
            if "information_schema.columns" in self._last:
                return [("tickers", "symbol")]
            return []

        def fetchone(self):
            return [0]

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    results = setup_core.check_schema(FakeConn())
    by_name = {r.name: r for r in results}
    assert by_name["Tables"].status == FAIL
    assert "stock_prices" in by_name["Tables"].detail
    assert by_name["Tables"].required is True


def test_has_required_failures() -> None:
    ok = [CheckResult("a", OK), CheckResult("b", WARN, required=True)]
    assert setup_core.has_required_failures(ok) is False
    bad = [CheckResult("c", FAIL, required=True)]
    assert setup_core.has_required_failures(bad) is True
    # A non-required failure does not make the environment "not ready".
    soft = [CheckResult("d", FAIL, required=False)]
    assert setup_core.has_required_failures(soft) is False


def test_is_local_host() -> None:
    assert setup_core.is_local_host("localhost") is True
    assert setup_core.is_local_host("127.0.0.1") is True
    assert setup_core.is_local_host("") is True
    assert setup_core.is_local_host("db.example.com") is False


def test_setup_command_all_green(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_core,
        "run_all_checks",
        lambda: [CheckResult("All", OK, detail="good")],
    )
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0
    assert "Environment is ready" in result.stdout


def test_setup_command_required_failure(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        setup_core,
        "run_all_checks",
        lambda: [
            CheckResult(
                "DB_NAME",
                FAIL,
                detail="not set",
                remediation="Set DB_NAME.",
                required=True,
            )
        ],
    )
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 1
    assert "not ready" in result.stdout
    assert "Set DB_NAME." in result.stdout
