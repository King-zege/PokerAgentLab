"""Decision trace storage for agent observability."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class DecisionTrace:
    session_id: str
    hand_id: str
    street: str
    player_id: str
    observation: dict[str, Any]
    legal_actions: list[dict[str, Any]]
    chosen_action: str
    prompt_summary: str = ""
    llm_raw_response: str = ""
    tool_call: dict[str, Any] | None = None
    parsed_action: str = ""
    fallback_reason: str = ""
    memory_context_summary: str = ""
    strategy_context_summary: str = ""
    retrieved_memory_ids: list[str] | None = None
    retrieved_strategy_chunk_ids: list[str] | None = None
    memory_fallback_reason: str = ""
    latency_ms: float = 0.0
    timestamp: str = ""

    def to_json(self) -> str:
        data = asdict(self)
        if not data["timestamp"]:
            data["timestamp"] = datetime.now().isoformat()
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class DecisionTraceStore:
    """Append-only JSONL trace store."""

    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_session(cls, session_id: str) -> "DecisionTraceStore":
        return cls(f"data/traces/decision_trace_{session_id}.jsonl")

    def save(self, trace: DecisionTrace) -> None:
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(trace.to_json() + "\n")

    def load_all(self) -> list[dict[str, Any]]:
        if not self.filepath.exists():
            return []
        traces: list[dict[str, Any]] = []
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return traces

    def load_since_line(self, start_line: int = 0) -> tuple[list[dict[str, Any]], int]:
        """Load traces appended after start_line and return the next line cursor."""
        if start_line < 0:
            start_line = 0
        if not self.filepath.exists():
            return [], start_line

        traces: list[dict[str, Any]] = []
        line_count = 0
        with open(self.filepath, "r", encoding="utf-8") as f:
            for line in f:
                line_count += 1
                if line_count <= start_line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    traces.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return traces, line_count

    def load_by_hand(self, hand_id: str) -> list[dict[str, Any]]:
        return [t for t in self.load_all() if t.get("hand_id") == hand_id]

    def clear(self) -> None:
        if self.filepath.exists():
            self.filepath.unlink()
