"""Tool-calling loop on local Ollama. Falls back to a rule-based router if the LLM is unreachable."""
import json, os, re
import httpx
import tools as T

OLLAMA = os.getenv("OLLAMA_URL", "http://localhost:11434")
MODEL = os.getenv("LLM_MODEL", "qwen2.5:3b")
SYSTEM = """You are DhanLens, a market analyst assistant for Indian index options.
RULES: Never calculate or guess numbers. Always call a tool, then explain ONLY what the tool returned in 2-4 short sentences.
Default underlying: {u}. Orders are never placed by you: use propose_order and tell the user to press Confirm."""


def _llm(messages, u):
    r = httpx.post(f"{OLLAMA}/api/chat", timeout=120, json={
        "model": MODEL, "stream": False, "tools": T.SCHEMAS, "options": {"temperature": 0},
        "messages": [{"role": "system", "content": SYSTEM.format(u=u)}] + messages})
    r.raise_for_status()
    return r.json()["message"]


def _route(q, u):
    ql = q.lower()
    if "bank" in ql: u = "BANKNIFTY"
    elif "nifty" in ql and "bank" not in ql: u = "NIFTY"
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", ql)
    if re.search(r"risk|position|portfolio|drop|fall|crash", ql) and "pcr" not in ql:
        sh = float(m.group(1)) if m else 2.0
        return "get_portfolio_risk", {"shock_pct": -sh if re.search(r"drop|fall|down|crash", ql) or not m else sh}
    if "pcr" in ql: return "get_pcr_trend", {"underlying": u}
    if "pain" in ql: return "get_max_pain", {"underlying": u}
    if re.search(r"skew|iv|volatil", ql): return "get_iv_skew", {"underlying": u}
    if re.search(r"candle|price|trend|move", ql): return "get_candles", {"underlying": u}
    return "get_option_chain", {"underlying": u}


def run(message, history, u):
    trace, highlights, pending = [], [], None
    msgs = [{"role": h["role"], "content": h["content"]} for h in history[-6:]] + [{"role": "user", "content": message}]
    try:
        for _ in range(4):
            m = _llm(msgs, u)
            calls = m.get("tool_calls") or []
            if not calls:
                return dict(answer=m.get("content", "").strip(), highlights=highlights, tools=trace, pending=pending, mode="llm")
            msgs.append(m)
            for c in calls:
                name, args = c["function"]["name"], c["function"].get("arguments") or {}
                if isinstance(args, str): args = json.loads(args)
                res = T.call(name, args)
                trace.append(name); highlights = res.get("highlight_strikes") or highlights
                if res.get("status") == "PENDING_CONFIRMATION": pending = res["token"]
                slim = {k: v for k, v in res.items() if k != "highlight_strikes"}
                msgs.append({"role": "tool", "content": json.dumps(slim, default=str)})
        return dict(answer="I couldn't finish that; try rephrasing.", highlights=highlights, tools=trace, pending=pending, mode="llm")
    except (httpx.HTTPError, KeyError, ValueError):
        name, args = _route(message, u)
        res = T.call(name, args)
        return dict(answer=res["summary"], highlights=res.get("highlight_strikes", []), tools=[name], pending=None,
                    mode="rule-based (Ollama not reachable)")
