"""Background memory governance agent for session-level profile updates."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis.analysis_agent import AnalysisAgent
from analysis.coach_agent import CoachAgent
from memory.decision_trace import DecisionTraceStore
from memory.hand_history import HandHistory
from memory.history_store import HistoryStore
from memory.temporary_memory import TemporaryMemoryStore
from memory.user_profile import LongTermUserProfile
from strategy.style_profile import StyleRegistry


REPORT_DIR = Path("data/memory")


@dataclass
class MemoryFinding:
    category: str
    content: str
    confidence: float
    evidence_session_ids: list[str]
    evidence_count: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "content": self.content,
            "confidence": self.confidence,
            "evidence_session_ids": self.evidence_session_ids,
            "evidence_count": self.evidence_count,
            "reason": self.reason,
        }


class MemoryManagerAgent:
    """Runs background memory consolidation, temporary memory, and profile governance."""

    def __init__(
        self,
        user_profile: LongTermUserProfile | None = None,
        temporary_store: TemporaryMemoryStore | None = None,
        auto_accept_threshold: float | None = None,
        temporary_threshold: float | None = None,
        promote_hits: int | None = None,
        archive_misses: int | None = None,
    ):
        self.user_profile = user_profile or LongTermUserProfile()
        self.temporary_store = temporary_store or TemporaryMemoryStore(user_id=self.user_profile.user_id)
        self.enabled = os.environ.get("POKER_MEMORY_AGENT_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
        self.auto_accept_threshold = auto_accept_threshold if auto_accept_threshold is not None else float(os.environ.get("POKER_MEMORY_AUTO_ACCEPT_THRESHOLD", "0.82"))
        self.temporary_threshold = temporary_threshold if temporary_threshold is not None else float(os.environ.get("POKER_MEMORY_TEMPORARY_THRESHOLD", "0.55"))
        self.promote_hits = promote_hits if promote_hits is not None else int(os.environ.get("POKER_MEMORY_TEMP_PROMOTE_HITS", "3"))
        self.archive_misses = archive_misses if archive_misses is not None else int(os.environ.get("POKER_MEMORY_TEMP_ARCHIVE_MISSES", "5"))

    @classmethod
    def report_path(cls, session_id: str) -> Path:
        return REPORT_DIR / f"memory_agent_report_{session_id}.json"

    @classmethod
    def load_report(cls, session_id: str) -> dict[str, Any] | None:
        path = cls.report_path(session_id)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def run_session(
        self,
        session_id: str,
        histories: list[HandHistory] | None = None,
        coach_result: dict[str, Any] | None = None,
        focus_player_id: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        report_path = self.report_path(session_id)
        if report_path.exists() and not force:
            return json.loads(report_path.read_text(encoding="utf-8"))

        report: dict[str, Any] = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "enabled": self.enabled,
            "focus_player_id": focus_player_id,
            "findings": [],
            "actions": [],
            "temporary_updates": [],
            "archived_memories": [],
            "fallback_reason": "",
        }
        if not self.enabled:
            report["fallback_reason"] = "POKER_MEMORY_AGENT_ENABLED is false"
            return self._save_report(report_path, report)

        try:
            histories = histories if histories is not None else HistoryStore(f"data/history/hand_history_{session_id}.jsonl").load_all()
            traces = DecisionTraceStore.for_session(session_id).load_all()
            if coach_result is None:
                coach = CoachAgent(AnalysisAgent(StyleRegistry("config/styles")))
                coach_result = coach.review_session(histories, focus_player_id=focus_player_id)

            findings = self._build_findings(session_id, histories, coach_result, focus_player_id)
            report["findings"] = [finding.to_dict() for finding in findings]
            report["trace_count"] = len(traces)
            report["total_hands"] = len(histories)

            report["temporary_updates"] = self.temporary_store.mark_misses(
                [finding.content for finding in findings],
                archive_misses=self.archive_misses,
            )
            report["archived_memories"] = self.user_profile.decay_unsupported_memories(
                [finding.content for finding in findings],
                archive_below=self.temporary_threshold,
            )

            for finding in findings:
                action = self._apply_finding(finding)
                report["actions"].append(action)

            report["governance_summary"] = self._governance_summary(report)
            return self._save_report(report_path, report)
        except Exception as exc:
            report["fallback_reason"] = f"memory manager agent failed: {exc}"
            return self._save_report(report_path, report)

    def promote_temporary(self, memory_id: str, status: str = "candidate") -> dict[str, Any] | None:
        temporary = self.temporary_store.get(memory_id)
        if temporary is None:
            return None
        if status not in {"candidate", "accepted"}:
            raise ValueError("Temporary memory can only be promoted to candidate or accepted")
        memory = self.user_profile.upsert_memory(
            temporary.category,
            temporary.content,
            temporary.evidence_session_ids,
            temporary.confidence,
            status=status,
        )
        self.temporary_store.set_status(memory_id, "promoted")
        return {"temporary_memory": temporary.to_dict(), "long_term_memory": memory.to_dict()}

    def reject_temporary(self, memory_id: str) -> dict[str, Any] | None:
        memory = self.temporary_store.set_status(memory_id, "rejected")
        return memory.to_dict() if memory else None

    def _build_findings(
        self,
        session_id: str,
        histories: list[HandHistory],
        coach_result: dict[str, Any],
        focus_player_id: str | None,
    ) -> list[MemoryFinding]:
        action_counts: Counter[str] = Counter()
        street_action_counts: Counter[str] = Counter()
        for history in histories:
            for action in history.actions:
                if focus_player_id and action.player_id != focus_player_id:
                    continue
                action_name = (action.action or "unknown").split()[0].lower().replace("-", "_")
                action_counts[action_name] += 1
                street_action_counts[f"{action.street}:{action_name}"] += 1

        findings: list[MemoryFinding] = []
        total_actions = sum(action_counts.values())
        if total_actions >= 4:
            call_ratio = action_counts.get("call", 0) / total_actions
            raise_ratio = (action_counts.get("raise", 0) + action_counts.get("bet", 0) + action_counts.get("all_in", 0)) / total_actions
            fold_ratio = action_counts.get("fold", 0) / total_actions
            if call_ratio >= 0.45:
                findings.append(MemoryFinding(
                    "leaks",
                    "User shows a recurring passive calling tendency; prioritize raise-or-fold review spots.",
                    round(min(0.9, 0.58 + call_ratio * 0.6), 2),
                    [session_id],
                    action_counts.get("call", 0),
                    f"call ratio {call_ratio:.2%} across {total_actions} actions",
                ))
            if raise_ratio >= 0.55:
                findings.append(MemoryFinding(
                    "leaks",
                    "User shows a recurring high-aggression tendency; review value thresholds and bluff selection.",
                    round(min(0.9, 0.56 + raise_ratio * 0.55), 2),
                    [session_id],
                    action_counts.get("raise", 0) + action_counts.get("bet", 0) + action_counts.get("all_in", 0),
                    f"aggressive action ratio {raise_ratio:.2%} across {total_actions} actions",
                ))
            if fold_ratio >= 0.55:
                findings.append(MemoryFinding(
                    "leaks",
                    "User may be over-folding; review pot odds and defense frequencies.",
                    round(min(0.88, 0.56 + fold_ratio * 0.55), 2),
                    [session_id],
                    action_counts.get("fold", 0),
                    f"fold ratio {fold_ratio:.2%} across {total_actions} actions",
                ))

        for leak in coach_result.get("leak_candidates", [])[:3]:
            title = str(leak.get("title") or "").strip()
            recommendation = str(leak.get("recommendation") or "").strip()
            if not title:
                continue
            content = f"Coach finding: {title}. {recommendation}".strip()
            severity = leak.get("severity", "low")
            confidence = 0.72 if severity == "high" else 0.62 if severity == "medium" else 0.56
            findings.append(MemoryFinding("leaks", content, confidence, [session_id], 1, "coach leak candidate"))

        if histories:
            findings.append(MemoryFinding(
                "goals",
                "Next training block: review critical decisions from the latest session before increasing hand volume.",
                0.58,
                [session_id],
                len(histories),
                "session completed with reviewable hand history",
            ))
        return findings

    def _apply_finding(self, finding: MemoryFinding) -> dict[str, Any]:
        existing = self.user_profile.search(finding.content, status=None, limit=5)
        repeated_existing = any(
            item.get("status") in {"accepted", "candidate"} and item.get("category") == finding.category
            for item in existing
        )
        enough_evidence = finding.evidence_count >= 2 or repeated_existing
        if finding.confidence >= self.auto_accept_threshold and enough_evidence:
            memory = self.user_profile.upsert_memory(
                finding.category,
                finding.content,
                finding.evidence_session_ids,
                finding.confidence,
                status="accepted",
            )
            return {"type": "accepted", "finding": finding.to_dict(), "memory": memory.to_dict()}

        temporary = None
        if finding.confidence >= self.temporary_threshold:
            temporary = self.temporary_store.upsert(
                finding.category,
                finding.content,
                finding.evidence_session_ids,
                finding.confidence,
            )
            if temporary.hit_count >= self.promote_hits:
                status = "accepted" if temporary.confidence >= self.auto_accept_threshold else "candidate"
                memory = self.user_profile.upsert_memory(
                    temporary.category,
                    temporary.content,
                    temporary.evidence_session_ids,
                    temporary.confidence,
                    status=status,
                )
                self.temporary_store.set_status(temporary.id, "promoted")
                return {
                    "type": f"temporary_promoted_to_{status}",
                    "finding": finding.to_dict(),
                    "temporary_memory": temporary.to_dict(),
                    "memory": memory.to_dict(),
                }
            return {"type": "temporary", "finding": finding.to_dict(), "temporary_memory": temporary.to_dict()}

        return {"type": "dropped_low_confidence", "finding": finding.to_dict()}

    def _governance_summary(self, report: dict[str, Any]) -> dict[str, Any]:
        actions = report.get("actions", [])
        return {
            "accepted_count": sum(1 for action in actions if action.get("type") == "accepted" or action.get("type", "").endswith("_to_accepted")),
            "candidate_count": sum(1 for action in actions if action.get("type", "").endswith("_to_candidate")),
            "temporary_count": sum(1 for action in actions if action.get("type") == "temporary"),
            "dropped_count": sum(1 for action in actions if action.get("type") == "dropped_low_confidence"),
            "temporary_miss_updates": len(report.get("temporary_updates", [])),
        }

    def _save_report(self, path: Path, report: dict[str, Any]) -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(path)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def run_memory_agent_async(session_id: str, focus_player_id: str | None = None, force: bool = False) -> None:
    """Run memory manager agent in a daemon thread."""
    import threading

    def _run() -> None:
        MemoryManagerAgent().run_session(session_id=session_id, focus_player_id=focus_player_id, force=force)

    threading.Thread(target=_run, daemon=True).start()
