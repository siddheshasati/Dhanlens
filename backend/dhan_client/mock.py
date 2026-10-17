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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from .models import (
    Candle, CandleResponse, OptionChainSnapshot, OptionStrike,
    OrderProposal, OrderResponse, Position, PositionsResponse, Tick,
    OrderRequest, OrderModifyRequest, OrderSliceRequest, OrderRecord,
    ForeverOrderRequest, ForeverOrderRecord,
    SuperOrderRequest, SuperOrderRecord,
    Holding, HoldingsResponse, PositionConvertRequest,
    GlobalHolding, GlobalStocksResponse,
    FundLimit, MarginCalculationItem, MarginCalculationResponse,
    TradeRecord, KillSwitchStatus, PnlExitConfig,
    LedgerEntry, LedgerResponse,
    EdisTpinResponse, EdisFormRequest, EdisFormResponse, EdisStatusResponse,
)

log = logging.getLogger(__name__)

_GOLD_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "gold", "dhanlens.duckdb"))


class MockClient:
    """Mock implementation of the Dhan client for offline/demo usage."""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or _GOLD_DB
        self._con: duckdb.DuckDBPyConnection | None = None
        self._blotter: list[dict] = []
        self._orders: list[OrderRecord] = []
        self._forever_orders: list[ForeverOrderRecord] = []
        self._super_orders: list[SuperOrderRecord] = []
        self._kill_switch_status: str = "DEACTIVATE"
        self._pnl_config = PnlExitConfig(status="INACTIVE", is_enabled=False)

        if not os.path.exists(self._db_path):
            log.info("Gold DB not found at %s — running pipeline...", self._db_path)
            import pipeline
            pipeline.run_all()

        self._con = duckdb.connect(self._db_path, read_only=True)
        self._timestamps = self._load_timestamps()
        self._current_idx = len(self._timestamps) - 1
        log.info("MockClient ready: %d timestamps, DB at %s", len(self._timestamps), self._db_path)

    def close(self):
        if self._con:
            self._con.close()

    # ── Time control (for live replay) ───────────────────────────────

    @property
    def current_ts(self) -> datetime:
        return self._timestamps[self._current_idx]

    def set_time(self, ts: datetime | str | None = None, idx: int | None = None) -> datetime:
        if idx is not None:
            self._current_idx = max(0, min(idx, len(self._timestamps) - 1))
        elif ts is not None:
            target = pd.Timestamp(ts).to_pydatetime() if isinstance(ts, str) else ts
            self._current_idx = min(
                range(len(self._timestamps)),
                key=lambda i: abs((self._timestamps[i] - target).total_seconds()),
            )
        return self.current_ts

    def advance(self, steps: int = 1) -> datetime:
        self._current_idx = min(self._current_idx + steps, len(self._timestamps) - 1)
        return self.current_ts

    @property
    def total_timestamps(self) -> int:
        return len(self._timestamps)

    # ── Option Chain ─────────────────────────────────────────────────

    def option_chain(self, underlying: str, expiry: str = "") -> OptionChainSnapshot:
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
        return self._get_candles(underlying, from_date, to_date)

    def candles_intraday(self, security_id: int = 0, exchange_segment: str = "",
                          from_date: str = "", to_date: str = "",
                          underlying: str = "NIFTY") -> CandleResponse:
        return self._get_candles(underlying, from_date, to_date)

    # ── Positions & Holdings ─────────────────────────────────────────

    def positions(self) -> PositionsResponse:
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

    def holdings(self) -> HoldingsResponse:
        return HoldingsResponse(
            holdings=[
                Holding(
                    security_id="1333",
                    trading_symbol="HDFCBANK",
                    exchange="NSE",
                    isin="INE040A01034",
                    total_qty=50,
                    dp_qty=50,
                    t1_qty=0,
                    available_qty=50,
                    avg_cost_price=1620.0,
                    ltp=1685.5,
                    current_val=84275.0,
                    pnl=3275.0,
                    pnl_pct=4.04,
                ),
                Holding(
                    security_id="2885",
                    trading_symbol="RELIANCE",
                    exchange="NSE",
                    isin="INE002A01018",
                    total_qty=20,
                    dp_qty=20,
                    t1_qty=0,
                    available_qty=20,
                    avg_cost_price=2840.0,
                    ltp=2920.0,
                    current_val=58400.0,
                    pnl=1600.0,
                    pnl_pct=2.82,
                ),
            ],
            total_investment=137800.0,
            total_current_val=142675.0,
            total_pnl=4875.0,
            total_pnl_pct=3.54,
        )

    def convert_position(self, req: PositionConvertRequest) -> dict:
        return {"status": "SUCCESS", "message": f"Converted {req.convert_qty} of {req.trading_symbol} to {req.to_product_type}"}

    # ── Orders ───────────────────────────────────────────────────────

    def place_order(self, proposal: OrderProposal) -> OrderResponse:
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
        order_id = uuid.uuid4().hex[:8]
        return OrderResponse(
            order_id=order_id,
            status="PAPER_FILL",
            message=f"PAPER fill: {proposal.side} {proposal.qty} {proposal.underlying} {proposal.strike}{proposal.opt_type} @ {proposal.price:.2f}",
        )

    def place_order_custom(self, req: OrderRequest) -> OrderResponse:
        order_id = uuid.uuid4().hex[:8]
        rec = OrderRecord(
            order_id=order_id,
            dhan_client_id="DEMO_CLIENT",
            order_status="TRADED",
            transaction_type=req.transaction_type,
            exchange_segment=req.exchange_segment,
            product_type=req.product_type,
            order_type=req.order_type,
            validity=req.validity,
            trading_symbol=f"SEC_{req.security_id}",
            security_id=str(req.security_id),
            quantity=req.quantity,
            price=req.price,
            filled_qty=req.quantity,
            pending_qty=0,
            avg_traded_price=req.price,
            update_time=datetime.now().strftime("%H:%M:%S"),
            remarks="Paper order filled",
        )
        self._orders.insert(0, rec)
        return OrderResponse(order_id=order_id, status="TRADED", message="Paper order placed and executed")

    def modify_order(self, req: OrderModifyRequest) -> OrderResponse:
        for o in self._orders:
            if o.order_id == req.order_id:
                o.price = req.price
                o.quantity = req.quantity
                o.order_type = req.order_type
                return OrderResponse(order_id=req.order_id, status="MODIFIED", message="Paper order modified")
        return OrderResponse(order_id=req.order_id, status="MODIFIED", message="Paper order modified")

    def cancel_order(self, order_id: str) -> OrderResponse:
        for o in self._orders:
            if o.order_id == order_id:
                o.order_status = "CANCELLED"
        return OrderResponse(order_id=order_id, status="CANCELLED", message="Paper order cancelled")

    def orders(self) -> list[OrderRecord]:
        return list(self._orders)

    def order_by_id(self, order_id: str) -> dict:
        for o in self._orders:
            if o.order_id == order_id:
                return o.model_dump()
        return {"orderId": order_id, "orderStatus": "NOT_FOUND"}

    def slice_order(self, req: OrderSliceRequest) -> list[OrderResponse]:
        sub_qty = max(1, req.quantity // 2)
        return [
            OrderResponse(order_id=uuid.uuid4().hex[:8], status="TRADED", message=f"Slice 1 ({sub_qty} qty) filled"),
            OrderResponse(order_id=uuid.uuid4().hex[:8], status="TRADED", message=f"Slice 2 ({req.quantity - sub_qty} qty) filled"),
        ]

    # ── Forever Orders (GTT / OCO) ───────────────────────────────────

    def place_forever_order(self, req: ForeverOrderRequest) -> OrderResponse:
        order_id = uuid.uuid4().hex[:8]
        self._forever_orders.append(ForeverOrderRecord(
            order_id=order_id,
            order_flag=req.order_flag,
            order_status="ACTIVE",
            transaction_type=req.transaction_type,
            exchange_segment=req.exchange_segment,
            product_type=req.product_type,
            security_id=str(req.security_id),
            quantity=req.quantity,
            price=req.price,
            trigger_price=req.trigger_price,
            price1=req.price1,
            trigger_price1=req.trigger_price1,
            quantity1=req.quantity1,
            create_time=datetime.now().isoformat(),
        ))
        return OrderResponse(order_id=order_id, status="ACTIVE", message="Forever GTT order placed")

    def modify_forever_order(self, order_id: str, req: ForeverOrderRequest) -> OrderResponse:
        return OrderResponse(order_id=order_id, status="MODIFIED", message="Forever order modified")

    def cancel_forever_order(self, order_id: str) -> OrderResponse:
        self._forever_orders = [o for o in self._forever_orders if o.order_id != order_id]
        return OrderResponse(order_id=order_id, status="CANCELLED", message="Forever order cancelled")

    def forever_orders(self) -> list[ForeverOrderRecord]:
        return list(self._forever_orders)

    # ── Super Orders (Bracket Orders) ─────────────────────────────────

    def place_super_order(self, req: SuperOrderRequest) -> OrderResponse:
        order_id = uuid.uuid4().hex[:8]
        self._super_orders.append(SuperOrderRecord(
            order_id=order_id,
            parent_order_id=order_id,
            order_status="ACTIVE",
            leg_name="ENTRY_LEG",
            security_id=str(req.security_id),
            transaction_type=req.transaction_type,
            exchange_segment=req.exchange_segment,
            product_type="BO",
            quantity=req.quantity,
            price=req.price,
            target_price=req.target_price,
            stop_loss_price=req.stop_loss_price,
            trailing_stop_loss=req.trailing_stop_loss,
        ))
        return OrderResponse(order_id=order_id, status="ACTIVE", message="Super Order (Bracket) created")

    def modify_super_order(self, order_id: str, req: SuperOrderRequest) -> OrderResponse:
        return OrderResponse(order_id=order_id, status="MODIFIED", message="Super Order modified")

    def cancel_super_order_leg(self, order_id: str, leg_name: str) -> OrderResponse:
        return OrderResponse(order_id=order_id, status="CANCELLED", message=f"Super Order leg {leg_name} cancelled")

    def super_orders(self) -> list[SuperOrderRecord]:
        return list(self._super_orders)

    # ── US Stocks API ────────────────────────────────────────────────

    def global_stocks_holdings(self) -> GlobalStocksResponse:
        return GlobalStocksResponse(
            holdings=[
                GlobalHolding(
                    security_id="AAPL",
                    symbol="AAPL",
                    company_name="Apple Inc.",
                    quantity=5.0,
                    avg_price=215.0,
                    ltp=228.4,
                    total_value=1142.0,
                    unrealized_pnl=67.0,
                    unrealized_pnl_pct=6.23,
                ),
                GlobalHolding(
                    security_id="NVDA",
                    symbol="NVDA",
                    company_name="NVIDIA Corporation",
                    quantity=8.0,
                    avg_price=118.0,
                    ltp=132.5,
                    total_value=1060.0,
                    unrealized_pnl=116.0,
                    unrealized_pnl_pct=12.29,
                ),
            ],
            total_value_usd=2202.0,
            total_pnl_usd=183.0,
            account_status="ACTIVE",
            message="Mock US stocks portfolio active",
        )

    def global_stocks_orders(self) -> list[dict]:
        return []

    # ── Funds & Margin API ───────────────────────────────────────────

    def funds(self) -> FundLimit:
        return FundLimit(
            dhan_client_id="DEMO_CLIENT",
            available_balance=500000.0,
            sod_limit=500000.0,
            collateral_amount=150000.0,
            receiveable_amount=0.0,
            utilized_amount=125000.0,
            blocked_payout_amount=0.0,
            withdrawable_balance=375000.0,
        )

    def calculate_margin(self, item: MarginCalculationItem) -> MarginCalculationResponse:
        req_margin = round(item.quantity * item.price * (0.2 if item.product_type == "INTRADAY" else 1.0), 2)
        return MarginCalculationResponse(
            total_margin=req_margin,
            span_margin=round(req_margin * 0.7, 2),
            exposure_margin=round(req_margin * 0.3, 2),
            variable_margin=0.0,
            available_balance=500000.0,
            insufficient_balance=0.0,
            leverage="5x" if item.product_type == "INTRADAY" else "1x",
        )

    def calculate_margin_multi(self, items: list[MarginCalculationItem]) -> MarginCalculationResponse:
        tot = sum(it.quantity * it.price * 0.2 for it in items)
        return MarginCalculationResponse(
            total_margin=round(tot, 2),
            span_margin=round(tot * 0.75, 2),
            exposure_margin=round(tot * 0.25, 2),
            variable_margin=0.0,
            available_balance=500000.0,
            insufficient_balance=0.0,
            leverage="5x",
        )

    # ── Past Trades API ──────────────────────────────────────────────

    def trades(self) -> list[TradeRecord]:
        return [
            TradeRecord(
                trade_id="TRD1001",
                order_id="ORD5001",
                exchange_segment="NSE_EQ",
                transaction_type="BUY",
                exchange_time="10:15:22",
                trading_symbol="INFY",
                security_id="1594",
                traded_price=1885.0,
                traded_quantity=25,
                create_time=datetime.now().strftime("%Y-%m-%d 10:15:22"),
            )
        ]

    def trades_by_order(self, order_id: str) -> list[TradeRecord]:
        return [t for t in self.trades() if t.order_id == order_id]

    def historical_trades(self, from_date: str, to_date: str, page: int = 0) -> list[TradeRecord]:
        return self.trades()

    # ── Kill Switch API ──────────────────────────────────────────────

    def get_kill_switch(self) -> KillSwitchStatus:
        return KillSwitchStatus(
            dhan_client_id="DEMO_CLIENT",
            kill_switch_status=self._kill_switch_status,
            message="Kill switch status checked",
        )

    def set_kill_switch(self, status: str = "ACTIVATE") -> KillSwitchStatus:
        self._kill_switch_status = status.upper()
        return KillSwitchStatus(
            dhan_client_id="DEMO_CLIENT",
            kill_switch_status=self._kill_switch_status,
            message=f"Kill switch set to {self._kill_switch_status}",
        )

    # ── P&L Exit API ─────────────────────────────────────────────────

    def get_pnl_exit(self) -> PnlExitConfig:
        return self._pnl_config

    def set_pnl_exit(self, profit_threshold: float, loss_threshold: float) -> PnlExitConfig:
        self._pnl_config = PnlExitConfig(
            status="ACTIVE",
            profit_threshold=profit_threshold,
            loss_threshold=loss_threshold,
            is_enabled=True,
            message="P&L Exit auto-squareoff armed",
        )
        return self._pnl_config

    def stop_pnl_exit(self) -> PnlExitConfig:
        self._pnl_config = PnlExitConfig(status="DISABLED", is_enabled=False, message="P&L exit disarmed")
        return self._pnl_config

    def exit_all_positions(self) -> dict:
        return {"status": "SUCCESS", "message": "All mock positions squared off successfully"}

    # ── Ledger API ───────────────────────────────────────────────────

    def ledger(self, from_date: str, to_date: str) -> LedgerResponse:
        return LedgerResponse(
            from_date=from_date,
            to_date=to_date,
            entries=[
                LedgerEntry(
                    dhan_client_id="DEMO_CLIENT",
                    narration="Funds Added via UPI",
                    voucher_date=from_date,
                    exchange="NSE",
                    voucher_desc="UPI Inward Payment",
                    voucher_amount=500000.0,
                    dr_cr="CR",
                    running_balance=500000.0,
                ),
                LedgerEntry(
                    dhan_client_id="DEMO_CLIENT",
                    narration="Securities Margin Obligation",
                    voucher_date=to_date,
                    exchange="NSE_FNO",
                    voucher_desc="Derivative Margin Debited",
                    voucher_amount=125000.0,
                    dr_cr="DR",
                    running_balance=375000.0,
                ),
            ],
            total_debit=125000.0,
            total_credit=500000.0,
            closing_balance=375000.0,
        )

    # ── EDIS API ─────────────────────────────────────────────────────

    def generate_tpin(self) -> EdisTpinResponse:
        return EdisTpinResponse(status="SUCCESS", message="Mock TPIN sent to your registered mobile number.")

    def generate_edis_form(self, req: EdisFormRequest) -> EdisFormResponse:
        return EdisFormResponse(
            form_html="<div><h3>CDSL eDIS Demo Form</h3><p>Authorized for demo.</p></div>",
            edis_partner_id="PARTNER_12345",
            status="SUCCESS",
        )

    def inquire_edis_status(self, isin: str) -> EdisStatusResponse:
        return EdisStatusResponse(isin=isin, status="AUTHORIZED", authorized_qty=100, remaining_qty=0)

    # ── Static IP API ────────────────────────────────────────────────

    def get_ip(self) -> dict:
        return {"status": "SUCCESS", "ip": "127.0.0.1", "ipFlag": "PRIMARY", "lockin_date": "NONE"}

    def set_ip(self, ip: str, ip_flag: str = "PRIMARY") -> dict:
        return {"status": "SUCCESS", "message": f"Static IP {ip} ({ip_flag}) configured successfully in mock mode."}

    # ── Blotter ──────────────────────────────────────────────────────

    @property
    def blotter(self) -> list[dict]:
        return list(self._blotter)

    # ── Internal ─────────────────────────────────────────────────────

    def _load_timestamps(self) -> list[datetime]:
        rows = self._con.execute(
            "SELECT DISTINCT ts FROM candles WHERE underlying = 'NIFTY' ORDER BY ts"
        ).fetchall()
        return [r[0] for r in rows]

    def _get_candles(self, underlying: str, from_date: str = "", to_date: str = "") -> CandleResponse:
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
