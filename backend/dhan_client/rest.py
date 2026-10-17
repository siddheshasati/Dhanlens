"""REST API wrapper for DhanHQ v2.

Covers:
- Option Chain & Historical/Intraday Candles
- Orders API (Place, Modify, Cancel, Order Book, Slice)
- Conditional & Multi Order API (Forever Orders / GTT / OCO)
- Super Order API (Bracket Orders)
- Portfolio & Positions API (Positions, Demat Holdings, Product Conversion)
- US Stocks API (Global Stocks Holdings & Orders)
- Funds & Margin API (Fund limit, Margin Calculator)
- Past Trades API (Trade book, Trades by Order, History)
- Kill Switch API (Status, Activate, Deactivate)
- P&L Exit API (Config, Trigger, Stop, Square Off)
- Ledger API (Statements, Debits, Credits)
- EDIS API (TPIN generation, Authorization form, Status inquiry)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx

from .config import (
    DhanConfig, BASE_URL, INDEX_SECURITY_IDS, IDX_I, NSE_FNO, NSE_EQ,
    OPTION_CHAIN_INTERVAL_S,
)
from .exceptions import AuthenticationError, DhanError, RateLimitError
from .models import (
    Candle, CandleResponse, OptionChainSnapshot, OptionStrike,
    OrderProposal, OrderResponse, Position, PositionsResponse,
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


class DhanRestClient:
    """Synchronous REST client for DhanHQ v2 API."""

    def __init__(self, config: DhanConfig):
        self._cfg = config
        self._http = httpx.Client(
            base_url=config.base_url,
            headers={
                "access-token": config.access_token,
                "client-id": config.client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=config.http_timeout,
        )
        self._last_oc_call: float = 0.0

    def close(self):
        self._http.close()

    # ── Option Chain ─────────────────────────────────────────────────

    def option_chain(self, underlying: str, expiry: str) -> OptionChainSnapshot:
        """Fetch the full option chain for an underlying and expiry date."""
        self._enforce_oc_rate_limit()

        sec_id = INDEX_SECURITY_IDS.get(underlying.upper())
        if sec_id is None:
            raise DhanError(f"Unknown underlying '{underlying}'. Known: {list(INDEX_SECURITY_IDS)}")

        body = {
            "UnderlyingScrip": sec_id,
            "UnderlyingSeg": IDX_I,
            "Expiry": expiry,
        }
        data = self._post("/optionchain", body)
        return self._parse_option_chain(data, underlying, expiry)

    # ── Historical / Intraday Candles ────────────────────────────────

    def candles_daily(self, security_id: int, exchange_segment: str,
                      from_date: str, to_date: str) -> CandleResponse:
        body = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": "INDEX" if exchange_segment == IDX_I else "EQUITY",
            "fromDate": from_date,
            "toDate": to_date,
        }
        data = self._post("/charts/historical", body)
        return self._parse_candles(data, security_id)

    def candles_intraday(self, security_id: int, exchange_segment: str,
                          from_date: str, to_date: str) -> CandleResponse:
        body = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": "OPTIDX" if exchange_segment in (NSE_FNO,) else "INDEX",
            "fromDate": from_date,
            "toDate": to_date,
        }
        data = self._post("/charts/intraday", body)
        return self._parse_candles(data, security_id)

    # ── 1. Orders API ────────────────────────────────────────────────

    def place_order(self, proposal: OrderProposal) -> OrderResponse:
        """Place standard order (legacy / simplified interface)."""
        body = {
            "dhanClientId": self._cfg.client_id,
            "securityId": str(proposal.security_id),
            "exchangeSegment": proposal.exchange_segment,
            "transactionType": proposal.side,
            "orderType": proposal.order_type,
            "productType": proposal.product_type,
            "quantity": proposal.qty,
            "price": proposal.price,
            "validity": "DAY",
        }
        data = self._post("/orders", body)
        return OrderResponse(
            order_id=str(data.get("orderId", "")),
            status=data.get("orderStatus", "PENDING"),
            message=data.get("remarks", "Order submitted"),
        )

    def place_order_custom(self, req: OrderRequest) -> OrderResponse:
        """Place a fully customized DhanHQ order."""
        body: dict[str, Any] = {
            "dhanClientId": self._cfg.client_id,
            "securityId": str(req.security_id),
            "exchangeSegment": req.exchange_segment,
            "transactionType": req.transaction_type.upper(),
            "orderType": req.order_type.upper(),
            "productType": req.product_type.upper(),
            "quantity": req.quantity,
            "price": req.price,
            "triggerPrice": req.trigger_price,
            "validity": req.validity.upper(),
            "disclosedQuantity": req.disclosed_quantity,
            "afterMarketOrder": req.after_market_order,
        }
        if req.correlation_id:
            body["correlationId"] = req.correlation_id

        data = self._post("/orders", body)
        return OrderResponse(
            order_id=str(data.get("orderId", "")),
            status=data.get("orderStatus", "PENDING"),
            message=data.get("remarks", "Order submitted successfully"),
        )

    def modify_order(self, req: OrderModifyRequest) -> OrderResponse:
        """Modify an existing pending order."""
        body = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "orderId": str(req.order_id),
            "orderType": req.order_type.upper(),
            "quantity": req.quantity,
            "price": req.price,
            "triggerPrice": req.trigger_price,
            "validity": req.validity.upper(),
            "disclosedQuantity": req.disclosed_quantity,
        }
        data = self._put(f"/orders/{req.order_id}", body)
        return OrderResponse(
            order_id=str(data.get("orderId", req.order_id)),
            status=data.get("orderStatus", "MODIFIED"),
            message=data.get("remarks", "Order modified successfully"),
        )

    def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an existing pending order."""
        data = self._delete(f"/orders/{order_id}")
        return OrderResponse(
            order_id=str(data.get("orderId", order_id)),
            status=data.get("orderStatus", "CANCELLED"),
            message=data.get("remarks", "Order cancelled successfully"),
        )

    def orders(self) -> list[OrderRecord]:
        """Fetch all orders for the current trading day (Order Book)."""
        data = self._get("/orders")
        items = data if isinstance(data, list) else data.get("data", [])
        records = []
        for r in items:
            records.append(OrderRecord(
                order_id=str(r.get("orderId", "")),
                dhan_client_id=str(r.get("dhanClientId", "")),
                order_status=str(r.get("orderStatus", "")),
                transaction_type=str(r.get("transactionType", "")),
                exchange_segment=str(r.get("exchangeSegment", "")),
                product_type=str(r.get("productType", "")),
                order_type=str(r.get("orderType", "")),
                validity=str(r.get("validity", "")),
                trading_symbol=str(r.get("tradingSymbol", "")),
                security_id=str(r.get("securityId", "")),
                quantity=int(r.get("quantity", 0) or 0),
                disclosed_quantity=int(r.get("disclosedQuantity", 0) or 0),
                price=float(r.get("price", 0.0) or 0.0),
                trigger_price=float(r.get("triggerPrice", 0.0) or 0.0),
                after_market_order=bool(r.get("afterMarketOrder", False)),
                filled_qty=int(r.get("filledQty", 0) or 0),
                pending_qty=int(r.get("pendingQty", 0) or 0),
                avg_traded_price=float(r.get("avgTradedPrice", 0.0) or 0.0),
                exchange_order_id=str(r.get("exchangeOrderId", "")),
                update_time=str(r.get("updateTime", "") or r.get("orderTimestamp", "")),
                remarks=str(r.get("remarks", "")),
            ))
        return records

    def order_by_id(self, order_id: str) -> dict:
        """Fetch status and full detail of a specific order by ID."""
        data = self._get(f"/orders/{order_id}")
        return data if isinstance(data, dict) else {"data": data}

    def slice_order(self, req: OrderSliceRequest) -> list[OrderResponse]:
        """Slice large orders into smaller legs over exchange freeze limits."""
        body = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "securityId": str(req.security_id),
            "exchangeSegment": req.exchange_segment,
            "transactionType": req.transaction_type.upper(),
            "orderType": req.order_type.upper(),
            "productType": req.product_type.upper(),
            "quantity": req.quantity,
            "price": req.price,
        }
        data = self._post("/orders/slicing", body)
        items = data if isinstance(data, list) else [data]
        return [
            OrderResponse(
                order_id=str(x.get("orderId", "")),
                status=str(x.get("orderStatus", "PENDING")),
                message=str(x.get("remarks", "Sliced order accepted")),
            )
            for x in items
        ]

    # ── 2. Conditional & Multi Order API (Forever Orders / GTT) ──────

    def place_forever_order(self, req: ForeverOrderRequest) -> OrderResponse:
        """Place a Forever / GTT / OCO order."""
        body: dict[str, Any] = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "orderFlag": req.order_flag.upper(),
            "transactionType": req.transaction_type.upper(),
            "exchangeSegment": req.exchange_segment,
            "productType": req.product_type.upper(),
            "securityId": str(req.security_id),
            "quantity": req.quantity,
            "price": req.price,
            "triggerPrice": req.trigger_price,
        }
        if req.order_flag.upper() == "OCO":
            body["price1"] = req.price1
            body["triggerPrice1"] = req.trigger_price1
            body["quantity1"] = req.quantity1 or req.quantity

        data = self._post("/forever/orders", body)
        return OrderResponse(
            order_id=str(data.get("orderId", "")),
            status=data.get("orderStatus", "PENDING"),
            message=data.get("remarks", "Forever order placed"),
        )

    def modify_forever_order(self, order_id: str, req: ForeverOrderRequest) -> OrderResponse:
        """Modify an active Forever order."""
        body: dict[str, Any] = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "orderFlag": req.order_flag.upper(),
            "orderId": order_id,
            "transactionType": req.transaction_type.upper(),
            "exchangeSegment": req.exchange_segment,
            "productType": req.product_type.upper(),
            "securityId": str(req.security_id),
            "quantity": req.quantity,
            "price": req.price,
            "triggerPrice": req.trigger_price,
        }
        if req.order_flag.upper() == "OCO":
            body["price1"] = req.price1
            body["triggerPrice1"] = req.trigger_price1
            body["quantity1"] = req.quantity1 or req.quantity

        data = self._put(f"/forever/orders/{order_id}", body)
        return OrderResponse(
            order_id=str(data.get("orderId", order_id)),
            status=data.get("orderStatus", "MODIFIED"),
            message=data.get("remarks", "Forever order modified"),
        )

    def cancel_forever_order(self, order_id: str) -> OrderResponse:
        """Cancel a pending Forever order."""
        data = self._delete(f"/forever/orders/{order_id}")
        return OrderResponse(
            order_id=str(data.get("orderId", order_id)),
            status=data.get("orderStatus", "CANCELLED"),
            message=data.get("remarks", "Forever order cancelled"),
        )

    def forever_orders(self) -> list[ForeverOrderRecord]:
        """List all active Forever / GTT orders."""
        data = self._get("/forever/orders")
        items = data if isinstance(data, list) else data.get("data", [])
        records = []
        for r in items:
            records.append(ForeverOrderRecord(
                order_id=str(r.get("orderId", "")),
                order_flag=str(r.get("orderFlag", "SINGLE")),
                order_status=str(r.get("orderStatus", "")),
                transaction_type=str(r.get("transactionType", "")),
                exchange_segment=str(r.get("exchangeSegment", "")),
                product_type=str(r.get("productType", "")),
                security_id=str(r.get("securityId", "")),
                quantity=int(r.get("quantity", 0) or 0),
                price=float(r.get("price", 0.0) or 0.0),
                trigger_price=float(r.get("triggerPrice", 0.0) or 0.0),
                price1=float(r.get("price1", 0.0) or 0.0),
                trigger_price1=float(r.get("triggerPrice1", 0.0) or 0.0),
                quantity1=int(r.get("quantity1", 0) or 0),
                create_time=str(r.get("createTime", "")),
            ))
        return records

    # ── 3. Super Order API (Bracket Orders) ───────────────────────────

    def place_super_order(self, req: SuperOrderRequest) -> OrderResponse:
        """Place a Super Order (Bracket Order with Entry, Target & Stop Loss)."""
        body = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "securityId": str(req.security_id),
            "exchangeSegment": req.exchange_segment,
            "transactionType": req.transaction_type.upper(),
            "orderType": req.order_type.upper(),
            "productType": "BO",
            "quantity": req.quantity,
            "price": req.price,
            "targetPrice": req.target_price,
            "stopLossPrice": req.stop_loss_price,
            "trailingStopLoss": req.trailing_stop_loss,
        }
        data = self._post("/super/orders", body)
        return OrderResponse(
            order_id=str(data.get("orderId", "")),
            status=data.get("orderStatus", "PENDING"),
            message=data.get("remarks", "Super Order placed"),
        )

    def modify_super_order(self, order_id: str, req: SuperOrderRequest) -> OrderResponse:
        """Modify an active Super Order."""
        body = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "orderId": order_id,
            "price": req.price,
            "targetPrice": req.target_price,
            "stopLossPrice": req.stop_loss_price,
            "trailingStopLoss": req.trailing_stop_loss,
            "quantity": req.quantity,
        }
        data = self._put(f"/super/orders/{order_id}", body)
        return OrderResponse(
            order_id=str(data.get("orderId", order_id)),
            status=data.get("orderStatus", "MODIFIED"),
            message=data.get("remarks", "Super Order modified"),
        )

    def cancel_super_order_leg(self, order_id: str, leg_name: str) -> OrderResponse:
        """Cancel a pending Super Order leg."""
        data = self._delete(f"/super/orders/{order_id}/{leg_name}")
        return OrderResponse(
            order_id=str(data.get("orderId", order_id)),
            status=data.get("orderStatus", "CANCELLED"),
            message=data.get("remarks", f"Super Order leg {leg_name} cancelled"),
        )

    def super_orders(self) -> list[SuperOrderRecord]:
        """Fetch all Super Orders."""
        data = self._get("/super/orders")
        items = data if isinstance(data, list) else data.get("data", [])
        records = []
        for r in items:
            records.append(SuperOrderRecord(
                order_id=str(r.get("orderId", "")),
                parent_order_id=str(r.get("parentOrderId", "")),
                order_status=str(r.get("orderStatus", "")),
                leg_name=str(r.get("legName", "")),
                security_id=str(r.get("securityId", "")),
                transaction_type=str(r.get("transactionType", "")),
                exchange_segment=str(r.get("exchangeSegment", "")),
                product_type=str(r.get("productType", "")),
                quantity=int(r.get("quantity", 0) or 0),
                price=float(r.get("price", 0.0) or 0.0),
                target_price=float(r.get("targetPrice", 0.0) or 0.0),
                stop_loss_price=float(r.get("stopLossPrice", 0.0) or 0.0),
                trailing_stop_loss=float(r.get("trailingStopLoss", 0.0) or 0.0),
            ))
        return records

    # ── 4. Portfolio & Positions API ─────────────────────────────────

    def positions(self) -> PositionsResponse:
        """Retrieve all open and closed positions for the day."""
        data = self._get("/positions")
        positions = []
        for p in (data if isinstance(data, list) else data.get("data", [])):
            try:
                positions.append(Position(
                    underlying=p.get("tradingSymbol", "").split("-")[0] if "-" in p.get("tradingSymbol", "") else p.get("tradingSymbol", ""),
                    strike=int(float(p.get("strikePrice", 0) or 0)),
                    opt_type=_map_opt_type(p.get("optionType", "")),
                    qty=int(p.get("netQty", 0)),
                    avg_price=float(p.get("costPrice", 0) or 0),
                    ltp=float(p.get("ltp", 0) or 0),
                    pnl=float(p.get("realizedProfit", 0) or 0) + float(p.get("unrealizedProfit", 0) or 0),
                    security_id=int(p.get("securityId", 0) or 0),
                    exchange_segment=p.get("exchangeSegment", NSE_FNO),
                ))
            except (ValueError, KeyError) as exc:
                log.warning("Skipping position row: %s — %s", p, exc)
        return PositionsResponse(positions=positions)

    def holdings(self) -> HoldingsResponse:
        """Retrieve user's equity demat holdings."""
        try:
            data = self._get("/holdings")
        except DhanError as exc:
            # Dhan returns 500 when no holdings exist
            if "No holdings available" in str(exc) or "HOLDING_ERROR" in str(exc):
                return HoldingsResponse(holdings=[])
            raise

        items = data if isinstance(data, list) else data.get("data", [])
        holdings_list = []
        tot_inv = 0.0
        tot_cur = 0.0

        for h in items:
            tot_qty = int(h.get("totalQty", 0) or 0)
            avg_cost = float(h.get("avgCostPrice", 0.0) or 0.0)
            ltp = float(h.get("ltp", 0.0) or 0.0)
            cur_val = tot_qty * ltp
            inv_val = tot_qty * avg_cost
            pnl = cur_val - inv_val
            tot_inv += inv_val
            tot_cur += cur_val

            holdings_list.append(Holding(
                security_id=str(h.get("securityId", "")),
                trading_symbol=str(h.get("tradingSymbol", "")),
                exchange=str(h.get("exchange", "NSE")),
                isin=str(h.get("isin", "")),
                total_qty=tot_qty,
                dp_qty=int(h.get("dpQty", 0) or 0),
                t1_qty=int(h.get("t1Qty", 0) or 0),
                available_qty=int(h.get("availableQty", 0) or 0),
                avg_cost_price=avg_cost,
                ltp=ltp,
                current_val=round(cur_val, 2),
                pnl=round(pnl, 2),
                pnl_pct=round((pnl / inv_val * 100) if inv_val > 0 else 0.0, 2),
            ))

        tot_pnl = tot_cur - tot_inv
        tot_pnl_pct = (tot_pnl / tot_inv * 100) if tot_inv > 0 else 0.0

        return HoldingsResponse(
            holdings=holdings_list,
            total_investment=round(tot_inv, 2),
            total_current_val=round(tot_cur, 2),
            total_pnl=round(tot_pnl, 2),
            total_pnl_pct=round(tot_pnl_pct, 2),
        )

    def convert_position(self, req: PositionConvertRequest) -> dict:
        """Convert position product type (e.g. INTRADAY -> CNC / MARGIN)."""
        body = {
            "dhanClientId": self._cfg.client_id or req.dhan_client_id,
            "fromProductType": req.from_product_type.upper(),
            "toProductType": req.to_product_type.upper(),
            "exchangeSegment": req.exchange_segment,
            "securityId": str(req.security_id),
            "tradingSymbol": req.trading_symbol,
            "convertQty": req.convert_qty,
        }
        return self._post("/positions/convert", body)

    # ── 5. US Stocks API ─────────────────────────────────────────────

    def global_stocks_holdings(self) -> GlobalStocksResponse:
        """Retrieve Global / US Stocks holdings."""
        try:
            data = self._get("/globalstocks/holdings")
        except DhanError as exc:
            if "User not found" in str(exc) or "HOLDING_ERROR" in str(exc):
                return GlobalStocksResponse(
                    holdings=[],
                    account_status="NOT_ACTIVATED",
                    message="Global/US stock trading is not activated on this account.",
                )
            raise

        items = data if isinstance(data, list) else data.get("data", [])
        holdings_list = []
        tot_val = 0.0
        tot_pnl = 0.0

        for g in items:
            qty = float(g.get("quantity", 0.0) or 0.0)
            avg_p = float(g.get("avgPrice", 0.0) or 0.0)
            ltp = float(g.get("ltp", 0.0) or 0.0)
            val = qty * ltp
            pnl = val - (qty * avg_p)
            tot_val += val
            tot_pnl += pnl

            holdings_list.append(GlobalHolding(
                security_id=str(g.get("securityId", "")),
                symbol=str(g.get("symbol", "")),
                company_name=str(g.get("companyName", "")),
                quantity=qty,
                avg_price=avg_p,
                ltp=ltp,
                total_value=round(val, 2),
                unrealized_pnl=round(pnl, 2),
                unrealized_pnl_pct=round((pnl / (qty * avg_p) * 100) if (qty * avg_p) > 0 else 0.0, 2),
            ))

        return GlobalStocksResponse(
            holdings=holdings_list,
            total_value_usd=round(tot_val, 2),
            total_pnl_usd=round(tot_pnl, 2),
            account_status="ACTIVE",
            message="Active global portfolio",
        )

    def global_stocks_orders(self) -> list[dict]:
        """Retrieve Global / US stock order book."""
        try:
            data = self._get("/globalstocks/orders")
            return data if isinstance(data, list) else data.get("data", [])
        except DhanError:
            return []

    # ── 6. Funds & Margin API ────────────────────────────────────────

    def funds(self) -> FundLimit:
        """Retrieve available funds, SOD limits, and margin details."""
        data = self._get("/fundlimit")
        return FundLimit(
            dhan_client_id=str(data.get("dhanClientId", self._cfg.client_id)),
            available_balance=float(data.get("availabelBalance", data.get("availableBalance", 0.0)) or 0.0),
            sod_limit=float(data.get("sodLimit", 0.0) or 0.0),
            collateral_amount=float(data.get("collateralAmount", 0.0) or 0.0),
            receiveable_amount=float(data.get("receiveableAmount", 0.0) or 0.0),
            utilized_amount=float(data.get("utilizedAmount", 0.0) or 0.0),
            blocked_payout_amount=float(data.get("blockedPayoutAmount", 0.0) or 0.0),
            withdrawable_balance=float(data.get("withdrawableBalance", 0.0) or 0.0),
        )

    def calculate_margin(self, item: MarginCalculationItem) -> MarginCalculationResponse:
        """Calculate margin requirements for a proposed trade."""
        body = {
            "dhanClientId": self._cfg.client_id,
            "exchangeSegment": item.exchange_segment,
            "transactionType": item.transaction_type.upper(),
            "quantity": item.quantity,
            "productType": item.product_type.upper(),
            "securityId": str(item.security_id),
            "price": item.price,
            "triggerPrice": item.trigger_price,
        }
        data = self._post("/margincalculator", body)
        return MarginCalculationResponse(
            total_margin=float(data.get("totalMargin", 0.0) or 0.0),
            span_margin=float(data.get("spanMargin", 0.0) or 0.0),
            exposure_margin=float(data.get("exposureMargin", 0.0) or 0.0),
            variable_margin=float(data.get("variableMargin", 0.0) or 0.0),
            available_balance=float(data.get("availableBalance", 0.0) or 0.0),
            insufficient_balance=float(data.get("insufficientBalance", 0.0) or 0.0),
            leverage=str(data.get("leverage", "")),
        )

    def calculate_margin_multi(self, items: list[MarginCalculationItem]) -> MarginCalculationResponse:
        """Calculate combined margin requirements for multi-leg strategies."""
        body = [
            {
                "dhanClientId": self._cfg.client_id,
                "exchangeSegment": it.exchange_segment,
                "transactionType": it.transaction_type.upper(),
                "quantity": it.quantity,
                "productType": it.product_type.upper(),
                "securityId": str(it.security_id),
                "price": it.price,
                "triggerPrice": it.trigger_price,
            }
            for it in items
        ]
        data = self._post("/margincalculator/multi", body)
        return MarginCalculationResponse(
            total_margin=float(data.get("totalMargin", 0.0) or 0.0),
            span_margin=float(data.get("spanMargin", 0.0) or 0.0),
            exposure_margin=float(data.get("exposureMargin", 0.0) or 0.0),
            variable_margin=float(data.get("variableMargin", 0.0) or 0.0),
            available_balance=float(data.get("availableBalance", 0.0) or 0.0),
            insufficient_balance=float(data.get("insufficientBalance", 0.0) or 0.0),
            leverage=str(data.get("leverage", "")),
        )

    # ── 7. Past Trades API ───────────────────────────────────────────

    def trades(self) -> list[TradeRecord]:
        """Fetch all trades executed today."""
        data = self._get("/trades")
        items = data if isinstance(data, list) else data.get("data", [])
        records = []
        for r in items:
            records.append(TradeRecord(
                trade_id=str(r.get("tradeId", "")),
                order_id=str(r.get("orderId", "")),
                exchange_segment=str(r.get("exchangeSegment", "")),
                transaction_type=str(r.get("transactionType", "")),
                exchange_time=str(r.get("exchangeTime", "")),
                trading_symbol=str(r.get("tradingSymbol", "")),
                security_id=str(r.get("securityId", "")),
                traded_price=float(r.get("tradedPrice", 0.0) or 0.0),
                traded_quantity=int(r.get("tradedQuantity", 0) or 0),
                create_time=str(r.get("createTime", "")),
            ))
        return records

    def trades_by_order(self, order_id: str) -> list[TradeRecord]:
        """Fetch trades associated with a specific order ID."""
        data = self._get(f"/trades/{order_id}")
        items = data if isinstance(data, list) else data.get("data", [])
        return [
            TradeRecord(
                trade_id=str(r.get("tradeId", "")),
                order_id=str(r.get("orderId", order_id)),
                exchange_segment=str(r.get("exchangeSegment", "")),
                transaction_type=str(r.get("transactionType", "")),
                exchange_time=str(r.get("exchangeTime", "")),
                trading_symbol=str(r.get("tradingSymbol", "")),
                security_id=str(r.get("securityId", "")),
                traded_price=float(r.get("tradedPrice", 0.0) or 0.0),
                traded_quantity=int(r.get("tradedQuantity", 0) or 0),
                create_time=str(r.get("createTime", "")),
            )
            for r in items
        ]

    def historical_trades(self, from_date: str, to_date: str, page: int = 0) -> list[TradeRecord]:
        """Fetch historical trades within a date range."""
        path = f"/trades/{from_date}/{to_date}/{page}"
        try:
            data = self._get(path)
            items = data if isinstance(data, list) else data.get("data", [])
            return [
                TradeRecord(
                    trade_id=str(r.get("tradeId", "")),
                    order_id=str(r.get("orderId", "")),
                    exchange_segment=str(r.get("exchangeSegment", "")),
                    transaction_type=str(r.get("transactionType", "")),
                    exchange_time=str(r.get("exchangeTime", "")),
                    trading_symbol=str(r.get("tradingSymbol", "")),
                    security_id=str(r.get("securityId", "")),
                    traded_price=float(r.get("tradedPrice", 0.0) or 0.0),
                    traded_quantity=int(r.get("tradedQuantity", 0) or 0),
                    create_time=str(r.get("createTime", "")),
                )
                for r in items
            ]
        except Exception:
            return []

    # ── 8. Kill Switch API ───────────────────────────────────────────

    def get_kill_switch(self) -> KillSwitchStatus:
        """Fetch current Kill Switch status."""
        data = self._get("/killswitch")
        return KillSwitchStatus(
            dhan_client_id=str(data.get("dhanClientId", self._cfg.client_id)),
            kill_switch_status=str(data.get("killSwitchStatus", "")),
            message=str(data.get("message", "Kill switch status retrieved")),
        )

    def set_kill_switch(self, status: str = "ACTIVATE") -> KillSwitchStatus:
        """Activate or Deactivate the trading Kill Switch."""
        data = self._post(f"/killswitch?killSwitchStatus={status.upper()}", {})
        return KillSwitchStatus(
            dhan_client_id=str(data.get("dhanClientId", self._cfg.client_id)),
            kill_switch_status=status.upper(),
            message=str(data.get("message", f"Kill switch {status.lower()}d successfully")),
        )

    # ── 9. P&L Exit API ──────────────────────────────────────────────

    def get_pnl_exit(self) -> PnlExitConfig:
        """Fetch active P&L based exit configuration."""
        data = self._get("/pnlExit")
        return PnlExitConfig(
            status=str(data.get("status", "SUCCESS")),
            profit_threshold=float(data.get("profitThreshold", 0.0) or 0.0),
            loss_threshold=float(data.get("lossThreshold", 0.0) or 0.0),
            exit_time=str(data.get("exitTime", "")),
            is_enabled=bool(data.get("isEnabled", False)),
            message=str(data.get("message", "")),
        )

    def set_pnl_exit(self, profit_threshold: float, loss_threshold: float) -> PnlExitConfig:
        """Configure automated P&L based exit thresholds."""
        body = {
            "dhanClientId": self._cfg.client_id,
            "profitThreshold": profit_threshold,
            "lossThreshold": loss_threshold,
        }
        data = self._post("/pnlExit", body)
        return PnlExitConfig(
            status=str(data.get("status", "SUCCESS")),
            profit_threshold=profit_threshold,
            loss_threshold=loss_threshold,
            is_enabled=True,
            message=str(data.get("message", "P&L exit configured successfully")),
        )

    def stop_pnl_exit(self) -> PnlExitConfig:
        """Disable automated P&L exit rules."""
        data = self._delete("/pnlExit")
        return PnlExitConfig(
            status="DISABLED",
            is_enabled=False,
            message=str(data.get("message", "P&L exit rule disabled")),
        )

    def exit_all_positions(self) -> dict:
        """Emergency square-off of all active intraday and open positions."""
        return self._delete("/positions")

    # ── 10. Ledger API ───────────────────────────────────────────────

    def ledger(self, from_date: str, to_date: str) -> LedgerResponse:
        """Retrieve accounting ledger statement for the specified period."""
        path = f"/ledger?from-date={from_date}&to-date={to_date}"
        data = self._get(path)
        items = data if isinstance(data, list) else data.get("data", [])
        entries = []
        tot_dr = 0.0
        tot_cr = 0.0
        closing = 0.0

        for it in items:
            amt = float(it.get("voucheramount", 0.0) or 0.0)
            dr_cr = str(it.get("drcr", it.get("dr_cr", "CR"))).upper()
            if dr_cr == "DR":
                tot_dr += amt
            else:
                tot_cr += amt
            run_bal = float(it.get("runningbalance", 0.0) or 0.0)
            closing = run_bal

            entries.append(LedgerEntry(
                dhan_client_id=str(it.get("dhanClientId", self._cfg.client_id)),
                narration=str(it.get("narration", "")),
                voucher_date=str(it.get("voucherdate", "")),
                exchange=str(it.get("exchange", "")),
                voucher_desc=str(it.get("voucherdesc", "")),
                voucher_amount=amt,
                dr_cr=dr_cr,
                running_balance=run_bal,
            ))

        return LedgerResponse(
            from_date=from_date,
            to_date=to_date,
            entries=entries,
            total_debit=round(tot_dr, 2),
            total_credit=round(tot_cr, 2),
            closing_balance=round(closing, 2),
        )

    # ── 11. EDIS API ─────────────────────────────────────────────────

    def generate_tpin(self) -> EdisTpinResponse:
        """Request a CDSL T-PIN generation SMS."""
        data = self._get("/edis/tpin")
        return EdisTpinResponse(
            status="SUCCESS",
            message=str(data.get("message", "T-PIN generation triggered successfully. Check SMS.")),
        )

    def generate_edis_form(self, req: EdisFormRequest) -> EdisFormResponse:
        """Generate CDSL eDIS authorization form HTML payload."""
        body = {
            "dhanClientId": self._cfg.client_id,
            "isin": req.isin,
            "qty": req.qty,
            "exchange": req.exchange,
            "segment": req.segment,
            "bulk": req.bulk,
        }
        data = self._post("/edis/form", body)
        return EdisFormResponse(
            form_html=str(data.get("edisForm", data.get("formHtml", ""))),
            edis_partner_id=str(data.get("edisPartnerId", "")),
            status="SUCCESS",
        )

    def inquire_edis_status(self, isin: str) -> EdisStatusResponse:
        """Inquire authorization status for a specific stock ISIN."""
        data = self._get(f"/edis/inquire/{isin}")
        return EdisStatusResponse(
            isin=isin,
            status=str(data.get("status", "AUTHORIZED")),
            authorized_qty=int(data.get("authorizedQty", 0) or 0),
            remaining_qty=int(data.get("remainingQty", 0) or 0),
        )

    # ── 12. Static IP API ────────────────────────────────────────────

    def get_ip(self) -> dict:
        """Retrieve registered static IP configurations."""
        return self._get("/ip/getIP")

    def set_ip(self, ip: str, ip_flag: str = "PRIMARY") -> dict:
        """Configure static IP for order placement."""
        body = {
            "dhanClientId": self._cfg.client_id,
            "ip": ip,
            "ipFlag": ip_flag.upper(),
        }
        return self._post("/ip/setIP", body)

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _post(self, path: str, body: dict | list) -> dict:
        return self._request("POST", path, json=body)

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        return self._request("GET", path, params=params)

    def _put(self, path: str, body: dict) -> dict:
        return self._request("PUT", path, json=body)

    def _delete(self, path: str, params: dict | None = None) -> dict:
        return self._request("DELETE", path, params=params)

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        """Execute an HTTP request with error mapping and safe parsing."""
        try:
            r = self._http.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise DhanError(f"Request timed out: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise DhanError(f"HTTP error: {exc}") from exc

        if r.status_code in (401, 403):
            raise AuthenticationError(
                "Authentication failed — check DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN",
                status_code=r.status_code,
            )
        if r.status_code == 429:
            raise RateLimitError(
                "DhanHQ rate limit exceeded. Retry after a few seconds.",
                retry_after=OPTION_CHAIN_INTERVAL_S,
                status_code=429,
            )
        if r.status_code >= 400:
            raise DhanError(
                f"DhanHQ API error {r.status_code}: {r.text[:500]}",
                status_code=r.status_code,
                response=r.json() if r.headers.get("content-type", "").startswith("application/json") else {},
            )

        # Handle empty responses (like 202, 204 or empty string bodies)
        if not r.text or not r.text.strip():
            return {"status": "SUCCESS", "status_code": r.status_code}

        try:
            return r.json()
        except Exception:
            return {"raw": r.text, "status_code": r.status_code}

    def _enforce_oc_rate_limit(self) -> None:
        """Sleep if needed to respect the 1-request-per-3-seconds option chain limit."""
        elapsed = time.monotonic() - self._last_oc_call
        if elapsed < OPTION_CHAIN_INTERVAL_S:
            wait = OPTION_CHAIN_INTERVAL_S - elapsed
            log.debug("Option chain rate limit: sleeping %.1fs", wait)
            time.sleep(wait)
        self._last_oc_call = time.monotonic()

    # ── Response parsers ─────────────────────────────────────────────

    @staticmethod
    def _parse_option_chain(data: dict, underlying: str, expiry: str) -> OptionChainSnapshot:
        oc_data = data.get("data", data)
        spot = float(oc_data.get("last_price", 0) or oc_data.get("ltp", 0))
        strikes_raw = oc_data.get("oc", {})
        ts = datetime.now().replace(second=0, microsecond=0)

        strikes: list[OptionStrike] = []
        for strike_str, sides in strikes_raw.items():
            strike_price = int(float(strike_str))
            for side_key, side_label in (("ce", "CE"), ("pe", "PE")):
                side = sides.get(side_key)
                if not side:
                    continue
                strikes.append(OptionStrike(
                    strike=strike_price,
                    opt_type=side_label,
                    ltp=float(side.get("last_price", 0) or side.get("ltp", 0)),
                    iv=(float(side.get("implied_volatility", 0) or 0)) / 100.0,
                    oi=int(side.get("oi", 0) or 0),
                    volume=int(side.get("volume", 0) or 0),
                    delta=_safe_float(side.get("greeks", {}).get("delta")),
                    gamma=_safe_float(side.get("greeks", {}).get("gamma")),
                    theta=_safe_float(side.get("greeks", {}).get("theta")),
                    vega=_safe_float(side.get("greeks", {}).get("vega")),
                    security_id=int(side.get("security_id", 0) or side.get("securityId", 0) or 0),
                ))

        return OptionChainSnapshot(
            ts=ts, underlying=underlying.upper(), spot=spot, expiry=expiry, strikes=strikes,
        )

    @staticmethod
    def _parse_candles(data: dict, security_id: int) -> CandleResponse:
        candle_data = data.get("data", data)
        candles = []

        if isinstance(candle_data, list):
            for row in candle_data:
                if len(row) < 6:
                    continue
                candles.append(Candle(
                    ts=datetime.fromisoformat(str(row[0])) if isinstance(row[0], str)
                       else datetime.fromtimestamp(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=int(row[5]),
                    oi=int(row[6]) if len(row) > 6 else None,
                ))

        return CandleResponse(
            underlying="", security_id=security_id, candles=candles,
        )


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _map_opt_type(raw: str) -> str:
    r = raw.upper().strip()
    if r in ("CE", "CALL"):
        return "CE"
    if r in ("PE", "PUT"):
        return "PE"
    return r
