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
        self.session_id = session_id or "default"
        self.user_profile = LongTermUserProfile(user_id=user_id)
        self.short_term = ShortTermHandMemory(self.session_id)
        self.strategy_rag = StrategyRAG()
        self.last_context: DecisionMemoryContext | None = None

    def build_decision_context(self, obs: Observation, legal_actions: list[Action]) -> DecisionMemoryContext:
        if not self.enabled:
            context = DecisionMemoryContext("", "", "", "", [], [], "Poker memory disabled")
            self.last_context = context
            return context
        query = self._query_from_observation(obs, legal_actions)
        short_term = self.short_term.build_context(player_id=obs.player_id)
        user_context, memory_ids = self.user_profile.build_context(query=query)
        strategy_context, chunk_ids = self.strategy_rag.build_context(obs=obs)
        fallback_parts = [p for p in (self.user_profile.last_fallback_reason, self.strategy_rag.last_fallback_reason) if p]
        prompt_block = "\n\n".join([short_term, user_context, strategy_context])
        context = DecisionMemoryContext(
            prompt_block=prompt_block,
            short_term_context=short_term,
            user_memory_context=user_context,
            strategy_context=strategy_context,
            retrieved_memory_ids=memory_ids,
            retrieved_strategy_chunk_ids=chunk_ids,
            memory_fallback_reason="; ".join(fallback_parts),
        )
        self.last_context = context
        return context

    def memory_context_snapshot(self, obs: Observation | None = None, legal_actions: list[Action] | None = None) -> dict[str, Any]:
        if obs is not None:
            context = self.build_decision_context(obs, legal_actions or [])
        elif self.last_context is not None:
            context = self.last_context
        else:
            short_term = self.short_term.build_context()
            user_context, memory_ids = self.user_profile.build_context()
            strategy_context, chunk_ids = self.strategy_rag.build_context(query="")
            context = DecisionMemoryContext("\n\n".join([short_term, user_context, strategy_context]), short_term, user_context, strategy_context, memory_ids, chunk_ids)
        return {
            "session_id": self.session_id,
            "enabled": self.enabled,
            "short_term_context": context.short_term_context,
            "user_memory_context": context.user_memory_context,
            "strategy_context": context.strategy_context,
            "retrieved_memory_ids": context.retrieved_memory_ids,
            "retrieved_strategy_chunk_ids": context.retrieved_strategy_chunk_ids,
            "memory_fallback_reason": context.memory_fallback_reason,
        }

    def _query_from_observation(self, obs: Observation, legal_actions: list[Action]) -> str:
        legal = " ".join(a.type.value for a in legal_actions)
        board = " ".join(str(c) for c in obs.community_cards)
        return f"{obs.player_id} {obs.style} {obs.street} {obs.position_name} pot {obs.pot_bb:.1f} call {obs.current_bet_to_call_bb:.1f} spr {obs.spr:.1f} board {board} legal {legal}"
