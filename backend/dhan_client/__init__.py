"""DhanLens API client package.

Provides a unified interface to DhanHQ market data and full trading suite,
with a seamless mock mode for demos and automated testing.

Modules covered:
- Option Chain & Candles
- Orders API (Place, Modify, Cancel, Order Book, Slice)
- Conditional & Multi Order API (Forever Orders / GTT / OCO)
- Super Order API (Bracket Orders)
- Live Order Update (WebSocket feed)
- P&L Exit API (Config, Square-off)
- Portfolio & Positions API (Holdings, Day Positions, Product Conversion)
- US Stocks API (Global Holdings & Orders)
- Funds & Margin API (Fund limit, Margin Calculator)
- Past Trades API (Trade book, Trades by order, History)
- Kill Switch API (Emergency trading lock)
- Ledger API (Statements, Debits, Credits)
- Postback Webhook & EDIS (TPIN, CDSL form, ISIN inquiry)
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
    OrderRequest,
    OrderModifyRequest,
    OrderSliceRequest,
    OrderRecord,
    ForeverOrderRequest,
    ForeverOrderRecord,
    SuperOrderRequest,
    SuperOrderRecord,
    Holding,
    HoldingsResponse,
    PositionConvertRequest,
    GlobalHolding,
    GlobalStocksResponse,
    FundLimit,
    MarginCalculationItem,
    MarginCalculationResponse,
    TradeRecord,
    KillSwitchStatus,
    PnlExitConfig,
    LedgerEntry,
    LedgerResponse,
    EdisTpinResponse,
    EdisFormRequest,
    EdisFormResponse,
    EdisStatusResponse,
    LiveOrderUpdate,
    IpConfigRequest,
    IpConfigResponse,
)
from .mock import MockClient
from .rest import DhanRestClient
from .scrip_master import ScripMaster
from .websocket_feed import DhanWebSocketFeed, MODE_LTP, MODE_QUOTE, MODE_FULL
from .order_feed import DhanOrderUpdateFeed

log = logging.getLogger(__name__)

AnyClient = Union[DhanRestClient, MockClient]


class DhanClient:
    """Unified façade that wraps REST, WebSockets, and scrip master."""

    def __init__(self, config: DhanConfig | None = None):
        self.config = config or DhanConfig()
        self.config.validate()

        if self.config.is_mock:
            self._rest: AnyClient = MockClient()
            self._scrip: ScripMaster | None = None
            self._feed: DhanWebSocketFeed | None = None
            self._order_feed: DhanOrderUpdateFeed | None = None
            log.info("DhanClient initialized in MOCK mode")
        else:
            self._rest = DhanRestClient(self.config)
            self._scrip = ScripMaster(self.config)
            self._feed = None
            self._order_feed = None
            log.info("DhanClient initialized in LIVE mode (Client ID: %s)", self.config.client_id)

    # ── Market Data ──────────────────────────────────────────────────

    def option_chain(self, underlying: str, expiry: str = "") -> OptionChainSnapshot:
        if not expiry and self._scrip:
            expiry = self._scrip.nearest_expiry(underlying)
        return self._rest.option_chain(underlying, expiry)

    def candles_daily(self, **kw) -> CandleResponse:
        return self._rest.candles_daily(**kw)

    def candles_intraday(self, **kw) -> CandleResponse:
        return self._rest.candles_intraday(**kw)

    # ── 1. Orders API ────────────────────────────────────────────────

    def place_order(self, proposal: OrderProposal) -> OrderResponse:
        return self._rest.place_order(proposal)

    def place_order_custom(self, req: OrderRequest) -> OrderResponse:
        return self._rest.place_order_custom(req)

    def modify_order(self, req: OrderModifyRequest) -> OrderResponse:
        return self._rest.modify_order(req)

    def cancel_order(self, order_id: str) -> OrderResponse:
        return self._rest.cancel_order(order_id)

    def orders(self) -> list[OrderRecord]:
        return self._rest.orders()

    def order_by_id(self, order_id: str) -> dict:
        return self._rest.order_by_id(order_id)

    def slice_order(self, req: OrderSliceRequest) -> list[OrderResponse]:
        return self._rest.slice_order(req)

    # ── 2. Conditional & Multi Order API (Forever Orders / GTT) ──────

    def place_forever_order(self, req: ForeverOrderRequest) -> OrderResponse:
        return self._rest.place_forever_order(req)

    def modify_forever_order(self, order_id: str, req: ForeverOrderRequest) -> OrderResponse:
        return self._rest.modify_forever_order(order_id, req)

    def cancel_forever_order(self, order_id: str) -> OrderResponse:
        return self._rest.cancel_forever_order(order_id)

    def forever_orders(self) -> list[ForeverOrderRecord]:
        return self._rest.forever_orders()

    # ── 3. Super Order API (Bracket Orders) ───────────────────────────

    def place_super_order(self, req: SuperOrderRequest) -> OrderResponse:
        return self._rest.place_super_order(req)

    def modify_super_order(self, order_id: str, req: SuperOrderRequest) -> OrderResponse:
        return self._rest.modify_super_order(order_id, req)

    def cancel_super_order_leg(self, order_id: str, leg_name: str) -> OrderResponse:
        return self._rest.cancel_super_order_leg(order_id, leg_name)

    def super_orders(self) -> list[SuperOrderRecord]:
        return self._rest.super_orders()

    # ── 4. Portfolio & Positions API ─────────────────────────────────

    def positions(self) -> PositionsResponse:
        return self._rest.positions()

    def holdings(self) -> HoldingsResponse:
        return self._rest.holdings()

    def convert_position(self, req: PositionConvertRequest) -> dict:
        return self._rest.convert_position(req)

    # ── 5. US Stocks API ─────────────────────────────────────────────

    def global_stocks_holdings(self) -> GlobalStocksResponse:
        return self._rest.global_stocks_holdings()

    def global_stocks_orders(self) -> list[dict]:
        return self._rest.global_stocks_orders()

    # ── 6. Funds & Margin API ────────────────────────────────────────

    def funds(self) -> FundLimit:
        return self._rest.funds()

    def calculate_margin(self, req: MarginCalculationItem) -> MarginCalculationResponse:
        return self._rest.calculate_margin(req)

    def calculate_margin_multi(self, reqs: list[MarginCalculationItem]) -> MarginCalculationResponse:
        return self._rest.calculate_margin_multi(reqs)

    # ── 7. Past Trades API ───────────────────────────────────────────

    def trades(self) -> list[TradeRecord]:
        return self._rest.trades()

    def trades_by_order(self, order_id: str) -> list[TradeRecord]:
        return self._rest.trades_by_order(order_id)

    def historical_trades(self, from_date: str, to_date: str, page: int = 0) -> list[TradeRecord]:
        return self._rest.historical_trades(from_date, to_date, page)

    # ── 8. Kill Switch API ───────────────────────────────────────────

    def get_kill_switch(self) -> KillSwitchStatus:
        return self._rest.get_kill_switch()

    def set_kill_switch(self, status: str = "ACTIVATE") -> KillSwitchStatus:
        return self._rest.set_kill_switch(status)

    # ── 9. P&L Exit API ──────────────────────────────────────────────

    def get_pnl_exit(self) -> PnlExitConfig:
        return self._rest.get_pnl_exit()

    def set_pnl_exit(self, profit: float, loss: float) -> PnlExitConfig:
        return self._rest.set_pnl_exit(profit, loss)

    def stop_pnl_exit(self) -> PnlExitConfig:
        return self._rest.stop_pnl_exit()

    def exit_all_positions(self) -> dict:
        return self._rest.exit_all_positions()

    # ── 10. Ledger API ───────────────────────────────────────────────

    def ledger(self, from_date: str, to_date: str) -> LedgerResponse:
        return self._rest.ledger(from_date, to_date)

    # ── 11. EDIS API ─────────────────────────────────────────────────

    def generate_tpin(self) -> EdisTpinResponse:
        return self._rest.generate_tpin()

    def generate_edis_form(self, req: EdisFormRequest) -> EdisFormResponse:
        return self._rest.generate_edis_form(req)

    def inquire_edis_status(self, isin: str) -> EdisStatusResponse:
        return self._rest.inquire_edis_status(isin)

    # ── 12. Static IP API ────────────────────────────────────────────

    def get_ip(self) -> dict:
        return self._rest.get_ip()

    def set_ip(self, ip: str, ip_flag: str = "PRIMARY") -> dict:
        return self._rest.set_ip(ip, ip_flag)

    # ── Scrip Master & Feeds ─────────────────────────────────────────

    @property
    def scrip(self) -> ScripMaster | None:
        return self._scrip

    def start_feed(self, on_tick=None, on_connect=None, on_disconnect=None) -> DhanWebSocketFeed:
        if self.config.is_mock:
            raise MockModeError("WebSocket market feed is not available in mock mode.")
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

    # ── Live Order Update Feed ───────────────────────────────────────

    def start_order_feed(self, on_order_update=None, on_connect=None, on_disconnect=None) -> DhanOrderUpdateFeed:
        if self._order_feed and self._order_feed.is_running:
            return self._order_feed

        self._order_feed = DhanOrderUpdateFeed(
            self.config,
            on_order_update=on_order_update,
            on_connect=on_connect,
            on_disconnect=on_disconnect,
        )
        if self.config.is_live:
            self._order_feed.start()
        return self._order_feed

    def stop_order_feed(self) -> None:
        if self._order_feed:
            self._order_feed.stop()
            self._order_feed = None

    @property
    def order_feed(self) -> DhanOrderUpdateFeed | None:
        return self._order_feed

    @property
    def mock(self) -> MockClient | None:
        return self._rest if isinstance(self._rest, MockClient) else None

    # ── Cleanup ──────────────────────────────────────────────────────

    def close(self) -> None:
        self.stop_feed()
        self.stop_order_feed()
        if hasattr(self._rest, "close"):
            self._rest.close()


def get_client(config: DhanConfig | None = None) -> DhanClient:
    cfg = config or DhanConfig()
    return DhanClient(cfg)


__all__ = [
    "DhanClient",
    "get_client",
    "DhanConfig",
    "DhanRestClient",
    "MockClient",
    "ScripMaster",
    "DhanWebSocketFeed",
    "DhanOrderUpdateFeed",
    "MODE_LTP",
    "MODE_QUOTE",
    "MODE_FULL",
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
    "OrderRequest",
    "OrderModifyRequest",
    "OrderSliceRequest",
    "OrderRecord",
    "ForeverOrderRequest",
    "ForeverOrderRecord",
    "SuperOrderRequest",
    "SuperOrderRecord",
    "Holding",
    "HoldingsResponse",
    "PositionConvertRequest",
    "GlobalHolding",
    "GlobalStocksResponse",
    "FundLimit",
    "MarginCalculationItem",
    "MarginCalculationResponse",
    "TradeRecord",
    "KillSwitchStatus",
    "PnlExitConfig",
    "LedgerEntry",
    "LedgerResponse",
    "EdisTpinResponse",
    "EdisFormRequest",
    "EdisFormResponse",
    "EdisStatusResponse",
    "LiveOrderUpdate",
    "IpConfigRequest",
    "IpConfigResponse",
    "DhanError",
    "AuthenticationError",
    "RateLimitError",
    "InstrumentNotFoundError",
    "WebSocketError",
    "WebSocketDisconnected",
    "MockModeError",
]
