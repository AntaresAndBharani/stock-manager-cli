"""
Core configuration and constants for the CLI app.
"""

# Re-export the package version as the single source of truth so the CLI
# --version flag always matches the published (hatch dynamic) version.
from tradingtools_stock import __version__

__all__ = ["__version__"]
