"""LLM-powered agent using Anthropic Claude API."""

import os
import json
import re
from agent.base_agent import BaseAgent
from agent.observation import Observation
from engine.action import Action, ActionType


# Tool definition for poker action (BigModel format)
POKER_ACTION_TOOL = {
    "type": "function",
    "function": {
        "name": "poker_action",
        "description": "在德州扑克中执行动作",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["fold", "call", "check", "bet", "raise", "all_in"],
                    "description": "要执行的动作类型"
                },
                "amount": {
                    "type": "number",
                    "description": "下注/加注金额（BB为单位），仅bet/raise时需要"
                }
            },
            "required": ["action"]
        }
    }
}


class LLMAgent(BaseAgent):
    """Agent that uses LLM to make poker decisions."""

    def __init__(
        self,
        player_id: str,
        api_key: str | None = None,
        api_base: str = "https://open.bigmodel.cn/api/paas/v4",
        model: str = "glm-4.6V",
        style: str = "balanced",
        skills_dir: str = "strategy/skills",
        use_skills_in_prompt: bool = True,
        memory_manager=None,
    ):
        self.player_id = player_id
        self.style = style
        self.api_key = api_key or os.environ.get("POKER_LLM_API_KEY", "")
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.skills_dir = skills_dir
        self.use_skills_in_prompt = use_skills_in_prompt
        self.memory_manager = memory_manager
        self._call_count = 0  # Debug counter
        self.last_trace: dict = {}

    def decide(self, observation: Observation, legal_actions: list[Action]) -> Action:
        """Use LLM to decide which action to take."""
        self._call_count += 1
        import time
        start_time = time.time()

        prompt = self._build_prompt(observation, legal_actions)
        self.last_trace = {
            "prompt_summary": prompt[:1200],
            "fallback_reason": "",
        }

        try:
            response, num_calls = self._call_llm_with_count(prompt)
            elapsed = time.time() - start_time
            print(f"  AI {self.player_id} 思考{elapsed:.1f}s后行动：", end=" ", flush=True)
            action = self._parse_action(response, legal_actions, observation)
            self.last_trace.update({
                "llm_raw_response": response,
                "parsed_action": str(action) if action is not None else "",
                "latency_ms": elapsed * 1000,
            })
            if action is not None and action in legal_actions:
                return action
            self.last_trace["fallback_reason"] = "Parsed action was missing or not exactly legal"
        except Exception as e:
            print(f"\n  [LLM Error #{self._call_count}] {e}")
            self.last_trace.update({
                "llm_raw_response": "",
                "fallback_reason": str(e),
                "latency_ms": (time.time() - start_time) * 1000,
            })

        # Fallback to first legal action
        print(f"  AI {self.player_id} 思考{time.time()-start_time:.1f}s后行动：", end=" ", flush=True)
        return legal_actions[0]

    def explain(self, observation: Observation, chosen_action: Action) -> str:
        """Generate explanation using LLM."""
        prompt = f"""德州扑克：玩家 {self.player_id} 选择 {chosen_action}。
请简短解释原因（10字以内）。"""
        try:
            return self._call_llm(prompt)
        except:
            return f"LLM: {chosen_action}"

    def _build_prompt(self, obs: Observation, legal_actions: list[Action]) -> str:
        """Build a compact prompt for the LLM."""
        hole_cards = " ".join(str(c) for c in obs.hole_cards)
        community = " ".join(str(c) for c in obs.community_cards) if obs.community_cards else "无"

        # Build compact actions list
        actions_list = []
        for i, a in enumerate(legal_actions):
            if a.amount > 0:
                actions_list.append(f"{i+1}.{a.type.value.upper()}:{a.amount}")
            else:
                actions_list.append(f"{i+1}.{a.type.value.upper()}")
        actions_str = " ".join(actions_list)

        street_map = {"preflop": "翻前", "flop": "翻牌", "turn": "转牌", "river": "河牌"}
        street_cn = street_map.get(obs.street, obs.street)

        base_prompt = f"""扑克：你{self.player_id}在{obs.position_name}({street_cn})
手牌{hole_cards} 公共{community} 池{obs.pot_bb:.1f}BB 跟注{obs.current_bet_to_call_bb:.1f}BB 筹码{obs.stack_bb:.1f}BB
可选动作: {actions_str}
直接返回: {{"a":"1"}} 或 {{"a":"fold"}}"""

        if self.memory_manager is not None:
            memory_context = self.memory_manager.build_decision_context(obs, legal_actions)
            self.last_trace.update({
                "memory_context_summary": memory_context.memory_context_summary,
                "strategy_context_summary": memory_context.strategy_context_summary,
                "retrieved_memory_ids": memory_context.retrieved_memory_ids,
                "retrieved_strategy_chunk_ids": memory_context.retrieved_strategy_chunk_ids,
                "memory_fallback_reason": memory_context.memory_fallback_reason,
            })
            return base_prompt + "\n\n" + memory_context.prompt_block

        # Inject street-specific skills content if enabled
        if self.use_skills_in_prompt:
            skills_content = self._load_skills_for_style(obs.street)
            if skills_content:
                # Game state first (more important for decision), then GTO guidance
                return base_prompt + "\n\n" + skills_content

        return base_prompt

    def _load_skills_for_style(self, street: str) -> str:
        """Load GTO skills content for the current style and street."""
        from pathlib import Path
        skills_path = Path(self.skills_dir) / f"{self.style}_skills.md"
        if not skills_path.exists():
            import logging
            logging.warning(f"[LLMAgent] Skills file not found: {skills_path}")
            return ""

        try:
            content = skills_path.read_text(encoding="utf-8")
            return self._extract_gto_section(content, street)
        except Exception as e:
            import logging
            logging.warning(f"[LLMAgent] Failed to load skills: {e}")
            return ""

    def _extract_gto_section(self, content: str, street: str) -> str:
        """Extract GTO reference section for postflop streets only.

        Preflop GTO is now in YAML (gto_preflop), so preflop returns empty.
        For flop/turn/river, returns postflop betting guidance from skills.
        """
        # Preflop GTO is in YAML - LLM doesn't need it from skills
        if street == "preflop":
            return ""

        lines = content.split("\n")
        gto_lines = []
        in_gto_section = False
        in_preflop_block = False  # Track if we're in a preflop subsection
        line_count = 0
        max_lines = 60

        for line in lines:
            if "## GTO 参考" in line or "##GTO参考" in line:
                in_gto_section = True
                gto_lines.append(line)
                line_count += 1
                continue

            if in_gto_section:
                # Break on top-level section header (##, not ###)
                if line.startswith("## ") or line == "##" or line.startswith("##\n"):
                    break

                if line.startswith("### "):
                    # For postflop: include only postflop betting sections and style
                    # Skip preflop subsections (翻前开池范围, 翻前加注尺寸, 3-bet范围)
                    is_preflop = (
                        "翻前" in line
                        or "3-bet" in line  # 3-bet is preflop action
                    )
                    is_style = "风格调整" in line
                    # Include postflop betting sections
                    is_postflop_betting = (
                        "C-bet" in line or "转牌" in line or "河牌" in line
                        or "下注尺寸" in line
                    )
                    if is_preflop:
                        in_preflop_block = True
                        continue
                    in_preflop_block = False
                    if is_style or is_postflop_betting:
                        gto_lines.append(line)
                        line_count += 1
                elif not in_preflop_block:
                    # Only include content lines if NOT in a preflop block
                    gto_lines.append(line)
                    line_count += 1
                    if line_count >= max_lines:
                        break

        if not gto_lines:
            return ""

        return "\n".join(gto_lines)

    def _call_llm(self, prompt: str) -> str:
        """Call LLM API with tool support."""
        import urllib.request
        import urllib.error

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 512,
            "tools": [POKER_ACTION_TOOL],
        }

        url = f"{self.api_base}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._extract_content(result)
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            raise Exception(f"HTTP {e.code}: {error_body}")
        except Exception as e:
            raise Exception(f"LLM Call Failed: {e}")

    def _call_llm_with_count(self, prompt: str) -> tuple[str, int]:
        """Call LLM API and return content plus call count."""
        import urllib.request
        import urllib.error

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        messages = [{"role": "user", "content": prompt}]
        call_count = 0

        url = f"{self.api_base}/chat/completions"

        while True:
            call_count += 1

            data = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.3,
                "max_tokens": 512,
                "tools": [POKER_ACTION_TOOL],
            }

            req = urllib.request.Request(
                url,
                data=json.dumps(data).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            try:
                with urllib.request.urlopen(req, timeout=60) as response:
                    result = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8") if e.fp else ""
                raise Exception(f"HTTP {e.code}: {error_body}")
            except Exception as e:
                raise Exception(f"LLM Call Failed: {e}")

            choices = result.get("choices", [])
            if not choices:
                raise Exception("No choices in response")

            msg = choices[0].get("message", {})

            # Check for tool_call
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                # Extract assistant's reasoning (any text content before tool call)
                content = msg.get("content", "") or ""
                # Add assistant message with tool_call to messages
                messages.append({
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls
                })
                # Process each tool_call and add tool response
                for call in tool_calls:
                    if call.get("function", {}).get("name") == "poker_action":
                        # This is our poker action tool - extract and return immediately
                        args = call.get("function", {}).get("arguments", "{}")
                        try:
                            args_dict = json.loads(args) if isinstance(args, str) else args
                            action = args_dict.get("action", "")
                            amount = args_dict.get("amount")
                            return json.dumps({"a": action, "amt": amount}), call_count
                        except:
                            pass
                    # Add generic tool response for other tools
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "content": "{}"
                    })
                continue

            # No tool call, return content
            content = msg.get("content", "")
            if content:
                return content.strip(), call_count

            # Fallback to reasoning_content
            reasoning = msg.get("reasoning_content", "")
            if reasoning:
                return reasoning.strip(), call_count

            raise Exception("No content or tool_call found")

    def _extract_content(self, result: dict) -> str:
        """Extract text or tool_use content from API response."""
        choices = result.get("choices", [])
        if not choices:
            return ""

        msg = choices[0].get("message", {})

        # Check for tool_call first
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            # Extract action from tool call
            for call in tool_calls:
                if call.get("function", {}).get("name") == "poker_action":
                    args = call.get("function", {}).get("arguments", "{}")
                    try:
                        args_dict = json.loads(args) if isinstance(args, str) else args
                        action = args_dict.get("action", "")
                        amount = args_dict.get("amount")
                        return json.dumps({"a": action, "amt": amount})
                    except:
                        pass

        # Fallback to content
        content = msg.get("content", "")
        if content:
            return content.strip()

        # Fallback: check reasoning_content
        reasoning = msg.get("reasoning_content", "")
        if reasoning:
            return reasoning.strip()

        return ""

    def _parse_chinese_action(self, text: str) -> Action | None:
        """Try to parse a Chinese text response for action keywords."""
        keywords = {
            "弃牌": ActionType.FOLD,
            "fold": ActionType.FOLD,
            "f": ActionType.FOLD,
            "跟注": ActionType.CALL,
            "call": ActionType.CALL,
            "c": ActionType.CALL,
            "过牌": ActionType.CHECK,
            "check": ActionType.CHECK,
            "x": ActionType.CHECK,
            "加注": ActionType.RAISE,
            "raise": ActionType.RAISE,
            "r": ActionType.RAISE,
            "bet": ActionType.BET,
            "b": ActionType.BET,
            "全押": ActionType.ALL_IN,
            "all_in": ActionType.ALL_IN,
            "allin": ActionType.ALL_IN,
            "a": ActionType.ALL_IN,
        }

        text_lower = text.lower()
        for keyword, action_type in keywords.items():
            if keyword in text_lower:
                return action_type

        return None

    def _parse_action(
        self, response: str, legal_actions: list[Action], obs: Observation
    ) -> Action | None:
        """Parse LLM response to get action."""
        try:
            response = re.sub(r"```json\s*", "", response)
            response = re.sub(r"```\s*", "", response)
            response = response.strip()

            # Find JSON object
            match = re.search(r"\{[^}]+\}", response, re.DOTALL)
            if not match:
                match = re.search(r'"(?:a|action)"\s*:\s*"?([^",}]+)"?', response, re.IGNORECASE)

            if not match:
                chinese_action = self._parse_chinese_action(response)
                if chinese_action:
                    for action in legal_actions:
                        if action.type == chinese_action:
                            return self._make_action(action, None, obs)
                    return self._make_action(legal_actions[0], None, obs)
                return None

            if "{" in match.group(0):
                json_str = match.group(0)
            else:
                action_val = match.group(1).strip()
                for i, action in enumerate(legal_actions):
                    if action.type.value == action_val.lower() or str(i+1) == action_val:
                        return self._make_action(action, None, obs)
                return None

            data = json.loads(json_str)
            action_str = str(data.get("a", data.get("action", ""))).strip()
            amount = data.get("amt", data.get("amount"))

            if action_str.isdigit():
                idx = int(action_str) - 1
                if 0 <= idx < len(legal_actions):
                    return self._make_action(legal_actions[idx], amount, obs)

            action_lower = action_str.lower()
            for action in legal_actions:
                if action.type.value == action_lower or action_lower in action.type.value:
                    return self._make_action(action, amount, obs)

            for action in legal_actions:
                if action.type.value.startswith(action_lower):
                    return self._make_action(action, amount, obs)

            return None

        except Exception as e:
            print(f"[LLM Parse Error] {e}")
            return None

    def _make_action(self, action: Action, amount, obs: Observation) -> Action:
        """Create action with proper amount."""
        if action.type == ActionType.ALL_IN:
            return Action(ActionType.ALL_IN, obs.stack_bb)
        elif action.type in (ActionType.BET, ActionType.RAISE) and amount:
            return Action(action.type, float(amount))
        return action
