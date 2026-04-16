import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    """Provides a Typer CLI Runner for testing."""
    return CliRunner()
