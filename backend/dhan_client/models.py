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
