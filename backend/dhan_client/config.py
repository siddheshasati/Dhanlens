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
SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_MASTER_DETAILED_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

# ─── Rate Limits ──────────────────────────────────────────────────────
OPTION_CHAIN_INTERVAL_S = 3.0       # Min seconds between option chain requests
WS_MAX_INSTRUMENTS_PER_SUB = 100    # Max instruments per single subscribe packet
WS_MAX_INSTRUMENTS_TOTAL = 5000     # Max instruments per WebSocket connection
WS_MAX_CONNECTIONS = 5              # Max concurrent WebSocket connections


@dataclass
class DhanConfig:
    """Runtime configuration, populated from environment variables."""

    client_id: str = field(default_factory=lambda: os.getenv("DHAN_CLIENT_ID", ""))
    access_token: str = field(default_factory=lambda: os.getenv("DHAN_ACCESS_TOKEN", ""))
    mode: str = field(default_factory=lambda: os.getenv("DHAN_MODE", "mock").lower())
    base_url: str = BASE_URL
    ws_url: str = WS_URL
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
