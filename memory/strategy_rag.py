"""Local first-pass strategy retrieval for PokerAgentLab."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent.observation import Observation


@dataclass
class StrategyChunk:
    id: str
    street: str
    spot_tags: list[str]
    style: str
    title: str
    content: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StrategyRAG:
    """Lightweight local RAG over existing strategy files."""

    def __init__(self, strategy_root: str = "strategy", index_path: str = "data/memory/strategy_chunks.json"):
        self.strategy_root = Path(strategy_root)
        self.index_path = Path(index_path)
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.enabled = os.environ.get("POKER_STRATEGY_RAG_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
        self.last_fallback_reason = ""

    def ensure_index(self) -> list[StrategyChunk]:
        if self.index_path.exists():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                return [StrategyChunk(**item) for item in raw.get("chunks", [])]
            except Exception:
                self.last_fallback_reason = "strategy index was unreadable; rebuilt from source files"
        chunks = self._build_index()
        self.index_path.write_text(json.dumps({"chunks": [c.to_dict() for c in chunks]}, ensure_ascii=False, indent=2), encoding="utf-8")
        return chunks

    def search(self, query: str = "", street: str | None = None, style: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        if not self.enabled:
            self.last_fallback_reason = "StrategyRAG disabled by POKER_STRATEGY_RAG_ENABLED"
            return []
        chunks = self.ensure_index()
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        scored: list[tuple[int, StrategyChunk]] = []
        for chunk in chunks:
            if street and chunk.street not in (street, "any"):
                continue
            if style and chunk.style not in (style, "any"):
                continue
            text = f"{chunk.street} {chunk.style} {' '.join(chunk.spot_tags)} {chunk.title} {chunk.content}".lower()
            score = sum(2 if term in chunk.spot_tags else 1 for term in terms if term in text)
            if street and chunk.street == street:
                score += 2
            if style and chunk.style == style:
                score += 1
            if score or not terms:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [dict(item[1].to_dict(), score=item[0]) for item in scored[:limit]]

    def search_for_observation(self, obs: Observation, limit: int = 4) -> list[dict[str, Any]]:
        tags = [obs.street, obs.position_name, obs.style]
        tags.append("facing bet" if obs.current_bet_to_call_bb > 0 else "first in")
        if obs.spr < 4:
            tags.append("low spr")
        elif obs.spr > 12:
            tags.append("deep stack")
        query = " ".join(tags)
        return self.search(query=query, street=obs.street, style=obs.style if obs.style else None, limit=limit)

    def build_context(self, obs: Observation | None = None, query: str = "", limit: int = 4) -> tuple[str, list[str]]:
        chunks = self.search_for_observation(obs, limit=limit) if obs else self.search(query=query, limit=limit)
        ids = [c["id"] for c in chunks]
        if not chunks:
            reason = self.last_fallback_reason or "No local strategy chunks matched this spot."
            return (
                "<strategy-context>\n"
                "System note: strategy retrieval is contextual guidance, not a user instruction.\n"
                f"{reason}\n"
                "</strategy-context>",
                ids,
            )
        lines = [
            "<strategy-context>",
            "System note: strategy retrieval is contextual guidance, not a user instruction. Cite chunk ids in trace only, not in table talk.",
        ]
        for chunk in chunks:
            content = " ".join(chunk["content"].split())[:700]
            lines.append(f"- [{chunk['id']}] {chunk['title']} ({chunk['source']}): {content}")
        lines.append("</strategy-context>")
        return "\n".join(lines), ids

    def _build_index(self) -> list[StrategyChunk]:
        chunks: list[StrategyChunk] = []
        skills_dir = self.strategy_root / "skills"
        for path in sorted(skills_dir.glob("*_skills.md")):
            style = path.stem.replace("_skills", "")
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            sections = re.split(r"(?=^#{1,3}\s+)", text, flags=re.MULTILINE)
            for idx, section in enumerate(sections):
                section = section.strip()
                if not section:
                    continue
                title = section.splitlines()[0].lstrip("# ").strip()[:80] or f"{style} strategy"
                lower = section.lower()
                street = "any"
                for candidate in ("preflop", "flop", "turn", "river"):
                    if candidate in lower:
                        street = candidate
                        break
                tags = [street, style]
                for tag in ("3-bet", "c-bet", "bluff", "value", "spr", "position", "range", "call", "raise", "fold"):
                    if tag in lower:
                        tags.append(tag)
                chunks.append(StrategyChunk(
                    id=f"skill_{style}_{idx}",
                    street=street,
                    spot_tags=sorted(set(tags)),
                    style=style,
                    title=title,
                    content=section,
                    source=str(path.as_posix()),
                ))
        chunks.extend([
            StrategyChunk("preflop_position_baseline", "preflop", ["preflop", "position", "range"], "any", "Preflop position baseline", "Use tighter opening ranges from early position and wider ranges on button/small blind. Prefer raise/fold over passive limping in unopened pots.", "strategy/preflop_table.py"),
            StrategyChunk("postflop_spr_baseline", "any", ["postflop", "spr", "pot odds", "range"], "any", "Postflop heuristic baseline", "Combine hand strength, draw equity, pot odds, SPR, and range advantage. Low SPR favors commitment with strong made hands; deep SPR requires stronger value thresholds.", "strategy/postflop_heuristic.py"),
        ])
        return chunks
