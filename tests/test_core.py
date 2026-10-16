import os, sys
import duckdb, pandas as pd, pytest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import analytics as A, tools as T, pipeline


@pytest.fixture(scope="module")
def con():
    if not os.path.exists(pipeline.GOLD_DB): pipeline.run_all()
    c = duckdb.connect(pipeline.GOLD_DB, read_only=True); T.CTX["con"] = c
    return c


def test_put_call_parity():
    S, K, Tm, iv = 24500, 24600, 0.02, 0.14
    c, p = A.bs(S, K, Tm, iv, "CE")["price"], A.bs(S, K, Tm, iv, "PE")["price"]
    assert abs((c - p) - (S - K * pow(2.718281828, -A.R * Tm))) < 0.5


def test_max_pain_toy():
    s = pd.DataFrame({"ce_oi": [100, 10, 10], "pe_oi": [10, 10, 100]}, index=[100, 200, 300])
    assert A.max_pain(s) == 200


def test_silver_gold_row_counts_match(con):
    n_silver = duckdb.connect().execute(f"select count(*) from read_parquet('{pipeline.SILVER.replace(chr(92), '/')}/chain.parquet')").fetchone()[0]
    assert n_silver == con.execute("select count(*) from chain").fetchone()[0] > 300_000


@pytest.mark.parametrize("name,args", [("get_option_chain", {}), ("get_pcr_trend", {"underlying": "BANKNIFTY"}),
    ("get_max_pain", {}), ("get_iv_skew", {}), ("get_candles", {}), ("get_portfolio_risk", {"shock_pct": -2})])
def test_tools_return_summary_and_highlights(con, name, args):
    r = T.call(name, args)
    assert "error" not in r and r["summary"] and "highlight_strikes" in r


def test_order_is_never_executed_without_confirm(con):
    r = T.call("propose_order", {"strike": 24500, "opt_type": "CE", "side": "BUY", "qty": 75})
    assert r["status"] == "PENDING_CONFIRMATION" and not T.BLOTTER
    assert "PAPER" in T.confirm(r["token"]) and len(T.BLOTTER) == 1
