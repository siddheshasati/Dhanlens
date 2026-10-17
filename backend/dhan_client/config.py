"""Configuration and constants for DhanHQ API v2."""
import os
from dataclasses import dataclass, field

# ─── Exchange Segments ────────────────────────────────────────────────
# Segment strings used in DhanHQ API requests and scrip master lookups.
NSE_EQ = "NSE_EQ"       # NSE equity (cash)
NSE_FNO = "NSE_FNO"     # NSE F&O
BSE_EQ = "BSE_EQ"       # BSE equity
BSE_FNO = "BSE_FNO"     # BSE F&O
IDX_I = "IDX_I"         # Index instrument segment
MCX_COMM = "MCX_COMM"   # MCX commodity

# Segment integer codes used by the WebSocket binary feed.
WS_SEGMENT_CODES = {
    NSE_EQ: 1, NSE_FNO: 2, BSE_EQ: 3, BSE_FNO: 4, MCX_COMM: 5, IDX_I: 0,
}

# ─── Well-Known Index Security IDs ────────────────────────────────────
INDEX_SECURITY_IDS = {
    "NIFTY": 13,
    "BANKNIFTY": 25,
    "FINNIFTY": 27,
    "MIDCPNIFTY": 442,
    "SENSEX": 51,
    "BANKEX": 52,
}

# ─── API URLs ─────────────────────────────────────────────────────────
BASE_URL = "https://api.dhan.co/v2"
WS_URL = "wss://api-feed.dhan.co"
WS_ORDER_UPDATE_URL = "wss://api-order-update.dhan.co"
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_MASTER_DETAILED_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

# ─── Rate Limits ──────────────────────────────────────────────────────
OPTION_CHAIN_INTERVAL_S = 3.0       # Min seconds between option chain requests
WS_MAX_INSTRUMENTS_PER_SUB = 100    # Max instruments per single subscribe packet
WS_MAX_INSTRUMENTS_TOTAL = 5000     # Max instruments per WebSocket connection
WS_MAX_CONNECTIONS = 5              # Max concurrent WebSocket connections


def _load_env_file():
    """Load .env file if environment variables aren't already exported."""
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "..", ".env"),
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
    ]
    is_pytest = "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ
    for p in candidates:
        abs_p = os.path.abspath(p)
        if os.path.isfile(abs_p):
            try:
                with open(abs_p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            if is_pytest and k == "DHAN_MODE" and "FORCE_DHAN_LIVE" not in os.environ:
                                continue
                            if k and k not in os.environ:
                                os.environ[k] = v
            except Exception:
                pass
            break


import sys
_load_env_file()


@dataclass
class DhanConfig:
    """Runtime configuration, populated from environment variables."""

    client_id: str = field(default_factory=lambda: os.getenv("DHAN_CLIENT_ID", ""))
    access_token: str = field(default_factory=lambda: os.getenv("DHAN_ACCESS_TOKEN", ""))
    mode: str = field(default_factory=lambda: "mock" if ("pytest" in sys.modules and "FORCE_DHAN_LIVE" not in os.environ) else os.getenv("DHAN_MODE", "mock").lower())
    base_url: str = BASE_URL
    ws_url: str = WS_URL
    ws_order_url: str = WS_ORDER_UPDATE_URL
    http_timeout: float = 20.0
    ws_reconnect_max_retries: int = 10
    ws_reconnect_base_delay: float = 1.0    # seconds, doubles each retry
    ws_reconnect_max_delay: float = 60.0    # cap on backoff

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def is_mock(self) -> bool:
        return self.mode == "mock"

    def validate(self):
        """Raise if live mode is requested but credentials are missing."""
        if self.is_live and (not self.client_id or not self.access_token):
            raise ValueError(
                "DHAN_MODE=live requires DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN env vars. "
                "Set DHAN_MODE=mock to run without credentials."
            )
