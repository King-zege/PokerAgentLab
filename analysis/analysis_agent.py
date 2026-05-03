"""Analysis agent: post-hand review comparing actions against style guidelines."""

from dataclasses import dataclass
from memory.hand_history import ActionRecord, HandHistory
from strategy.style_profile import StyleProfile, StyleRegistry


@dataclass
class ActionReview:
    """Review of a single action decision."""
    street: str
    player_id: str
    style: str
    action_taken: str
    explanation: str
    was_style_consistent: bool
    deviation_description: str | None
    suggested_action: str | None
    suggestion_reason: str | None


@dataclass
class HandAnalysis:
    """Complete analysis of a hand."""
    hand_id: str
    action_reviews: list[ActionReview]
    overall_notes: list[str]
    style_deviation_count: dict[str, int]


class AnalysisAgent:
    """Reviews completed hands and produces analysis."""

    def __init__(self, style_registry: StyleRegistry):
        self.styles = style_registry

    def analyze_hand(self, history: HandHistory) -> HandAnalysis:
        """Analyze a completed hand."""
        reviews: list[ActionReview] = []
        deviations_by_style: dict[str, int] = {}

        for record in history.actions:
            player_id = record.player_id
            style_name = record.style

            # Get player's style profile
            style = self.styles.get(style_name)

            # Analyze the action
            review = self._evaluate_action(record, style, history)
            reviews.append(review)

            if not review.was_style_consistent:
                deviations_by_style[style_name] = deviations_by_style.get(style_name, 0) + 1

        # Generate overall notes
        notes = self._generate_notes(reviews, history)

        return HandAnalysis(
            hand_id=history.hand_id,
            action_reviews=reviews,
            overall_notes=notes,
            style_deviation_count=deviations_by_style,
        )

    def _evaluate_action(
        self,
        record: ActionRecord,
        style: StyleProfile | None,
        history: HandHistory,
    ) -> ActionReview:
        """Evaluate if an action was consistent with the player's style."""
        if style is None:
            return ActionReview(
                street=record.street,
                player_id=record.player_id,
                style=record.style,
                action_taken=record.action,
                explanation=record.explanation,
                was_style_consistent=True,
                deviation_description=None,
                suggested_action=None,
                suggestion_reason=None,
            )

        street = record.street
        action = record.action.lower()

        # Simple rule-based evaluation
        is_consistent = True
        deviation = None
        suggested = None
        reason = None

        # Check fold actions by style
        if "fold" in action:
            if style.name.lower() in ("lag", "maniac", "callingstation", "lp"):
                # These styles shouldn't fold too often
                is_consistent = False
                deviation = f"{style.display_name} 不应该频繁弃牌"
                suggested = "call 或 check"
                reason = "松风格玩家应该更激进"
            elif style.name.lower() in ("nit", "tag"):
                # These styles often fold - likely consistent
                pass

        # Check raise/bet actions by style
        elif any(x in action for x in ["raise", "bet"]):
            if style.name.lower() in ("nit", "tp", "lp", "callingstation"):
                # These styles are passive - should raise less
                is_consistent = False
                deviation = f"{style.display_name} 应该更被动，少加注"
                suggested = "call 或 check"
                reason = "被动风格玩家应减少加注"

        # Check call actions by style
        elif "call" in action:
            if style.name.lower() in ("lag", "maniac"):
                # These styles are aggressive - might want to raise
                is_consistent = False
                deviation = f"{style.display_name} 应该更激进，考虑加注"
                suggested = "raise 或 bet"
                reason = "松凶风格应该加注而不是跟注"

        # Check all-in actions
        elif "all_in" in action:
            if style.name.lower() in ("nit", "tp"):
                # These styles rarely go all-in
                is_consistent = False
                deviation = f"{style.display_name} 全押不常见"
                suggested = "bet 或 call"
                reason = "紧风格玩家全押应该只用于坚果牌"

        return ActionReview(
            street=record.street,
            player_id=record.player_id,
            style=style.display_name,
            action_taken=record.action,
            explanation=record.explanation,
            was_style_consistent=is_consistent,
            deviation_description=deviation,
            suggested_action=suggested,
            suggestion_reason=reason,
        )

    def _generate_notes(self, reviews: list[ActionReview], history: HandHistory) -> list[str]:
        """Generate overall notes about the hand."""
        notes = []

        # Check for frequent deviations
        if len(reviews) > 3:
            inconsistent_count = sum(1 for r in reviews if not r.was_style_consistent)
            if inconsistent_count > len(reviews) * 0.5:
                notes.append(f"⚠️ 这手牌有 {inconsistent_count} 个偏离风格的操作")

        # Check winners
        for pot in history.pots:
            for winner in pot.get("winners", []):
                player = winner.get("player", "")
                hand = winner.get("hand", "")
                notes.append(f"✓ {player} 赢得底池（{self._translate_hand_name(hand)}）")

        return notes

    def _translate_hand_name(self, hand_name: str) -> str:
        hand_map = {
            "High Card": "高牌",
            "Pair": "一对",
            "Two Pair": "两对",
            "Three of a Kind": "三条",
            "Straight": "顺子",
            "Flush": "同花",
            "Full House": "葫芦",
            "Four of a Kind": "四条",
            "Straight Flush": "同花顺",
            "Royal Flush": "皇家同花顺",
            "Last player": "最后未弃牌玩家",
        }
        return hand_map.get(hand_name, hand_name)


def print_analysis(analysis: HandAnalysis) -> None:
    """Print analysis in a readable format."""
    print(f"\n{'='*60}")
    print(f"手牌分析: {analysis.hand_id}")
    print(f"{'='*60}")

    for review in analysis.action_reviews:
        status = "✓" if review.was_style_consistent else "✗"
        print(f"\n{status} {review.player_id} ({review.style})")
        print(f"  街: {review.street}")
        print(f"  动作: {review.action_taken}")
        print(f"  解释: {review.explanation}")

        if not review.was_style_consistent:
            print(f"  ⚠️ 偏离: {review.deviation_description}")
            print(f"  💡 建议: {review.suggested_action} - {review.suggestion_reason}")

    if analysis.overall_notes:
        print(f"\n整体评注:")
        for note in analysis.overall_notes:
            print(f"  {note}")

    if analysis.style_deviation_count:
        print(f"\n偏离统计:")
        for style, count in analysis.style_deviation_count.items():
            print(f"  {style}: {count} 次")
