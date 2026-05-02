"""Session memory consolidation into candidate long-term profile memories."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from memory.decision_trace import DecisionTraceStore
from memory.hand_history import HandHistory
from memory.short_term import ShortTermHandMemory
from memory.user_profile import LongTermUserProfile


class MemoryConsolidator:
    """Promotes repeated session patterns into reviewable candidate memories."""

    def __init__(self, user_profile: LongTermUserProfile | None = None):
        self.user_profile = user_profile or LongTermUserProfile()

    def consolidate_session(
        self,
        session_id: str,
        histories: list[HandHistory],
        coach_result: dict[str, Any] | None = None,
        focus_player_id: str | None = None,
    ) -> dict[str, Any]:
        trace_store = DecisionTraceStore.for_session(session_id)
        traces = trace_store.load_all()
        candidates = []
        action_counts: Counter[str] = Counter()
        street_counts: Counter[str] = Counter()
        deviation_counts: Counter[str] = Counter()

        for history in histories:
            for action in history.actions:
                if focus_player_id and action.player_id != focus_player_id:
                    continue
                action_name = action.action.split()[0].lower()
                action_counts[action_name] += 1
                street_counts[f"{action.street}:{action_name}"] += 1

        if coach_result:
            for hand in coach_result.get("hand_reviews", []):
                for decision in hand.get("critical_decisions", []):
                    issue = decision.get("issue") or "style or strategy deviation"
                    deviation_counts[issue] += 1

        total_actions = sum(action_counts.values())
        if total_actions >= 4:
            call_ratio = action_counts.get("call", 0) / total_actions
            raise_ratio = (action_counts.get("raise", 0) + action_counts.get("bet", 0)) / total_actions
            fold_ratio = action_counts.get("fold", 0) / total_actions
            if call_ratio >= 0.45:
                candidates.append(self.user_profile.upsert_candidate("leaks", "User shows a recurring passive/calling tendency; prioritize spots where raise-or-fold is clearer.", [session_id], 0.68).to_dict())
            if raise_ratio >= 0.55:
                candidates.append(self.user_profile.upsert_candidate("leaks", "User shows a recurring high-aggression tendency; review value thresholds and bluff selection.", [session_id], 0.64).to_dict())
            if fold_ratio >= 0.55:
                candidates.append(self.user_profile.upsert_candidate("leaks", "User may be over-folding in recent hands; review pot odds and defense frequencies.", [session_id], 0.62).to_dict())

        for issue, count in deviation_counts.items():
            if count >= 2:
                candidates.append(self.user_profile.upsert_candidate("leaks", f"Repeated review finding: {issue}", [session_id], min(0.9, 0.55 + count * 0.1)).to_dict())

        if histories:
            candidates.append(self.user_profile.upsert_candidate("goals", "Next training block: replay critical decisions from this session before increasing table size or LLM complexity.", [session_id], 0.55).to_dict())

        session_summary = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "total_hands": len(histories),
            "focus_player_id": focus_player_id,
            "action_counts": dict(action_counts),
            "street_action_counts": dict(street_counts),
            "trace_count": len(traces),
            "candidate_memory_ids": sorted({c["id"] for c in candidates}),
            "coach_findings": (coach_result or {}).get("key_findings", []),
        }
        training_plan = self._build_training_plan(session_summary, candidates)
        ShortTermHandMemory(session_id).save_session_summary({"summary": session_summary, "training_plan": training_plan})
        return {
            "session_id": session_id,
            "session_summary": session_summary,
            "candidate_memories": candidates,
            "training_plan": training_plan,
        }

    def _build_training_plan(self, summary: dict[str, Any], candidates: list[dict[str, Any]]) -> list[str]:
        plan = []
        leaks = [c for c in candidates if c.get("category") == "leaks"]
        if leaks:
            plan.append("Review the repeated leak candidates and accept only the ones that match your intent.")
            plan.append("Run a 20-hand focused drill after accepting a leak memory, then compare the next session trace.")
        else:
            plan.append("Run a longer sample before promoting any single-hand pattern into long-term memory.")
        if summary.get("trace_count", 0) > 0:
            plan.append("Open AgentTracePanel and inspect whether memory/RAG evidence aligns with each decision.")
        return plan
