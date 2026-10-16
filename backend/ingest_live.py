"""UNVERIFIED against the live DhanHQ API (no keys/network in my sandbox). Appends one real option-chain
snapshot to bronze/chain_live.csv in the same schema as chain_raw.csv. Check field names against Dhan docs first.
Usage: DHAN_CLIENT_ID=.. DHAN_ACCESS_TOKEN=.. python backend/ingest_live.py NIFTY 2026-10-20"""
import csv, os, sys, time
from datetime import datetime
import httpx
from pipeline import BRONZE

SCRIP = {"NIFTY": 13, "BANKNIFTY": 25}  # IDX_I security ids


def snapshot(u, expiry):
    r = httpx.post("https://api.dhan.co/v2/optionchain", timeout=20,
                   headers={"access-token": os.environ["DHAN_ACCESS_TOKEN"], "client-id": os.environ["DHAN_CLIENT_ID"]},
                   json={"UnderlyingScrip": SCRIP[u], "UnderlyingSeg": "IDX_I", "Expiry": expiry})
    r.raise_for_status()
    d = r.json()["data"]; ts = datetime.now().replace(second=0, microsecond=0)
    for k, v in d["oc"].items():
        for side, typ in (("ce", "CE"), ("pe", "PE")):
            if side in v:
                x = v[side]
                yield [ts, u, int(float(k)), typ, x.get("last_price", 0), (x.get("implied_volatility") or 0) / 100,
                       x.get("oi", 0), x.get("volume", 0), d["last_price"]]


if __name__ == "__main__":
    u, exp = sys.argv[1], sys.argv[2]
    path = os.path.join(BRONZE, "chain_live.csv"); new = not os.path.exists(path)
    os.makedirs(BRONZE, exist_ok=True)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new: w.writerow(["ts", "underlying", "strike", "opt_type", "ltp", "iv", "oi", "volume", "spot"])
        w.writerows(snapshot(u, exp))
    print("appended snapshot; Dhan option-chain limit is ~1 request / 3 s")
