import time
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

import main_api
from agent.llm_agent import LLMAgent
from agent.observation import Observation
from engine.action import Action, ActionType
from engine.card import Card, Rank, Suit
from engine.game import Game
from engine.pot import PotWinner
from api.game_runner import _build_last_hand_result
from analysis.coach_agent import CoachAgent
from analysis.analysis_agent import AnalysisAgent
from strategy.style_profile import StyleRegistry
from memory.hand_history import ActionRecord, HandHistory
from memory.decision_trace import DecisionTrace, DecisionTraceStore


def test_non_interactive_session_generates_history_and_traces():
    session_id = "pytest_cli"
    game = Game("config/game_config.yaml", session_id=session_id)
    game.history_store.clear()
    game.trace_store.clear()

    results = game.play_session(num_hands=1, interactive=False)

    assert len(results) == 1
    assert len(game.history_store.load_all()) == 1
    assert len(game.trace_store.load_all()) >= 1


def test_decision_trace_store_reads_incremental_lines():
    session_id = f"pytest_trace_{uuid4().hex}"
    store = DecisionTraceStore.for_session(session_id)
    store.clear()

    store.save(DecisionTrace(session_id=session_id, hand_id="h1", street="preflop", player_id="Alice", observation={}, legal_actions=[], chosen_action="call"))
    traces, next_line = store.load_since_line(0)

    assert len(traces) == 1
    assert traces[0]["player_id"] == "Alice"

    store.save(DecisionTrace(session_id=session_id, hand_id="h1", street="preflop", player_id="Bob", observation={}, legal_actions=[], chosen_action="fold"))
    traces, next_line = store.load_since_line(next_line)

    assert len(traces) == 1
    assert traces[0]["player_id"] == "Bob"
    assert next_line == 2


def test_trace_stream_returns_sse_trace_event():
    client = TestClient(main_api.app)
    session_id = f"pytest_sse_{uuid4().hex}"
    store = DecisionTraceStore.for_session(session_id)
    store.clear()
    store.save(DecisionTrace(session_id=session_id, hand_id="h1", street="flop", player_id="Alice", observation={}, legal_actions=[], chosen_action="bet 2BB"))

    with client.stream("GET", f"/sessions/{session_id}/trace-stream?once=true") as response:
        assert response.status_code == 200
        text = next(response.iter_text())

    assert "event: trace" in text
    assert '"player_id":"Alice"' in text


def test_trace_stream_empty_session_sends_keepalive():
    client = TestClient(main_api.app)

    with client.stream("GET", f"/sessions/missing_{uuid4().hex}/trace-stream?once=true") as response:
        assert response.status_code == 200
        text = next(response.iter_text())

    assert ": keep-alive" in text


def test_last_hand_result_reveals_hole_cards_only_at_showdown():
    result = SimpleNamespace(
        hand_id="h_showdown",
        pot_total_bb=200,
        community_cards=[
            Card(Rank.ACE, Suit.SPADES),
            Card(Rank.KING, Suit.HEARTS),
            Card(Rank.QUEEN, Suit.CLUBS),
            Card(Rank.JACK, Suit.DIAMONDS),
            Card(Rank.TEN, Suit.SPADES),
        ],
        final_seats=[
            {
                "player_id": "Human",
                "stack_bb": 0,
                "position_name": "BTN/SB",
                "folded": False,
                "all_in": True,
                "is_active": True,
                "hole_cards": [Card(Rank.TWO, Suit.SPADES), Card(Rank.THREE, Suit.SPADES)],
            },
            {
                "player_id": "Alice",
                "stack_bb": 200,
                "position_name": "BB",
                "folded": False,
                "all_in": True,
                "is_active": True,
                "hole_cards": [Card(Rank.ACE, Suit.CLUBS), Card(Rank.ACE, Suit.DIAMONDS)],
            },
        ],
        winners=[PotWinner(seat_index=1, amount_bb=200, hand_name="Pair")],
    )

    payload = _build_last_hand_result(result)

    assert payload["showdown"] is True
    assert len(payload["community_cards"]) == 5
    assert payload["players"][0]["hole_cards"]
    assert payload["players"][1]["hole_cards"]


def test_last_hand_result_hides_hole_cards_when_everyone_else_folds():
    result = SimpleNamespace(
        hand_id="h_fold",
        pot_total_bb=3,
        community_cards=[],
        final_seats=[
            {
                "player_id": "Human",
                "stack_bb": 98,
                "position_name": "BTN/SB",
                "folded": True,
                "all_in": False,
                "is_active": True,
                "hole_cards": [Card(Rank.TWO, Suit.SPADES), Card(Rank.THREE, Suit.SPADES)],
            },
            {
                "player_id": "Alice",
                "stack_bb": 102,
                "position_name": "BB",
                "folded": False,
                "all_in": False,
                "is_active": True,
                "hole_cards": [Card(Rank.ACE, Suit.CLUBS), Card(Rank.ACE, Suit.DIAMONDS)],
            },
        ],
        winners=[PotWinner(seat_index=1, amount_bb=3, hand_name="Last player")],
    )

    payload = _build_last_hand_result(result)

    assert payload["showdown"] is False
    assert all("hole_cards" not in player for player in payload["players"])


def test_coach_review_returns_training_report_without_deviations():
    history = HandHistory(
        hand_id="h_review",
        timestamp="2026-05-02 20:00:00",
        table_size=2,
        button_index=0,
        small_blind_bb=0.5,
        big_blind_bb=1,
        players=[
            {"id": "Human", "style": "human", "stack_bb": 100},
            {"id": "Alice", "style": "llm", "stack_bb": 100},
        ],
        hole_cards={"Human": ["As", "Kd"], "Alice": ["Qs", "Qd"]},
        community_cards=["2s", "7h", "Jc", "4d", "9s"],
        actions=[
            ActionRecord(
                street="preflop",
                seat_index=0,
                player_id="Human",
                action="call",
                action_amount=1,
                stack_before_bb=100,
                pot_before_bb=1.5,
                explanation="",
                position_name="BTN/SB",
                style="human",
            )
        ],
        pots=[{"amount_bb": 2, "winners": [{"player": "Human", "hand": "High Card"}]}],
        final_stacks={"Human": 101, "Alice": 99},
    )

    coach = CoachAgent(AnalysisAgent(StyleRegistry("config/styles")))
    result = coach.review_session([history], focus_player_id="Human")

    assert result["total_hands"] == 1
    assert result["report_title"] == "Poker Agent Lab 训练报告"
    assert result["summary"]["total_actions"] == 1
    assert result["summary"]["showdown_hands"] == 1
    assert result["action_profile"][0]["label"] == "跟注"
    assert any("动作样本" in finding for finding in result["key_findings"])
    assert any("跟注=1" in finding for finding in result["key_findings"])
    assert result["training_goals"]
    assert result["training_plan"]
    assert result["next_drill"]["hands"] >= 10
    assert result["hand_reviews"][0]["action_count"] == 1


def test_coach_review_returns_empty_training_report_without_history():
    coach = CoachAgent(AnalysisAgent(StyleRegistry("config/styles")))
    result = coach.review_session([], focus_player_id="Human")

    assert result["total_hands"] == 0
    assert result["summary"]["sample_note"] == "暂无可复盘样本。"
    assert result["action_profile"] == []
    assert result["street_profile"] == []
    assert result["training_plan"][0]["title"] == "扩大样本到 10 手牌"
    assert "至少一手牌" in result["key_findings"][0]


def test_api_session_reaches_waiting_state_and_accepts_action():
    client = TestClient(main_api.app)
    session_id = f"pytest_api_{int(time.time() * 1000)}"

    response = client.post(
        "/sessions",
        json={
            "session_id": session_id,
            "mode": "fixed",
            "num_hands": 1,
            "config_path": "config/game_config.yaml",
        },
    )
    assert response.status_code == 200

    state = {}
    for _ in range(20):
        state = client.get(f"/sessions/{session_id}/state").json()
        if state["status"] == "waiting_for_action" and state["legal_actions"]:
            break
        time.sleep(0.1)

    assert state["status"] == "waiting_for_action"
    action = state["legal_actions"][0]
    action_response = client.post(
        f"/sessions/{session_id}/action",
        json={"action": action["type"], "amount": action.get("min") or action.get("amount") or 0},
    )
    assert action_response.status_code == 200


def test_api_continue_advances_to_next_hand_and_clears_completed_actions():
    client = TestClient(main_api.app)
    session_id = f"pytest_continue_{int(time.time() * 1000)}"

    response = client.post(
        "/sessions",
        json={
            "session_id": session_id,
            "mode": "fixed",
            "num_hands": 2,
            "config_path": "config/game_config.yaml",
        },
    )
    assert response.status_code == 200

    state = {}
    for _ in range(30):
        state = client.get(f"/sessions/{session_id}/state").json()
        if state["status"] == "waiting_for_action" and state["legal_actions"]:
            break
        time.sleep(0.1)

    fold = next(a for a in state["legal_actions"] if a["type"] == "fold")
    action_response = client.post(
        f"/sessions/{session_id}/action",
        json={"action": fold["type"], "amount": 0},
    )
    assert action_response.status_code == 200

    completed = {}
    for _ in range(30):
        completed = client.get(f"/sessions/{session_id}/state").json()
        if completed["hand_complete"]:
            break
        time.sleep(0.1)

    assert completed["hand_complete"] is True
    assert completed["can_continue"] is True
    assert completed["legal_actions"] == []

    continue_response = client.post(
        f"/sessions/{session_id}/continue",
        json={"continue_game": True},
    )
    assert continue_response.status_code == 200

    next_state = {}
    for _ in range(30):
        next_state = client.get(f"/sessions/{session_id}/state").json()
        if next_state["current_hand"] == 2 and (next_state["status"] == "waiting_for_action" or next_state["hand_complete"]):
            break
        time.sleep(0.1)

    assert next_state["current_hand"] == 2
    if next_state["hand_complete"]:
        assert next_state["legal_actions"] == []
        assert next_state["can_continue"] is True
    else:
        assert next_state["legal_actions"]


def test_llm_action_parser_falls_back_to_legal_action():
    agent = LLMAgent("Tester")
    legal = [Action(ActionType.FOLD), Action(ActionType.CALL)]
    obs = Observation(
        player_id="Tester",
        style="llm",
        hole_cards=[Card(Rank.ACE, Suit.SPADES), Card(Rank.KING, Suit.SPADES)],
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

    assert agent._parse_action('{"a":"not_real"}', legal, obs) is None
    parsed = agent._parse_action('{"a":"call"}', legal, obs)
    assert parsed == Action(ActionType.CALL)


def test_self_play_api_generates_report_with_action_distribution():
    client = TestClient(main_api.app)
    experiment_id = f"pytest_selfplay_{uuid4().hex[:8]}"

    response = client.post(
        "/experiments/self-play",
        json={"experiment_id": experiment_id, "num_hands": 2, "seed": 7},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["experiment_id"] == experiment_id
    assert payload["num_hands"] == 2
    assert payload["seed"] == 7
    assert payload["summary"]

    first_player = next(iter(payload["summary"].values()))
    assert "bb_per_100" in first_player
    assert "vpip" in first_player
    assert "pfr" in first_player
    assert "aggression_factor" in first_player
    assert "action_distribution" in first_player

    report_response = client.get(f"/experiments/{experiment_id}/report")
    assert report_response.status_code == 200
    assert report_response.json()["experiment_id"] == experiment_id

