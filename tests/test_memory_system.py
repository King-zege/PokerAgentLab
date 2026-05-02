from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4

import main_api
from memory.consolidator import MemoryConsolidator
from memory.strategy_rag import StrategyRAG
from memory.user_profile import LongTermUserProfile


def _test_path(name: str) -> str:
    path = Path("data/test_memory") / f"{name}_{uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def test_long_term_profile_candidate_accept_search():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    memory = profile.add_candidate("leaks", "User over-calls river spots", ["s1"], 0.7)

    assert profile.search("river", status="accepted") == []
    accepted = profile.set_status(memory.id, "accepted")

    assert accepted is not None
    hits = profile.search("river", status="accepted")
    assert hits and hits[0]["id"] == memory.id


def test_strategy_rag_returns_source_and_chunk_id():
    rag = StrategyRAG(index_path=_test_path("chunks"))
    chunks = rag.search(query="preflop position raise", street="preflop", limit=3)

    assert chunks
    assert "id" in chunks[0]
    assert "source" in chunks[0]


def test_memory_context_uses_fences():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    memory = profile.add_candidate("goals", "Practice blind defense", ["s1"], 0.6)
    profile.set_status(memory.id, "accepted")

    context, ids = profile.build_context("blind defense")

    assert "<user-memory-context>" in context
    assert memory.id in ids


def test_consolidator_does_not_promote_empty_single_hand():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))

    result = MemoryConsolidator(profile).consolidate_session("empty", [], {"hand_reviews": [], "key_findings": []})

    assert result["candidate_memories"] == []
    assert result["training_plan"]


def test_memory_api_profile_and_strategy_search():
    client = TestClient(main_api.app)

    profile = client.get("/memory/profile")
    strategy = client.post("/strategy/search", json={"query": "preflop position", "street": "preflop", "limit": 2})

    assert profile.status_code == 200
    assert strategy.status_code == 200
    assert "chunks" in strategy.json()
