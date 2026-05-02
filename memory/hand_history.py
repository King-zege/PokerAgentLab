"""Hand history data structures and serialization."""

from dataclasses import dataclass
from engine.card import Card
from engine.action import Action, ActionType
from engine.hand import HandResult, PotWinner


@dataclass
class ActionRecord:
    """A single action in a hand with full context."""
    street: str
    seat_index: int
    player_id: str
    action: str
    action_amount: float
    stack_before_bb: float
    pot_before_bb: float
    explanation: str
    position_name: str
    style: str = ""


@dataclass
class HandHistory:
    """Complete record of a hand for storage and analysis."""
    hand_id: str
    timestamp: str
    table_size: int
    button_index: int
    small_blind_bb: float
    big_blind_bb: float

    # Player initial states
    players: list[dict]  # [{"id": ..., "style": ..., "initial_stack_bb": ...}]

    # Cards
    hole_cards: dict[str, list[str]]  # player_id -> ["Ah", "Ks"]
    community_cards: list[str]        # ["Ah", "Kd", "Qc", "2s", "7d"]

    # All actions
    actions: list[ActionRecord]

    # Pots and winners
    pots: list[dict]  # [{"amount_bb": ..., "eligible": [...], "winners": [...]}]

    # Final stacks
    final_stacks: dict[str, float]

    # Analysis (populated later by AnalysisAgent)
    analysis: dict | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "hand_id": self.hand_id,
            "timestamp": self.timestamp,
            "table_size": self.table_size,
            "button_index": self.button_index,
            "small_blind_bb": self.small_blind_bb,
            "big_blind_bb": self.big_blind_bb,
            "players": self.players,
            "hole_cards": self.hole_cards,
            "community_cards": self.community_cards,
            "actions": [
                {
                    "street": a.street,
                    "seat_index": a.seat_index,
                    "player_id": a.player_id,
                    "action": a.action,
                    "action_amount": a.action_amount,
                    "stack_before_bb": a.stack_before_bb,
                    "pot_before_bb": a.pot_before_bb,
                    "explanation": a.explanation,
                    "position_name": a.position_name,
                    "style": a.style,
                }
                for a in self.actions
            ],
            "pots": self.pots,
            "final_stacks": self.final_stacks,
            "analysis": self.analysis,
        }

    def to_json(self) -> str:
        """Convert to JSON string (compact single line for JSONL)."""
        import json
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_result(
        cls,
        result: HandResult,
        players: list[dict],
        small_blind_bb: float,
        big_blind_bb: float,
        table_size: int,
        timestamp: str | None = None,
    ) -> "HandHistory":
        """Build HandHistory from a HandResult."""
        from datetime import datetime

        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Convert hole cards to strings
        hole_cards: dict[str, list[str]] = {}
        for seat in result.final_seats:
            pid = seat["player_id"]
            cards = seat.get("hole_cards", [])
            if cards:
                hole_cards[pid] = [str(c) for c in cards]
            else:
                hole_cards[pid] = []

        # Convert community cards to strings
        community_cards = [str(c) for c in result.community_cards]

        # Convert actions
        actions = []
        style_by_player = {p.get("id"): p.get("style", "") for p in players}
        for record in result.actions:
            action_amount = 0.0
            action_obj = record.get("action_obj")
            if action_obj is not None:
                action_amount = getattr(action_obj, "amount", 0.0)

            actions.append(ActionRecord(
                street=record["street"],
                seat_index=record["seat_index"],
                player_id=record["player_id"],
                action=record["action"],
                action_amount=action_amount,
                stack_before_bb=record["stack_before_bb"],
                pot_before_bb=record.get("pot_before_bb", record.get("pot_after_bb", 0.0)),
                explanation=record.get("explanation", ""),
                position_name=record.get("position_name", ""),
                style=style_by_player.get(record["player_id"], ""),
            ))

        # Convert pots
        pots = []
        for w in result.winners:
            player_id = result.final_seats[w.seat_index]["player_id"]
            pots.append({
                "amount_bb": w.amount_bb,
                "winners": [
                    {
                        "player": player_id,
                        "hand": w.hand_name,
                    }
                ],
            })

        # Final stacks
        final_stacks = {seat["player_id"]: seat["stack_bb"] for seat in result.final_seats}

        return cls(
            hand_id=result.hand_id,
            timestamp=timestamp,
            table_size=table_size,
            button_index=0,
            small_blind_bb=small_blind_bb,
            big_blind_bb=big_blind_bb,
            players=players,
            hole_cards=hole_cards,
            community_cards=community_cards,
            actions=actions,
            pots=pots,
            final_stacks=final_stacks,
        )
