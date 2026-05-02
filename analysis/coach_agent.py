"""Coach-style session review built on top of hand analysis."""

from __future__ import annotations

from typing import Any

from analysis.analysis_agent import AnalysisAgent
from memory.hand_history import HandHistory


class CoachAgent:
    """Turns hand reviews into interview-friendly training feedback."""

    def __init__(self, analysis_agent: AnalysisAgent):
        self.analysis_agent = analysis_agent

    def review_session(self, histories: list[HandHistory], focus_player_id: str | None = None) -> dict[str, Any]:
        hand_reviews: list[dict[str, Any]] = []
        key_findings: list[str] = []
        training_goals: list[str] = []
        total_deviations = 0

        for history in histories:
            analysis = self.analysis_agent.analyze_hand(history)
            reviews = analysis.action_reviews
            if focus_player_id:
                reviews = [r for r in reviews if r.player_id == focus_player_id]

            critical = [
                {
                    "street": r.street,
                    "player_id": r.player_id,
                    "action_taken": r.action_taken,
                    "issue": r.deviation_description,
                    "suggested_action": r.suggested_action,
                    "reason": r.suggestion_reason,
                }
                for r in reviews
                if not r.was_style_consistent
            ]
            total_deviations += len(critical)
            hand_reviews.append({
                "hand_id": history.hand_id,
                "critical_decisions": critical[:3],
                "overall_notes": analysis.overall_notes,
            })

        if not histories:
            key_findings.append("No completed hands were found for this session.")
        elif total_deviations == 0:
            key_findings.append("Decision patterns were consistent with configured agent styles.")
            training_goals.append("Run a longer sample and compare VPIP/PFR against target ranges.")
        else:
            key_findings.append(f"Found {total_deviations} style or strategy deviations across {len(histories)} hands.")
            training_goals.append("Review the marked critical decisions before increasing table size or LLM complexity.")
            training_goals.append("Track whether the same deviation repeats in the next self-play report.")

        return {
            "total_hands": len(histories),
            "key_findings": key_findings,
            "training_goals": training_goals,
            "hand_reviews": hand_reviews,
        }
