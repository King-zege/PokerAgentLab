"""Decision logger: writes agent decision explanations to a log file."""

from pathlib import Path
from engine.hand import HandResult


class DecisionLogger:
    """Writes agent decision explanations to decision log file."""

    def __init__(self, filepath: str = "decision_log.txt"):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._file = None
        self._write_header()

    def _write_header(self) -> None:
        """Write header line if file doesn't exist."""
        if not self.filepath.exists():
            with open(self.filepath, "w", encoding="utf-8") as f:
                f.write("Hand | Street | Player (Position) | Action | Style | Reason\n")
                f.write("-" * 100 + "\n")

    def log_hand(self, result: HandResult, players: list[dict]) -> None:
        """Log all decisions from a completed hand."""
        with open(self.filepath, "a", encoding="utf-8") as f:
            for record in result.actions:
                player_id = record["player_id"]
                position = record.get("position_name", "")
                action = record["action"]
                street = record["street"]
                explanation = record.get("explanation", "")

                # Get player style
                style = "N/A"
                for p in players:
                    if p["id"] == player_id:
                        style = p["style"]
                        break

                f.write(
                    f"{result.hand_id} | {street:7} | {player_id} ({position}) | "
                    f"{action:15} | {style:4} | {explanation}\n"
                )

    def clear(self) -> None:
        """Clear the decision log file."""
        if self.filepath.exists():
            self.filepath.unlink()
        self._write_header()
