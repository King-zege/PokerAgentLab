"""Betting round management: legal actions, completion detection."""

import time

from engine.action import Action, ActionType
from agent.observation import Observation
from agent.legal_action_filter import compute_legal_actions
from agent.base_agent import BaseAgent
from memory.decision_trace import DecisionTrace, DecisionTraceStore


def _build_observation(
    seat_index: int,
    seat_data: dict,
    state: dict,
) -> Observation:
    """Build an Observation for the agent at seat_index."""
    return Observation(
        player_id=seat_data["player_id"],
        style=seat_data["style"],
        hole_cards=seat_data["hole_cards"],
        stack_bb=seat_data["stack_bb"],
        seat_index=seat_index,
        button_index=state["button_index"],
        num_players=state["num_players"],
        position_name=seat_data["position_name"],
        street=state["street"],
        community_cards=state["community_cards"],
        pot_bb=state["pot_bb"],
        current_bet_to_call_bb=state["current_bet_bb"] - seat_data["current_bet_bb"],
        min_raise_bb=state["min_raise_bb"],
        max_raise_bb=seat_data["stack_bb"] + seat_data["current_bet_bb"],
        actions_this_street=state["actions_this_street"],
        active_opponents=state["active_opponents"],
        spr=seat_data["stack_bb"] / state["pot_bb"] if state["pot_bb"] > 0 else float("inf"),
    )


class BettingRound:
    """Manages a single betting round within a street."""

    def __init__(
        self,
        seats: list[dict],        # List of seat dicts (mutable)
        action_order: list[int],   # Seat indices in acting order
        street: str,
        current_bet_bb: float,     # Current highest bet on this street
        pot_bb: float,
        big_blind_bb: float,
        button_index: int,
        community_cards: list,
        num_players: int,
        hand_id: str = "",
        session_id: str = "",
        trace_store: DecisionTraceStore | None = None,
        state_callback=None,
    ):
        self.seats = seats
        self.action_order = action_order
        self.street = street
        self._current_bet_bb = current_bet_bb
        self._pot_bb = pot_bb
        self._big_blind_bb = big_blind_bb
        self._button_index = button_index
        self._community_cards = community_cards
        self._num_players = num_players
        self._hand_id = hand_id
        self._session_id = session_id or "default"
        self._trace_store = trace_store
        self._state_callback = state_callback

        self._last_raise_increment = current_bet_bb if street == "preflop" and current_bet_bb > 0 else big_blind_bb
        self._acted_this_round: set[int] = set()
        self._last_raiser: int | None = None
        self._action_records: list[dict] = []

    @property
    def current_bet_bb(self) -> float:
        return self._current_bet_bb

    @property
    def min_raise_bb(self) -> float:
        """Minimum legal raise total amount."""
        return self._current_bet_bb + self._last_raise_increment

    @property
    def pot_bb(self) -> float:
        return self._pot_bb

    @property
    def action_records(self) -> list[dict]:
        return self._action_records

    def _emit_state(self) -> None:
        if self._state_callback is None:
            return
        self._state_callback({
            "street": self.street,
            "pot_bb": self._pot_bb,
            "community_cards": [str(c) for c in self._community_cards],
            "players": [
                {
                    "id": s["player_id"],
                    "stack_bb": s["stack_bb"],
                    "position": s.get("position_name", ""),
                    "folded": s.get("folded", False),
                    "all_in": s.get("all_in", False),
                    "current_bet_bb": s.get("current_bet_bb", 0.0),
                    "total_bet_bb": s.get("total_bet_bb", 0.0),
                }
                for s in self.seats
            ],
        })

    def _count_active(self) -> int:
        """Count seats that are not folded and not all-in."""
        return sum(
            1 for i, s in enumerate(self.seats)
            if not s["folded"] and not s["all_in"] and s["is_active"]
        )

    def _count_non_folded(self) -> int:
        """Count seats that are not folded."""
        return sum(1 for s in self.seats if not s["folded"] and s["is_active"])

    def _print_action(self, seat: dict, action: Action, stack_after: float) -> None:
        """Print an action immediately for real-time display."""
        pos_name = seat.get("position_name", "")
        player_id = seat["player_id"]
        pos_display = f"({pos_name})" if pos_name else ""
        # Always show stack after (0BB means all-in, show it as 0)
        stack_display = f"[{stack_after:.1f}BB]"
        street_display = {
            "preflop": "翻前",
            "flop": "翻牌",
            "turn": "转牌",
            "river": "河牌",
        }.get(self.street, self.street)
        print(f"  【{street_display}】 {player_id} {pos_display} {stack_display}: {action}", flush=True)

    def run(self, agent_map: dict[str, BaseAgent]) -> list[dict]:
        """
        Run the betting round to completion.

        Returns list of action record dicts.
        """
        # For preflop, BB hasn't acted yet even though their blind counts as a bet
        max_orbits = 10  # Safety limit to prevent infinite loops

        for _ in range(max_orbits):
            round_complete = False
            for seat_index in self.action_order:
                seat = self.seats[seat_index]

                # Skip folded, all-in, or inactive seats
                if seat["folded"] or seat["all_in"] or not seat["is_active"]:
                    continue

                # Only one active player left — round is done
                if self._count_active() <= 1 and self._count_non_folded() <= 1:
                    round_complete = True
                    break

                # If this seat has already acted and no new raise since, skip
                if seat_index in self._acted_this_round:
                    # If this player is the last raiser, round is complete (came back to them)
                    if self._last_raiser is not None and seat_index == self._last_raiser:
                        round_complete = True
                        break
                    # Skip only if this player is already all-in or has matched current bet
                    if seat["all_in"] or seat["current_bet_bb"] >= self._current_bet_bb:
                        continue
                    # Otherwise, this player needs to match the new bet - don't skip them

                # Build observation and compute legal actions
                obs = self._build_observation_for_seat(seat_index)
                legal = compute_legal_actions(
                    stack_bb=seat["stack_bb"],
                    current_bet_bb=seat["current_bet_bb"],
                    highest_bet_bb=self._current_bet_bb,
                    min_raise_bb=self.min_raise_bb,
                    pot_bb=self._pot_bb,
                    big_blind_bb=self._big_blind_bb,
                )

                if not legal:
                    continue

                latency_ms = 0.0
                fallback_reason = ""

                # If only one legal action (e.g., check), auto-apply
                if len(legal) == 1:
                    action = legal[0]
                else:
                    # Agent decides
                    agent = agent_map.get(seat["player_id"])
                    if agent is None:
                        action = legal[0]  # Fallback to first legal action
                    else:
                        start = time.perf_counter()
                        action = agent.decide(obs, legal)
                        latency_ms = (time.perf_counter() - start) * 1000

                    # Validate action is legal (check type only, not exact amount)
                    legal_types = {a.type for a in legal}
                    if action.type not in legal_types:
                        fallback_reason = f"Illegal action type {action.type.value}; fell back to first legal action"
                        # Find fallback action of same type or first legal
                        for la in legal:
                            if la.type == action.type:
                                # Same type, update amount to minimum
                                action = Action(action.type, max(action.amount, la.amount))
                                break
                        else:
                            action = legal[0]

                # Get explanation from agent
                explanation = ""
                agent = agent_map.get(seat["player_id"])
                if agent is not None:
                    explanation = agent.explain(obs, action)

                # Apply the action
                pot_before_bb = self._pot_bb
                self._apply_action(seat_index, action)

                # Record
                stack_after = seat["stack_bb"]
                self._action_records.append({
                    "street": self.street,
                    "seat_index": seat_index,
                    "player_id": seat["player_id"],
                    "action": str(action),
                    "action_obj": action,
                    "stack_before_bb": stack_after + (action.amount if action.type in (ActionType.BET, ActionType.RAISE, ActionType.ALL_IN) else 0),
                    "stack_after_bb": stack_after,
                    "pot_before_bb": pot_before_bb,
                    "pot_after_bb": self._pot_bb,
                    "explanation": explanation,
                    "position_name": seat["position_name"],
                })

                # Print action immediately for real-time display
                self._print_action(seat, action, stack_after)
                self._emit_state()

                if agent is not None:
                    self._save_decision_trace(
                        obs=obs,
                        legal_actions=legal,
                        chosen_action=action,
                        agent=agent,
                        latency_ms=latency_ms,
                        fallback_reason=fallback_reason,
                    )

                self._acted_this_round.add(seat_index)

                # If BET, RAISE, or ALL_IN, reset acted set — everyone else must act again
                if action.type in (ActionType.BET, ActionType.RAISE, ActionType.ALL_IN):
                    self._last_raiser = seat_index
                    self._acted_this_round = {seat_index}

                # Check if only one non-folded player remains
                if self._count_non_folded() <= 1:
                    round_complete = True
                    break

            if round_complete:
                break

            # Check if all active players have acted and bets are equal
            all_acted = all(
                i in self._acted_this_round
                for i in range(len(self.seats))
                if not self.seats[i]["folded"] and not self.seats[i]["all_in"] and self.seats[i]["is_active"]
            )
            if all_acted:
                break

        return self._action_records

    def _save_decision_trace(
        self,
        obs: Observation,
        legal_actions: list[Action],
        chosen_action: Action,
        agent: BaseAgent,
        latency_ms: float,
        fallback_reason: str,
    ) -> None:
        if self._trace_store is None:
            return

        extra = getattr(agent, "last_trace", {}) or {}
        trace = DecisionTrace(
            session_id=self._session_id,
            hand_id=self._hand_id,
            street=self.street,
            player_id=obs.player_id,
            observation=self._observation_to_dict(obs),
            legal_actions=[self._action_to_dict(a) for a in legal_actions],
            chosen_action=str(chosen_action),
            prompt_summary=extra.get("prompt_summary", ""),
            llm_raw_response=extra.get("llm_raw_response", ""),
            tool_call=extra.get("tool_call"),
            parsed_action=extra.get("parsed_action", str(chosen_action)),
            fallback_reason=extra.get("fallback_reason") or fallback_reason,
            memory_context_summary=extra.get("memory_context_summary", ""),
            strategy_context_summary=extra.get("strategy_context_summary", ""),
            retrieved_memory_ids=extra.get("retrieved_memory_ids", []),
            retrieved_strategy_chunk_ids=extra.get("retrieved_strategy_chunk_ids", []),
            memory_fallback_reason=extra.get("memory_fallback_reason", ""),
            latency_ms=extra.get("latency_ms", latency_ms),
        )
        self._trace_store.save(trace)

    def _action_to_dict(self, action: Action) -> dict:
        return {"type": action.type.value, "amount": action.amount}

    def _observation_to_dict(self, obs: Observation) -> dict:
        return {
            "player_id": obs.player_id,
            "style": obs.style,
            "hole_cards": [str(c) for c in obs.hole_cards],
            "stack_bb": obs.stack_bb,
            "seat_index": obs.seat_index,
            "button_index": obs.button_index,
            "num_players": obs.num_players,
            "position_name": obs.position_name,
            "street": obs.street,
            "community_cards": [str(c) for c in obs.community_cards],
            "pot_bb": obs.pot_bb,
            "current_bet_to_call_bb": obs.current_bet_to_call_bb,
            "min_raise_bb": obs.min_raise_bb,
            "max_raise_bb": obs.max_raise_bb,
            "actions_this_street": [
                {
                    "player_id": pid,
                    "action": str(action),
                    "position_name": position,
                    "stack_after_bb": stack,
                }
                for pid, action, position, stack in obs.actions_this_street
            ],
            "active_opponents": obs.active_opponents,
            "spr": obs.spr,
        }

    def _build_observation_for_seat(self, seat_index: int) -> Observation:
        """Build an Observation for the given seat."""
        seat = self.seats[seat_index]
        active_opponents = sum(
            1 for i, s in enumerate(self.seats)
            if i != seat_index and not s["folded"] and s["is_active"]
        )

        # Build actions_this_street from already-recorded actions for this street only
        # Tuple: (player_id, action, position_name, stack_after_bb)
        actions_this_street = [
            (r["player_id"], r["action_obj"], r.get("position_name", ""), r.get("stack_after_bb", 0))
            for r in self._action_records
            if r["street"] == self.street
        ]

        return Observation(
            player_id=seat["player_id"],
            style=seat["style"],
            hole_cards=seat["hole_cards"],
            stack_bb=seat["stack_bb"],
            seat_index=seat_index,
            button_index=self._button_index,
            num_players=self._num_players,
            position_name=seat["position_name"],
            street=self.street,
            community_cards=self._community_cards,
            pot_bb=self._pot_bb,
            current_bet_to_call_bb=max(0, self._current_bet_bb - seat["current_bet_bb"]),
            min_raise_bb=self.min_raise_bb,
            max_raise_bb=seat["stack_bb"] + seat["current_bet_bb"],
            actions_this_street=actions_this_street,
            active_opponents=active_opponents,
            spr=seat["stack_bb"] / self._pot_bb if self._pot_bb > 0 else float("inf"),
        )

    def _apply_action(self, seat_index: int, action: Action) -> None:
        """Apply an action, updating seat and pot state."""
        seat = self.seats[seat_index]

        if action.type == ActionType.FOLD:
            seat["folded"] = True

        elif action.type == ActionType.CHECK:
            pass  # No chips moved

        elif action.type == ActionType.CALL:
            call_amount = min(
                self._current_bet_bb - seat["current_bet_bb"],
                seat["stack_bb"],
            )
            seat["stack_bb"] -= call_amount
            seat["current_bet_bb"] += call_amount
            seat["total_bet_bb"] += call_amount
            self._pot_bb += call_amount
            if seat["stack_bb"] <= 0:
                seat["all_in"] = True
                seat["stack_bb"] = 0

        elif action.type == ActionType.BET:
            bet_amount = min(action.amount, seat["stack_bb"])
            actual_bet = bet_amount - seat["current_bet_bb"]
            seat["stack_bb"] -= actual_bet
            seat["current_bet_bb"] = bet_amount
            seat["total_bet_bb"] += actual_bet
            self._pot_bb += actual_bet
            self._last_raise_increment = bet_amount - self._current_bet_bb
            self._current_bet_bb = bet_amount
            if seat["stack_bb"] <= 0:
                seat["all_in"] = True
                seat["stack_bb"] = 0

        elif action.type == ActionType.RAISE:
            raise_total = min(action.amount, seat["stack_bb"] + seat["current_bet_bb"])
            raise_increment = raise_total - self._current_bet_bb
            chips_needed = raise_total - seat["current_bet_bb"]
            seat["stack_bb"] -= chips_needed
            seat["current_bet_bb"] = raise_total
            seat["total_bet_bb"] += chips_needed
            self._pot_bb += chips_needed
            self._last_raise_increment = raise_increment
            self._current_bet_bb = raise_total
            if seat["stack_bb"] <= 0:
                seat["all_in"] = True
                seat["stack_bb"] = 0

        elif action.type == ActionType.ALL_IN:
            all_in_amount = seat["stack_bb"]
            new_bet_total = seat["current_bet_bb"] + all_in_amount
            seat["stack_bb"] = 0
            seat["current_bet_bb"] = new_bet_total
            seat["total_bet_bb"] += all_in_amount
            seat["all_in"] = True
            self._pot_bb += all_in_amount
            # Only update current bet and raise increment if all-in exceeds current bet
            if new_bet_total > self._current_bet_bb:
                # Check if this is a full raise (reopens betting) or partial
                raise_amount = new_bet_total - self._current_bet_bb
                self._last_raise_increment = raise_amount
                self._current_bet_bb = new_bet_total
