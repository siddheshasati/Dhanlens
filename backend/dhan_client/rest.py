"""REST API wrapper for DhanHQ v2.

Covers: option chain snapshots, historical/intraday candles, positions, orders, and funds.
All methods return normalized Pydantic models from models.py.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from .config import (
    DhanConfig, BASE_URL, INDEX_SECURITY_IDS, IDX_I, NSE_FNO,
    OPTION_CHAIN_INTERVAL_S,
)
from .exceptions import AuthenticationError, DhanError, RateLimitError
from .models import (
    Candle, CandleResponse, OptionChainSnapshot, OptionStrike,
    OrderProposal, OrderResponse, Position, PositionsResponse,
)

log = logging.getLogger(__name__)


class DhanRestClient:
    """Synchronous REST client for DhanHQ v2 API.

    Usage::

        cfg = DhanConfig()  # reads env vars
        rest = DhanRestClient(cfg)
        snap = rest.option_chain("NIFTY", "2026-10-20")
    """

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
        self._last_oc_call: float = 0.0  # rate-limit tracking for option chain

    def close(self):
        self._http.close()

    # ── Option Chain ─────────────────────────────────────────────────

    def option_chain(self, underlying: str, expiry: str) -> OptionChainSnapshot:
        """Fetch the full option chain for an underlying and expiry date.

        Rate limit: enforces ≥3 s between calls (DhanHQ hard limit).

        Args:
            underlying: "NIFTY", "BANKNIFTY", etc.
            expiry: "YYYY-MM-DD"

        Returns:
            OptionChainSnapshot with all strikes, LTP, IV, OI, Greeks.
        """
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
        """Fetch daily OHLCV candles.

        Args:
            security_id: DhanHQ security ID.
            exchange_segment: e.g. "NSE_EQ", "IDX_I".
            from_date: "YYYY-MM-DD"
            to_date: "YYYY-MM-DD"
        """
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
        """Fetch intraday (minute-level) OHLCV + OI candles.

        Args:
            security_id: DhanHQ security ID.
            exchange_segment: e.g. "NSE_FNO".
            from_date: "YYYY-MM-DD"
            to_date: "YYYY-MM-DD"
        """
        body = {
            "securityId": str(security_id),
            "exchangeSegment": exchange_segment,
            "instrument": "OPTIDX" if exchange_segment in (NSE_FNO,) else "INDEX",
            "fromDate": from_date,
            "toDate": to_date,
        }
        data = self._post("/charts/intraday", body)
        return self._parse_candles(data, security_id)

    # ── Positions ────────────────────────────────────────────────────

    def positions(self) -> PositionsResponse:
        """Retrieve all open positions for the day."""
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

    # ── Orders ───────────────────────────────────────────────────────

    def place_order(self, proposal: OrderProposal) -> OrderResponse:
        """Place a real order on DhanHQ (LIVE mode only).

        ⚠️  This is the only method that mutates state. Use with caution.
        """
        body = {
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

    # ── Funds ────────────────────────────────────────────────────────

    def funds(self) -> dict:
        """Retrieve available funds and margin details."""
        return self._get("/fundlimit")

    # ── HTTP helpers ─────────────────────────────────────────────────

    def _post(self, path: str, body: dict) -> dict:
        return self._request("POST", path, json=body)

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        return self._request("GET", path, params=params)

    def _request(self, method: str, path: str, **kwargs) -> dict | list:
        """Execute an HTTP request with error mapping."""
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

        return r.json()

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
        """Parse DhanHQ option chain JSON into our normalized model."""
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
        """Parse DhanHQ candle data (both daily and intraday formats)."""
        candle_data = data.get("data", data)
        candles = []

        # Dhan returns candles as arrays: [timestamp, open, high, low, close, volume, oi?]
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
    """Convert a value to float, returning None if missing or invalid."""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _map_opt_type(raw: str) -> str:
    """Normalize Dhan's option type strings to CE/PE."""
    r = raw.upper().strip()
    if r in ("CE", "CALL"):
        return "CE"
    if r in ("PE", "PUT"):
        return "PE"
    return r
