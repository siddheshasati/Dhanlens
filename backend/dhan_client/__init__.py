"""DhanLens API client package.

Provides a unified interface to DhanHQ market data, with a mock mode for demos.

Quick start::

    from dhan_client import get_client

    client = get_client()           # auto-detects DHAN_MODE env var (default: "mock")
    snap = client.option_chain("NIFTY", "2026-10-20")

Set DHAN_MODE=live (with DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN) for real market data.
"""
from __future__ import annotations

import logging
from typing import Union

from .config import DhanConfig
from .exceptions import (
    AuthenticationError,
    DhanError,
    InstrumentNotFoundError,
    MockModeError,
    RateLimitError,
    WebSocketDisconnected,
    WebSocketError,
)
from .models import (
    Candle,
    CandleResponse,
    OptionChainSnapshot,
    OptionStrike,
    OrderProposal,
    OrderResponse,
    Position,
    PositionsResponse,
    ScripEntry,
    Tick,
)
from .mock import MockClient
from .rest import DhanRestClient
from .scrip_master import ScripMaster
from .websocket_feed import DhanWebSocketFeed, MODE_LTP, MODE_QUOTE, MODE_FULL

log = logging.getLogger(__name__)

# Type alias for either client
AnyClient = Union[DhanRestClient, MockClient]


class DhanClient:
    """Unified façade that wraps REST, WebSocket, and scrip master.

    Use get_client() instead of constructing this directly — it handles
    mock vs. live mode selection automatically.
    """

    def __init__(self, config: DhanConfig | None = None):
        self.config = config or DhanConfig()
        self.config.validate()

        if self.config.is_mock:
            self._rest: AnyClient = MockClient()
            self._scrip: ScripMaster | None = None
            self._feed: DhanWebSocketFeed | None = None
            log.info("DhanClient initialized in MOCK mode")
        else:
            self._rest = DhanRestClient(self.config)
            self._scrip = ScripMaster(self.config)
            self._feed = None  # Created lazily via start_feed()
            log.info("DhanClient initialized in LIVE mode")

    # ── Delegated API methods ────────────────────────────────────────

    def option_chain(self, underlying: str, expiry: str = "") -> OptionChainSnapshot:
        """Fetch option chain snapshot (live or mock)."""
        if not expiry and self._scrip:
            expiry = self._scrip.nearest_expiry(underlying)
        return self._rest.option_chain(underlying, expiry)

    def candles_daily(self, **kw) -> CandleResponse:
        return self._rest.candles_daily(**kw)

    def candles_intraday(self, **kw) -> CandleResponse:
        return self._rest.candles_intraday(**kw)

    def positions(self) -> PositionsResponse:
        return self._rest.positions()

    def place_order(self, proposal: OrderProposal) -> OrderResponse:
        return self._rest.place_order(proposal)

    def funds(self) -> dict:
        return self._rest.funds()

    # ── Scrip master ─────────────────────────────────────────────────

    @property
    def scrip(self) -> ScripMaster | None:
        return self._scrip

    # ── WebSocket feed ───────────────────────────────────────────────

    def start_feed(self, on_tick=None, on_connect=None, on_disconnect=None) -> DhanWebSocketFeed:
        """Start the live market feed (live mode only).

        Raises MockModeError in mock mode.
        """
        if self.config.is_mock:
            raise MockModeError("WebSocket feed is not available in mock mode.")
        self._feed = DhanWebSocketFeed(
            self.config,
            on_tick=on_tick,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
        )
        self._feed.start()
        return self._feed

    def stop_feed(self) -> None:
        if self._feed:
            self._feed.stop()
            self._feed = None

    @property
    def feed(self) -> DhanWebSocketFeed | None:
        return self._feed

    # ── Mock-specific helpers ────────────────────────────────────────

    @property
    def mock(self) -> MockClient | None:
        """Access MockClient-specific features (time control, blotter). Returns None in live mode."""
        return self._rest if isinstance(self._rest, MockClient) else None

    # ── Cleanup ──────────────────────────────────────────────────────

    def close(self) -> None:
        self.stop_feed()
        if hasattr(self._rest, "close"):
            self._rest.close()


def get_client(config: DhanConfig | None = None) -> DhanClient:
    """Factory: create a DhanClient based on the DHAN_MODE env var.

    - DHAN_MODE=mock (default): Uses local DuckDB data, no API keys needed.
    - DHAN_MODE=live: Requires DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN.

    Returns:
        A fully initialized DhanClient.
    """
    cfg = config or DhanConfig()
    return DhanClient(cfg)


__all__ = [
    # Main entry points
    "DhanClient",
    "get_client",
    "DhanConfig",
    # Sub-components
    "DhanRestClient",
    "MockClient",
    "ScripMaster",
    "DhanWebSocketFeed",
    "MODE_LTP",
    "MODE_QUOTE",
    "MODE_FULL",
    # Models
    "OptionChainSnapshot",
    "OptionStrike",
    "Candle",
    "CandleResponse",
    "Position",
    "PositionsResponse",
    "OrderProposal",
    "OrderResponse",
    "ScripEntry",
    "Tick",
    # Exceptions
    "DhanError",
    "AuthenticationError",
    "RateLimitError",
    "InstrumentNotFoundError",
    "WebSocketError",
    "WebSocketDisconnected",
    "MockModeError",
]
