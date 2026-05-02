"""Hand orchestrator: manages a single hand from deal to showdown."""

from dataclasses import dataclass, field
from engine.card import Card
from engine.deck import Deck
from engine.action import Action, ActionType
from engine.pot import Pot, PotManager, PotWinner
from engine.betting import BettingRound
from engine.hand_evaluator import HandEvaluator
from agent.base_agent import BaseAgent
from memory.decision_trace import DecisionTraceStore


def _get_position_name(seat_index: int, button_index: int, num_players: int) -> str:
    """Get position name based on seat index relative to button."""
    if num_players == 2:
        # Heads-up: button is SB, other is BB
        if seat_index == button_index:
            return "BTN/SB"
        return "BB"

    # Distance from button (clockwise)
    dist = (seat_index - button_index) % num_players

    if dist == 0:
        return "BTN"
    elif dist == 1:
        return "SB"
    elif dist == 2:
        return "BB"

    # Map remaining positions based on table size
    remaining = num_players - 3
    if remaining == 1:
        if dist == 3:
            return "CO"
    elif remaining == 2:
        if dist == 3:
            return "CO"
        if dist == 4:
            return "HJ"
    elif remaining == 3:
        if dist == 3:
            return "CO"
        if dist == 4:
            return "HJ"
        if dist == 5:
            return "MP"
    elif remaining >= 4:
        # 8+ player table
        early = remaining - 2  # Number of early positions
        if dist <= early + 2:
            return f"UTG+{dist - 3}" if dist > 3 else "UTG"
        elif dist == remaining + 1:
            return "HJ"
        elif dist == remaining + 2:
            return "CO"
        # Simplified: use generic names
        if dist == 3:
            return "UTG"
        if dist == 4:
            return "UTG+1"
        if dist == num_players - 2:
            return "HJ"
        if dist == num_players - 1:
            return "CO"

    # Fallback
    positions_6 = ["BTN", "SB", "BB", "UTG", "MP", "CO"]
    positions_9 = ["BTN", "SB", "BB", "UTG", "UTG+1", "MP", "LJ", "HJ", "CO"]

    if num_players <= len(positions_6) and dist < len(positions_6):
        return positions_6[dist]
    elif num_players <= len(positions_9) and dist < len(positions_9):
        return positions_9[dist]
    return f"POS{dist}"


@dataclass
class HandResult:
    """Result of a completed hand."""
    hand_id: str
    winners: list[PotWinner]
    final_seats: list[dict]  # Final state of each seat
    community_cards: list[Card]
    actions: list[dict]      # All action records
    pot_total_bb: float


class Hand:
    """Orchestrates a single hand from deal to showdown."""

    def __init__(
        self,
        players: list[dict],       # [{"id": str, "style": str, "stack_bb": float}]
        button_index: int,
        small_blind_bb: float,
        big_blind_bb: float,
        hand_id: str,
        deck_seed: int | None = None,
        session_id: str = "",
        trace_store: DecisionTraceStore | None = None,
    ):
        self.hand_id = hand_id
        self.session_id = session_id
        self.trace_store = trace_store
        self.small_blind_bb = small_blind_bb
        self.big_blind_bb = big_blind_bb
        self.button_index = button_index
        self.num_players = len(players)

        # Initialize seats as mutable dicts
        self.seats: list[dict] = []
        for i, p in enumerate(players):
            self.seats.append({
                "player_id": p["id"],
                "style": p["style"],
                "initial_stack_bb": p["stack_bb"],
                "stack_bb": p["stack_bb"],
                "hole_cards": [],
                "current_bet_bb": 0.0,
                "total_bet_bb": 0.0,
                "folded": False,
                "all_in": False,
                "is_active": True,
                "position_name": _get_position_name(i, button_index, self.num_players),
            })

        # Deck
        self.deck = Deck(seed=deck_seed)

        # Community cards
        self.community_cards: list[Card] = []

        # Pot tracking
        self.pot_bb = 0.0

        # All action records
        self.all_actions: list[dict] = []

        # Burned cards (for history)
        self.burned_cards: list[Card] = []

    def play(self, agent_map: dict[str, BaseAgent]) -> HandResult:
        """Run the complete hand from deal to showdown."""
        separator = "\n" + "=" * 50

        # Helper: check if only one player who hasn't folded remains (including all-in players)
        def only_one_left():
            """Returns True if only one non-folded player remains."""
            return sum(1 for s in self.seats if not s["folded"]) == 1

        # 1. Post blinds
        self._post_blinds()

        # 2. Deal hole cards
        self._deal_hole_cards()

        # 3. Preflop betting
        print(f"{separator}")
        print("【翻前】 - PREFLOP")
        print(f"{separator}\n")
        pot_awarded = self._run_street("preflop", agent_map)
        if pot_awarded:
            return self._build_result(pot_awarded)

        # In Texas Hold'em, we ALWAYS deal all 5 community cards if 2+ players remain
        # Only skip to showdown if exactly one player hasn't folded (everyone else folded)
        if only_one_left():
            return self._build_result(self._award_to_last_player())

        # 4. Flop
        self._deal_community("flop")
        print(f"\n{separator}")
        print("【翻牌】 - FLOP")
        cards = " ".join(str(c) for c in self.community_cards)
        print(f"公共牌: {cards}")
        print(f"{separator}\n")
        pot_awarded = self._run_street("flop", agent_map)
        if pot_awarded:
            return self._build_result(pot_awarded)
        if only_one_left():
            return self._build_result(self._award_to_last_player())

        # 5. Turn
        self._deal_community("turn")
        print(f"\n{separator}")
        print("【转牌】 - TURN")
        cards = " ".join(str(c) for c in self.community_cards)
        print(f"公共牌: {cards}")
        print(f"{separator}\n")
        pot_awarded = self._run_street("turn", agent_map)
        if pot_awarded:
            return self._build_result(pot_awarded)
        if only_one_left():
            return self._build_result(self._award_to_last_player())

        # 6. River
        self._deal_community("river")
        print(f"\n{separator}")
        print("【河牌】 - RIVER")
        cards = " ".join(str(c) for c in self.community_cards)
        print(f"公共牌: {cards}")
        print(f"{separator}\n")
        pot_awarded = self._run_street("river", agent_map)
        if pot_awarded:
            return self._build_result(pot_awarded)

        # 7. Showdown
        print(f"\n{separator}")
        print("【摊牌】 - SHOWDOWN")
        print(f"{separator}\n")
        winners = self._showdown()
        return self._build_result(winners)

    def _post_blinds(self) -> None:
        """Post small blind and big blind."""
        if self.num_players == 2:
            # Heads-up: button posts SB, other posts BB
            sb_index = self.button_index
            bb_index = (self.button_index + 1) % self.num_players
        else:
            sb_index = (self.button_index + 1) % self.num_players
            bb_index = (self.button_index + 2) % self.num_players

        # Post SB
        sb_amount = min(self.small_blind_bb, self.seats[sb_index]["stack_bb"])
        self.seats[sb_index]["stack_bb"] -= sb_amount
        self.seats[sb_index]["current_bet_bb"] = sb_amount
        self.seats[sb_index]["total_bet_bb"] = sb_amount
        self.pot_bb += sb_amount
        if self.seats[sb_index]["stack_bb"] <= 0:
            self.seats[sb_index]["all_in"] = True

        # Post BB
        bb_amount = min(self.big_blind_bb, self.seats[bb_index]["stack_bb"])
        self.seats[bb_index]["stack_bb"] -= bb_amount
        self.seats[bb_index]["current_bet_bb"] = bb_amount
        self.seats[bb_index]["total_bet_bb"] = bb_amount
        self.pot_bb += bb_amount
        if self.seats[bb_index]["stack_bb"] <= 0:
            self.seats[bb_index]["all_in"] = True

    def _deal_hole_cards(self) -> None:
        """Deal 2 hole cards to each active seat."""
        for seat in self.seats:
            if seat["is_active"]:
                seat["hole_cards"] = self.deck.deal(2)

    def _deal_community(self, street: str) -> None:
        """Burn a card, then deal community cards for the street."""
        burned = self.deck.burn()
        self.burned_cards.append(burned)

        if street == "flop":
            self.community_cards.extend(self.deck.deal(3))
        elif street in ("turn", "river"):
            self.community_cards.extend(self.deck.deal(1))

        # Reset current_bet_bb for each seat (new street)
        for seat in self.seats:
            seat["current_bet_bb"] = 0.0

    def _action_order(self, street: str) -> list[int]:
        """Return seat indices in action order for the given street."""
        n = self.num_players
        btn = self.button_index

        if street == "preflop":
            if n == 2:
                start = btn  # HU: button/SB acts first preflop
            else:
                start = (btn + 3) % n  # UTG
        else:
            # Postflop: start from SB (first active player left of button)
            start = (btn + 1) % n

        order = []
        for i in range(n):
            idx = (start + i) % n
            seat = self.seats[idx]
            if not seat["folded"] and not seat["all_in"] and seat["is_active"]:
                order.append(idx)
        return order

    def _run_street(self, street: str, agent_map: dict[str, BaseAgent]) -> list[PotWinner] | None:
        """Run a betting round for a street. Returns winners if hand is over, None otherwise."""
        action_order = self._action_order(street)

        if not action_order:
            # No one can act - check if everyone else folded (award pot)
            # or all remaining players are all-in (continue to showdown)
            non_folded = [i for i, s in enumerate(self.seats) if not s["folded"] and s["is_active"]]
            if len(non_folded) <= 1:
                return self._award_to_last_player()
            # All remaining players are all-in - continue to next street
            return None

        current_bet_bb = 0.0
        if street == "preflop":
            # Current bet is the BB amount
            current_bet_bb = self.big_blind_bb

        betting_round = BettingRound(
            seats=self.seats,
            action_order=action_order,
            street=street,
            current_bet_bb=current_bet_bb,
            pot_bb=self.pot_bb,
            big_blind_bb=self.big_blind_bb,
            button_index=self.button_index,
            community_cards=self.community_cards,
            num_players=self.num_players,
            hand_id=self.hand_id,
            session_id=self.session_id,
            trace_store=self.trace_store,
        )

        records = betting_round.run(agent_map)
        self.all_actions.extend(records)
        self.pot_bb = betting_round.pot_bb

        return None

    def _award_to_last_player(self) -> list[PotWinner]:
        """Award the pot to the last remaining player."""
        non_folded = [i for i, s in enumerate(self.seats) if not s["folded"] and s["is_active"]]
        if not non_folded:
            return []

        winner_idx = non_folded[0]
        self.seats[winner_idx]["stack_bb"] += self.pot_bb
        return [PotWinner(
            seat_index=winner_idx,
            amount_bb=self.pot_bb,
            hand_name="最后一个玩家（其他人弃牌）",
        )]

    def _showdown(self) -> list[PotWinner]:
        """Evaluate hands and award pots at showdown."""
        total_bets = [s["total_bet_bb"] for s in self.seats]
        folded = [s["folded"] for s in self.seats]
        hole_cards = [s["hole_cards"] for s in self.seats]

        # Calculate pots (main + side pots)
        pots = PotManager.calculate_pots(total_bets, folded)

        # Award each pot
        winners = PotManager.award_pots(pots, hole_cards, self.community_cards, folded)

        # Update stacks for winners
        for w in winners:
            self.seats[w.seat_index]["stack_bb"] += w.amount_bb

        return winners

    def _build_result(self, winners: list[PotWinner]) -> HandResult:
        """Build HandResult from current state."""
        return HandResult(
            hand_id=self.hand_id,
            winners=winners,
            final_seats=[dict(s) for s in self.seats],
            community_cards=list(self.community_cards),
            actions=list(self.all_actions),
            pot_total_bb=self.pot_bb,
        )
