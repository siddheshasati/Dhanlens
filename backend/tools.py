"""Deterministic, read-only tools the LLM may call. Each returns data + a ready 'summary'."""
import uuid
import numpy as np
import analytics as A

CTX = {"con": None, "asof": None, "default_u": "NIFTY"}
PENDING, BLOTTER = {}, []
U = {"type": "string", "enum": ["NIFTY", "BANKNIFTY"], "description": "Index underlying"}


def _env(u):
    u = (u or CTX["default_u"]).upper().replace(" ", "")
    u = "BANKNIFTY" if "BANK" in u else "NIFTY"
    ts = A.resolve(CTX["con"], CTX["asof"])
    snap, spot = A.snapshot(CTX["con"], u, ts)
    return u, ts, snap, spot


def get_option_chain(underlying=None):
    u, ts, s, spot = _env(underlying)
    a = A.atm(s, spot)
    cw, pw = int(s.ce_oi.idxmax()), int(s.pe_oi.idxmax())
    return dict(underlying=u, spot=round(spot, 2), atm=a, pcr=round(A.pcr(s), 3), max_pain=A.max_pain(s),
                call_wall=cw, put_wall=pw, highlight_strikes=[cw, pw],
                summary=f"{u} spot {spot:.0f}, ATM {a}, PCR {A.pcr(s):.2f}. Biggest call OI at {cw} (resistance), "
                        f"biggest put OI at {pw} (support). Max pain {A.max_pain(s)}.")


def get_pcr_trend(underlying=None, hours=6):
    u, ts, s, spot = _env(underlying)
    series = A.pcr_series(CTX["con"], u, ts, int(hours))
    dce, dpe = A.oi_change(CTX["con"], u, ts, min(int(hours), 6))
    top_ce = [int(k) for k in dce.abs().sort_values(ascending=False).index[:2]]
    top_pe = [int(k) for k in dpe.abs().sort_values(ascending=False).index[:2]]
    chg = series[-1][1] - series[0][1]
    d = "falling" if chg < 0 else "rising"
    drv = "put OI being unwound" if dpe.sum() < dce.sum() else "call OI building faster than puts"
    return dict(underlying=u, series=series, change=round(chg, 3), highlight_strikes=sorted(set(top_ce + top_pe)),
                call_oi_change=int(dce.sum()), put_oi_change=int(dpe.sum()),
                summary=f"{u} PCR {d}: {series[0][1]} -> {series[-1][1]} over {hours}h. Net call OI change "
                        f"{int(dce.sum()):+,}, put OI change {int(dpe.sum()):+,} ({drv}). "
                        f"Largest OI shifts at calls {top_ce} and puts {top_pe}.")


def get_max_pain(underlying=None):
    u, ts, s, spot = _env(underlying)
    mp = A.max_pain(s)
    return dict(underlying=u, max_pain=mp, spot=round(spot, 2), highlight_strikes=[mp],
                summary=f"{u} max pain is {mp}; spot is {spot:.0f} ({spot - mp:+.0f} pts from it).")


def get_iv_skew(underlying=None):
    u, ts, s, spot = _env(underlying)
    a = A.atm(s, spot); st = A.STEP[u]
    lo, hi = a - 5 * st, a + 5 * st
    skew = float(s.pe_iv.get(lo, np.nan) - s.ce_iv.get(hi, np.nan)) * 100
    return dict(underlying=u, atm_iv=round(float(s.ce_iv[a]) * 100, 2), put_minus_call_iv_pts=round(skew, 2),
                highlight_strikes=[lo, a, hi],
                summary=f"{u} ATM IV {s.ce_iv[a]*100:.1f}%. OTM put ({lo}) IV minus OTM call ({hi}) IV = {skew:+.1f} pts "
                        f"({'puts richer: downside hedging demand' if skew > 0 else 'calls richer'}).")


def get_candles(underlying=None, interval_min=30, last_n=8):
    u, ts, s, spot = _env(underlying)
    df = CTX["con"].execute(
        f"""select time_bucket(to_minutes({int(interval_min)}), ts) b, first(open order by ts) o, max(high) h,
            min(low) l, last(close order by ts) c from candles where underlying=? and ts<=? group by b
            order by b desc limit {int(last_n)}""", [u, ts]).df().iloc[::-1]
    rows = [dict(t=str(r.b)[5:16], o=r.o, h=r.h, l=r.l, c=r.c) for r in df.itertuples()]
    move = rows[-1]["c"] - rows[0]["o"]
    return dict(underlying=u, candles=rows, highlight_strikes=[],
                summary=f"{u} last {len(rows)} x {interval_min}m candles: moved {move:+.0f} pts "
                        f"({rows[0]['o']:.0f} -> {rows[-1]['c']:.0f}).")


def get_portfolio_risk(shock_pct=-2.0, underlying=None):
    con, ts = CTX["con"], A.resolve(CTX["con"], CTX["asof"])
    pos = con.execute("select * from positions").df()
    rows, T = [], A.tte(ts)
    for p in pos.itertuples():
        snap, spot = A.snapshot(con, p.underlying, ts)
        col = "ce" if p.opt_type == "CE" else "pe"
        ltp, iv = float(snap[f"{col}_ltp"][p.strike]), float(snap[f"{col}_iv"][p.strike])
        g = A.bs(spot, p.strike, T, iv, p.opt_type)
        shocked = A.bs(spot * (1 + float(shock_pct) / 100), p.strike, T, iv, p.opt_type)["price"]
        rows.append(dict(underlying=p.underlying, strike=int(p.strike), type=p.opt_type, qty=int(p.qty),
                         pnl_now=round((ltp - p.avg_price) * p.qty), pnl_shock=round((shocked - ltp) * p.qty),
                         delta=round(g["delta"] * p.qty, 1), vega=round(g["vega"] * p.qty)))
    if underlying: rows = [r for r in rows if r["underlying"] == _env(underlying)[0]]
    rows.sort(key=lambda r: r["pnl_shock"])
    tot = sum(r["pnl_shock"] for r in rows)
    w = rows[0]
    return dict(shock_pct=shock_pct, total_shock_pnl=tot, positions=rows[:5],
                highlight_strikes=[w["strike"]], underlying=w["underlying"],
                summary=f"If spot moves {shock_pct}%, portfolio P&L changes by {tot:+,} INR. Riskiest position: "
                        f"{w['underlying']} {w['strike']}{w['type']} qty {w['qty']} -> {w['pnl_shock']:+,} INR. "
                        f"Net delta {sum(r['delta'] for r in rows):+.0f}.")


def propose_order(underlying=None, strike=0, opt_type="CE", side="BUY", qty=75):
    u, ts, s, spot = _env(underlying)
    ltp = float(s[f"{opt_type.lower()}_ltp"][int(strike)])
    tok = uuid.uuid4().hex[:8]
    PENDING[tok] = dict(underlying=u, strike=int(strike), type=opt_type, side=side.upper(), qty=int(qty), price=ltp)
    return dict(status="PENDING_CONFIRMATION", token=tok, highlight_strikes=[int(strike)], underlying=u,
                summary=f"Order NOT placed. Proposed: {side.upper()} {qty} {u} {strike}{opt_type} @ ~{ltp:.2f}. "
                        f"Waiting for explicit user confirmation.")


def confirm(token):
    o = PENDING.pop(token, None)
    if not o: return "Unknown or already-handled order token."
    BLOTTER.append(o)  # paper fill only
    return f"PAPER fill: {o['side']} {o['qty']} {o['underlying']} {o['strike']}{o['type']} @ {o['price']:.2f}"


def get_strategy_playbook(query=""):
    from rag_engine import ENGINE
    matches = ENGINE.search_playbook(query, top_k=1)
    pb = matches[0] if matches else {"title": "Risk Defense", "content": "Maintain delta neutrality and cap wing risk."}
    return dict(
        playbook_title=pb["title"],
        rule=pb["content"],
        highlight_strikes=[],
        summary=f"Strategy Playbook [{pb['title']}]: {pb['content']}"
    )


def _s(props, req=()): return {"type": "object", "properties": props, "required": list(req)}
REG = {
    "get_option_chain": (get_option_chain, "Snapshot: spot, ATM, PCR, max pain, OI walls", _s({"underlying": U})),
    "get_pcr_trend": (get_pcr_trend, "PCR trend over last N hours and which strikes drive the change (use for 'why is PCR falling')",
                      _s({"underlying": U, "hours": {"type": "integer"}})),
    "get_max_pain": (get_max_pain, "Max pain strike", _s({"underlying": U})),
    "get_iv_skew": (get_iv_skew, "IV skew / ATM IV", _s({"underlying": U})),
    "get_candles": (get_candles, "Recent OHLC candles for price trend",
                    _s({"underlying": U, "interval_min": {"type": "integer"}, "last_n": {"type": "integer"}})),
    "get_portfolio_risk": (get_portfolio_risk, "Stress-test the user's option positions for a % spot move (negative = fall)",
                           _s({"shock_pct": {"type": "number", "description": "e.g. -2 for a 2% drop"}, "underlying": U})),
    "get_strategy_playbook": (get_strategy_playbook, "Retrieve institutional derivative adjustment rules and risk defense playbooks (Iron Condors, Strangles, Skew, Gamma squeeze)",
                              _s({"query": {"type": "string", "description": "Strategy or adjustment query"}})),
    "propose_order": (propose_order, "Propose (never execute) an order; needs user confirmation",
                      _s({"underlying": U, "strike": {"type": "integer"}, "opt_type": {"type": "string", "enum": ["CE", "PE"]},
                          "side": {"type": "string", "enum": ["BUY", "SELL"]}, "qty": {"type": "integer"}}, ["strike", "opt_type", "side"])),
}
SCHEMAS = [{"type": "function", "function": {"name": n, "description": d, "parameters": p}} for n, (_, d, p) in REG.items()]


def call(name, args):
    if name not in REG: return {"error": f"unknown tool {name}", "highlight_strikes": []}
    fn = REG[name][0]
    try:
        return fn(**{k: v for k, v in (args or {}).items() if k in REG[name][2]["properties"]})
    except Exception as e:  # keep the agent loop alive
        return {"error": str(e), "highlight_strikes": [], "summary": f"Tool {name} failed: {e}"}
