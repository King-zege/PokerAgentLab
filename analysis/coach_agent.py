"""Coach-style training report built on top of hand analysis."""

from __future__ import annotations

from collections import Counter
from typing import Any

from analysis.analysis_agent import AnalysisAgent
from memory.hand_history import HandHistory


ACTION_LABELS = {
    "fold": "弃牌",
    "check": "过牌",
    "call": "跟注",
    "bet": "下注",
    "raise": "加注",
    "all_in": "全下",
    "all-in": "全下",
    "unknown": "未知",
}

STREET_LABELS = {
    "preflop": "翻前",
    "flop": "翻牌",
    "turn": "转牌",
    "river": "河牌",
    "showdown": "摊牌",
}


class CoachAgent:
    """Turns hand reviews into an interview-friendly training report."""

    def __init__(self, analysis_agent: AnalysisAgent):
        self.analysis_agent = analysis_agent

    def review_session(self, histories: list[HandHistory], focus_player_id: str | None = None) -> dict[str, Any]:
        hand_reviews: list[dict[str, Any]] = []
        key_findings: list[str] = []
        action_counts: Counter[str] = Counter()
        street_counts: Counter[str] = Counter()
        issue_counts: Counter[str] = Counter()
        total_deviations = 0
        total_focus_actions = 0
        showdown_count = 0
        critical_spots: list[dict[str, Any]] = []
        focus_start_stack = 0.0
        focus_final_stack = 0.0
        focus_stack_seen = False

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

            for player in history.players:
                if focus_player_id and player.get("id") == focus_player_id and not focus_stack_seen:
                    focus_start_stack = float(player.get("stack_bb", 0.0) or 0.0)
                    focus_stack_seen = True

            if focus_player_id and focus_player_id in history.final_stacks:
                focus_final_stack = float(history.final_stacks.get(focus_player_id, 0.0) or 0.0)

            for action in focus_actions:
                action_name = self._normalize_action(action.action)
                action_counts[action_name] += 1
                street_counts[action.street] += 1

            if len(history.community_cards) == 5:
                showdown_count += 1

            critical = []
            for review in reviews:
                if review.was_style_consistent:
                    continue
                issue = review.deviation_description or "策略或风格偏离"
                issue_counts[issue] += 1
                spot = {
                    "hand_id": history.hand_id,
                    "street": review.street,
                    "street_label": self._translate_street(review.street),
                    "player_id": review.player_id,
                    "action_taken": review.action_taken,
                    "issue": issue,
                    "suggested_action": review.suggested_action,
                    "reason": review.suggestion_reason,
                    "board": history.community_cards,
                }
                critical.append(spot)
                critical_spots.append(spot)

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

        summary = self._build_summary(
            histories=histories,
            focus_player_id=focus_player_id,
            total_actions=total_focus_actions,
            total_deviations=total_deviations,
            showdown_count=showdown_count,
            action_counts=action_counts,
            net_stack_change=focus_final_stack - focus_start_stack if focus_stack_seen else 0.0,
        )
        action_profile = self._build_profile(action_counts, ACTION_LABELS, total_focus_actions)
        street_profile = self._build_profile(street_counts, STREET_LABELS, total_focus_actions)
        leak_candidates = self._build_leak_candidates(action_counts, issue_counts, total_focus_actions, showdown_count)
        training_plan = self._build_training_plan(len(histories), leak_candidates, critical_spots, showdown_count)
        next_drill = self._build_next_drill(leak_candidates, critical_spots, len(histories))

        if not histories:
            key_findings.append("当前 session 还没有已完成手牌，先完成至少一手牌再生成有效训练报告。")
        elif total_deviations == 0:
            key_findings.append(
                f"已复盘 {len(histories)} 手牌，关注对象：{focus_player_id or '全部玩家'}；暂未检测到明显风格偏离。"
            )
            key_findings.append(f"动作样本：共追踪 {total_focus_actions} 次决策，常见动作：{self._format_counter(action_counts)}。")
        else:
            key_findings.append(f"在 {len(histories)} 手牌中发现 {total_deviations} 个风格或策略偏离点。")
            key_findings.append(f"常见动作：{self._format_counter(action_counts)}。")
        if showdown_count:
            key_findings.append(f"有 {showdown_count} 手牌进入五张公共牌摊牌，建议复盘全下/跟注范围。")

        training_goals = [item["title"] for item in training_plan]

        return {
            "report_title": "Poker Agent Lab 训练报告",
            "focus_player_id": focus_player_id,
            "summary": summary,
            "action_profile": action_profile,
            "street_profile": street_profile,
            "leak_candidates": leak_candidates,
            "critical_spots": critical_spots[:8],
            "training_plan": training_plan,
            "next_drill": next_drill,
            "total_hands": len(histories),
            "key_findings": key_findings,
            "training_goals": training_goals,
            "hand_reviews": hand_reviews,
        }

    def _build_summary(
        self,
        histories: list[HandHistory],
        focus_player_id: str | None,
        total_actions: int,
        total_deviations: int,
        showdown_count: int,
        action_counts: Counter[str],
        net_stack_change: float,
    ) -> dict[str, Any]:
        if not histories:
            sample_note = "暂无可复盘样本。"
        elif len(histories) < 5:
            sample_note = "样本较少，结论只适合作为训练方向，不应直接沉淀为长期漏洞。"
        else:
            sample_note = "样本量可用于观察初步趋势。"

        main_action = action_counts.most_common(1)[0][0] if action_counts else "unknown"
        return {
            "focus_player_id": focus_player_id or "全部玩家",
            "total_hands": len(histories),
            "total_actions": total_actions,
            "showdown_hands": showdown_count,
            "deviation_count": total_deviations,
            "main_pattern": self._translate_action(main_action),
            "net_stack_change_bb": round(net_stack_change, 2),
            "sample_note": sample_note,
        }

    def _build_profile(self, counts: Counter[str], labels: dict[str, str], total: int) -> list[dict[str, Any]]:
        items = []
        keys = [key for key in labels if key in counts]
        keys.extend(key for key in counts if key not in labels)
        for key in keys:
            count = counts.get(key, 0)
            items.append({
                "key": key,
                "label": labels.get(key, key),
                "count": count,
                "percentage": round((count / total) * 100, 1) if total else 0.0,
            })
        return items

    def _build_leak_candidates(
        self,
        action_counts: Counter[str],
        issue_counts: Counter[str],
        total_actions: int,
        showdown_count: int,
    ) -> list[dict[str, Any]]:
        leaks = []
        if total_actions >= 4:
            call_ratio = action_counts.get("call", 0) / total_actions
            raise_ratio = (action_counts.get("raise", 0) + action_counts.get("bet", 0)) / total_actions
            fold_ratio = action_counts.get("fold", 0) / total_actions
            if call_ratio >= 0.45:
                leaks.append({
                    "title": "跟注倾向偏高",
                    "severity": "medium",
                    "evidence": f"跟注占比 {round(call_ratio * 100, 1)}%，需要区分价值跟注和被动跟注。",
                    "recommendation": "复盘每个跟注点的底池赔率、对手范围和后续街计划。",
                })
            if raise_ratio >= 0.55:
                leaks.append({
                    "title": "进攻频率偏高",
                    "severity": "medium",
                    "evidence": f"下注/加注占比 {round(raise_ratio * 100, 1)}%，需要检查价值阈值和诈唬组合。",
                    "recommendation": "把加注拆成价值下注和诈唬下注，确认每类动作都有明确目标。",
                })
            if fold_ratio >= 0.55:
                leaks.append({
                    "title": "弃牌频率偏高",
                    "severity": "medium",
                    "evidence": f"弃牌占比 {round(fold_ratio * 100, 1)}%，可能防守不足。",
                    "recommendation": "优先训练盲注防守、底池赔率和最小防守频率。",
                })

        for issue, count in issue_counts.most_common(3):
            if count >= 1:
                leaks.append({
                    "title": "关键决策偏离",
                    "severity": "high" if count >= 2 else "low",
                    "evidence": f"{issue}，出现 {count} 次。",
                    "recommendation": "下次训练前先重放这些决策点，再进入新一轮对局。",
                })

        if showdown_count > 0 and not leaks:
            leaks.append({
                "title": "摊牌样本待复查",
                "severity": "low",
                "evidence": f"本次有 {showdown_count} 手牌进入摊牌。",
                "recommendation": "检查河牌跟注阈值、全下范围和摊牌时的价值下注尺度。",
            })
        return leaks

    def _build_training_plan(
        self,
        hand_count: int,
        leak_candidates: list[dict[str, Any]],
        critical_spots: list[dict[str, Any]],
        showdown_count: int,
    ) -> list[dict[str, Any]]:
        plan = []
        if hand_count < 5:
            plan.append({
                "step": 1,
                "title": "扩大样本到 10 手牌",
                "description": "当前样本较少，先完成更多手牌，避免把单手结果误判为长期漏洞。",
                "success_metric": "至少完成 10 手牌，并能看到动作画像分布。",
            })
        if critical_spots:
            plan.append({
                "step": len(plan) + 1,
                "title": "复盘关键决策点",
                "description": "逐条检查实际动作、建议动作和原因，确认偏离来自策略选择还是随机牌面。",
                "success_metric": "每个关键点都能写出一个更优动作或保持原动作的理由。",
            })
        if leak_candidates:
            plan.append({
                "step": len(plan) + 1,
                "title": "选择一个漏洞做专项训练",
                "description": "只选择一个最可信的漏洞进入下一轮训练，避免同时调整过多策略变量。",
                "success_metric": "下一轮同类问题出现次数下降，或解释质量明显提高。",
            })
        if showdown_count:
            plan.append({
                "step": len(plan) + 1,
                "title": "复查摊牌和全下范围",
                "description": "重点检查摊牌前最后一次跟注/下注/全下是否符合赔率和范围优势。",
                "success_metric": "能说明每个摊牌手牌的跟注阈值和对手价值范围。",
            })
        if not plan:
            plan.append({
                "step": 1,
                "title": "保持当前策略并继续采样",
                "description": "暂未发现明显漏洞，下一轮重点观察是否出现重复偏差。",
                "success_metric": "完成 20 手牌后仍未出现重复偏离。",
            })
        return plan

    def _build_next_drill(
        self,
        leak_candidates: list[dict[str, Any]],
        critical_spots: list[dict[str, Any]],
        hand_count: int,
    ) -> dict[str, Any]:
        if critical_spots:
            return {
                "title": "10 手关键决策重放训练",
                "hands": 10,
                "focus": "重放本场出现偏离的街道，训练实际动作与建议动作的对比解释。",
            }
        if leak_candidates:
            return {
                "title": "20 手单漏洞专项训练",
                "hands": 20,
                "focus": leak_candidates[0]["title"],
            }
        return {
            "title": "20 手稳定性采样训练",
            "hands": 20 if hand_count else 10,
            "focus": "继续收集动作分布和摊牌样本，确认是否存在重复模式。",
        }

    def _format_counter(self, counts: Counter[str]) -> str:
        if not counts:
            return "暂无"
        return "，".join(f"{self._translate_action(name)}={count}" for name, count in counts.most_common(4))

    def _normalize_action(self, action: str) -> str:
        if not action:
            return "unknown"
        name = action.split()[0].lower().replace("-", "_")
        if name == "all":
            return "all_in"
        return name

    def _translate_action(self, action: str) -> str:
        return ACTION_LABELS.get(action, action)

    def _translate_street(self, street: str) -> str:
        return STREET_LABELS.get(street, street)
