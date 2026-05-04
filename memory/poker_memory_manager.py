"""Poker memory orchestration inspired by Hermes-style memory lifecycle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from agent.observation import Observation
from engine.action import Action
from memory.short_term import ShortTermHandMemory
from memory.strategy_rag import StrategyRAG
from memory.user_profile import LongTermUserProfile


@dataclass
class DecisionMemoryContext:
    prompt_block: str
    short_term_context: str
    user_memory_context: str
    strategy_context: str
    retrieved_memory_ids: list[str]
    retrieved_strategy_chunk_ids: list[str]
    memory_fallback_reason: str = ""
    user_memory_excluded_reason: str = ""

    @property
    def memory_context_summary(self) -> str:
        return self._summarize(self.short_term_context + "\n" + self.user_memory_context)

    @property
    def strategy_context_summary(self) -> str:
        return self._summarize(self.strategy_context)

    def _summarize(self, text: str, limit: int = 600) -> str:
        return " ".join(text.split())[:limit]


class PokerMemoryManager:
    """Coordinates short-term hand memory, long-term profile memory, and strategy retrieval."""

    def __init__(self, session_id: str = "default", user_id: str | None = None, enabled: bool | None = None):
        env_enabled = os.environ.get("POKER_MEMORY_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
        self.enabled = env_enabled if enabled is None else enabled
        self.decision_user_memory_enabled = os.environ.get("POKER_DECISION_USER_MEMORY_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")
        self.session_id = session_id or "default"
        self.user_profile = LongTermUserProfile(user_id=user_id)
        self.short_term = ShortTermHandMemory(self.session_id)
        self.strategy_rag = StrategyRAG()
        self.last_context: DecisionMemoryContext | None = None

    def build_decision_context(self, obs: Observation, legal_actions: list[Action]) -> DecisionMemoryContext:
        """Build decision context for a playing agent.

        Decision agents may use short-term hand context and StrategyRAG, but
        they do not read long-term user profile memory. Long-term user memory
        belongs to the coach/learning branch so user leaks do not become poker
        instructions.
        """
        if not self.enabled:
            context = DecisionMemoryContext("", "", "", "", [], [], "Poker memory disabled")
            self.last_context = context
            return context
        short_term = self.short_term.build_context(player_id=obs.player_id)
        query = self._query_from_observation(obs, legal_actions)
        user_context = ""
        memory_ids: list[str] = []
        if self.decision_user_memory_enabled:
            user_context, memory_ids = self.user_profile.build_context(query=query)
        strategy_context, chunk_ids = self.strategy_rag.build_context(obs=obs)
        fallback_parts = [p for p in (self.user_profile.last_fallback_reason if self.decision_user_memory_enabled else "", self.strategy_rag.last_fallback_reason) if p]
        prompt_parts = [short_term]
        if user_context:
            prompt_parts.append(user_context)
        prompt_parts.append(strategy_context)
        prompt_block = "\n\n".join(prompt_parts)
        context = DecisionMemoryContext(
            prompt_block=prompt_block,
            short_term_context=short_term,
            user_memory_context=user_context,
            strategy_context=strategy_context,
            retrieved_memory_ids=memory_ids,
            retrieved_strategy_chunk_ids=chunk_ids,
            memory_fallback_reason="; ".join(fallback_parts),
            user_memory_excluded_reason="" if self.decision_user_memory_enabled else "Decision agent does not use long-term user profile",
        )
        self.last_context = context
        return context

    def build_coach_context(self, obs: Observation | None = None, legal_actions: list[Action] | None = None) -> DecisionMemoryContext:
        """Build coach context with long-term user profile memory included."""
        if not self.enabled:
            return DecisionMemoryContext("", "", "", "", [], [], "Poker memory disabled")
        query = self._query_from_observation(obs, legal_actions or []) if obs else ""
        short_term = self.short_term.build_context(player_id=obs.player_id if obs else None)
        user_context, memory_ids = self.user_profile.build_context(query=query)
        strategy_context, chunk_ids = self.strategy_rag.build_context(obs=obs) if obs else self.strategy_rag.build_context(query=query)
        fallback_parts = [p for p in (self.user_profile.last_fallback_reason, self.strategy_rag.last_fallback_reason) if p]
        return DecisionMemoryContext(
            prompt_block="\n\n".join([short_term, user_context, strategy_context]),
            short_term_context=short_term,
            user_memory_context=user_context,
            strategy_context=strategy_context,
            retrieved_memory_ids=memory_ids,
            retrieved_strategy_chunk_ids=chunk_ids,
            memory_fallback_reason="; ".join(fallback_parts),
        )

    def memory_context_snapshot(self, obs: Observation | None = None, legal_actions: list[Action] | None = None) -> dict[str, Any]:
        decision_context = self.build_decision_context(obs, legal_actions or []) if obs is not None else (self.last_context or self.build_decision_context_snapshot())
        coach_context = self.build_coach_context(obs, legal_actions or []) if obs is not None else self.build_coach_context()
        return {
            "session_id": self.session_id,
            "enabled": self.enabled,
            "short_term_context": decision_context.short_term_context,
            "user_memory_context": coach_context.user_memory_context,
            "strategy_context": decision_context.strategy_context,
            "retrieved_memory_ids": decision_context.retrieved_memory_ids,
            "retrieved_strategy_chunk_ids": decision_context.retrieved_strategy_chunk_ids,
            "memory_fallback_reason": decision_context.memory_fallback_reason,
            "decision_context": self._context_to_dict(decision_context),
            "coach_context": self._context_to_dict(coach_context),
        }

    def build_decision_context_snapshot(self) -> DecisionMemoryContext:
        if not self.enabled:
            return DecisionMemoryContext("", "", "", "", [], [], "Poker memory disabled")
        short_term = self.short_term.build_context()
        strategy_context, chunk_ids = self.strategy_rag.build_context(query="")
        return DecisionMemoryContext(
            prompt_block="\n\n".join([short_term, strategy_context]),
            short_term_context=short_term,
            user_memory_context="",
            strategy_context=strategy_context,
            retrieved_memory_ids=[],
            retrieved_strategy_chunk_ids=chunk_ids,
            user_memory_excluded_reason="Decision agent does not use long-term user profile",
        )

    def _context_to_dict(self, context: DecisionMemoryContext) -> dict[str, Any]:
        return {
            "prompt_block": context.prompt_block,
            "short_term_context": context.short_term_context,
            "user_memory_context": context.user_memory_context,
            "strategy_context": context.strategy_context,
            "retrieved_memory_ids": context.retrieved_memory_ids,
            "retrieved_strategy_chunk_ids": context.retrieved_strategy_chunk_ids,
            "memory_fallback_reason": context.memory_fallback_reason,
            "user_memory_excluded_reason": context.user_memory_excluded_reason,
        }

    def _query_from_observation(self, obs: Observation, legal_actions: list[Action]) -> str:
        legal = " ".join(a.type.value for a in legal_actions)
        board = " ".join(str(c) for c in obs.community_cards)
        return f"{obs.player_id} {obs.style} {obs.street} {obs.position_name} pot {obs.pot_bb:.1f} call {obs.current_bet_to_call_bb:.1f} spr {obs.spr:.1f} board {board} legal {legal}"
