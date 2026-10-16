"""Unit tests for the RAG & Semantic Embedding Engine."""
import pytest
from rag_engine import ENGINE, PLAYBOOKS
import tools as T


def test_rag_playbooks_loaded():
    assert len(PLAYBOOKS) >= 7
    pb = ENGINE.search_playbook("Iron Condor adjustments")
    assert len(pb) > 0
    assert "Iron Condor" in pb[0]["title"]


def test_rag_semantic_routing_fallback():
    tool, args, score = ENGINE.route_query("Why is Bank Nifty PCR falling?")
    assert tool == "get_pcr_trend"
    assert args.get("underlying") == "BANKNIFTY"


def test_rag_strategy_playbook_tool():
    res = T.call("get_strategy_playbook", {"query": "How to defend my short strangle?"})
    assert "error" not in res
    assert "Short Strangle" in res.get("playbook_title", "")
    assert "delta" in res.get("rule", "").lower()
