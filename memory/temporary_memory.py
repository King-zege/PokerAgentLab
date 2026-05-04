"""Temporary memory pool for repeated low-confidence profile findings."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


TEMP_STATUSES = {"temporary", "promoted", "rejected", "archived"}


@dataclass
class TemporaryMemory:
    id: str
    category: str
    content: str
    evidence_session_ids: list[str]
    hit_count: int
    miss_count: int
    confidence: float
    last_seen_at: str
    created_at: str
    status: str = "temporary"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TemporaryMemoryStore:
    """JSON-backed temporary memory store used by MemoryManagerAgent."""

    def __init__(self, user_id: str | None = None, filepath: str | None = None):
        self.user_id = user_id or os.environ.get("POKER_MEMORY_USER_ID", "default_user")
        self.filepath = Path(filepath or f"data/memory/temporary_memory_{self.user_id}.json")
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self.last_fallback_reason = ""

    def list_memories(self, status: str | None = None) -> list[TemporaryMemory]:
        memories = self._load()
        if status:
            memories = [m for m in memories if m.status == status]
        return memories

    def upsert(
        self,
        category: str,
        content: str,
        evidence_session_ids: list[str] | None = None,
        confidence: float = 0.5,
        similarity_threshold: float = 0.62,
    ) -> TemporaryMemory:
        cleaned = " ".join(content.strip().split())
        if not cleaned:
            raise ValueError("Temporary memory content cannot be empty")
        now = datetime.now().isoformat()
        memories = self._load()
        best_match: TemporaryMemory | None = None
        best_score = 0.0
        for memory in memories:
            if memory.status != "temporary" or memory.category != category:
                continue
            score = self._similarity(memory.content, cleaned)
            if memory.content.lower() == cleaned.lower() or score >= similarity_threshold:
                best_match = memory
                best_score = score
                break
            if score > best_score:
                best_match = memory
                best_score = score

        if best_match and best_match.status == "temporary" and best_score >= similarity_threshold:
            best_match.evidence_session_ids = sorted(set(best_match.evidence_session_ids + (evidence_session_ids or [])))
            best_match.hit_count += 1
            best_match.miss_count = 0
            best_match.confidence = max(best_match.confidence, max(0.0, min(1.0, confidence)))
            best_match.last_seen_at = now
            self._save(memories)
            return best_match

        memory = TemporaryMemory(
            id=f"tmp_{uuid4().hex[:12]}",
            category=category,
            content=cleaned,
            evidence_session_ids=evidence_session_ids or [],
            hit_count=1,
            miss_count=0,
            confidence=max(0.0, min(1.0, confidence)),
            last_seen_at=now,
            created_at=now,
        )
        memories.append(memory)
        self._save(memories)
        return memory

    def mark_misses(self, observed_contents: list[str], archive_misses: int) -> list[dict[str, Any]]:
        observed = [text for text in observed_contents if text]
        memories = self._load()
        changed = []
        for memory in memories:
            if memory.status != "temporary":
                continue
            if any(self._similarity(memory.content, text) >= 0.62 for text in observed):
                continue
            memory.miss_count += 1
            if memory.miss_count >= archive_misses:
                memory.status = "rejected"
            changed.append(memory.to_dict())
        if changed:
            self._save(memories)
        return changed

    def set_status(self, memory_id: str, status: str) -> TemporaryMemory | None:
        if status not in TEMP_STATUSES:
            raise ValueError(f"Invalid temporary memory status: {status}")
        memories = self._load()
        for memory in memories:
            if memory.id == memory_id:
                memory.status = status
                self._save(memories)
                return memory
        return None

    def get(self, memory_id: str) -> TemporaryMemory | None:
        for memory in self._load():
            if memory.id == memory_id:
                return memory
        return None

    def _load(self) -> list[TemporaryMemory]:
        self.last_fallback_reason = ""
        if not self.filepath.exists():
            return []
        try:
            raw = json.loads(self.filepath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.last_fallback_reason = f"temporary memory unavailable: {exc}"
            return []
        memories = []
        for item in raw.get("memories", []):
            try:
                if item.get("status") in TEMP_STATUSES:
                    memories.append(TemporaryMemory(**item))
            except TypeError:
                continue
        return memories

    def _save(self, memories: list[TemporaryMemory]) -> None:
        data = {"user_id": self.user_id, "memories": [m.to_dict() for m in memories]}
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _similarity(self, left: str, right: str) -> float:
        left_terms = {t for t in re.split(r"\W+", left.lower()) if len(t) > 2}
        right_terms = {t for t in re.split(r"\W+", right.lower()) if len(t) > 2}
        if not left_terms or not right_terms:
            return 0.0
        return len(left_terms & right_terms) / len(left_terms | right_terms)
