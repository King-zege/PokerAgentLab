"""Game runner - manages background game execution with queue-based human agent."""

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Dict

from engine.game import Game
from api.agents.async_human_agent import QueueHumanAgent
from api.session import session_store


def _is_showdown_result(result) -> bool:
    """A real showdown has five board cards and at least two non-folded players."""
    non_folded = [
        seat
        for seat in result.final_seats
        if seat.get("is_active", True) and not seat.get("folded", False)
    ]
    return len(result.community_cards) == 5 and len(non_folded) >= 2


def _build_last_hand_result(result: object) -> dict:
    """Build API-safe hand result, exposing hole cards only at showdown."""
    is_showdown = _is_showdown_result(result)
    players = []
    for seat in result.final_seats:
        player = {
            "id": seat["player_id"],
            "stack_bb": seat["stack_bb"],
            "position": seat.get("position_name", ""),
            "folded": seat.get("folded", False),
            "all_in": seat.get("all_in", False),
        }
        if is_showdown and not seat.get("folded", False):
            player["hole_cards"] = [str(c) for c in seat.get("hole_cards", [])]
        players.append(player)

    return {
        "hand_id": result.hand_id,
        "pot_total_bb": result.pot_total_bb,
        "community_cards": [str(c) for c in result.community_cards],
        "showdown": is_showdown,
        "players": players,
        "winners": [
            {
                "player_id": result.final_seats[w.seat_index]["player_id"],
                "amount_bb": w.amount_bb,
                "hand_name": w.hand_name,
            }
            for w in result.winners
        ],
    }


@dataclass
class GameState:
    """Current state of a game session for API queries."""
    session_id: str
    status: str = "created"  # created | waiting_for_action | running | completed | error
    current_hand: int = 0
    street: str | None = None
    pot_bb: float = 0.0
    community_cards: list[str] = field(default_factory=list)
    players: list[dict] = field(default_factory=list)
    current_player_id: str | None = None
    human_state: dict = field(default_factory=dict)
    waiting_on_human: bool = False
    hand_complete: bool = False
    last_hand_result: dict | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    @property
    def can_continue(self) -> bool:
        """True if hand is complete and user can choose to continue or end."""
        return self.hand_complete and self.status == "waiting_for_action"


class GameRunner:
    """
    Manages a background game loop with queue-based human interaction.

    Two separate queues:
    - action_queue: for human player actions (fold, call, raise, etc.)
    - control_queue: for game control signals (continue, end)

    This prevents actions from one hand being consumed by the next.
    """

    def __init__(self, session_id: str, game: Game, human_id: str):
        self.session_id = session_id
        self.game = game
        self.human_id = human_id
        self.action_queue: queue.Queue = queue.Queue()
        self.control_queue: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.state = GameState(session_id=session_id)
        self._thread: threading.Thread | None = None
        if hasattr(self.game, "set_state_callback"):
            self.game.set_state_callback(self._update_live_state)
        self._replace_human_agent()

    def _update_live_state(self, snapshot: dict):
        """Receive live table snapshots from the engine thread."""
        self.state.street = snapshot.get("street")
        self.state.pot_bb = snapshot.get("pot_bb", self.state.pot_bb)
        self.state.community_cards = snapshot.get("community_cards", self.state.community_cards)
        self.state.players = snapshot.get("players", self.state.players)

    def _replace_human_agent(self):
        """Replace the HumanAgent with QueueHumanAgent."""
        if self.human_id in self.game.agent_map:
            new_agent = QueueHumanAgent(
                player_id=self.human_id,
                style="human",
            )
            new_agent.set_queue(self.action_queue)
            new_agent.set_stop_event(self.stop_event)
            self.game.agent_map[self.human_id] = new_agent

    def start(self):
        """Start the game in a background thread."""
        self.state.status = "running"
        self._thread = threading.Thread(target=self._run_game, daemon=True)
        self._thread.start()

    def _run_game(self):
        """Run the game loop (called in background thread)."""
        try:
            num_hands = self.game.config.get("session", {}).get("num_hands", 10)

            for hand_num in range(1, num_hands + 1):
                if self.stop_event.is_set():
                    break

                self.state.current_hand = hand_num
                self.state.hand_complete = False
                self.state.last_hand_result = None
                self.state.status = "running"
                session_store.update_progress(self.session_id, current_hand=hand_num, status="running")

                # Play one hand
                result = self.game.play_hand()
                self.game._save_hand_history(result)
                self.state.last_hand_result = _build_last_hand_result(result)
                self.state.hand_complete = True

                # Check if all players busted
                active = [p for p in self.game.players if p["stack_bb"] > 0]
                if len(active) < 2:
                    self.state.status = "completed"
                    break

                # Check if human busted
                human = next((p for p in self.game.players if p["id"] == self.human_id), None)
                if human and human["stack_bb"] <= 0:
                    self.state.status = "completed"
                    break

                # Wait for human to decide to continue or end (uses control queue)
                self.state.status = "waiting_for_action"
                session_store.update_progress(self.session_id, current_hand=hand_num, status="waiting_for_action")
                self._wait_for_human_decision()

                if self.stop_event.is_set():
                    break

            self.state.status = "completed"
            session_store.update_progress(self.session_id, status="completed")

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.state.error = str(e)
            self.state.status = "error"
            session_store.update_status(self.session_id, "error", str(e))

    def _wait_for_human_decision(self):
        """Wait for human to send continue or end signal (from control queue)."""
        try:
            decision = self.control_queue.get(timeout=3600.0)
            if decision.get("type") == "end":
                self.stop_event.set()
        except queue.Empty:
            # Timeout - auto continue
            pass

    def submit_action(self, action: str, amount: float = 0.0):
        """Submit a human player action (fold, call, raise, etc.) - goes to action queue."""
        self.action_queue.put({"action": action, "amount": amount})

    def continue_game(self):
        """Tell the game to continue to the next hand - goes to control queue."""
        self.state.hand_complete = False
        self.state.status = "running"
        human_agent = self.game.agent_map.get(self.human_id)
        if human_agent and hasattr(human_agent, "clear_state"):
            human_agent.clear_state()
        session_store.update_progress(self.session_id, status="running")
        self.control_queue.put({"type": "continue"})

    def end_game(self):
        """Tell the game to stop after current hand - goes to control queue."""
        self.control_queue.put({"type": "end"})
        self.stop_event.set()
        session_store.update_progress(self.session_id, status="completed")

    def get_state(self) -> dict:
        """Get current game state for API response."""
        # Check if human agent has current state
        human_agent = self.game.agent_map.get(self.human_id)
        human_state = {}
        if human_agent and hasattr(human_agent, 'get_state'):
            human_state = human_agent.get_state()
            if hasattr(human_agent, 'waiting_for_action'):
                self.state.waiting_on_human = human_agent.waiting_for_action
                # Update status if human is waiting during a hand
                if human_agent.waiting_for_action and self.state.status == "running" and not self.state.hand_complete:
                    self.state.status = "waiting_for_action"

        if self.state.hand_complete:
            human_state = {}
            self.state.waiting_on_human = False

        community_cards = human_state.get("community_cards") or self.state.community_cards
        if self.state.hand_complete and self.state.last_hand_result:
            community_cards = self.state.last_hand_result.get("community_cards") or community_cards

        return {
            "session_id": self.session_id,
            "status": self.state.status,
            "current_hand": self.state.current_hand,
            "street": human_state.get("street"),
            "pot_bb": human_state.get("pot_bb") or self.state.pot_bb,
            "community_cards": community_cards,
            "current_player_id": human_state.get("player_id") or self.state.current_player_id,
            "hole_cards": human_state.get("hole_cards") or [],
            "legal_actions": human_state.get("legal_actions") or [],
            "players": self._get_players_state(),
            "hand_complete": self.state.hand_complete,
            "can_continue": self.state.can_continue,
            "last_hand_result": self.state.last_hand_result,
            "waiting_on_human": self.state.waiting_on_human,
            "error": self.state.error,
        }

    def _get_players_state(self) -> list[dict]:
        """Get state of all players."""
        if self.state.hand_complete and self.state.last_hand_result and self.state.last_hand_result.get("players"):
            return self.state.last_hand_result["players"]
        if self.state.players:
            return self._merge_human_hole_cards(self.state.players)

        players = []
        for p in self.game.players:
            state = {
                "id": p["id"],
                "stack_bb": p["stack_bb"],
                "position": p.get("position_name", ""),
            }
            if p["id"] == self.human_id:
                ha = self.game.agent_map.get(self.human_id)
                if ha and hasattr(ha, '_current_observation') and ha._current_observation:
                    state["hole_cards"] = [str(c) for c in ha._current_observation.hole_cards]
            players.append(state)
        return players

    def _merge_human_hole_cards(self, players: list[dict]) -> list[dict]:
        merged = [dict(p) for p in players]
        ha = self.game.agent_map.get(self.human_id)
        if ha and hasattr(ha, '_current_observation') and ha._current_observation:
            for p in merged:
                if p["id"] == self.human_id:
                    p["hole_cards"] = [str(c) for c in ha._current_observation.hole_cards]
        return merged


# Global registry of active game runners
_runners: Dict[str, GameRunner] = {}


def get_runner(session_id: str) -> GameRunner | None:
    return _runners.get(session_id)


def create_runner(session_id: str, game: Game, human_id: str) -> GameRunner:
    runner = GameRunner(session_id, game, human_id)
    _runners[session_id] = runner
    return runner


def remove_runner(session_id: str) -> bool:
    runner = _runners.pop(session_id, None)
    if runner:
        runner.end_game()
        return True
    return False
