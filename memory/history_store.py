"""History store: file-based hand history storage."""

import json
from pathlib import Path
from typing import Iterator
from memory.hand_history import HandHistory


class HistoryStore:
    """File-based hand history storage in JSONL format."""

    def __init__(self, filepath: str = "hand_history.jsonl"):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    def save(self, history: HandHistory) -> None:
        """Append hand history to JSONL file."""
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(history.to_json() + "\n")

    def load_all(self) -> list[HandHistory]:
        """Load all hand histories from file."""
        histories = []
        if not self.filepath.exists():
            return histories

        with open(self.filepath, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return histories

            # Handle both JSON objects per line and array format
            lines = content.strip().split("\n")
            for line in lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    try:
                        data = json.loads(line)
                        histories.append(self._dict_to_history(data))
                    except json.JSONDecodeError:
                        continue

        return histories

    def load_by_id(self, hand_id: str) -> HandHistory | None:
        """Load a specific hand by ID."""
        if not self.filepath.exists():
            return None

        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    if data.get("hand_id") == hand_id:
                        return self._dict_to_history(data)

        return None

    def get_player_stats(self, player_id: str) -> dict:
        """Aggregate statistics for a player across all hands."""
        histories = self.load_all()

        stats = {
            "hands_played": 0,
            "wins": 0,
            "total_profit_bb": 0.0,
            "vpip": 0.0,  # Voluntarily Put $ In Pot
        }

        for h in histories:
            for p in h.players:
                if p["id"] == player_id:
                    stats["hands_played"] += 1

            for pot in h.pots:
                for winner in pot.get("winners", []):
                    if winner["player"] == player_id:
                        stats["wins"] += 1
                        stats["total_profit_bb"] += winner.get("amount_bb", 0)

            if player_id in h.final_stacks:
                initial = next((p["initial_stack_bb"] for p in h.players if p["id"] == player_id), 0)
                profit = h.final_stacks[player_id] - initial
                stats["total_profit_bb"] += profit

        return stats

    def iter_histories(self) -> Iterator[HandHistory]:
        """Iterate over all hand histories lazily."""
        if not self.filepath.exists():
            return

        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    yield self._dict_to_history(data)

    def _dict_to_history(self, data: dict) -> HandHistory:
        """Convert dictionary to HandHistory object."""
        from memory.hand_history import ActionRecord, HandHistory as HH

        actions = [
            ActionRecord(
                street=a["street"],
                seat_index=a["seat_index"],
                player_id=a["player_id"],
                action=a["action"],
                action_amount=a.get("action_amount", 0),
                stack_before_bb=a.get("stack_before_bb", 0),
                pot_before_bb=a.get("pot_before_bb", 0),
                explanation=a.get("explanation", ""),
                position_name=a.get("position_name", ""),
                style=a.get("style", ""),
            )
            for a in data.get("actions", [])
        ]

        return HH(
            hand_id=data["hand_id"],
            timestamp=data.get("timestamp", ""),
            table_size=data.get("table_size", 0),
            button_index=data.get("button_index", 0),
            small_blind_bb=data.get("small_blind_bb", 0),
            big_blind_bb=data.get("big_blind_bb", 0),
            players=data.get("players", []),
            hole_cards=data.get("hole_cards", {}),
            community_cards=data.get("community_cards", []),
            actions=actions,
            pots=data.get("pots", []),
            final_stacks=data.get("final_stacks", {}),
            analysis=data.get("analysis"),
        )

    def clear(self) -> None:
        """Clear all hand history."""
        if self.filepath.exists():
            self.filepath.unlink()
