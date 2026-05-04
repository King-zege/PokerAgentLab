from fastapi.testclient import TestClient
from pathlib import Path
from uuid import uuid4

import main_api
from agent.observation import Observation
from engine.card import Card, Rank, Suit
from memory.consolidator import MemoryConsolidator
from memory.hand_history import ActionRecord, HandHistory
from memory.memory_manager_agent import MemoryManagerAgent
from memory.strategy_rag import StrategyRAG
from memory.temporary_memory import TemporaryMemoryStore
from memory.user_profile import LongTermUserProfile
from evaluation.rag_eval import compute_retrieval_metrics, load_rag_dataset, run_rag_evaluation
from evaluation.system_eval import run_system_evaluation


def _test_path(name: str) -> str:
    path = Path("data/test_memory") / f"{name}_{uuid4().hex}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _history_with_actions(session_id: str, actions: list[str]) -> HandHistory:
    records = [
        ActionRecord(
            street="river",
            seat_index=0,
            player_id="Hero",
            action=action,
            action_amount=1.0,
            stack_before_bb=100.0,
            pot_before_bb=10.0,
            explanation="pytest",
            position_name="BTN",
            style="human",
        )
        for action in actions
    ]
    return HandHistory(
        hand_id=f"{session_id}_h1",
        timestamp="2026-01-01T00:00:00",
        table_size=2,
        button_index=0,
        small_blind_bb=0.5,
        big_blind_bb=1.0,
        players=[{"id": "Hero", "style": "human", "stack_bb": 100}],
        hole_cards={"Hero": ["As", "Ah"]},
        community_cards=["2c", "7d", "9h", "Ts", "Kd"],
        actions=records,
        pots=[],
        final_stacks={"Hero": 100.0},
    )


def test_long_term_profile_candidate_accept_search():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    memory = profile.add_candidate("leaks", "User over-calls river spots", ["s1"], 0.7)

    assert profile.search("river", status="accepted") == []
    accepted = profile.set_status(memory.id, "accepted")

    assert accepted is not None
    hits = profile.search("river", status="accepted")
    assert hits and hits[0]["id"] == memory.id


def test_long_term_profile_rejected_memory_is_not_reactivated():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    memory = profile.add_candidate("leaks", "User over-calls river spots", ["s1"], 0.7)
    profile.set_status(memory.id, "rejected")

    updated = profile.upsert_memory("leaks", "User over-calls river spots", ["s2"], 0.9, status="accepted")

    assert updated.id == memory.id
    assert updated.status == "rejected"
    assert profile.search("river", status="accepted") == []


def test_strategy_rag_returns_source_and_chunk_id():
    rag = StrategyRAG(index_path=_test_path("chunks"))
    chunks = rag.search(query="preflop position raise", street="preflop", limit=3)

    assert chunks
    assert "id" in chunks[0]
    assert "source" in chunks[0]
    assert "matched_terms" in chunks[0]
    assert "matched_tags" in chunks[0]
    assert "score_breakdown" in chunks[0]
    assert "reason" in chunks[0]


def test_strategy_rag_preflop_observation_matches_hand_class():
    rag = StrategyRAG(index_path=_test_path("chunks"))
    obs = Observation(
        player_id="Hero",
        style="tag",
        hole_cards=[Card(Rank.ACE, Suit.SPADES), Card(Rank.ACE, Suit.HEARTS)],
        stack_bb=100,
        seat_index=0,
        button_index=0,
        num_players=2,
        position_name="BTN",
        street="preflop",
        community_cards=[],
        pot_bb=1.5,
        current_bet_to_call_bb=1,
        min_raise_bb=2,
        max_raise_bb=100,
        actions_this_street=[],
        active_opponents=1,
        spr=66.6,
    )

    chunks = rag.search_for_observation(obs, limit=3)

    assert chunks
    assert chunks[0]["id"] == "preflop_premium_pair_value"
    assert "premium_pair" in chunks[0]["matched_tags"]
    assert chunks[0]["score_breakdown"]["hand_class"] > 0


def test_strategy_rag_late_position_suited_connector_beats_early_position_chunk():
    rag = StrategyRAG(index_path=_test_path("chunks"))
    chunks = rag.search(
        query="preflop BTN suited_connector first_in",
        street="preflop",
        limit=3,
        hand_class="suited_connector",
        position="BTN",
        action_tags=["first_in"],
    )

    assert chunks
    assert chunks[0]["id"] == "preflop_late_position_wide_range"
    assert "BTN" in chunks[0]["matched_tags"]
    assert "suited_connector" in chunks[0]["matched_tags"]


def test_strategy_rag_postflop_low_spr_matches_spr_baseline():
    rag = StrategyRAG(index_path=_test_path("chunks"))
    obs = Observation(
        player_id="Hero",
        style="balanced",
        hole_cards=[Card(Rank.ACE, Suit.SPADES), Card(Rank.KING, Suit.SPADES)],
        stack_bb=20,
        seat_index=0,
        button_index=0,
        num_players=2,
        position_name="BB",
        street="flop",
        community_cards=[Card(Rank.ACE, Suit.CLUBS), Card(Rank.SEVEN, Suit.HEARTS), Card(Rank.TWO, Suit.DIAMONDS)],
        pot_bb=8,
        current_bet_to_call_bb=4,
        min_raise_bb=8,
        max_raise_bb=20,
        actions_this_street=[],
        active_opponents=1,
        spr=2.5,
    )

    chunks = rag.search_for_observation(obs, limit=3)

    assert chunks
    assert any(c["id"] == "postflop_low_spr_commitment" for c in chunks)
    low_spr = next(c for c in chunks if c["id"] == "postflop_low_spr_commitment")
    assert "low_spr" in low_spr["matched_tags"]


def test_strategy_rag_rebuilds_unreadable_index():
    path = Path(_test_path("broken_chunks"))
    path.write_text("{not valid json", encoding="utf-8")
    rag = StrategyRAG(index_path=str(path))

    chunks = rag.search(query="preflop premium_pair", street="preflop", hand_class="premium_pair", limit=1)

    assert chunks
    assert rag.last_fallback_reason == "strategy index was unreadable; rebuilt from source files"


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


def test_memory_manager_agent_auto_accepts_high_confidence_repeated_leak():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    temp = TemporaryMemoryStore(user_id="pytest", filepath=_test_path("temporary"))
    agent = MemoryManagerAgent(profile, temp, auto_accept_threshold=0.82)

    report = agent.run_session(
        "pytest_memory_agent_high",
        histories=[_history_with_actions("high", ["call", "call", "call", "call"])],
        coach_result={"leak_candidates": []},
        focus_player_id="Hero",
        force=True,
    )

    assert any(action["type"] == "accepted" for action in report["actions"])
    accepted = profile.list_memories(status="accepted")
    assert accepted
    assert "calling tendency" in accepted[0].content


def test_memory_manager_agent_stores_low_confidence_goal_as_temporary():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    temp = TemporaryMemoryStore(user_id="pytest", filepath=_test_path("temporary"))
    agent = MemoryManagerAgent(profile, temp, promote_hits=3)

    report = agent.run_session(
        "pytest_memory_agent_temp",
        histories=[_history_with_actions("temp", ["check"])],
        coach_result={"leak_candidates": []},
        focus_player_id="Hero",
        force=True,
    )

    assert any(action["type"] == "temporary" for action in report["actions"])
    assert temp.list_memories(status="temporary")
    assert profile.list_memories(status="accepted") == []


def test_temporary_memory_promotes_after_repeated_hits():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    temp = TemporaryMemoryStore(user_id="pytest", filepath=_test_path("temporary"))
    agent = MemoryManagerAgent(profile, temp, promote_hits=2)

    for session_id in ("pytest_promote_1", "pytest_promote_2"):
        agent.run_session(
            session_id,
            histories=[_history_with_actions(session_id, ["check"])],
            coach_result={"leak_candidates": []},
            focus_player_id="Hero",
            force=True,
        )

    assert temp.list_memories(status="promoted")
    assert profile.list_memories(status="candidate")


def test_temporary_memory_rejects_after_misses_and_handles_broken_file():
    path = Path(_test_path("temporary"))
    temp = TemporaryMemoryStore(user_id="pytest", filepath=str(path))
    memory = temp.upsert("leaks", "User calls too much on river", ["s1"], 0.6)

    updates = temp.mark_misses(["unrelated finding"], archive_misses=1)

    assert updates
    assert temp.get(memory.id).status == "rejected"

    path.write_text("{broken json", encoding="utf-8")
    broken = TemporaryMemoryStore(user_id="pytest", filepath=str(path))
    assert broken.list_memories() == []
    assert "temporary memory unavailable" in broken.last_fallback_reason


def test_memory_manager_agent_archives_stale_accepted_memory():
    profile = LongTermUserProfile(user_id="pytest", filepath=_test_path("profile"))
    temp = TemporaryMemoryStore(user_id="pytest", filepath=_test_path("temporary"))
    stale = profile.upsert_memory("leaks", "User over-calls river spots", ["old"], 0.56, status="accepted")
    agent = MemoryManagerAgent(profile, temp, temporary_threshold=0.55)

    report = agent.run_session(
        "pytest_stale_archive",
        histories=[_history_with_actions("stale", ["check"])],
        coach_result={"leak_candidates": []},
        focus_player_id="Hero",
        force=True,
    )

    assert report["archived_memories"]
    archived = profile.list_memories(status="archived")
    assert archived and archived[0].id == stale.id


def test_memory_api_profile_and_strategy_search():
    client = TestClient(main_api.app)

    profile = client.get("/memory/profile")
    strategy = client.post("/strategy/search", json={"query": "preflop position", "street": "preflop", "limit": 2})

    assert profile.status_code == 200
    assert strategy.status_code == 200
    assert "chunks" in strategy.json()
    first = strategy.json()["chunks"][0]
    assert "score_breakdown" in first
    assert "reason" in first


def test_rag_eval_metrics_handle_hits_and_empty_results():
    metrics = compute_retrieval_metrics(
        retrieved_ids=["wrong", "preflop_premium_pair_value"],
        relevant_ids=["preflop_premium_pair_value"],
        top_k=3,
    )

    assert metrics["hit_at_1"] == 0.0
    assert metrics["hit_at_3"] == 1.0
    assert metrics["precision_at_k"] == 0.3333
    assert metrics["recall_at_k"] == 1.0
    assert metrics["mrr"] == 0.5

    empty = compute_retrieval_metrics([], ["preflop_premium_pair_value"], top_k=3)
    assert empty["hit_at_1"] == 0.0
    assert empty["recall_at_k"] == 0.0


def test_rag_eval_dataset_missing_fields_returns_clear_error():
    path = Path(_test_path("bad_rag_dataset"))
    path.write_text('{"query":"preflop"}\n', encoding="utf-8")

    try:
        load_rag_dataset(str(path))
    except ValueError as exc:
        assert "Missing required fields" in str(exc)
    else:
        raise AssertionError("Expected ValueError for missing relevant_chunk_ids")


def test_rag_evaluation_generates_precision_recall_report():
    run_id = f"pytest_rag_{uuid4().hex[:8]}"

    report = run_rag_evaluation(top_k=3, run_id=run_id)

    assert report["run_id"] == run_id
    assert report["kind"] == "rag"
    assert report["metrics"]["hit_at_3"] > 0
    assert "precision_at_k" in report["metrics"]
    assert Path(report["report_path"]).exists()
    assert Path(report["markdown_path"]).exists()


def test_system_evaluation_generates_usefulness_signal_report():
    run_id = f"pytest_system_{uuid4().hex[:8]}"

    report = run_system_evaluation(num_hands=1, seed=9, variants=["baseline"], run_id=run_id)

    assert report["run_id"] == run_id
    assert report["kind"] == "system"
    assert report["variants"][0]["variant"] == "baseline"
    assert "trace_coverage" in report["variants"][0]["trace_metrics"]
    assert report["variants"][0]["coach_metrics"]["has_training_plan"] is True
