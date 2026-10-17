"""Live Order Update WebSocket client for DhanHQ v2.

Connects to wss://api-order-update.dhan.co, authorizes with RequestCode 42,
and streams real-time order status updates (TRANSIT, PENDING, TRADED, REJECTED, CANCELLED).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import Callable

import websocket

from .config import DhanConfig
from .models import LiveOrderUpdate

log = logging.getLogger(__name__)


class DhanOrderUpdateFeed:
    """Streams live order events directly from DhanHQ order update WebSocket."""

    def __init__(self, config: DhanConfig,
                 on_order_update: Callable[[LiveOrderUpdate], None] | None = None,
                 on_connect: Callable[[], None] | None = None,
                 on_disconnect: Callable[[str], None] | None = None):
        self._cfg = config
        self._on_order_update = on_order_update or (lambda ev: None)
        self._on_connect = on_connect or (lambda: None)
        self._on_disconnect = on_disconnect or (lambda r: None)

        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._recent_updates: list[LiveOrderUpdate] = []
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start listening for live order updates in a daemon thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Order update feed is already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="dhan-order-feed")
        self._thread.start()
        log.info("Live order update feed started")

    def stop(self) -> None:
        """Stop the order update feed."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        log.info("Live order update feed stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def recent_updates(self) -> list[LiveOrderUpdate]:
        with self._lock:
            return list(self._recent_updates)

    def record_update(self, update: LiveOrderUpdate) -> None:
        """Manually or externally record an update (used by postback webhook or simulator)."""
        with self._lock:
            self._recent_updates.insert(0, update)
            if len(self._recent_updates) > 100:
                self._recent_updates.pop()
        try:
            self._on_order_update(update)
        except Exception as exc:
            log.warning("Error in on_order_update callback: %s", exc)

    def _run_loop(self) -> None:
        delay = self._cfg.ws_reconnect_base_delay
        while not self._stop_event.is_set():
            try:
                self._connect_and_listen()
            except Exception as exc:
                log.warning("Order feed connection error: %s", exc)

            if self._stop_event.is_set():
                break

            log.info("Reconnecting order update feed in %.1fs...", delay)
            self._stop_event.wait(delay)
            delay = min(delay * 2, self._cfg.ws_reconnect_max_delay)

    def _connect_and_listen(self) -> None:
        url = self._cfg.ws_order_url

        def on_open(ws):
            log.info("Order update WebSocket connected to %s", url)
            auth_msg = {
                "RequestCode": 42,
                "ClientId": self._cfg.client_id,
                "Token": self._cfg.access_token,
                "UserType": "SELF",
            }
            ws.send(json.dumps(auth_msg))
            self._on_connect()

        def on_message(ws, msg):
            try:
                data = json.loads(msg) if isinstance(msg, str) else json.loads(msg.decode("utf-8"))
            except Exception:
                return

            ev = LiveOrderUpdate(
                order_id=str(data.get("orderId", data.get("order_id", ""))),
                dhan_client_id=str(data.get("dhanClientId", self._cfg.client_id)),
                order_status=str(data.get("orderStatus", data.get("status", "UPDATED"))),
                transaction_type=str(data.get("transactionType", "")),
                exchange_segment=str(data.get("exchangeSegment", "")),
                trading_symbol=str(data.get("tradingSymbol", "")),
                security_id=str(data.get("securityId", "")),
                price=float(data.get("price", 0.0) or 0.0),
                quantity=int(data.get("quantity", 0) or 0),
                filled_qty=int(data.get("filledQty", 0) or 0),
                pending_qty=int(data.get("pendingQty", 0) or 0),
                avg_price=float(data.get("avgPrice", 0.0) or 0.0),
                update_time=datetime.now().strftime("%H:%M:%S"),
                source="WEBSOCKET",
                raw=data if isinstance(data, dict) else {},
            )
            self.record_update(ev)

        def on_error(ws, error):
            log.warning("Order update WS error: %s", error)

        def on_close(ws, close_status_code, close_msg):
            log.info("Order update WS closed (%s: %s)", close_status_code, close_msg)
            self._on_disconnect(str(close_msg))

        self._ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)
