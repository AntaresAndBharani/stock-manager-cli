from typer.testing import CliRunner

from tradingtools_stock.core.config import __version__
from tradingtools_stock.main import app


def test_version_flag(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_example_hello(runner: CliRunner) -> None:
    result = runner.invoke(app, ["example", "hello", "World"])
    assert result.exit_code == 0
    assert "Hello, World!" in result.stdout


def test_example_info(runner: CliRunner) -> None:
    result = runner.invoke(app, ["example", "info"])
    assert result.exit_code == 0
    assert "Ready!" in result.stdout
