"""Game session: orchestrates multiple hands."""

import yaml
from pathlib import Path
from engine.hand import Hand, HandResult
from agent.base_agent import BaseAgent
from agent.style_agent import StyleAgent
from agent.human_agent import HumanAgent
from agent.llm_agent import LLMAgent
from agent.rule_agent import RuleAgent
from strategy.style_profile import StyleRegistry
from memory.decision_logger import DecisionLogger
from memory.history_store import HistoryStore
from memory.hand_history import HandHistory
from memory.decision_trace import DecisionTraceStore
from memory.poker_memory_manager import PokerMemoryManager


class Game:
    """Orchestrates a multi-hand poker session."""

    def __init__(self, config_path: str, session_id: str | None = None):
        self.config = self._load_config(config_path)
        self.players: list[dict] = []
        self.button_index = 0
        self.hand_count = 0
        self.results: list[HandResult] = []
        self.session_id = session_id
        self.state_callback = None

        # Load style profiles
        styles_dir = Path(config_path).parent / "styles"
        self.style_registry = StyleRegistry(str(styles_dir))

        # Initialize players from config
        for p in self.config["players"]:
            normalized = self._normalize_player_config(p)
            stack_bb = float(p["stack_bb"])
            self.players.append({
                "id": p["id"],
                "agent_type": normalized["agent_type"],
                "style": normalized["style"],
                "stack_bb": stack_bb,
                "initial_stack_bb": stack_bb,
            })

        # Create agent map
        self.memory_manager = PokerMemoryManager(session_id=session_id or "default")
        self.agent_map: dict[str, BaseAgent] = {}
        self.human_id = None
        llm_config = self._resolve_llm_config(self.config.get("llm", {}))

        for p in self.players:
            agent_type = p.get("agent_type", "llm")
            style_profile = None
            if p["style"] != "human":
                style_profile = self._get_style_profile(p["style"])

            if agent_type == "human":
                self.agent_map[p["id"]] = HumanAgent(p["id"], "human")
                self.human_id = p["id"]
            elif agent_type == "llm":
                import os
                if llm_config.get("enabled") and llm_config.get("api_key"):
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(config_path)))
                    skills_dir = os.path.join(project_root, "strategy", "skills")
                    self.agent_map[p["id"]] = LLMAgent(
                        player_id=p["id"],
                        api_key=llm_config.get("api_key"),
                        api_base=llm_config.get("api_base", "https://api.openai.com/v1"),
                        model=llm_config.get("model", "gpt-4o-mini"),
                        style=p["style"],
                        style_profile=style_profile,
                        skills_dir=skills_dir,
                        use_skills_in_prompt=llm_config.get("use_skills_in_prompt", True),
                        memory_manager=self.memory_manager,
                    )
                else:
                    self.agent_map[p["id"]] = StyleAgent(p["id"], style_profile)
            elif agent_type == "rule":
                self.agent_map[p["id"]] = RuleAgent(p["id"], style=p["style"])
            else:
                self.agent_map[p["id"]] = StyleAgent(p["id"], style_profile)

        # Initialize memory modules
        session_config = self.config.get("session", {})
        base_history = session_config.get("history_file", "hand_history.jsonl")
        base_log = session_config.get("decision_log", "decision_log.txt")

        if session_id:
            # Generate session-specific filenames
            history_path = f"data/history/{self._generate_session_filename(base_history, session_id)}"
            log_path = f"data/history/{self._generate_session_filename(base_log, session_id)}"
        else:
            history_path = base_history
            log_path = base_log

        self.history_store = HistoryStore(history_path)
        self.decision_logger = DecisionLogger(log_path)
        self.trace_store = DecisionTraceStore.for_session(session_id or "default")
        self.table_size = self.config.get("table", {}).get("size", 6)

    def _normalize_player_config(self, player: dict) -> dict:
        """Normalize old and new player config shapes.

        New shape uses agent_type for implementation and style for poker style.
        Old shape style=llm + llm_style=balanced is still supported.
        """
        style = player.get("style", "balanced")
        agent_type = player.get("agent_type")
        if agent_type is None:
            if style == "human":
                agent_type = "human"
            elif style == "llm":
                agent_type = "llm"
                style = player.get("llm_style", "balanced")
            else:
                agent_type = "llm"
        return {"agent_type": agent_type, "style": style}

    def _get_style_profile(self, style_name: str):
        style_profile = self.style_registry.get(style_name)
        if style_profile is not None:
            return style_profile
        fallback_name = "balanced" if "balanced" in self.style_registry.list_styles() else self.style_registry.list_styles()[0]
        return self.style_registry.get(fallback_name)

    def set_state_callback(self, callback) -> None:
        """Register a callback for live table snapshots during a hand."""
        self.state_callback = callback

    def _resolve_llm_config(self, llm_config: dict) -> dict:
        """Merge YAML LLM settings with environment variables."""
        import os
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass

        resolved = dict(llm_config or {})
        enabled_env = os.environ.get("POKER_LLM_ENABLED")
        if enabled_env is not None:
            resolved["enabled"] = enabled_env.strip().lower() in ("1", "true", "yes", "on")
        else:
            resolved["enabled"] = bool(resolved.get("enabled", False))

        resolved["api_key"] = os.environ.get("POKER_LLM_API_KEY", resolved.get("api_key", ""))
        resolved["api_base"] = os.environ.get("POKER_LLM_API_BASE", resolved.get("api_base", "https://api.openai.com/v1"))
        resolved["model"] = os.environ.get("POKER_LLM_MODEL", resolved.get("model", "gpt-4o-mini"))
        return resolved

    def _generate_session_filename(self, base: str, session_id: str) -> str:
        """Generate session-specific filename by inserting session_id before extension."""
        import os
        name, ext = os.path.splitext(base)
        return f"{name}_{session_id}{ext}"

    def play_hand(self) -> HandResult:
        """Play a single hand."""
        self.hand_count += 1
        hand_id = f"h{self.hand_count:03d}"

        # Get active players (with chips)
        active_players = [p for p in self.players if p["stack_bb"] > 0]
        if len(active_players) < 2:
            raise ValueError("Need at least 2 players with chips to play")

        # Determine seed for this hand
        seed = None
        if self.config.get("session", {}).get("seed") is not None:
            seed = self.config["session"]["seed"] + self.hand_count

        hand = Hand(
            players=active_players,
            button_index=self.button_index % len(active_players),
            small_blind_bb=self.config["table"]["small_blind_bb"],
            big_blind_bb=self.config["table"]["big_blind_bb"],
            hand_id=hand_id,
            deck_seed=seed,
            session_id=self.session_id or "default",
            trace_store=self.trace_store,
            state_callback=self.state_callback,
        )

        result = hand.play(self.agent_map)

        # Update player stacks from result
        for final_seat in result.final_seats:
            for p in self.players:
                if p["id"] == final_seat["player_id"]:
                    p["stack_bb"] = final_seat["stack_bb"]

        # Advance button
        self.button_index = (self.button_index + 1) % len(active_players)

        self.results.append(result)
        return result

    def play_session(self, num_hands: int | None = None, interactive: bool = False) -> list[HandResult]:
        """Play multiple hands. Prompts between hands to continue or quit."""
        if num_hands is None:
            num_hands = self.config.get("session", {}).get("num_hands", 10)
        if not interactive and self.human_id and isinstance(self.agent_map.get(self.human_id), HumanAgent):
            self.agent_map[self.human_id] = RuleAgent(self.human_id, style="human-auto")

        hands_played = 0

        while True:
            active = [p for p in self.players if p["stack_bb"] > 0]
            if len(active) < 2:
                print(f"\n只有 {len(active)} 个玩家有筹码，游戏结束。")
                break

            # Check if human has lost all chips
            human = next((p for p in self.players if p["id"] == self.human_id), None)
            if human and human["stack_bb"] <= 0:
                print(f"\n【{human['id']}】筹码归零，游戏结束。")
                break

            result = self.play_hand()
            self._print_hand_result(result)
            self._save_hand_history(result)
            hands_played += 1

            if not interactive:
                if num_hands is not None and hands_played >= num_hands:
                    break
                continue

            # Ask user if they want to continue in interactive mode
            print(f"\n{'='*40}")
            print(f"已玩 {hands_played} 手牌")
            print(f"当前筹码: ", end="")
            for p in self.players:
                if p["stack_bb"] > 0:
                    print(f"{p['id']}: {p['stack_bb']:.1f}BB  ", end="")
            print()
            print(f"{'='*40}")

            try:
                choice = input("\n> 输入 q 退出，其他键继续下一手牌: ").strip().lower()
                if choice == "q":
                    print("\n游戏结束。")
                    break
            except (EOFError, KeyboardInterrupt):
                print("\n\n游戏结束。")
                break

        return self.results

    def _save_hand_history(self, result: HandResult) -> None:
        """Save hand history and decision log."""
        # Save decision log
        self.decision_logger.log_hand(result, self.players)

        # Build and save hand history
        history = HandHistory.from_result(
            result=result,
            players=self.players,
            small_blind_bb=self.config["table"]["small_blind_bb"],
            big_blind_bb=self.config["table"]["big_blind_bb"],
            table_size=self.table_size,
        )
        self.history_store.save(history)

    def _print_hand_result(self, result: HandResult) -> None:
        """Print a hand result to console (clean output, no style/reason)."""
        print(f"\n{'='*50}")
        print(f"Hand #{result.hand_id} ({self.config['table']['size']}-max)")
        print(f"{'='*50}")

        # Print button position
        print(f"Button: Seat {result.final_seats[0].get('position_name', '?')}")

        # Community cards
        if result.community_cards:
            cards_str = " ".join(str(c) for c in result.community_cards)
            print(f"\n公共牌: {cards_str}")

        # Showdown - only show this since actions are printed in real-time
        if result.winners:
            print(f"\n--- 摊牌 ---")
            # Check if this was a real showdown or a win by default (everyone else folded)
            # In Texas Hold'em, a player who wins by default (everyone folds) does NOT need to show cards
            showdown_winners = [
                w for w in result.winners
                if w.hand_name != "最后一个玩家（其他人弃牌）"
            ]
            if showdown_winners:
                # Real showdown - show hole cards for non-folded players
                for seat in result.final_seats:
                    if not seat["folded"] and seat["hole_cards"]:
                        cards = " ".join(str(c) for c in seat["hole_cards"])
                        print(f"  {seat['player_id']}: {cards}")

            for w in result.winners:
                player_id = result.final_seats[w.seat_index]["player_id"]
                print(f"  {player_id} 赢得 {w.amount_bb}BB ({w.hand_name})")

        # Final stacks
        print(f"\n--- 筹码 ---")
        for p in self.players:
            print(f"  {p['id']}: {p['stack_bb']:.1f}BB")

    def _load_config(self, path: str) -> dict:
        """Load YAML config file."""
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
