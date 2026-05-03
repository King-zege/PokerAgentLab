"""Coach-style session review built on top of hand analysis."""

from __future__ import annotations

from collections import Counter
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
        action_counts: Counter[str] = Counter()
        street_counts: Counter[str] = Counter()
        showdown_count = 0
        total_focus_actions = 0

        for history in histories:
            analysis = self.analysis_agent.analyze_hand(history)
            reviews = analysis.action_reviews
            if focus_player_id:
                reviews = [r for r in reviews if r.player_id == focus_player_id]

            focus_actions = [
                action for action in history.actions
                if focus_player_id is None or action.player_id == focus_player_id
            ]
            total_focus_actions += len(focus_actions)
            for action in focus_actions:
                action_name = action.action.split()[0].lower() if action.action else "unknown"
                action_counts[action_name] += 1
                street_counts[action.street] += 1
            if len(history.community_cards) == 5:
                showdown_count += 1

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
                "action_count": len(focus_actions),
                "showdown": len(history.community_cards) == 5,
                "board": history.community_cards,
                "final_stacks": history.final_stacks,
            })

        if not histories:
            key_findings.append("当前 session 还没有已完成手牌。")
        elif total_deviations == 0:
            key_findings.append(
                f"已复盘 {len(histories)} 手牌，关注对象：{focus_player_id or '全部玩家'}；"
                "暂未检测到明显风格偏离。"
            )
            key_findings.append(
                f"动作样本：共追踪 {total_focus_actions} 次决策，"
                f"常见动作：{self._format_counter(action_counts)}。"
            )
            if showdown_count:
                key_findings.append(f"有 {showdown_count} 手牌进入五张公共牌摊牌，优先复盘全下/跟注范围。")
            else:
                key_findings.append("本次样本没有进入五张公共牌摊牌，主要验证了弃牌/跟注/加注流程。")
            training_goals.append("先累计至少 10 手牌，再对照目标风格比较弃牌/跟注/加注频率。")
            training_goals.append("每场标记一个河牌或全下决策点，判断跟注/弃牌阈值是否合理。")
        else:
            key_findings.append(f"在 {len(histories)} 手牌中发现 {total_deviations} 个风格或策略偏离点。")
            key_findings.append(f"常见动作：{self._format_counter(action_counts)}。")
            training_goals.append("先复盘标记出的关键决策，再增加桌面人数或 LLM 复杂度。")
            training_goals.append("下一次自博弈报告中继续追踪同类偏离是否重复出现。")

        return {
            "total_hands": len(histories),
            "key_findings": key_findings,
            "training_goals": training_goals,
            "hand_reviews": hand_reviews,
        }

    def _format_counter(self, counts: Counter[str]) -> str:
        if not counts:
            return "暂无"
        return "，".join(f"{self._translate_action(name)}={count}" for name, count in counts.most_common(4))

    def _translate_action(self, action: str) -> str:
        action_map = {
            "fold": "弃牌",
            "check": "过牌",
            "call": "跟注",
            "bet": "下注",
            "raise": "加注",
            "all_in": "全下",
            "all-in": "全下",
            "unknown": "未知",
        }
        return action_map.get(action, action)
