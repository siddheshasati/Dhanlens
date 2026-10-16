"""Mock client that mirrors the DhanClient interface using locally generated data.

Returns the same Pydantic models as the live client, powered by generate.py +
the gold DuckDB layer. No network calls, no API keys needed.

This is what runs during demos and development (DHAN_MODE=mock).
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime

import duckdb
import pandas as pd

# Ensure backend/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from .models import (
    Candle, CandleResponse, OptionChainSnapshot, OptionStrike,
    OrderProposal, OrderResponse, Position, PositionsResponse, Tick,
)

log = logging.getLogger(__name__)

# Path to the gold DuckDB (created by pipeline.py)
_GOLD_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold", "dhanlens.duckdb"))


class MockClient:
    """Mock implementation of the Dhan client for offline/demo usage.

    Reads from the gold DuckDB created by pipeline.py. If the DB doesn't exist,
    it runs the pipeline to generate synthetic data first.

    All methods have the same signatures and return types as DhanRestClient,
    making the mock a drop-in replacement.
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _GOLD_DB
        self._con: duckdb.DuckDBPyConnection | None = None
        self._blotter: list[dict] = []  # paper fills

        # Ensure the gold DB exists
        if not os.path.exists(self._db_path):
            log.info("Gold DB not found at %s — running pipeline...", self._db_path)
            import pipeline
            pipeline.run_all()

        self._con = duckdb.connect(self._db_path, read_only=True)
        self._timestamps = self._load_timestamps()
        self._current_idx = len(self._timestamps) - 1  # start at latest
        log.info("MockClient ready: %d timestamps, DB at %s", len(self._timestamps), self._db_path)

    def close(self):
        if self._con:
            self._con.close()

    # ── Time control (for live replay) ───────────────────────────────

    @property
    def current_ts(self) -> datetime:
        return self._timestamps[self._current_idx]

    def set_time(self, ts: datetime | str | None = None, idx: int | None = None) -> datetime:
        """Advance the mock clock to a specific timestamp or index."""
        if idx is not None:
            self._current_idx = max(0, min(idx, len(self._timestamps) - 1))
        elif ts is not None:
            target = pd.Timestamp(ts).to_pydatetime() if isinstance(ts, str) else ts
            # Find nearest timestamp
            self._current_idx = min(
                range(len(self._timestamps)),
                key=lambda i: abs((self._timestamps[i] - target).total_seconds()),
            )
        return self.current_ts

    def advance(self, steps: int = 1) -> datetime:
        """Move forward by N timestamps (for replay)."""
        self._current_idx = min(self._current_idx + steps, len(self._timestamps) - 1)
        return self.current_ts

    @property
    def total_timestamps(self) -> int:
        return len(self._timestamps)

    # ── Option Chain ─────────────────────────────────────────────────

    def option_chain(self, underlying: str, expiry: str = "") -> OptionChainSnapshot:
        """Return a snapshot of the option chain at the current mock timestamp."""
        u = underlying.upper()
        ts = self.current_ts

        df = self._con.execute(
            "SELECT strike, opt_type, ltp, iv, oi, volume, spot "
            "FROM chain WHERE underlying = ? AND ts = ?",
            [u, ts],
        ).df()

        if df.empty:
            log.warning("No chain data for %s at %s", u, ts)
            return OptionChainSnapshot(ts=ts, underlying=u, spot=0.0, expiry=expiry, strikes=[])

        spot = float(df.spot.iloc[0])
        strikes = [
            OptionStrike(
                strike=int(r.strike),
                opt_type=r.opt_type,
                ltp=float(r.ltp),
                iv=float(r.iv),
                oi=int(r.oi),
                volume=int(r.volume),
            )
            for r in df.itertuples()
        ]

        return OptionChainSnapshot(ts=ts, underlying=u, spot=spot, expiry=expiry, strikes=strikes)

    # ── Candles ──────────────────────────────────────────────────────

    def candles_daily(self, security_id: int = 0, exchange_segment: str = "",
                      from_date: str = "", to_date: str = "",
                      underlying: str = "NIFTY") -> CandleResponse:
        """Return daily OHLCV from mock data."""
        return self._get_candles(underlying, from_date, to_date)

    def candles_intraday(self, security_id: int = 0, exchange_segment: str = "",
                         from_date: str = "", to_date: str = "",
                         underlying: str = "NIFTY") -> CandleResponse:
        """Return minute-level OHLCV from mock data."""
        return self._get_candles(underlying, from_date, to_date)

    # ── Positions ────────────────────────────────────────────────────

    def positions(self) -> PositionsResponse:
        """Return mock positions from the positions table."""
        df = self._con.execute("SELECT * FROM positions").df()
        positions = [
            Position(
                underlying=r.underlying,
                strike=int(r.strike),
                opt_type=r.opt_type,
                qty=int(r.qty),
                avg_price=float(r.avg_price),
            )
            for r in df.itertuples()
        ]
        return PositionsResponse(positions=positions)

    # ── Orders (paper only) ──────────────────────────────────────────

    def place_order(self, proposal: OrderProposal) -> OrderResponse:
        """Simulate a paper fill."""
        fill = {
            "underlying": proposal.underlying,
            "strike": proposal.strike,
            "type": proposal.opt_type,
            "side": proposal.side,
            "qty": proposal.qty,
            "price": proposal.price,
            "filled_at": datetime.now().isoformat(),
        }
        self._blotter.append(fill)
        return OrderResponse(
            order_id=uuid.uuid4().hex[:8],
            status="PAPER_FILL",
            message=f"PAPER fill: {proposal.side} {proposal.qty} {proposal.underlying} "
                    f"{proposal.strike}{proposal.opt_type} @ {proposal.price:.2f}",
        )

    # ── Funds (mock) ─────────────────────────────────────────────────

    def funds(self) -> dict:
        """Return mock fund limits."""
        return {
            "availableBalance": 500000.0,
            "utilizedMargin": 125000.0,
            "collateralValue": 0.0,
        }

    # ── Blotter ──────────────────────────────────────────────────────

    @property
    def blotter(self) -> list[dict]:
        """All paper fills during this session."""
        return list(self._blotter)

    # ── Internal ─────────────────────────────────────────────────────

    def _load_timestamps(self) -> list[datetime]:
        """Load all unique timestamps from the candles table (NIFTY)."""
        rows = self._con.execute(
            "SELECT DISTINCT ts FROM candles WHERE underlying = 'NIFTY' ORDER BY ts"
        ).fetchall()
        return [r[0] for r in rows]

    def _get_candles(self, underlying: str, from_date: str = "", to_date: str = "") -> CandleResponse:
        """Query candles from mock DuckDB."""
        u = underlying.upper()
        ts = self.current_ts

        query = "SELECT ts, open, high, low, close, volume FROM candles WHERE underlying = ?"
        params: list = [u]

        if from_date and to_date:
            query += " AND ts::date >= ? AND ts::date <= ?"
            params.extend([from_date, to_date])
        else:
            query += " AND ts <= ?"
            params.append(ts)

        query += " ORDER BY ts"
        df = self._con.execute(query, params).df()

        candles = [
            Candle(
                ts=r.ts,
                open=float(getattr(r, 'open')),
                high=float(r.high),
                low=float(r.low),
                close=float(r.close),
                volume=int(r.volume),
            )
            for r in df.itertuples()
        ]
        return CandleResponse(underlying=u, candles=candles)
