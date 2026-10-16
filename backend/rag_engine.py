"""RAG & Semantic Embedding Engine for DhanLens AI.
Uses local Ollama 'nomic-embed-text' (768-dim) and cached vector index for:
1. Sub-1ms cached semantic intent routing (maps trader queries to quantitative tools).
2. Options Trading & Risk Strategy Playbook RAG (retrieves verified institutional adjustment playbooks).
"""
import os, re, json
import httpx
import numpy as np

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "embeddings_cache.json")

# 1. CURATED DERIVATIVE STRATEGY & ADJUSTMENT PLAYBOOKS
PLAYBOOKS = [
    {
        "id": "iron_condor_adjustments",
        "title": "Iron Condor Delta & Wing Adjustments",
        "keywords": ["iron condor", "wing adjustment", "condor breached", "tested side"],
        "content": (
            "Iron Condor Defense Rule: When spot tests your short strike (delta of tested short leg reaches 0.30+), "
            "do NOT panic-close. Roll the untested side closer to collect additional credit and neutralize net delta. "
            "If the inner short strike is breached at expiry, close the tested credit spread to cap losses at the wing width, "
            "or roll the entire structure out to the next weekly/monthly expiry for a net credit."
        )
    },
    {
        "id": "short_strangle_management",
        "title": "Short Strangle Delta Neutralization & Defense",
        "keywords": ["strangle adjustment", "short strangle", "roll untested leg", "strangle delta"],
        "content": (
            "Short Strangle Management: Maintain portfolio net delta within ±15 to ±20. When spot rallies and tests your short Call, "
            "your net delta turns negative. Roll up the short Put strike to the 0.20-0.25 delta strike to harvest credit and balance delta. "
            "To prevent undefined tail risk, convert the tested side into an inverted strangle or buy far OTM wings to cap margin spikes."
        )
    },
    {
        "id": "max_pain_expiry_pinning",
        "title": "Max Pain Pinning & Expiry Afternoon Tactics",
        "keywords": ["max pain pin", "expiry pinning", "pin risk", "settlement strike"],
        "content": (
            "Max Pain Settlement Dynamics: After 1:00 PM on expiry day, market makers and institutional option writers aggressively "
            "hedge gamma to pin the underlying index near the Max Pain strike (minimizing cumulative payouts to option buyers). "
            "If spot is within 30-50 points of Max Pain, mean-reversion trades towards the pin strike have high statistical probability. "
            "Avoid holding long OTM options into the final hour due to rapid terminal theta decay."
        )
    },
    {
        "id": "put_skew_tail_hedging",
        "title": "Put Skew Interpretation & Downside Tail Hedging",
        "keywords": ["put skew", "volatility skew", "tail hedge", "rich puts", "iv spread"],
        "content": (
            "Volatility Skew Interpretation: In Indian index derivatives, OTM puts typically trade at higher IV than equidistant OTM calls. "
            "When the 5-strike OTM Put IV exceeds OTM Call IV by more than 3.5 percentage points, institutional desks are actively pricing "
            "downside crash protection. Best response: avoid selling naked puts; instead, use bear put spreads or sell high-IV OTM puts to fund "
            "cheaper ATM debit puts (ratio spread)."
        )
    },
    {
        "id": "pcr_divergence_breakout",
        "title": "PCR & Spot Price Divergence Playbook",
        "keywords": ["pcr divergence", "pcr falling spot rising", "false breakout", "bull trap"],
        "content": (
            "PCR Divergence Alert: If the underlying index spot is making higher highs while the Put-Call Ratio (PCR) is steadily declining, "
            "it signals a classic 'Bull Trap'. Institutional participants are writing calls to supply liquidity and refusing to write put floors. "
            "Expect a sharp reversal or distribution phase. Do not initiate fresh long breakout trades when PCR is declining."
        )
    },
    {
        "id": "gamma_squeeze_defense",
        "title": "Expiry Day Gamma Squeeze & Risk Defense",
        "keywords": ["gamma squeeze", "expiry gamma", "terminal theta", "last hour volatility"],
        "content": (
            "Gamma Squeeze Protection: During the final 2 hours of expiry, ATM gamma spikes exponentially. A small 40-point spot move "
            "can cause a 400% surge in near-the-money option premiums. Risk rule: Never hold naked short ATM options past 2:00 PM on expiry day. "
            "Always transition open exposure into fixed-risk spreads or square off short gamma positions."
        )
    },
    {
        "id": "iv_crush_post_event",
        "title": "IV Crush Post-Event Playbook",
        "keywords": ["iv crush", "event volatility", "rbi policy iv", "budget iv"],
        "content": (
            "IV Crush Mitigation: Implied Volatility expands into major scheduled events (RBI Policy, Union Budget, Election results) and collapses "
            "immediately upon announcement. Option buyers face severe losses even if their directional thesis was correct. Pre-event playbook: "
            "deploy credit spreads (Iron Condors / Calendars) to exploit premium collapse, and avoid buying outright naked calls or puts."
        )
    }
]


class RagEngine:
    def __init__(self):
        self._intent_vectors = {}
        self._playbook_vectors = {}
        self._load_cache()

    def _load_cache(self):
        """Loads precomputed 768-dim embeddings in sub-millisecond time."""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                intents = data.get("intents", {})
                for k, v in intents.items():
                    arr = np.array(v, dtype=np.float32)
                    norm = np.linalg.norm(arr)
                    self._intent_vectors[k] = arr / norm if norm > 0 else arr
                
                playbooks = data.get("playbooks", {})
                pb_by_id = {p["id"]: p for p in PLAYBOOKS}
                for pid, v in playbooks.items():
                    if pid in pb_by_id:
                        arr = np.array(v, dtype=np.float32)
                        norm = np.linalg.norm(arr)
                        self._playbook_vectors[pid] = (pb_by_id[pid], arr / norm if norm > 0 else arr)
            except Exception:
                pass

    def get_embedding(self, text: str) -> np.ndarray | None:
        """Fetch 768-dim embedding from local Ollama nomic-embed-text with tight timeout."""
        try:
            r = httpx.post(
                f"{OLLAMA_URL}/api/embeddings",
                json={"model": EMBED_MODEL, "prompt": text},
                timeout=httpx.Timeout(2.5, connect=0.8)
            )
            if r.status_code == 200:
                data = r.json()
                emb = data.get("embedding")
                if emb:
                    arr = np.array(emb, dtype=np.float32)
                    norm = np.linalg.norm(arr)
                    return arr / norm if norm > 0 else arr
        except Exception:
            pass
        return None

    def route_query(self, query: str, default_u: str = "NIFTY") -> tuple[str, dict, float]:
        """Semantically route a trader query to the most appropriate tool.
        Returns: (tool_name, tool_kwargs, confidence_score)
        """
        # 1. Resolve underlying
        ql = query.lower()
        u = "BANKNIFTY" if "bank" in ql else ("NIFTY" if "nifty" in ql else default_u)

        # 2. Extract shock percentage if applicable
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", ql)
        shock_val = float(m.group(1)) if m else 2.0
        if re.search(r"drop|fall|down|crash|dip", ql) or not m:
            shock_pct = -abs(shock_val)
        else:
            shock_pct = abs(shock_val)

        # 3. If query vector can be computed, use cosine similarity against cached vectors
        q_vec = self.get_embedding(query)
        if q_vec is not None and self._intent_vectors:
            scores = {}
            for tool_name, target_vec in self._intent_vectors.items():
                sim = float(np.dot(q_vec, target_vec))
                scores[tool_name] = sim

            best_tool = max(scores, key=scores.get)
            best_score = scores[best_tool]

            if best_score >= 0.42:
                if best_tool == "get_portfolio_risk":
                    return best_tool, {"shock_pct": shock_pct, "underlying": u}, best_score
                elif best_tool == "get_strategy_playbook":
                    return best_tool, {"query": query}, best_score
                elif best_tool == "get_pcr_trend":
                    return best_tool, {"underlying": u}, best_score
                elif best_tool in ("get_max_pain", "get_iv_skew", "get_option_chain", "get_candles"):
                    return best_tool, {"underlying": u}, best_score

        # 4. Fallback to heuristic keywords if embeddings offline or low similarity
        if re.search(r"playbook|adjust|hedge|iron condor|strangle|squeeze", ql):
            return "get_strategy_playbook", {"query": query}, 0.50
        if re.search(r"risk|position|portfolio|drop|fall|crash", ql) and "pcr" not in ql:
            return "get_portfolio_risk", {"shock_pct": shock_pct, "underlying": u}, 0.50
        if "pcr" in ql:
            return "get_pcr_trend", {"underlying": u}, 0.50
        if "pain" in ql:
            return "get_max_pain", {"underlying": u}, 0.50
        if re.search(r"skew|iv|volatil", ql):
            return "get_iv_skew", {"underlying": u}, 0.50
        if re.search(r"candle|price|trend|move", ql):
            return "get_candles", {"underlying": u}, 0.50

        return "get_option_chain", {"underlying": u}, 0.40

    def search_playbook(self, query: str, top_k: int = 1) -> list[dict]:
        """Retrieve the most relevant strategy playbooks for a trader query."""
        q_vec = self.get_embedding(query)
        if q_vec is not None and self._playbook_vectors:
            scored = []
            for pb_id, (pb, v) in self._playbook_vectors.items():
                sim = float(np.dot(q_vec, v))
                scored.append((sim, pb))
            scored.sort(key=lambda x: x[0], reverse=True)
            return [pb for sim, pb in scored[:top_k]]

        # Fallback to keyword matching
        ql = query.lower()
        matched = []
        for pb in PLAYBOOKS:
            if any(kw in ql for kw in pb["keywords"]):
                matched.append(pb)
        return matched[:top_k] if matched else [PLAYBOOKS[0]]


# Global singleton instance
ENGINE = RagEngine()
