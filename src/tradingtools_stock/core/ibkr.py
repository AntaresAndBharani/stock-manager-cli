"""
IBKR connectivity: IB Gateway discovery/launch and portfolio data fetching.

Connection settings come from environment variables:
- IB_HOST (default 127.0.0.1)
- IB_PORT (default 4002 = IB Gateway paper; 4001 live, 7497 TWS paper, 7496 TWS live)
- IB_CLIENT_ID (default 11)
- IB_GATEWAY_PATH (optional explicit path to the ibgateway/tws executable)
"""

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

import pandas as pd

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002
DEFAULT_CLIENT_ID = 11

GATEWAY_DOWNLOAD_URL = (
    "https://www.interactivebrokers.com/en/trading/ibgateway-stable.php"
)

SUMMARY_TAGS = [
    "NetLiquidation",
    "TotalCashValue",
    "BuyingPower",
    "GrossPositionValue",
    "AvailableFunds",
    "ExcessLiquidity",
    "MaintMarginReq",
]


def get_ib_settings() -> tuple[str, int, int]:
    """Return (host, port, client_id) for the TWS/IB Gateway API."""
    host = os.environ.get("IB_HOST", DEFAULT_HOST)
    port = int(os.environ.get("IB_PORT", DEFAULT_PORT))
    client_id = int(os.environ.get("IB_CLIENT_ID", DEFAULT_CLIENT_ID))
    return host, port, client_id


def is_api_port_open(
    host: str | None = None, port: int | None = None, timeout: float = 1.0
) -> bool:
    """Check whether the TWS/IB Gateway API socket is accepting connections."""
    default_host, default_port, _ = get_ib_settings()
    host = host or default_host
    port = port or default_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def find_gateway_executable() -> Path | None:
    """
    Locate the IB Gateway (or TWS) executable.

    Honors IB_GATEWAY_PATH, then scans the default Windows install root
    C:\\Jts (newest version first). Returns None if nothing is found.
    """
    env_path = os.environ.get("IB_GATEWAY_PATH")
    if env_path:
        path = Path(env_path)
        return path if path.exists() else None

    jts_root = Path("C:/Jts")
    candidates: list[Path] = []
    if jts_root.exists():
        candidates.extend(jts_root.glob("ibgateway/*/ibgateway.exe"))
        candidates.extend(jts_root.glob("*/tws.exe"))
    if not candidates:
        return None
    # Version directories are numeric (e.g. 1030); prefer the newest.
    return max(candidates, key=lambda p: p.parent.name)


def launch_gateway(executable: Path) -> None:
    """Launch IB Gateway detached so it outlives the CLI process."""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _ensure_event_loop() -> None:
    """
    Make sure the current thread has an asyncio event loop.

    Streamlit executes scripts in worker threads that have no default loop,
    which ib_async requires.
    """
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def fetch_portfolio(timeout: float = 10.0) -> dict:
    """
    Connect to TWS/IB Gateway and fetch account summary and open positions.

    Returns a dict with:
    - "account": the account id
    - "summary": {tag: float} for SUMMARY_TAGS
    - "positions": DataFrame with one row per portfolio item
    """
    from ib_async import IB

    _ensure_event_loop()
    host, port, client_id = get_ib_settings()

    ib = IB()
    # readonly keeps the API session safe until order placement is built.
    ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)
    try:
        accounts = ib.managedAccounts()
        account = accounts[0] if accounts else ""

        summary: dict[str, float] = {}
        for row in ib.accountSummary(account):
            if row.tag in SUMMARY_TAGS:
                try:
                    summary[row.tag] = float(row.value)
                except ValueError:
                    continue

        rows = []
        for item in ib.portfolio():
            cost_basis = item.averageCost * item.position
            rows.append(
                {
                    "Symbol": item.contract.symbol,
                    "Type": item.contract.secType,
                    "Currency": item.contract.currency,
                    "Quantity": item.position,
                    "Avg Cost": item.averageCost,
                    "Price": item.marketPrice,
                    "Market Value": item.marketValue,
                    "Unrealized P&L": item.unrealizedPNL,
                    "Unrealized %": (
                        item.unrealizedPNL / abs(cost_basis) * 100
                        if cost_basis
                        else None
                    ),
                    "Realized P&L": item.realizedPNL,
                }
            )
        positions = pd.DataFrame(rows)
        if not positions.empty:
            net_liq = summary.get("NetLiquidation")
            if net_liq:
                positions["Weight %"] = positions["Market Value"] / net_liq * 100
            positions = positions.sort_values(
                "Market Value", ascending=False
            ).reset_index(drop=True)

        return {"account": account, "summary": summary, "positions": positions}
    finally:
        ib.disconnect()
