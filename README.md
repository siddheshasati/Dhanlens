# DhanLens AI

**Ask your option chain in plain English. The LLM explains, deterministic tools calculate, and the chart zooms to the strikes that matter.**

Category: AI Workflow (Dhandev Pune #03 showcase). Runs fully local: DuckDB + Ollama, no broker keys needed.

## Architecture

```
 Browser (dashboard + chat)
        |  /api/chat            /api/chain
        v                           v
 FastAPI ---> agent.py (Ollama qwen2.5:3b, tool-calling loop)
                 |  calls only these:
                 v
            tools.py (read-only) ---> analytics.py (PCR, max pain, IV skew, Black-Scholes greeks, stress P&L)
                                          |
                                      data/gold/dhanlens.duckdb  <-  data/silver/*.parquet  <-  data/bronze/*.csv
```
"LLM explains, tools calculate": the model never sees raw data it could do math on, only tool results and a summary.

## Quick start (mock mode)

```bash
pip install -r requirements.txt
ollama pull qwen2.5:3b          # optional: without it the app uses a rule-based router
python backend/pipeline.py          # optional: data/ is already bundled (bronze -> silver -> gold)
uvicorn backend.main:app --reload    # run from repo root (also works: cd backend && uvicorn main:app)
# open http://localhost:8000
```
Set `OLLAMA_URL` / `LLM_MODEL` env vars to change the model. `nomic-embed-text` is not needed yet (reserved for the filings-RAG roadmap item).

## Demo script (5 min)
1. Press **Start live replay**: option chain ticks forward minute by minute.
2. Ask *"Why is Bank Nifty PCR falling?"* -> answer + chart zooms to the strikes driving it.
3. Ask *"Which of my positions has the most risk if Nifty drops 2%?"* -> stress P&L, riskiest strike highlighted.
4. Ask *"Buy 75 Nifty 24500 CE"* -> order is only *proposed*; press **Confirm** for a paper fill.

## Data layout (medallion)
| Layer | Path | Format | What |
|---|---|---|---|
| Bronze | `data/bronze/` | CSV | raw as generated / ingested (`chain_raw.csv`, `candles_raw.csv`, `positions_raw.csv`, `chain_live.csv`) |
| Silver | `data/silver/` | Parquet (zstd) | typed, de-duplicated, validated (oi>0, iv>0, high>=low) |
| Gold | `data/gold/dhanlens.duckdb` | DuckDB | serving tables + `pcr_minute` aggregate; the app reads only this |

Rebuild one stage: `python backend/pipeline.py silver` (or `bronze`, `gold`). Tests: `pip install pytest && pytest -q tests`.
Live ingest (unverified, needs your Dhan keys): `python backend/ingest_live.py NIFTY 2026-10-20` appends to `bronze/chain_live.csv`.

## Tools
`get_option_chain`, `get_pcr_trend`, `get_max_pain`, `get_iv_skew`, `get_candles`, `get_portfolio_risk`, `propose_order`

## Guardrails
- Read-only by default; DuckDB is opened `read_only`.
- No auto-trading: orders need an explicit confirm click and are paper fills only.
- Rate limit: 20 questions/minute. Tool errors never crash the agent loop.
- Fallback to a deterministic router if the LLM is unreachable.

## Roadmap
Swap mock data for the DhanHQ REST/WebSocket client (`dhan_client/`), filings/news RAG with nomic-embed-text, per-tool unit tests.

## Browse the data with DuckDB UI
```bash
duckdb -ui                                   # opens http://localhost:4213
-- then in a notebook cell:
ATTACH 'data/gold/dhanlens.duckdb' AS dl (READ_ONLY);
SELECT * FROM dl.chain LIMIT 100;
```
Use READ_ONLY so it doesn't clash with the running app.
