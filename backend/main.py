import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works from repo root or backend/
from collections import deque
import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import analytics as A, tools as T, agent

import pipeline
DB = pipeline.GOLD_DB
if not os.path.exists(DB):
    pipeline.run_all()  # bronze -> silver -> gold
con = duckdb.connect(DB, read_only=True)
T.CTX["con"] = con
app = FastAPI(title="DhanLens AI")
HITS = deque()  # simple rate limit: 20 chat calls / minute
FRONT = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")


class Chat(BaseModel):
    message: str
    underlying: str = "NIFTY"
    asof: str | None = None
    history: list[dict] = []


@app.get("/")
def index(): return FileResponse(FRONT)


@app.get("/api/meta")
def meta():
    ts = A.last_day_stamps(con)
    n = con.execute("select count(*) from chain").fetchone()[0]
    return {"stamps": [t.isoformat() for t in ts], "rows": n, "model": agent.MODEL}


@app.get("/api/chain")
def chain(underlying: str = "NIFTY", asof: str | None = None):
    ts = A.resolve(con, asof)
    s, spot = A.snapshot(con, underlying, ts)
    return {"ts": ts.isoformat(), "spot": spot, "atm": A.atm(s, spot), "pcr": round(A.pcr(s), 3),
            "max_pain": A.max_pain(s), "atm_iv": round(float(s.ce_iv[A.atm(s, spot)]) * 100, 2),
            "strikes": [{"k": int(k), "ce": int(r.ce_oi), "pe": int(r.pe_oi), "cel": r.ce_ltp, "pel": r.pe_ltp}
                        for k, r in s.iterrows()]}


@app.post("/api/chat")
def chat(c: Chat):
    now = time.time()
    while HITS and now - HITS[0] > 60: HITS.popleft()
    if len(HITS) >= 20: raise HTTPException(429, "Rate limit: max 20 questions per minute.")
    HITS.append(now)
    T.CTX.update(asof=c.asof, default_u=c.underlying)
    return agent.run(c.message, c.history, c.underlying)


@app.post("/api/confirm/{token}")
def confirm(token: str): return {"result": T.confirm(token)}
