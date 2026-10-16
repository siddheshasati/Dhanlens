"""Deterministic analytics. The LLM never does math: everything numeric lives here."""
import math
from datetime import datetime
import pandas as pd

R = 0.065
EXPIRY = datetime(2026, 10, 20, 15, 30)
STEP = {"NIFTY": 50, "BANKNIFTY": 100}


def ncdf(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def npdf(x): return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def tte(ts):
    return max((EXPIRY - ts).total_seconds() / (365 * 86400), 1e-6)


def bs(S, K, T, iv, typ):
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (R + iv * iv / 2) * T) / sq
    d2 = d1 - sq
    term1 = -(S * npdf(d1) * iv) / (2 * math.sqrt(T))
    if typ == "CE":
        price, delta = S * ncdf(d1) - K * math.exp(-R * T) * ncdf(d2), ncdf(d1)
        theta = (term1 - R * K * math.exp(-R * T) * ncdf(d2)) / 365
    else:
        price, delta = K * math.exp(-R * T) * ncdf(-d2) - S * ncdf(-d1), ncdf(d1) - 1
        theta = (term1 + R * K * math.exp(-R * T) * ncdf(-d2)) / 365
    return dict(price=price, delta=delta, gamma=npdf(d1) / (S * sq), vega=S * npdf(d1) * math.sqrt(T) / 100, theta=theta)


def last_day_stamps(con):
    d = con.execute("select max(ts)::date from candles").fetchone()[0]
    return [t for (t,) in con.execute(
        "select ts from candles where underlying='NIFTY' and ts::date=? order by ts", [d]).fetchall()]


def resolve(con, asof=None):
    if asof:
        return pd.Timestamp(asof).to_pydatetime()
    return con.execute("select max(ts) from candles").fetchone()[0]


def snapshot(con, u, ts):
    df = con.execute("select strike,opt_type,ltp,iv,oi,volume,spot from chain where underlying=? and ts=?", [u, ts]).df()
    ce = df[df.opt_type == "CE"].set_index("strike")
    pe = df[df.opt_type == "PE"].set_index("strike")
    out = pd.DataFrame({"ce_oi": ce.oi, "pe_oi": pe.oi, "ce_ltp": ce.ltp, "pe_ltp": pe.ltp,
                        "ce_iv": ce.iv, "pe_iv": pe.iv, "ce_vol": ce.volume, "pe_vol": pe.volume}).sort_index()
    return out, float(df.spot.iloc[0])


def atm(snap, spot):
    return int(min(snap.index, key=lambda k: abs(k - spot)))


def pcr(snap): return float(snap.pe_oi.sum() / snap.ce_oi.sum())


def max_pain(snap):
    import numpy as np
    ks = snap.index.to_numpy(); ce = snap.ce_oi.to_numpy(); pe = snap.pe_oi.to_numpy()
    pain = [(ce * np.clip(k - ks, 0, None)).sum() + (pe * np.clip(ks - k, 0, None)).sum() for k in ks]
    return int(ks[int(np.argmin(pain))])


def max_pain_curve(snap):
    import numpy as np
    ks = snap.index.to_numpy(); ce = snap.ce_oi.to_numpy(); pe = snap.pe_oi.to_numpy()
    pain = [(ce * np.clip(k - ks, 0, None)).sum() + (pe * np.clip(ks - k, 0, None)).sum() for k in ks]
    return [{"strike": int(k), "loss": int(p)} for k, p in zip(ks, pain)]


def pcr_series(con, u, ts, hours=6, points=12):
    df = con.execute(
        """select ts, pcr from pcr_minute  -- gold table
           where underlying=? and ts<=? and ts>=?-to_hours(?) order by ts""",
        [u, ts, ts, hours]).df()
    if len(df) > points:
        df = df.iloc[[int(i * (len(df) - 1) / (points - 1)) for i in range(points)]]
    return [(t.strftime("%d %b %H:%M"), round(float(p), 3)) for t, p in zip(df.ts, df.pcr)]


def oi_change(con, u, ts, hours=3):
    now, _ = snapshot(con, u, ts)
    t0 = ts - pd.Timedelta(hours=hours)
    prev_ts = con.execute("select max(ts) from chain where underlying=? and ts<=?", [u, t0]).fetchone()[0]
    if prev_ts is None:
        prev_ts = con.execute("select min(ts) from chain where underlying=?", [u]).fetchone()[0]
    prev, _ = snapshot(con, u, prev_ts)
    return (now.ce_oi - prev.ce_oi), (now.pe_oi - prev.pe_oi)
