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
                                      DuckDB: chain (307k rows), candles, positions
```
"LLM explains, tools calculate": the model never sees raw data it could do math on, only tool results and a summary.

## Quick start (mock mode)

```bash
pip install -r requirements.txt
ollama pull qwen2.5:3b          # optional: without it the app uses a rule-based router
cd backend && python generate.py && uvicorn main:app --reload
# open http://localhost:8000
```
Set `OLLAMA_URL` / `LLM_MODEL` env vars to change the model. `nomic-embed-text` is not needed yet (reserved for the filings-RAG roadmap item).

## Demo script (5 min)
1. Press **Start live replay**: option chain ticks forward minute by minute.
2. Ask *"Why is Bank Nifty PCR falling?"* -> answer + chart zooms to the strikes driving it.
3. Ask *"Which of my positions has the most risk if Nifty drops 2%?"* -> stress P&L, riskiest strike highlighted.
4. Ask *"Buy 75 Nifty 24500 CE"* -> order is only *proposed*; press **Confirm** for a paper fill.

## Tools
`get_option_chain`, `get_pcr_trend`, `get_max_pain`, `get_iv_skew`, `get_candles`, `get_portfolio_risk`, `propose_order`

## Guardrails
- Read-only by default; DuckDB is opened `read_only`.
- No auto-trading: orders need an explicit confirm click and are paper fills only.
- Rate limit: 20 questions/minute. Tool errors never crash the agent loop.
- Fallback to a deterministic router if the LLM is unreachable.

## Roadmap
Swap mock data for the DhanHQ REST/WebSocket client (`dhan_client/`), filings/news RAG with nomic-embed-text, per-tool unit tests.
