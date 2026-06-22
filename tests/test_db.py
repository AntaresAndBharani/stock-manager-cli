import pytest
from typer.testing import CliRunner

from tradingtools_stock.main import app


class _FakeCursor:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, *args):
        self._captured.setdefault("sql", []).append(sql)

    def fetchone(self):
        # Pretend the database does not exist yet so CREATE DATABASE runs.
        return None


class _FakeConn:
    def __init__(self, captured: dict) -> None:
        self._captured = captured

    def set_isolation_level(self, level):
        pass

    def cursor(self):
        return _FakeCursor(self._captured)

    def close(self):
        pass


def _patch_db(monkeypatch: pytest.MonkeyPatch, captured: dict) -> None:
    def fake_connect(**kwargs):
        captured["connect_kwargs"] = kwargs
        return _FakeConn(captured)

    monkeypatch.setattr("tradingtools_stock.commands.db.psycopg2.connect", fake_connect)
    monkeypatch.setattr(
        "tradingtools_stock.commands.db.get_db_connection",
        lambda: _FakeConn(captured),
    )
    monkeypatch.setattr(
        "tradingtools_stock.commands.db.create_tables_if_not_exist",
        lambda conn: captured.setdefault("created_tables", True),
    )


def test_db_setup_default_name_is_not_template_boilerplate(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    for var in ("DB_NAME", "DB_USER", "DB_PASS", "DB_HOST", "DB_PORT"):
        monkeypatch.delenv(var, raising=False)
    captured: dict = {}
    _patch_db(monkeypatch, captured)

    result = runner.invoke(app, ["db", "setup"])
    assert result.exit_code == 0
    assert "youtube_db" not in result.stdout
    assert "stockmanager" in result.stdout
    assert captured.get("created_tables") is True


def test_db_setup_uses_db_name_env_var(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DB_NAME", "fractanomicsdb")
    captured: dict = {}
    _patch_db(monkeypatch, captured)

    result = runner.invoke(app, ["db", "setup"])
    assert result.exit_code == 0
    assert "fractanomicsdb" in result.stdout
