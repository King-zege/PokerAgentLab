"""Pydantic models for request/response schemas."""

from datetime import datetime
from typing import Literal, Any

from pydantic import BaseModel, Field


# ─── Request Models ────────────────────────────────────────────────────────────

class CreateSessionRequest(BaseModel):
    """Request to create a new game session."""
    session_id: str = Field(..., description="Unique session identifier")
    mode: Literal["fixed", "infinite"] = Field(default="fixed", description="Game mode")
    num_hands: int | None = Field(default=10, ge=1, le=10000, description="Number of hands for fixed mode")
    config_path: str = Field(default="config/game_config.yaml", description="Path to game config")


class SubmitActionRequest(BaseModel):
    """Request to submit a human player action."""
    action: Literal["fold", "check", "call", "bet", "raise", "all_in"] = Field(..., description="Action type")
    amount: float = Field(default=0.0, description="Amount for bet/raise (optional, will use min if not provided)")


class ContinueRequest(BaseModel):
    """Request to continue to next hand or end session."""
    continue_game: bool = Field(..., description="True to continue, False to end")


class SelfPlayRequest(BaseModel):
    """Request for a self-play experiment."""
    experiment_id: str | None = Field(default=None, description="Optional experiment identifier")
    num_hands: int = Field(default=100, ge=1, le=10000)
    seed: int | None = Field(default=42)
    players: list[dict] | None = Field(
        default=None,
        description="Optional players list, e.g. [{'id':'Alice','style':'tag','stack_bb':100}]",
    )
    config_path: str = Field(default="config/game_config.yaml")


class RagEvaluationRequest(BaseModel):
    """Request for StrategyRAG retrieval evaluation."""
    run_id: str | None = Field(default=None, description="Optional evaluation run identifier")
    dataset_path: str = Field(default="evaluation/datasets/strategy_queries.jsonl")
    top_k: int = Field(default=3, ge=1, le=20)


class SystemEvaluationRequest(BaseModel):
    """Request for end-to-end usefulness signal evaluation."""
    run_id: str | None = Field(default=None, description="Optional evaluation run identifier")
    config_path: str = Field(default="config/game_config.yaml")
    num_hands: int = Field(default=20, ge=1, le=1000)
    seed: int | None = Field(default=42)
    variants: list[str] | None = Field(default=None)


class MemoryAgentRunRequest(BaseModel):
    """Request to run the background memory manager agent manually."""
    force: bool = Field(default=False)


# ─── Response Models ───────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    timestamp: str


class PlayerInfo(BaseModel):
    """Player information."""
    id: str
    style: str
    stack_bb: float
    initial_stack_bb: float
    hole_cards: list[str] | None = None


class ConfigResponse(BaseModel):
    """Game configuration response."""
    table_size: int
    small_blind_bb: float
    big_blind_bb: float
    players: list[PlayerInfo]
    session: dict
    llm: dict


class SessionResponse(BaseModel):
    """Session details response."""
    session_id: str
    status: str  # created | running | completed | error
    created_at: str
    current_hand: int
    num_hands: int | None
    mode: str
    error: str | None = None


class SessionListResponse(BaseModel):
    """List of active sessions."""
    sessions: list[SessionResponse]


class PlayerStateInfo(BaseModel):
    """Player state in current game."""
    id: str
    stack_bb: float
    position: str
    hole_cards: list[str] | None = None


class LegalActionInfo(BaseModel):
    """Legal action with constraints."""
    type: str
    min: float | None = None
    max: float | None = None
    amount: float | None = None


class GameStateResponse(BaseModel):
    """Current game state response."""
    session_id: str
    status: str  # created | waiting_for_action | running | completed | error
    current_hand: int
    street: str | None
    pot_bb: float
    community_cards: list[str]
    current_player_id: str | None
    hole_cards: list[str]
    legal_actions: list[LegalActionInfo]
    players: list[PlayerStateInfo]
    hand_complete: bool
    can_continue: bool
    last_hand_result: dict | None = None
    error: str | None = None


class ActionRecordResponse(BaseModel):
    """An action record in hand history."""
    street: str
    seat_index: int
    player_id: str
    action: str
    action_amount: float
    stack_before_bb: float
    pot_before_bb: float
    explanation: str
    position_name: str
    style: str = ""


class PotResponse(BaseModel):
    """Pot information."""
    amount_bb: float
    eligible: list[str] = []
    winners: list[Any]


class HandHistoryResponse(BaseModel):
    """Single hand history response."""
    hand_id: str
    timestamp: str
    table_size: int
    button_index: int
    small_blind_bb: float
    big_blind_bb: float
    players: list[dict]
    hole_cards: dict[str, list[str]]
    community_cards: list[str]
    actions: list[ActionRecordResponse]
    pots: list[PotResponse]
    final_stacks: dict[str, float]
    analysis: dict | None = None


class HistoryResponse(BaseModel):
    """Hand history list response."""
    session_id: str
    total_hands: int
    hands: list[HandHistoryResponse]
    player_stats: dict[str, dict] | None = None


class ActionReviewResponse(BaseModel):
    """Single action review."""
    street: str
    player_id: str
    style: str
    action_taken: str
    explanation: str
    was_style_consistent: bool
    deviation_description: str | None = None
    suggested_action: str | None = None
    suggestion_reason: str | None = None


class AnalysisResponse(BaseModel):
    """Analysis result for a hand."""
    hand_id: str
    action_reviews: list[ActionReviewResponse]
    overall_notes: list[str]
    style_deviation_count: dict[str, int]


class AnalyzeResponse(BaseModel):
    """Analysis result for multiple hands or entire session."""
    session_id: str
    total_hands_analyzed: int
    results: list[AnalysisResponse]
    summary_notes: list[str] = []


class ErrorResponse(BaseModel):
    """Error response."""
    error: str
    detail: str | None = None


class TraceListResponse(BaseModel):
    session_id: str
    total_traces: int
    traces: list[dict[str, Any]]


class SelfPlayResponse(BaseModel):
    experiment_id: str
    num_hands: int
    seed: int | None = None
    report_path: str
    markdown_path: str
    summary: dict[str, Any]
    memory_agent_report_path: str | None = None
    memory_agent_summary: dict[str, Any] = Field(default_factory=dict)


class CoachResponse(BaseModel):
    session_id: str
    report_title: str = "Poker Agent Lab 训练报告"
    focus_player_id: str | None = None
    total_hands: int
    summary: dict[str, Any] = Field(default_factory=dict)
    action_profile: list[dict[str, Any]] = Field(default_factory=list)
    street_profile: list[dict[str, Any]] = Field(default_factory=list)
    leak_candidates: list[dict[str, Any]] = Field(default_factory=list)
    critical_spots: list[dict[str, Any]] = Field(default_factory=list)
    training_plan: list[dict[str, Any]] = Field(default_factory=list)
    next_drill: dict[str, Any] = Field(default_factory=dict)
    key_findings: list[str]
    training_goals: list[str]
    hand_reviews: list[dict[str, Any]]


class MemorySearchRequest(BaseModel):
    """Search long-term user memory."""
    query: str = Field(default="")
    status: str | None = Field(default="accepted")
    limit: int = Field(default=10, ge=1, le=100)


class StrategySearchRequest(BaseModel):
    """Search local strategy knowledge."""
    query: str = Field(default="")
    street: str | None = Field(default=None)
    style: str | None = Field(default=None)
    limit: int = Field(default=5, ge=1, le=50)


class MemoryProfileResponse(BaseModel):
    user_id: str
    total_memories: int
    by_status: dict[str, list[dict[str, Any]]]
    accepted_by_category: dict[str, list[dict[str, Any]]]
    training_goals: list[str]
    leaks: list[str]
    governance_summary: dict[str, Any] = Field(default_factory=dict)


class MemorySearchResponse(BaseModel):
    query: str
    total: int
    memories: list[dict[str, Any]]


class TemporaryMemoryResponse(BaseModel):
    user_id: str
    total: int
    memories: list[dict[str, Any]]


class MemoryAgentReportResponse(BaseModel):
    session_id: str
    created_at: str
    enabled: bool
    focus_player_id: str | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    temporary_updates: list[dict[str, Any]] = Field(default_factory=list)
    archived_memories: list[dict[str, Any]] = Field(default_factory=list)
    governance_summary: dict[str, Any] = Field(default_factory=dict)
    fallback_reason: str = ""
    report_path: str = ""


class StrategySearchResponse(BaseModel):
    query: str
    total: int
    chunks: list[dict[str, Any]]


class ConsolidateResponse(BaseModel):
    session_id: str
    session_summary: dict[str, Any]
    candidate_memories: list[dict[str, Any]]
    training_plan: list[str]


class MemoryContextResponse(BaseModel):
    session_id: str
    enabled: bool
    short_term_context: str
    user_memory_context: str
    strategy_context: str
    retrieved_memory_ids: list[str]
    retrieved_strategy_chunk_ids: list[str]
    memory_fallback_reason: str = ""


class EvaluationReportResponse(BaseModel):
    run_id: str
    kind: str
    report_path: str
    markdown_path: str
    metrics: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    cases: list[dict[str, Any]] | None = None
    variants: list[dict[str, Any]] | None = None
