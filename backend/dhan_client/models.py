"""Pydantic models for normalized DhanHQ data.

Both the live REST client and the mock client return instances of these models,
giving the rest of the application a single stable interface regardless of data source.
"""
from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


# ─── Option Chain ─────────────────────────────────────────────────────

class OptionStrike(BaseModel):
    """One strike row within an option chain snapshot."""
    strike: int
    opt_type: str                                   # "CE" or "PE"
    ltp: float = 0.0                                # last traded price
    iv: float = 0.0                                 # implied volatility (decimal, e.g. 0.14)
    oi: int = 0                                     # open interest
    volume: int = 0
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    security_id: int | None = None                  # DhanHQ security ID for this contract


class OptionChainSnapshot(BaseModel):
    """Complete option chain at a single timestamp."""
    ts: datetime
    underlying: str                                 # "NIFTY" | "BANKNIFTY"
    spot: float
    expiry: str                                     # "YYYY-MM-DD"
    strikes: list[OptionStrike] = Field(default_factory=list)


# ─── Candles / OHLC ──────────────────────────────────────────────────

class Candle(BaseModel):
    """Single OHLCV candle."""
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    oi: int | None = None                           # OI included in intraday candles


class CandleResponse(BaseModel):
    """Collection of candles for one instrument."""
    underlying: str
    security_id: int | None = None
    candles: list[Candle] = Field(default_factory=list)


# ─── Positions & Holdings ────────────────────────────────────────────

class Position(BaseModel):
    """An open F&O position."""
    underlying: str
    strike: int
    opt_type: str                                   # "CE" | "PE"
    qty: int                                        # signed: + long, - short
    avg_price: float
    ltp: float = 0.0
    pnl: float = 0.0
    security_id: int | None = None
    exchange_segment: str = "NSE_FNO"


class PositionsResponse(BaseModel):
    """All open positions."""
    positions: list[Position] = Field(default_factory=list)


# ─── Orders ──────────────────────────────────────────────────────────

class OrderProposal(BaseModel):
    """An order that has been proposed (paper or real) but not yet executed."""
    underlying: str
    strike: int
    opt_type: str
    side: str                                       # "BUY" | "SELL"
    qty: int
    price: float
    security_id: int | None = None
    exchange_segment: str = "NSE_FNO"
    order_type: str = "LIMIT"
    product_type: str = "INTRA"                     # INTRA | CNC | MARGIN


class OrderResponse(BaseModel):
    """Response after placing or confirming an order."""
    order_id: str = ""
    status: str = ""                                # "PENDING" | "TRADED" | "REJECTED" | "PAPER_FILL"
    message: str = ""


# ─── Live Feed Tick ──────────────────────────────────────────────────

class Tick(BaseModel):
    """A single real-time market tick from the WebSocket feed."""
    security_id: int
    exchange_segment: str = ""
    ltp: float = 0.0
    volume: int = 0
    oi: int = 0
    bid: float = 0.0
    ask: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    close: float = 0.0                              # previous close
    ts: datetime | None = None


# ─── Scrip Master Entry ─────────────────────────────────────────────

class ScripEntry(BaseModel):
    """One row from the Dhan scrip master CSV."""
    security_id: int
    exchange_segment: str
    symbol: str = ""
    trading_symbol: str = ""
    underlying: str = ""
    strike: float = 0.0
    opt_type: str = ""                              # "CE" | "PE" | "" for futures/equity
    expiry: str = ""                                # "YYYY-MM-DD"
    lot_size: int = 1
    tick_size: float = 0.05


# ─── Full DhanHQ Orders API Models ───────────────────────────────────

class OrderRequest(BaseModel):
    """Standard order placement request."""
    dhan_client_id: str = ""
    security_id: str | int
    exchange_segment: str = "NSE_EQ"
    transaction_type: str = "BUY"                   # BUY | SELL
    order_type: str = "LIMIT"                       # LIMIT | MARKET | STOP_LOSS | STOP_LOSS_MARKET
    product_type: str = "CNC"                       # CNC | INTRADAY | MARGIN | MTF | CO | BO
    quantity: int = 1
    price: float = 0.0
    trigger_price: float = 0.0
    validity: str = "DAY"                           # DAY | IOC
    disclosed_quantity: int = 0
    after_market_order: bool = False
    correlation_id: str = ""


class OrderModifyRequest(BaseModel):
    """Modify a pending order."""
    dhan_client_id: str = ""
    order_id: str
    order_type: str = "LIMIT"
    quantity: int = 1
    price: float = 0.0
    trigger_price: float = 0.0
    validity: str = "DAY"
    disclosed_quantity: int = 0


class OrderSliceRequest(BaseModel):
    """Slice a large order into multiple sub-orders."""
    dhan_client_id: str = ""
    security_id: str | int
    exchange_segment: str = "NSE_EQ"
    transaction_type: str = "BUY"
    order_type: str = "LIMIT"
    product_type: str = "CNC"
    quantity: int = 1
    price: float = 0.0


class OrderRecord(BaseModel):
    """Order status item from DhanHQ order book."""
    order_id: str = ""
    dhan_client_id: str = ""
    order_status: str = ""                          # PENDING, TRADED, REJECTED, CANCELLED
    transaction_type: str = ""                      # BUY | SELL
    exchange_segment: str = ""
    product_type: str = ""
    order_type: str = ""
    validity: str = ""
    trading_symbol: str = ""
    security_id: str = ""
    quantity: int = 0
    disclosed_quantity: int = 0
    price: float = 0.0
    trigger_price: float = 0.0
    after_market_order: bool = False
    filled_qty: int = 0
    pending_qty: int = 0
    avg_traded_price: float = 0.0
    exchange_order_id: str = ""
    update_time: str = ""
    order_timestamp: str = ""
    remarks: str = ""


# ─── Conditional & Multi Order API (Forever / GTT) ────────────────────

class ForeverOrderRequest(BaseModel):
    """Forever Order (GTT / OCO) placement request."""
    dhan_client_id: str = ""
    order_flag: str = "SINGLE"                      # SINGLE | OCO
    transaction_type: str = "BUY"
    exchange_segment: str = "NSE_EQ"
    product_type: str = "CNC"
    security_id: str | int
    quantity: int
    price: float
    trigger_price: float
    price1: float = 0.0                             # Leg 2 for OCO
    trigger_price1: float = 0.0                     # Leg 2 trigger for OCO
    quantity1: int = 0


class ForeverOrderRecord(BaseModel):
    """Forever Order details."""
    order_id: str = ""
    order_flag: str = "SINGLE"
    order_status: str = ""
    transaction_type: str = ""
    exchange_segment: str = ""
    product_type: str = ""
    security_id: str = ""
    quantity: int = 0
    price: float = 0.0
    trigger_price: float = 0.0
    price1: float = 0.0
    trigger_price1: float = 0.0
    quantity1: int = 0
    create_time: str = ""


# ─── Super Order API (Bracket Order) ──────────────────────────────────

class SuperOrderRequest(BaseModel):
    """Super Order (Bracket Order) with Entry, Target, Stop-Loss, and Trailing SL."""
    dhan_client_id: str = ""
    security_id: str | int
    exchange_segment: str = "NSE_EQ"
    transaction_type: str = "BUY"
    order_type: str = "LIMIT"
    product_type: str = "BO"                        # Bracket Order
    quantity: int = 1
    price: float = 0.0
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    trailing_stop_loss: float = 0.0


class SuperOrderRecord(BaseModel):
    """Super order representation."""
    order_id: str = ""
    parent_order_id: str = ""
    order_status: str = ""
    leg_name: str = ""                              # ENTRY_LEG | TARGET_LEG | STOP_LOSS_LEG
    security_id: str = ""
    transaction_type: str = ""
    exchange_segment: str = ""
    product_type: str = ""
    quantity: int = 0
    price: float = 0.0
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    trailing_stop_loss: float = 0.0


# ─── Portfolio & Holdings ─────────────────────────────────────────────

class Holding(BaseModel):
    """Demat Holding row."""
    security_id: str = ""
    trading_symbol: str = ""
    exchange: str = "NSE"
    isin: str = ""
    total_qty: int = 0
    dp_qty: int = 0
    t1_qty: int = 0
    available_qty: int = 0
    avg_cost_price: float = 0.0
    ltp: float = 0.0
    current_val: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0


class HoldingsResponse(BaseModel):
    """Holdings summary."""
    holdings: list[Holding] = Field(default_factory=list)
    total_investment: float = 0.0
    total_current_val: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0


class PositionConvertRequest(BaseModel):
    """Convert position product type."""
    dhan_client_id: str = ""
    from_product_type: str = "INTRADAY"             # INTRADAY | CNC | MARGIN
    to_product_type: str = "CNC"
    exchange_segment: str = "NSE_EQ"
    security_id: str | int
    trading_symbol: str = ""
    convert_qty: int = 1


# ─── US Stocks API ────────────────────────────────────────────────────

class GlobalHolding(BaseModel):
    """Global (US) Stock Holding."""
    security_id: str = ""
    symbol: str = ""
    company_name: str = ""
    quantity: float = 0.0
    avg_price: float = 0.0
    ltp: float = 0.0
    total_value: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0


class GlobalStocksResponse(BaseModel):
    """Global/US stocks portfolio response."""
    holdings: list[GlobalHolding] = Field(default_factory=list)
    total_value_usd: float = 0.0
    total_pnl_usd: float = 0.0
    account_status: str = "ACTIVE"
    message: str = ""


# ─── Funds & Margin API ───────────────────────────────────────────────

class FundLimit(BaseModel):
    """Funds and margin limits from /fundlimit."""
    dhan_client_id: str = ""
    available_balance: float = 0.0
    sod_limit: float = 0.0
    collateral_amount: float = 0.0
    receiveable_amount: float = 0.0
    utilized_amount: float = 0.0
    blocked_payout_amount: float = 0.0
    withdrawable_balance: float = 0.0

    def __getitem__(self, item: str):
        mapping = {
            "availableBalance": self.available_balance,
            "sodLimit": self.sod_limit,
            "collateralAmount": self.collateral_amount,
            "receiveableAmount": self.receiveable_amount,
            "utilizedAmount": self.utilized_amount,
            "blockedPayoutAmount": self.blocked_payout_amount,
            "withdrawableBalance": self.withdrawable_balance,
            "dhanClientId": self.dhan_client_id,
        }
        if item in mapping:
            return mapping[item]
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(item)



class MarginCalculationItem(BaseModel):
    """Single item for margin calculation."""
    exchange_segment: str = "NSE_EQ"
    transaction_type: str = "BUY"                   # BUY | SELL
    quantity: int = 1
    product_type: str = "CNC"                       # CNC | INTRADAY | MARGIN
    security_id: str | int = ""
    price: float = 0.0
    trigger_price: float = 0.0


class MarginCalculationResponse(BaseModel):
    """Margin calculation breakdown."""
    total_margin: float = 0.0
    span_margin: float = 0.0
    exposure_margin: float = 0.0
    variable_margin: float = 0.0
    available_balance: float = 0.0
    insufficient_balance: float = 0.0
    leverage: str = ""


# ─── Past Trades API ──────────────────────────────────────────────────

class TradeRecord(BaseModel):
    """An executed trade item."""
    trade_id: str = ""
    order_id: str = ""
    exchange_segment: str = ""
    transaction_type: str = ""
    exchange_time: str = ""
    trading_symbol: str = ""
    security_id: str = ""
    traded_price: float = 0.0
    traded_quantity: int = 0
    create_time: str = ""


# ─── Kill Switch API ──────────────────────────────────────────────────

class KillSwitchStatus(BaseModel):
    """DhanHQ Kill Switch status."""
    dhan_client_id: str = ""
    kill_switch_status: str = ""                    # ACTIVATE | DEACTIVATE | ""
    message: str = ""


# ─── P&L Exit API ─────────────────────────────────────────────────────

class PnlExitConfig(BaseModel):
    """P&L based exit configuration."""
    status: str = ""                                # ACTIVE | INACTIVE | SUCCESS
    profit_threshold: float = 0.0
    loss_threshold: float = 0.0
    exit_time: str = ""
    is_enabled: bool = False
    message: str = ""


# ─── Ledger API ───────────────────────────────────────────────────────

class LedgerEntry(BaseModel):
    """A single ledger accounting voucher."""
    dhan_client_id: str = ""
    narration: str = ""
    voucher_date: str = ""
    exchange: str = ""
    voucher_desc: str = ""
    voucher_amount: float = 0.0
    dr_cr: str = ""                                 # DR | CR
    running_balance: float = 0.0


class LedgerResponse(BaseModel):
    """Full ledger report."""
    from_date: str = ""
    to_date: str = ""
    entries: list[LedgerEntry] = Field(default_factory=list)
    total_debit: float = 0.0
    total_credit: float = 0.0
    closing_balance: float = 0.0


# ─── EDIS ─────────────────────────────────────────────────────────────

class EdisTpinResponse(BaseModel):
    """TPIN request response."""
    status: str = ""
    message: str = ""


class EdisFormRequest(BaseModel):
    """Request for EDIS form authorization."""
    isin: str = ""
    qty: int = 0
    exchange: str = "NSE"
    segment: str = "EQ"
    bulk: bool = True


class EdisFormResponse(BaseModel):
    """HTML form payload returned for CDSL redirect."""
    form_html: str = ""
    edis_partner_id: str = ""
    status: str = ""


class EdisStatusResponse(BaseModel):
    """Status inquiry for ISIN holding authorization."""
    isin: str = ""
    status: str = ""
    authorized_qty: int = 0
    remaining_qty: int = 0


# ─── Live Order Update & Postback ────────────────────────────────────

class LiveOrderUpdate(BaseModel):
    """Event pushed from live WebSocket order feed or postback webhook."""
    order_id: str = ""
    dhan_client_id: str = ""
    order_status: str = ""
    transaction_type: str = ""
    exchange_segment: str = ""
    trading_symbol: str = ""
    security_id: str = ""
    price: float = 0.0
    quantity: int = 0
    filled_qty: int = 0
    pending_qty: int = 0
    avg_price: float = 0.0
    update_time: str = ""
    source: str = "WEBSOCKET"                       # WEBSOCKET | POSTBACK
    raw: dict = Field(default_factory=dict)


# ─── Static IP API Models ────────────────────────────────────────────

class IpConfigRequest(BaseModel):
    """Static IP registration request."""
    ip: str
    ip_flag: str = "PRIMARY"                         # PRIMARY | SECONDARY


class IpConfigResponse(BaseModel):
    """Static IP response details."""
    dhan_client_id: str = ""
    ip: str = ""
    ip_flag: str = "PRIMARY"
    status: str = "SUCCESS"
    message: str = ""
    current_public_ip: str = ""
    lockin_date: str = ""

