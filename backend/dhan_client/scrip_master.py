"""Scrip master downloader, cache, and symbol-to-security-ID resolver.

Downloads the Dhan scrip master CSV once per day, caches it locally,
and provides fast lookups: (underlying, strike, opt_type, expiry) → security_id.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from datetime import date, datetime
from pathlib import Path

import httpx

from .config import DhanConfig, SCRIP_MASTER_URL, NSE_FNO, IDX_I
from .exceptions import InstrumentNotFoundError
from .models import ScripEntry

log = logging.getLogger(__name__)

# Cache dir: sits next to the data/ folder at project root
_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / ".scrip_cache"


class ScripMaster:
    """Thread-safe, daily-cached scrip master with fast option contract lookups."""

    def __init__(self, config: DhanConfig, cache_dir: Path | None = None):
        self._config = config
        self._cache_dir = cache_dir or _CACHE_DIR
        self._entries: list[ScripEntry] = []
        # Lookup: (underlying, strike, opt_type, expiry) → security_id
        self._option_map: dict[tuple[str, float, str, str], int] = {}
        # Lookup: security_id → ScripEntry
        self._id_map: dict[int, ScripEntry] = {}
        self._loaded_date: date | None = None

    # ── Public API ───────────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Load (or reload if stale) the scrip master."""
        today = date.today()
        if self._loaded_date == today and self._entries:
            return
        cache_path = self._cache_dir / f"scrip_master_{today.isoformat()}.csv"
        if cache_path.exists():
            log.info("Loading cached scrip master: %s", cache_path)
            self._parse_csv(cache_path.read_text(encoding="utf-8"))
        else:
            log.info("Downloading scrip master from %s", SCRIP_MASTER_URL)
            self._download_and_cache(cache_path)
        self._loaded_date = today
        self._cleanup_old_caches(keep=today)

    def resolve_option(self, underlying: str, strike: float | int, opt_type: str,
                       expiry: str) -> int:
        """Return the security_id for a specific option contract.

        Args:
            underlying: "NIFTY", "BANKNIFTY", etc.
            strike: Strike price (e.g. 24500)
            opt_type: "CE" or "PE"
            expiry: "YYYY-MM-DD"

        Raises:
            InstrumentNotFoundError: If no matching contract exists.
        """
        self.ensure_loaded()
        key = (underlying.upper(), float(strike), opt_type.upper(), expiry)
        sid = self._option_map.get(key)
        if sid is None:
            raise InstrumentNotFoundError(
                f"No security_id for {underlying} {strike} {opt_type} exp {expiry}. "
                f"Check that the expiry date is valid and the scrip master is up-to-date."
            )
        return sid

    def resolve_options_for_chain(self, underlying: str, expiry: str) -> list[ScripEntry]:
        """Return all option contracts for a given underlying and expiry."""
        self.ensure_loaded()
        u = underlying.upper()
        return [
            e for e in self._entries
            if e.underlying == u and e.expiry == expiry and e.opt_type in ("CE", "PE")
        ]

    def get_entry(self, security_id: int) -> ScripEntry | None:
        """Look up full scrip details by security_id."""
        self.ensure_loaded()
        return self._id_map.get(security_id)

    def nearest_expiry(self, underlying: str) -> str:
        """Return the nearest option expiry (YYYY-MM-DD) that hasn't passed yet."""
        self.ensure_loaded()
        u = underlying.upper()
        today_str = date.today().isoformat()
        expiries = sorted({
            e.expiry for e in self._entries
            if e.underlying == u and e.opt_type in ("CE", "PE") and e.expiry >= today_str
        })
        if not expiries:
            raise InstrumentNotFoundError(f"No future expiries found for {underlying}")
        return expiries[0]

    # ── Internal ─────────────────────────────────────────────────────

    def _download_and_cache(self, cache_path: Path) -> None:
        """Download the CSV from Dhan and write to local cache."""
        r = httpx.get(SCRIP_MASTER_URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(r.text, encoding="utf-8")
        log.info("Scrip master cached: %s (%d bytes)", cache_path, len(r.text))
        self._parse_csv(r.text)

    def _parse_csv(self, text: str) -> None:
        """Parse the compact scrip master CSV into lookup structures."""
        reader = csv.DictReader(io.StringIO(text))
        entries: list[ScripEntry] = []
        option_map: dict[tuple[str, float, str, str], int] = {}
        id_map: dict[int, ScripEntry] = {}

        for row in reader:
            try:
                seg = row.get("SEM_EXM_EXCH_ID", "").strip()
                # We only care about NSE F&O and index instruments for option chains
                if seg not in (NSE_FNO, IDX_I, "NSE_FNO", "IDX_I", "NSE"):
                    continue

                sid = int(row.get("SEM_SMST_SECURITY_ID", 0) or row.get("security_id", 0))
                if sid == 0:
                    continue

                # Normalize fields — CSV column names vary between compact and detailed
                symbol = (row.get("SEM_CUSTOM_SYMBOL", "") or row.get("symbol", "")).strip()
                trading_sym = (row.get("SEM_TRADING_SYMBOL", "") or row.get("trading_symbol", "")).strip()
                underlying = (row.get("SEM_INSTRUMENT_NAME", "") or
                              row.get("underlying", "") or
                              _extract_underlying(trading_sym)).strip().upper()
                opt_type = (row.get("SEM_OPTION_TYPE", "") or row.get("opt_type", "")).strip().upper()
                strike_raw = row.get("SEM_STRIKE_PRICE", 0) or row.get("strike", 0)
                strike = float(strike_raw) if strike_raw else 0.0
                expiry_raw = row.get("SEM_EXPIRY_DATE", "") or row.get("expiry", "")
                expiry = _normalize_date(expiry_raw)
                lot_raw = row.get("SEM_LOT_UNITS", 1) or row.get("lot_size", 1)
                tick_raw = row.get("SEM_TICK_SIZE", 0.05) or row.get("tick_size", 0.05)

                entry = ScripEntry(
                    security_id=sid,
                    exchange_segment=seg,
                    symbol=symbol,
                    trading_symbol=trading_sym,
                    underlying=underlying,
                    strike=strike,
                    opt_type=opt_type if opt_type in ("CE", "PE") else "",
                    expiry=expiry,
                    lot_size=int(float(lot_raw)),
                    tick_size=float(tick_raw),
                )
                entries.append(entry)
                id_map[sid] = entry

                if entry.opt_type in ("CE", "PE") and entry.expiry:
                    key = (entry.underlying, entry.strike, entry.opt_type, entry.expiry)
                    option_map[key] = sid

            except (ValueError, KeyError) as exc:
                log.debug("Skipping scrip master row: %s — %s", row, exc)
                continue

        self._entries = entries
        self._option_map = option_map
        self._id_map = id_map
        log.info("Scrip master parsed: %d entries, %d option contracts", len(entries), len(option_map))

    def _cleanup_old_caches(self, keep: date) -> None:
        """Remove cached CSVs older than `keep`."""
        if not self._cache_dir.exists():
            return
        keep_name = f"scrip_master_{keep.isoformat()}.csv"
        for f in self._cache_dir.iterdir():
            if f.name.startswith("scrip_master_") and f.name != keep_name:
                f.unlink(missing_ok=True)
                log.debug("Removed stale scrip cache: %s", f.name)


def _extract_underlying(trading_symbol: str) -> str:
    """Best-effort extraction of underlying name from a trading symbol like 'NIFTY26OCT24500CE'."""
    for idx_name in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX", "BANKEX"):
        if trading_symbol.upper().startswith(idx_name):
            return idx_name
    return ""


def _normalize_date(raw: str) -> str:
    """Convert various date formats to YYYY-MM-DD."""
    raw = raw.strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw  # return as-is if we can't parse
