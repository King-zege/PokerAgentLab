"""Queue-based human agent for FastAPI integration.

Instead of blocking on stdin, this agent polls a queue with short timeouts,
allowing the game thread to remain responsive for state queries and stop checks.
"""

import queue
import time
import threading
from engine.action import Action, ActionType
from agent.base_agent import BaseAgent
from agent.observation import Observation


class QueueHumanAgent(BaseAgent):
    """
    Non-blocking human agent that receives actions via a queue.

    Uses short timeouts in a loop so the game thread stays responsive
    for state queries and stop checks.
    """

    def __init__(
        self,
        player_id: str,
        style: str = "human",
        poll_interval: float = 0.5,
        total_timeout: float = 3600.0,
    ):
        self.player_id = player_id
        self.style = style
        self._queue: queue.Queue | None = None
        self._current_observation: Observation | None = None
        self._current_legal_actions: list[Action] = []
        self._poll_interval = poll_interval
        self._total_timeout = total_timeout
        self._start_time: float | None = None
        self._stop_event: threading.Event | None = None

    def set_queue(self, q: queue.Queue):
        """Inject the queue for receiving actions from API."""
        self._queue = q

    def set_stop_event(self, event: threading.Event):
        """Inject a stop event to check during the wait loop."""
        self._stop_event = event

    def store_state(self, obs: Observation, legal_actions: list[Action]):
        """Store the current observation and legal actions for API queries."""
        self._current_observation = obs
        self._current_legal_actions = legal_actions
        self._start_time = time.time()

    @property
    def waiting_for_action(self) -> bool:
        """Return True if agent is waiting for action from queue."""
        if self._queue is None:
            return False
        return self._queue.empty() and self._current_observation is not None

    def get_state(self) -> dict:
        """Return current state for API queries."""
        obs = self._current_observation
        if obs is None:
            return {}

        legal = []
        for a in self._current_legal_actions:
            item = {"type": a.type.value}
            if a.type in (ActionType.BET, ActionType.RAISE):
                item["min"] = a.amount
                item["max"] = obs.max_raise_bb
            elif a.type == ActionType.ALL_IN:
                item["amount"] = obs.stack_bb
            legal.append(item)

        return {
            "player_id": self.player_id,
            "style": self.style,
            "hole_cards": [str(c) for c in obs.hole_cards],
            "stack_bb": obs.stack_bb,
            "position": obs.position_name,
            "street": obs.street,
            "pot_bb": obs.pot_bb,
            "community_cards": [str(c) for c in obs.community_cards],
            "current_bet_to_call_bb": obs.current_bet_to_call_bb,
            "legal_actions": legal,
            "active_opponents": obs.active_opponents,
        }

    def decide(self, observation: Observation, legal_actions: list[Action]) -> Action:
        """
        Wait for action from queue using short polls.
        Returns when action is received, stop event is set, or timeout.
        """
        # Store state for API queries
        self.store_state(observation, legal_actions)

        if self._queue is None:
            return self._auto_action(legal_actions)

        # Poll loop with short timeouts
        deadline = self._start_time + self._total_timeout if self._start_time else None

        while True:
            # Check overall timeout
            if deadline and time.time() >= deadline:
                return self._auto_action(legal_actions)

            # Check stop event
            if self._stop_event and self._stop_event.is_set():
                return self._auto_action(legal_actions)

            # Try to get action with short timeout
            try:
                action_data = self._queue.get(timeout=self._poll_interval)
                return self._parse_action(action_data, legal_actions)
            except queue.Empty:
                # Short timeout - loop back and check stop/timeout conditions
                continue

    def _auto_action(self, legal_actions: list[Action]) -> Action:
        """Return a default action when timeout or error."""
        for a in legal_actions:
            if a.type == ActionType.FOLD:
                return a
        return legal_actions[0]

    def _parse_action(self, action_data: dict, legal_actions: list[Action]) -> Action:
        """Parse action data and validate against legal actions."""
        action_type = action_data.get("action", "fold")
        amount = action_data.get("amount", 0.0)

        try:
            action_enum = ActionType(action_type)
        except ValueError:
            return self._auto_action(legal_actions)

        # Validate against legal actions
        for la in legal_actions:
            if la.type == action_enum:
                if action_enum in (ActionType.BET, ActionType.RAISE):
                    final_amount = max(amount, la.amount) if amount > 0 else la.amount
                    return Action(action_enum, final_amount)
                return Action(action_enum, amount if amount > 0 else la.amount)

        # Not found - auto fold
        return self._auto_action(legal_actions)

    def explain(self, observation: Observation, chosen_action: Action) -> str:
        """Return explanation for the action."""
        return f"Human player: {chosen_action}"
