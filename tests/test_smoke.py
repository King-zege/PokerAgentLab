import time

from fastapi.testclient import TestClient

import main_api
from agent.llm_agent import LLMAgent
from agent.observation import Observation
from engine.action import Action, ActionType
from engine.card import Card, Rank, Suit
from engine.game import Game


def test_non_interactive_session_generates_history_and_traces():
    session_id = "pytest_cli"
    game = Game("config/game_config.yaml", session_id=session_id)
    game.history_store.clear()
    game.trace_store.clear()

    results = game.play_session(num_hands=1, interactive=False)

    assert len(results) == 1
    assert len(game.history_store.load_all()) == 1
    assert len(game.trace_store.load_all()) >= 1


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
