"""Tests for backend/dhan_client/ — mock client, models, config, scrip master, and client factory."""
import os, sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dhan_client import (
    get_client, DhanClient, DhanConfig, MockClient, DhanRestClient,
    OptionChainSnapshot, OptionStrike, CandleResponse, PositionsResponse,
    OrderProposal, OrderResponse, MockModeError,
)
from dhan_client.config import INDEX_SECURITY_IDS, WS_SEGMENT_CODES, OPTION_CHAIN_INTERVAL_S
from dhan_client.models import Tick, ScripEntry, Position


# ── Config ───────────────────────────────────────────────────────────

class TestConfig:
    def test_defaults_to_mock(self):
        cfg = DhanConfig()
        assert cfg.is_mock
        assert not cfg.is_live

    def test_live_requires_credentials(self):
        cfg = DhanConfig(mode="live", client_id="", access_token="")
        with pytest.raises(ValueError, match="DHAN_CLIENT_ID"):
            cfg.validate()

    def test_live_with_credentials(self):
        cfg = DhanConfig(mode="live", client_id="abc", access_token="xyz")
        cfg.validate()  # should not raise
        assert cfg.is_live

    def test_index_security_ids(self):
        assert INDEX_SECURITY_IDS["NIFTY"] == 13
        assert INDEX_SECURITY_IDS["BANKNIFTY"] == 25
        assert "FINNIFTY" in INDEX_SECURITY_IDS


# ── Models ───────────────────────────────────────────────────────────

class TestModels:
    def test_option_strike_defaults(self):
        s = OptionStrike(strike=24500, opt_type="CE")
        assert s.ltp == 0.0
        assert s.oi == 0
        assert s.delta is None

    def test_tick_model(self):
        t = Tick(security_id=49081, ltp=125.50, volume=1000, oi=50000)
        assert t.security_id == 49081
        assert t.exchange_segment == ""

    def test_scrip_entry(self):
        e = ScripEntry(security_id=12345, exchange_segment="NSE_FNO",
                       underlying="NIFTY", strike=24500, opt_type="CE",
                       expiry="2026-10-20", lot_size=75)
        assert e.lot_size == 75


# ── MockClient ───────────────────────────────────────────────────────

class TestMockClient:
    @pytest.fixture(scope="class")
    def client(self):
        c = MockClient()
        yield c
        c.close()

    def test_option_chain_returns_snapshot(self, client):
        snap = client.option_chain("NIFTY")
        assert isinstance(snap, OptionChainSnapshot)
        assert snap.underlying == "NIFTY"
        assert snap.spot > 20000
        assert len(snap.strikes) > 20

    def test_option_chain_banknifty(self, client):
        snap = client.option_chain("BANKNIFTY")
        assert snap.underlying == "BANKNIFTY"
        assert snap.spot > 40000

    def test_strikes_have_ce_and_pe(self, client):
        snap = client.option_chain("NIFTY")
        types = {s.opt_type for s in snap.strikes}
        assert "CE" in types and "PE" in types

    def test_candles(self, client):
        res = client.candles_intraday(underlying="NIFTY")
        assert isinstance(res, CandleResponse)
        assert len(res.candles) > 0
        assert res.candles[0].open > 0

    def test_positions(self, client):
        pos = client.positions()
        assert isinstance(pos, PositionsResponse)
        assert len(pos.positions) > 0
        assert pos.positions[0].underlying in ("NIFTY", "BANKNIFTY")

    def test_paper_order(self, client):
        proposal = OrderProposal(
            underlying="NIFTY", strike=24500, opt_type="CE",
            side="BUY", qty=75, price=120.0,
        )
        resp = client.place_order(proposal)
        assert isinstance(resp, OrderResponse)
        assert resp.status == "PAPER_FILL"
        assert len(client.blotter) >= 1

    def test_funds_mock(self, client):
        f = client.funds()
        assert f["availableBalance"] > 0

    def test_time_control(self, client):
        ts0 = client.current_ts
        ts1 = client.advance(5)
        assert ts1 > ts0 or client._current_idx == len(client._timestamps) - 1

    def test_set_time_by_index(self, client):
        client.set_time(idx=0)
        assert client._current_idx == 0
        client.set_time(idx=100)
        assert client._current_idx == 100


# ── DhanClient (Façade) ──────────────────────────────────────────────

class TestDhanClientFacade:
    def test_get_client_returns_mock_by_default(self):
        c = get_client()
        assert c.mock is not None
        assert isinstance(c.mock, MockClient)
        c.close()

    def test_option_chain_via_facade(self):
        c = get_client()
        snap = c.option_chain("NIFTY")
        assert snap.underlying == "NIFTY"
        assert len(snap.strikes) > 0
        c.close()

    def test_positions_via_facade(self):
        c = get_client()
        pos = c.positions()
        assert len(pos.positions) > 0
        c.close()

    def test_feed_raises_in_mock_mode(self):
        c = get_client()
        with pytest.raises(MockModeError):
            c.start_feed()
        c.close()

    def test_scrip_is_none_in_mock(self):
        c = get_client()
        assert c.scrip is None
        c.close()
