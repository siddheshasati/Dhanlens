"""Create ~300k+ rows of dummy option-chain history in DuckDB (no Dhan keys needed)."""
import math, os
import numpy as np, pandas as pd
from analytics import EXPIRY, STEP, bs

DAYS = ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"]
START = {"NIFTY": 24500.0, "BANKNIFTY": 52000.0}
NK = 20  # strikes each side of ATM
rng = np.random.default_rng(7)


def stamps():
    out = []
    for d in DAYS:
        out += list(pd.date_range(f"{d} 09:15", f"{d} 15:29", freq="1min"))
    return pd.DatetimeIndex(out)


def build():
    """Return (chain, candles, positions) DataFrames of dummy market data."""
    ts = stamps(); n = len(ts)
    erf = np.vectorize(math.erf)
    chain_frames, candle_frames = [], []
    for u, s0 in START.items():
        step = STEP[u]
        lr = np.cumsum(rng.normal(0, 0.00028, n)); lr = lr - lr[-1] * np.linspace(0, 1, n) - np.linspace(0, 0.004, n)  # bridge: ends ~0.4% below start
        spot = s0 * np.exp(lr)
        base = round(s0 / step) * step
        strikes = base + step * np.arange(-NK, NK + 1)
        dist = (strikes - s0) / s0
        smile = 0.13 + 2.2 * dist**2 - 0.6 * dist  # put-skew
        T = np.array([max((EXPIRY - t).total_seconds() / (365 * 86400), 1e-6) for t in ts])
        for typ in ("CE", "PE"):
            # OI: peaks at a few "wall" strikes, random-walk through time
            wall = np.exp(-((strikes - (s0 + (1 if typ == "CE" else -1) * 6 * step)) / (4 * step))**2)
            oi0 = 4e5 * (0.3 + wall) * rng.lognormal(0, .25, len(strikes))
            drift = (-1 if (typ == "PE" and u == "BANKNIFTY") else 1) * rng.uniform(0, 30, len(strikes))
            noise = np.cumsum(rng.normal(0, 900, (n, len(strikes))), axis=0)
            oi = np.clip(oi0 + noise + np.outer(np.arange(n), drift), 5e3, None).astype(np.int64)
            S = spot[:, None]; K = strikes[None, :]; iv = smile[None, :] + rng.normal(0, .004, (n, 1))
            Tm = T[:, None]; sq = iv * np.sqrt(Tm)
            d1 = (np.log(S / K) + (0.065 + iv**2 / 2) * Tm) / sq; d2 = d1 - sq
            Nf = lambda x: 0.5 * (1 + erf(x / math.sqrt(2)))
            ltp = S * Nf(d1) - K * np.exp(-0.065 * Tm) * Nf(d2) if typ == "CE" else \
                  K * np.exp(-0.065 * Tm) * Nf(-d2) - S * Nf(-d1)
            vol = (oi * rng.uniform(0.01, 0.08, oi.shape)).astype(np.int64)
            chain_frames.append(pd.DataFrame({
                "ts": np.repeat(ts.values, len(strikes)), "underlying": u,
                "strike": np.tile(strikes, n).astype(int), "opt_type": typ,
                "ltp": np.round(ltp.ravel(), 2), "iv": np.round(iv.ravel() * np.ones_like(ltp).ravel(), 4),
                "oi": oi.ravel(), "volume": vol.ravel(), "spot": np.round(np.repeat(spot, len(strikes)), 2)}))
        o = spot * (1 + rng.normal(0, 3e-5, n)); c = spot
        candle_frames.append(pd.DataFrame({"ts": ts, "underlying": u, "open": o.round(2),
            "high": (np.maximum(o, c) * 1.0002).round(2), "low": (np.minimum(o, c) * .9998).round(2),
            "close": c.round(2), "volume": rng.integers(2e4, 9e4, n)}))
    chain = pd.concat(chain_frames, ignore_index=True)
    candles = pd.concat(candle_frames, ignore_index=True)
    pos = pd.DataFrame([  # signed qty: + long, - short (units, not lots)
        ("NIFTY", 24500, "CE", -150, 118.0), ("NIFTY", 24300, "PE", -150, 96.0),
        ("NIFTY", 24800, "CE", 75, 41.0), ("NIFTY", 24000, "PE", 150, 22.0),
        ("BANKNIFTY", 52000, "CE", -60, 420.0), ("BANKNIFTY", 51500, "PE", -60, 365.0),
        ("BANKNIFTY", 52500, "CE", 30, 190.0), ("BANKNIFTY", 51000, "PE", 90, 140.0)],
        columns=["underlying", "strike", "opt_type", "qty", "avg_price"])
    return chain, candles, pos


