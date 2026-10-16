"""WebSocket live market feed client for DhanHQ v2.

Connects to wss://api-feed.dhan.co, subscribes to option contracts by security_id,
parses binary tick packets, and delivers Tick objects via a callback.

Features:
- Automatic reconnection with exponential backoff
- Batch subscription (≤100 instruments per packet, ≤5000 total)
- Three feed modes: LTP, QUOTE (bid/ask/volume), FULL (depth + OI)
- Thread-safe start/stop
"""
from __future__ import annotations

import json
import logging
import struct
import threading
import time
from datetime import datetime
from typing import Callable

from .config import (
    DhanConfig, WS_MAX_INSTRUMENTS_PER_SUB, WS_MAX_INSTRUMENTS_TOTAL,
    WS_SEGMENT_CODES,
)
from .exceptions import WebSocketDisconnected, WebSocketError
from .models import Tick

log = logging.getLogger(__name__)

# ─── Feed Modes ──────────────────────────────────────────────────────
MODE_LTP = 11       # LTP only
MODE_QUOTE = 12     # LTP + bid/ask + volume
MODE_FULL = 13      # Full depth + OI

# ─── Binary Packet Layout (per DhanHQ docs) ─────────────────────────
# Header:  1 byte (feed response code) + 1 byte (message length type)
# Tick:    Security ID (4 bytes), LTP (4 bytes, float), ...
# Layout varies by mode. We parse the common fields and skip depth rows.

_TICK_HEADER_SIZE = 8    # response_code (1) + num_packets (1) + reserved (6)


class DhanWebSocketFeed:
    """Live market feed over DhanHQ WebSocket.

    Usage::

        feed = DhanWebSocketFeed(config, on_tick=my_callback)
        feed.start()
        feed.subscribe([("NSE_FNO", 49081), ("NSE_FNO", 49082)], mode=MODE_FULL)
        ...
        feed.stop()
    """

    def __init__(self, config: DhanConfig,
                 on_tick: Callable[[Tick], None] | None = None,
                 on_connect: Callable[[], None] | None = None,
                 on_disconnect: Callable[[str], None] | None = None):
        self._cfg = config
        self._on_tick = on_tick or (lambda t: None)
        self._on_connect = on_connect or (lambda: None)
        self._on_disconnect = on_disconnect or (lambda r: None)

        self._ws = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._subscribed: set[tuple[str, int]] = set()  # (exchange_segment, security_id)
        self._retry_count = 0

    # ── Lifecycle ────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket connection in a background thread."""
        if self._thread and self._thread.is_alive():
            log.warning("Feed already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="dhan-ws-feed")
        self._thread.start()
        log.info("WebSocket feed thread started")

    def stop(self) -> None:
        """Gracefully shut down the feed."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        log.info("WebSocket feed stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Subscription ─────────────────────────────────────────────────

    def subscribe(self, instruments: list[tuple[str, int]], mode: int = MODE_FULL) -> None:
        """Subscribe to instruments on the live feed.

        Args:
            instruments: List of (exchange_segment, security_id) tuples.
            mode: MODE_LTP, MODE_QUOTE, or MODE_FULL.

        Raises:
            WebSocketError: If total subscriptions exceed 5000.
        """
        new_set = set(instruments)
        combined = self._subscribed | new_set
        if len(combined) > WS_MAX_INSTRUMENTS_TOTAL:
            raise WebSocketError(
                f"Cannot subscribe to {len(combined)} instruments (max {WS_MAX_INSTRUMENTS_TOTAL})"
            )

        # Batch into chunks of 100
        to_add = list(new_set - self._subscribed)
        for i in range(0, len(to_add), WS_MAX_INSTRUMENTS_PER_SUB):
            batch = to_add[i:i + WS_MAX_INSTRUMENTS_PER_SUB]
            self._send_subscribe(batch, mode)

        self._subscribed = combined

    def unsubscribe(self, instruments: list[tuple[str, int]]) -> None:
        """Unsubscribe from instruments."""
        remove = set(instruments)
        self._subscribed -= remove
        if self._ws:
            self._send_json({
                "RequestCode": 16,  # Unsubscribe
                "InstrumentCount": len(remove),
                "InstrumentList": [
                    {"ExchangeSegment": seg, "SecurityId": str(sid)}
                    for seg, sid in remove
                ],
            })

    # ── Internal ─────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main loop: connect, receive, reconnect on failure."""
        try:
            import websocket as ws_lib  # websocket-client library
        except ImportError:
            log.error("websocket-client not installed. Run: pip install websocket-client")
            return

        while not self._stop_event.is_set():
            try:
                url = (
                    f"{self._cfg.ws_url}"
                    f"?version=2"
                    f"&token={self._cfg.access_token}"
                    f"&clientId={self._cfg.client_id}"
                    f"&authType=2"
                )
                log.info("Connecting to DhanHQ WebSocket feed...")
                self._ws = ws_lib.WebSocket()
                self._ws.connect(url, timeout=10)
                self._retry_count = 0
                log.info("WebSocket connected")
                self._on_connect()

                # Resubscribe to previously subscribed instruments
                if self._subscribed:
                    self.subscribe(list(self._subscribed))

                # Receive loop
                while not self._stop_event.is_set():
                    try:
                        data = self._ws.recv()
                    except ws_lib.WebSocketTimeoutException:
                        continue
                    except ws_lib.WebSocketConnectionClosedException:
                        raise WebSocketDisconnected("Connection closed by server")

                    if isinstance(data, bytes):
                        self._handle_binary(data)
                    elif isinstance(data, str):
                        self._handle_text(data)

            except WebSocketDisconnected as exc:
                log.warning("WebSocket disconnected: %s", exc)
                self._on_disconnect(str(exc))
            except Exception as exc:
                log.error("WebSocket error: %s", exc)
                self._on_disconnect(str(exc))

            if self._stop_event.is_set():
                break

            # Exponential backoff reconnect
            self._retry_count += 1
            if self._retry_count > self._cfg.ws_reconnect_max_retries:
                log.error("Max reconnection attempts (%d) exceeded", self._cfg.ws_reconnect_max_retries)
                break

            delay = min(
                self._cfg.ws_reconnect_base_delay * (2 ** (self._retry_count - 1)),
                self._cfg.ws_reconnect_max_delay,
            )
            log.info("Reconnecting in %.1fs (attempt %d/%d)",
                     delay, self._retry_count, self._cfg.ws_reconnect_max_retries)
            self._stop_event.wait(delay)

    def _send_subscribe(self, instruments: list[tuple[str, int]], mode: int) -> None:
        """Send a subscribe JSON packet."""
        self._send_json({
            "RequestCode": mode,
            "InstrumentCount": len(instruments),
            "InstrumentList": [
                {"ExchangeSegment": seg, "SecurityId": str(sid)}
                for seg, sid in instruments
            ],
        })

    def _send_json(self, payload: dict) -> None:
        """Send a JSON message over the WebSocket."""
        if self._ws is None:
            log.warning("Cannot send: WebSocket not connected")
            return
        try:
            self._ws.send(json.dumps(payload))
        except Exception as exc:
            log.error("Failed to send WebSocket message: %s", exc)

    def _handle_text(self, data: str) -> None:
        """Handle a text (JSON) message from the server — usually ack/error."""
        try:
            msg = json.loads(data)
            log.debug("WS text message: %s", msg)
        except json.JSONDecodeError:
            log.warning("Unparseable WS text: %s", data[:200])

    def _handle_binary(self, data: bytes) -> None:
        """Parse binary tick packets from DhanHQ feed.

        Binary layout (per Dhan docs, v2):
        - Bytes 0-1: response code (uint8), number of packets (uint8)
        - Each packet: security_id(4B int), exchange_seg(1B), ltp(4B float),
                        ... additional fields depend on mode.

        We parse the common LTP fields; full depth parsing can be extended later.
        """
        if len(data) < _TICK_HEADER_SIZE:
            return

        try:
            offset = 0
            response_code = data[offset]; offset += 1
            num_packets = data[offset]; offset += 1
            offset += 6  # reserved bytes

            for _ in range(num_packets):
                if offset + 26 > len(data):
                    break

                # Common fields (present in all modes)
                security_id = struct.unpack_from("<I", data, offset)[0]; offset += 4
                seg_code = data[offset]; offset += 1
                ltp = struct.unpack_from("<f", data, offset)[0]; offset += 4
                close = struct.unpack_from("<f", data, offset)[0]; offset += 4
                open_price = struct.unpack_from("<f", data, offset)[0]; offset += 4
                high = struct.unpack_from("<f", data, offset)[0]; offset += 4
                low = struct.unpack_from("<f", data, offset)[0]; offset += 4
                volume = struct.unpack_from("<I", data, offset)[0]; offset += 4

                # OI (if mode >= QUOTE, next 4 bytes)
                oi = 0
                if offset + 4 <= len(data):
                    oi = struct.unpack_from("<I", data, offset)[0]; offset += 4

                # Map segment code back to string
                seg_name = next(
                    (name for name, code in WS_SEGMENT_CODES.items() if code == seg_code),
                    f"SEG_{seg_code}",
                )

                tick = Tick(
                    security_id=security_id,
                    exchange_segment=seg_name,
                    ltp=round(ltp, 2),
                    volume=volume,
                    oi=oi,
                    high=round(high, 2),
                    low=round(low, 2),
                    open=round(open_price, 2),
                    close=round(close, 2),
                    ts=datetime.now(),
                )
                self._on_tick(tick)

        except struct.error as exc:
            log.warning("Binary parse error at offset %d: %s", offset, exc)
        except Exception as exc:
            log.error("Unexpected error parsing tick data: %s", exc)
