"""FastAPI for Poker Game - Complete API Implementation."""

import sys as _sys
# Set UTF-8 encoding for Windows console BEFORE any other imports
if _sys.platform == "win32":
    _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    _sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import yaml
import time
import threading
import json
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from api.models import (
    CreateSessionRequest, HealthResponse, ConfigResponse, PlayerInfo,
    SessionResponse, SessionListResponse, HistoryResponse, HandHistoryResponse,
    ActionRecordResponse, PotResponse, AnalyzeResponse, AnalysisResponse,
    ActionReviewResponse, ErrorResponse, GameStateResponse, PlayerStateInfo,
    LegalActionInfo, SubmitActionRequest, ContinueRequest, TraceListResponse,
    SelfPlayRequest, SelfPlayResponse, CoachResponse, MemorySearchRequest,
    StrategySearchRequest, MemoryProfileResponse, MemorySearchResponse,
    StrategySearchResponse, ConsolidateResponse, MemoryContextResponse,
    RagEvaluationRequest, SystemEvaluationRequest, EvaluationReportResponse,
    MemoryAgentRunRequest, TemporaryMemoryResponse, MemoryAgentReportResponse,
)
from api.session import session_store, GameSession
from api.game_runner import get_runner, create_runner, remove_runner
from engine.game import Game
from memory.history_store import HistoryStore
from memory.decision_trace import DecisionTraceStore
from memory.user_profile import LongTermUserProfile
from memory.strategy_rag import StrategyRAG
from memory.poker_memory_manager import PokerMemoryManager
from memory.consolidator import MemoryConsolidator
from memory.memory_manager_agent import MemoryManagerAgent
from memory.temporary_memory import TemporaryMemoryStore
from analysis.analysis_agent import AnalysisAgent
from analysis.coach_agent import CoachAgent
from strategy.style_profile import StyleRegistry
from api.experiments import run_self_play_experiment, load_experiment_report
from evaluation.rag_eval import run_rag_evaluation, load_rag_evaluation
from evaluation.system_eval import run_system_evaluation, load_system_evaluation


app = FastAPI(
    title="Poker Game API",
    version="1.0.0",
    description="德州扑克游戏 API - 简化版（无实时交互）",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load default config for /config endpoint
DEFAULT_CONFIG_PATH = "config/game_config.yaml"


# ─── Helper Functions ───────────────────────────────────────────────────────────

def load_game_config(config_path: str = DEFAULT_CONFIG_PATH) -> dict:
    """Load game configuration from YAML."""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_game_sync(session: GameSession, config_path: str):
    """Run game in a thread (non-blocking for FastAPI)."""
    try:
        session_store.update_status(session.session_id, "running")
        game = session.game

        # Override num_hands if specified
        if session.num_hands is not None:
            game.config.setdefault("session", {})["num_hands"] = session.num_hands

        num_hands = session.num_hands if session.num_hands else game.config.get("session", {}).get("num_hands", 10)
        session.current_hand = 0

        # Run the game session
        game.play_session(num_hands=num_hands, interactive=False)

        session_store.update_status(session.session_id, "completed")

    except Exception as e:
        session_store.update_status(session.session_id, "error", str(e))


# ─── System Status Endpoints ───────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now().isoformat(),
    )


@app.get("/config", response_model=ConfigResponse, tags=["System"])
def get_config():
    """Get current game configuration."""
    config = load_game_config()

    players = []
    for p in config.get("players", []):
        players.append(PlayerInfo(
            id=p["id"],
            style=p["style"],
            stack_bb=float(p["stack_bb"]),
            initial_stack_bb=float(p["stack_bb"]),
        ))

    return ConfigResponse(
        table_size=config.get("table", {}).get("size", 6),
        small_blind_bb=config.get("table", {}).get("small_blind_bb", 0.5),
        big_blind_bb=config.get("table", {}).get("big_blind_bb", 1.0),
        players=players,
        session=config.get("session", {}),
        llm=config.get("llm", {}),
    )


# ─── Session Management Endpoints ──────────────────────────────────────────────

def _find_human_id(game: Game) -> str | None:
    """Find the human player ID in a game."""
    for p in game.players:
        if p["style"] == "human":
            return p["id"]
    return None


@app.post("/sessions", response_model=SessionResponse, tags=["Sessions"])
def create_session(req: CreateSessionRequest, background_tasks: BackgroundTasks):
    """Create a new game session with interactive human play."""
    # Check if session already exists
    if session_store.get(req.session_id):
        raise HTTPException(status_code=400, detail=f"Session '{req.session_id}' already exists")

    if get_runner(req.session_id):
        raise HTTPException(status_code=400, detail=f"Session '{req.session_id}' already exists")

    # Determine num_hands
    num_hands = None
    if req.mode == "fixed":
        num_hands = req.num_hands or 10

    # Create Game instance
    try:
        game = Game(req.config_path, session_id=req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Config file not found: {req.config_path}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create game: {str(e)}")

    # Find human player
    human_id = _find_human_id(game)
    if not human_id:
        raise HTTPException(status_code=400, detail="No human player found in game config")

    # Create session
    session = session_store.create(
        session_id=req.session_id,
        game=game,
        mode=req.mode,
        num_hands=num_hands,
    )
    session.status = "running"

    # Create game runner with queue-based human agent
    runner = create_runner(req.session_id, game, human_id)

    # Start game in background thread
    background_tasks.add_task(runner.start)

    return SessionResponse(
        session_id=session.session_id,
        status="running",
        created_at=session.created_at_str,
        current_hand=0,
        num_hands=session.num_hands,
        mode=session.mode,
    )


@app.get("/sessions", response_model=SessionListResponse, tags=["Sessions"])
def list_sessions():
    """List all active sessions."""
    sessions = session_store.list_active()
    return SessionListResponse(
        sessions=[
            SessionResponse(
                session_id=s.session_id,
                status=s.status,
                created_at=s.created_at_str,
                current_hand=s.current_hand,
                num_hands=s.num_hands,
                mode=s.mode,
                error=s.error,
            )
            for s in sessions
        ]
    )


@app.get("/sessions/{session_id}", response_model=SessionResponse, tags=["Sessions"])
def get_session(session_id: str):
    """Get session details."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    return SessionResponse(
        session_id=session.session_id,
        status=session.status,
        created_at=session.created_at_str,
        current_hand=session.current_hand,
        num_hands=session.num_hands,
        mode=session.mode,
        error=session.error,
    )


@app.delete("/sessions/{session_id}", tags=["Sessions"])
def delete_session(session_id: str):
    """Delete a session."""
    # Remove from runner registry
    remove_runner(session_id)
    if not session_store.remove(session_id):
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    return {"success": True, "message": f"Session '{session_id}' deleted"}


# ─── Game State & Action Endpoints ──────────────────────────────────────────────

@app.get("/sessions/{session_id}/state", response_model=GameStateResponse, tags=["Game"])
def get_game_state(session_id: str):
    """Get current game state including legal actions for human player."""
    runner = get_runner(session_id)
    if not runner:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    state = runner.get_state()

    # Build player states
    players = []
    for p in state.get("players", []):
        players.append(PlayerStateInfo(
            id=p["id"],
            stack_bb=p["stack_bb"],
            position=p.get("position", ""),
            hole_cards=p.get("hole_cards"),
        ))

    # Build legal actions
    legal_actions = []
    for la in state.get("legal_actions", []):
        legal_actions.append(LegalActionInfo(
            type=la["type"],
            min=la.get("min"),
            max=la.get("max"),
            amount=la.get("amount"),
        ))

    return GameStateResponse(
        session_id=state["session_id"],
        status=state["status"],
        current_hand=state["current_hand"],
        street=state.get("street"),
        pot_bb=state.get("pot_bb", 0.0),
        community_cards=state.get("community_cards", []),
        current_player_id=state.get("current_player_id"),
        hole_cards=state.get("hole_cards", []),
        legal_actions=legal_actions,
        players=players,
        hand_complete=state.get("hand_complete", False),
        can_continue=state.get("can_continue", False),
        last_hand_result=state.get("last_hand_result"),
        error=state.get("error"),
    )


@app.post("/sessions/{session_id}/action", tags=["Game"])
def submit_action(session_id: str, req: SubmitActionRequest):
    """Submit a human player action (fold, call, raise, bet, all_in)."""
    runner = get_runner(session_id)
    if not runner:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    if runner.state.status != "waiting_for_action":
        raise HTTPException(status_code=400, detail=f"Session is not waiting for action (status: {runner.state.status})")

    runner.submit_action(req.action, req.amount)
    return {"success": True, "message": f"Action '{req.action}' submitted"}


@app.post("/sessions/{session_id}/continue", tags=["Game"])
def continue_or_end(session_id: str, req: ContinueRequest):
    """Continue to next hand or end session (shown after each hand completes)."""
    runner = get_runner(session_id)
    if not runner:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    if not runner.state.hand_complete:
        raise HTTPException(status_code=400, detail="Hand is not complete yet")

    if req.continue_game:
        runner.continue_game()
        return {"success": True, "message": "Continuing to next hand"}
    else:
        runner.end_game()
        return {"success": True, "message": "Ending session"}


# ─── History Endpoint ──────────────────────────────────────────────────────────

@app.get("/sessions/{session_id}/history", response_model=HistoryResponse, tags=["Game"])
def get_session_history(session_id: str):
    """Get hand history for a session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # Wait for session to complete if still running
    if session.status == "running":
        raise HTTPException(status_code=202, detail="Game is still running, please wait or check status later")

    # Load history from file
    history_file = f"data/history/hand_history_{session_id}.jsonl"
    history_store = HistoryStore(history_file)
    histories = history_store.load_all()

    # Build player stats
    player_stats = {}
    for p in session.game.players:
        pid = p["id"]
        stats = history_store.get_player_stats(pid)
        player_stats[pid] = stats

    # Convert to response models
    def read_field(record, field: str, default=None):
        if isinstance(record, dict):
            return record.get(field, default)
        return getattr(record, field, default)

    hands = []
    for h in histories:
        actions = [
            ActionRecordResponse(
                street=read_field(a, "street", ""),
                seat_index=read_field(a, "seat_index", 0),
                player_id=read_field(a, "player_id", ""),
                action=read_field(a, "action", ""),
                action_amount=read_field(a, "action_amount", 0.0),
                stack_before_bb=read_field(a, "stack_before_bb", 0.0),
                pot_before_bb=read_field(a, "pot_before_bb", 0.0),
                explanation=read_field(a, "explanation", ""),
                position_name=read_field(a, "position_name", ""),
                style=read_field(a, "style", ""),
            )
            for a in h.actions
        ]

        pots = [
            PotResponse(
                amount_bb=p.get("amount_bb", 0.0),
                eligible=p.get("eligible", []),
                winners=p.get("winners", []),
            )
            for p in h.pots
        ]

        hands.append(HandHistoryResponse(
            hand_id=h.hand_id,
            timestamp=h.timestamp,
            table_size=h.table_size,
            button_index=h.button_index,
            small_blind_bb=h.small_blind_bb,
            big_blind_bb=h.big_blind_bb,
            players=h.players,
            hole_cards=h.hole_cards,
            community_cards=h.community_cards,
            actions=actions,
            pots=pots,
            final_stacks=h.final_stacks,
            analysis=h.analysis,
        ))

    return HistoryResponse(
        session_id=session_id,
        total_hands=len(hands),
        hands=hands,
        player_stats=player_stats,
    )


# ─── Analysis Endpoints ────────────────────────────────────────────────────────

@app.post("/sessions/{session_id}/analyze", response_model=AnalyzeResponse, tags=["Analysis"])
def analyze_session(session_id: str):
    """Analyze all hands in a session for the human player."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    if session.status != "completed":
        raise HTTPException(status_code=400, detail="Session is not completed yet")

    # Load history
    history_file = f"data/history/hand_history_{session_id}.jsonl"
    history_store = HistoryStore(history_file)
    histories = history_store.load_all()

    if not histories:
        raise HTTPException(status_code=404, detail="No hand history found")

    # Find human player ID
    human_id = None
    for p in session.game.players:
        if p["style"] == "human":
            human_id = p["id"]
            break

    if not human_id:
        raise HTTPException(status_code=400, detail="No human player found in this session")

    # Initialize style registry and analysis agent
    styles_dir = Path(DEFAULT_CONFIG_PATH).parent / "styles"
    registry = StyleRegistry(str(styles_dir))
    analysis_agent = AnalysisAgent(registry)

    # Analyze each hand
    results = []
    all_notes = []

    for h in histories:
        analysis = analysis_agent.analyze_hand(h)

        action_reviews = [
            ActionReviewResponse(
                street=r.street,
                player_id=r.player_id,
                style=r.style,
                action_taken=r.action_taken,
                explanation=r.explanation,
                was_style_consistent=r.was_style_consistent,
                deviation_description=r.deviation_description,
                suggested_action=r.suggested_action,
                suggestion_reason=r.suggestion_reason,
            )
            for r in analysis.action_reviews
            if r.player_id == human_id  # Only human player's actions
        ]

        results.append(AnalysisResponse(
            hand_id=h.hand_id,
            action_reviews=action_reviews,
            overall_notes=analysis.overall_notes,
            style_deviation_count=analysis.style_deviation_count,
        ))

        all_notes.extend(analysis.overall_notes)

    # Generate summary notes
    summary_notes = []
    if results:
        total_deviations = sum(
            sum(r.style_deviation_count.values())
            for r in results
        )
        summary_notes.append(f"分析了 {len(results)} 手牌，发现 {total_deviations} 次风格偏差")
        # Add some overall observations
        if total_deviations > len(results) * 2:
            summary_notes.append("建议：你的打法偏离风格较多，注意保持一致性")
        elif total_deviations < len(results) * 0.5:
            summary_notes.append("很好：你较好地保持了风格一致性")

    return AnalyzeResponse(
        session_id=session_id,
        total_hands_analyzed=len(results),
        results=results,
        summary_notes=summary_notes,
    )


@app.post("/sessions/{session_id}/analyze/{hand_id}", response_model=AnalysisResponse, tags=["Analysis"])
def analyze_hand(session_id: str, hand_id: str):
    """Analyze a specific hand."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # Load history
    history_file = f"data/history/hand_history_{session_id}.jsonl"
    history_store = HistoryStore(history_file)
    h = history_store.load_by_id(hand_id)

    if not h:
        raise HTTPException(status_code=404, detail=f"Hand '{hand_id}' not found")

    # Initialize style registry and analysis agent
    styles_dir = Path(DEFAULT_CONFIG_PATH).parent / "styles"
    registry = StyleRegistry(str(styles_dir))
    analysis_agent = AnalysisAgent(registry)

    analysis = analysis_agent.analyze_hand(h)

    action_reviews = [
        ActionReviewResponse(
            street=r.street,
            player_id=r.player_id,
            style=r.style,
            action_taken=r.action_taken,
            explanation=r.explanation,
            was_style_consistent=r.was_style_consistent,
            deviation_description=r.deviation_description,
            suggested_action=r.suggested_action,
            suggestion_reason=r.suggestion_reason,
        )
        for r in analysis.action_reviews
    ]

    return AnalysisResponse(
        hand_id=h.hand_id,
        action_reviews=action_reviews,
        overall_notes=analysis.overall_notes,
        style_deviation_count=analysis.style_deviation_count,
    )


@app.get("/sessions/{session_id}/traces", response_model=TraceListResponse, tags=["Observability"])
def get_session_traces(session_id: str):
    """Get all decision traces for a session."""
    store = DecisionTraceStore.for_session(session_id)
    traces = store.load_all()
    return TraceListResponse(session_id=session_id, total_traces=len(traces), traces=traces)


def _compact_trace_for_stream(trace: dict) -> dict:
    """Keep SSE trace events small and focused on UI display fields."""
    return {
        "session_id": trace.get("session_id"),
        "hand_id": trace.get("hand_id"),
        "street": trace.get("street"),
        "player_id": trace.get("player_id"),
        "chosen_action": trace.get("chosen_action"),
        "parsed_action": trace.get("parsed_action"),
        "fallback_reason": trace.get("fallback_reason", ""),
        "retrieved_memory_ids": trace.get("retrieved_memory_ids") or [],
        "retrieved_strategy_chunk_ids": trace.get("retrieved_strategy_chunk_ids") or [],
        "timestamp": trace.get("timestamp", ""),
    }


def _sse_event(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


@app.get("/sessions/{session_id}/trace-stream", tags=["Observability"])
def stream_session_traces(session_id: str, once: bool = False):
    """Stream compact decision traces as Server-Sent Events."""
    store = DecisionTraceStore.for_session(session_id)

    def event_stream():
        next_line = 0
        completed_idle_ticks = 0

        while True:
            new_traces, next_line = store.load_since_line(next_line)
            for trace in new_traces:
                yield _sse_event("trace", _compact_trace_for_stream(trace))

            runner = get_runner(session_id)
            session = session_store.get(session_id)
            status = runner.state.status if runner else (session.status if session else None)
            if status in {"completed", "error"}:
                completed_idle_ticks += 1
                if completed_idle_ticks >= 4:
                    break
            else:
                completed_idle_ticks = 0

            if not new_traces:
                yield ": keep-alive\n\n"
            if once:
                break
            time.sleep(0.5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/sessions/{session_id}/hands/{hand_id}/traces", response_model=TraceListResponse, tags=["Observability"])
def get_hand_traces(session_id: str, hand_id: str):
    """Get decision traces for a single hand."""
    store = DecisionTraceStore.for_session(session_id)
    traces = store.load_by_hand(hand_id)
    return TraceListResponse(session_id=session_id, total_traces=len(traces), traces=traces)


@app.post("/experiments/self-play", response_model=SelfPlayResponse, tags=["Experiments"])
def create_self_play_experiment(req: SelfPlayRequest):
    """Run a self-play experiment and persist JSON/Markdown reports."""
    try:
        result = run_self_play_experiment(
            config_path=req.config_path,
            num_hands=req.num_hands,
            seed=req.seed,
            players=req.players,
            experiment_id=req.experiment_id,
        )
        return SelfPlayResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Self-play failed: {e}")


@app.get("/experiments/{experiment_id}/report", tags=["Experiments"])
def get_experiment_report(experiment_id: str):
    """Return a previously generated self-play report."""
    report = load_experiment_report(experiment_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Experiment '{experiment_id}' not found")
    return report


@app.post("/evaluation/rag", response_model=EvaluationReportResponse, tags=["Evaluation"])
def create_rag_evaluation(req: RagEvaluationRequest):
    """Evaluate StrategyRAG precision, recall, hit rate, and MRR."""
    try:
        report = run_rag_evaluation(
            dataset_path=req.dataset_path,
            top_k=req.top_k,
            run_id=req.run_id,
        )
        return EvaluationReportResponse(**report)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/evaluation/rag/{run_id}", response_model=EvaluationReportResponse, tags=["Evaluation"])
def get_rag_evaluation(run_id: str):
    """Return a persisted StrategyRAG evaluation report."""
    report = load_rag_evaluation(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"RAG evaluation '{run_id}' not found")
    return EvaluationReportResponse(**report)


@app.post("/evaluation/system", response_model=EvaluationReportResponse, tags=["Evaluation"])
def create_system_evaluation(req: SystemEvaluationRequest):
    """Evaluate repeatable usefulness signals across self-play variants."""
    try:
        report = run_system_evaluation(
            config_path=req.config_path,
            num_hands=req.num_hands,
            seed=req.seed,
            variants=req.variants,
            run_id=req.run_id,
        )
        return EvaluationReportResponse(**report)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/evaluation/system/{run_id}", response_model=EvaluationReportResponse, tags=["Evaluation"])
def get_system_evaluation(run_id: str):
    """Return a persisted system evaluation report."""
    report = load_system_evaluation(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"System evaluation '{run_id}' not found")
    return EvaluationReportResponse(**report)


@app.post("/sessions/{session_id}/coach", response_model=CoachResponse, tags=["Analysis"])
def coach_session(session_id: str):
    """Generate coach-style feedback for a completed or in-progress session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    history_store = HistoryStore(f"data/history/hand_history_{session_id}.jsonl")
    histories = history_store.load_all()

    styles_dir = Path(DEFAULT_CONFIG_PATH).parent / "styles"
    registry = StyleRegistry(str(styles_dir))
    coach = CoachAgent(AnalysisAgent(registry))
    focus_player = _find_human_id(session.game)
    result = coach.review_session(histories, focus_player_id=focus_player)

    return CoachResponse(
        session_id=session_id,
        report_title=result["report_title"],
        focus_player_id=result["focus_player_id"],
        total_hands=result["total_hands"],
        summary=result["summary"],
        action_profile=result["action_profile"],
        street_profile=result["street_profile"],
        leak_candidates=result["leak_candidates"],
        critical_spots=result["critical_spots"],
        training_plan=result["training_plan"],
        next_drill=result["next_drill"],
        key_findings=result["key_findings"],
        training_goals=result["training_goals"],
        hand_reviews=result["hand_reviews"],
    )


@app.get("/memory/profile", response_model=MemoryProfileResponse, tags=["Memory"])
def get_memory_profile():
    """Return the local long-term user profile."""
    profile = LongTermUserProfile()
    summary = profile.profile_summary()
    temporary_count = len(TemporaryMemoryStore(user_id=profile.user_id).list_memories(status="temporary"))
    summary.setdefault("governance_summary", {})["temporary_pool_count"] = temporary_count
    return MemoryProfileResponse(**summary)


@app.get("/memory/profile/candidates", response_model=MemorySearchResponse, tags=["Memory"])
def get_memory_candidates():
    """Return candidate memories waiting for user confirmation."""
    memories = [m.to_dict() for m in LongTermUserProfile().list_memories(status="candidate")]
    return MemorySearchResponse(query="status:candidate", total=len(memories), memories=memories)


@app.post("/memory/profile/candidates/{memory_id}/accept", tags=["Memory"])
def accept_memory_candidate(memory_id: str):
    """Promote a candidate memory into accepted long-term profile memory."""
    memory = LongTermUserProfile().set_status(memory_id, "accepted")
    if memory is None:
        raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")
    return {"success": True, "memory": memory.to_dict()}


@app.post("/memory/profile/candidates/{memory_id}/reject", tags=["Memory"])
def reject_memory_candidate(memory_id: str):
    """Reject a candidate memory so it will not enter decision prompts."""
    memory = LongTermUserProfile().set_status(memory_id, "rejected")
    if memory is None:
        raise HTTPException(status_code=404, detail=f"Memory '{memory_id}' not found")
    return {"success": True, "memory": memory.to_dict()}


@app.post("/memory/search", response_model=MemorySearchResponse, tags=["Memory"])
def search_memory(req: MemorySearchRequest):
    """Search long-term user profile memory."""
    memories = LongTermUserProfile().search(req.query, status=req.status, limit=req.limit)
    return MemorySearchResponse(query=req.query, total=len(memories), memories=memories)


@app.get("/memory/temporary", response_model=TemporaryMemoryResponse, tags=["Memory"])
def get_temporary_memories():
    """Return low-confidence temporary memories used for governance."""
    store = TemporaryMemoryStore()
    memories = [memory.to_dict() for memory in store.list_memories()]
    return TemporaryMemoryResponse(user_id=store.user_id, total=len(memories), memories=memories)


@app.post("/memory/temporary/{memory_id}/promote", tags=["Memory"])
def promote_temporary_memory(memory_id: str, status: str = "candidate"):
    """Promote a temporary memory into candidate or accepted long-term memory."""
    try:
        result = MemoryManagerAgent().promote_temporary(memory_id, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail=f"Temporary memory '{memory_id}' not found")
    return {"success": True, **result}


@app.post("/memory/temporary/{memory_id}/reject", tags=["Memory"])
def reject_temporary_memory(memory_id: str):
    """Reject a temporary memory so it will not be promoted."""
    memory = MemoryManagerAgent().reject_temporary(memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail=f"Temporary memory '{memory_id}' not found")
    return {"success": True, "memory": memory}


@app.post("/strategy/search", response_model=StrategySearchResponse, tags=["Strategy"])
def search_strategy(req: StrategySearchRequest):
    """Search local strategy chunks with source and chunk ids."""
    chunks = StrategyRAG().search(query=req.query, street=req.street, style=req.style, limit=req.limit)
    return StrategySearchResponse(query=req.query, total=len(chunks), chunks=chunks)


@app.post("/sessions/{session_id}/consolidate", response_model=ConsolidateResponse, tags=["Memory"])
def consolidate_session_memory(session_id: str):
    """Generate candidate long-term memories and a session training plan."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    history_store = HistoryStore(f"data/history/hand_history_{session_id}.jsonl")
    histories = history_store.load_all()
    styles_dir = Path(DEFAULT_CONFIG_PATH).parent / "styles"
    registry = StyleRegistry(str(styles_dir))
    coach = CoachAgent(AnalysisAgent(registry))
    focus_player = _find_human_id(session.game)
    coach_result = coach.review_session(histories, focus_player_id=focus_player)
    result = MemoryConsolidator().consolidate_session(session_id, histories, coach_result, focus_player_id=focus_player)
    return ConsolidateResponse(**result)


@app.post("/sessions/{session_id}/memory-agent/run", response_model=MemoryAgentReportResponse, tags=["Memory"])
def run_session_memory_agent(session_id: str, req: MemoryAgentRunRequest | None = None):
    """Manually run the background memory manager agent for a completed session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    force = bool(req.force) if req else False
    if session.status != "completed" and not force:
        raise HTTPException(status_code=400, detail="Session is not completed yet")
    history_store = HistoryStore(f"data/history/hand_history_{session_id}.jsonl")
    histories = history_store.load_all()
    styles_dir = Path(DEFAULT_CONFIG_PATH).parent / "styles"
    registry = StyleRegistry(str(styles_dir))
    coach = CoachAgent(AnalysisAgent(registry))
    focus_player = _find_human_id(session.game)
    coach_result = coach.review_session(histories, focus_player_id=focus_player)
    report = MemoryManagerAgent().run_session(
        session_id=session_id,
        histories=histories,
        coach_result=coach_result,
        focus_player_id=focus_player,
        force=force,
    )
    return MemoryAgentReportResponse(**report)


@app.get("/sessions/{session_id}/memory-agent/report", response_model=MemoryAgentReportResponse, tags=["Memory"])
def get_session_memory_agent_report(session_id: str):
    """Return the memory manager agent report for a session."""
    session = session_store.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")
    report = MemoryManagerAgent.load_report(session_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Memory agent report for session '{session_id}' not found")
    return MemoryAgentReportResponse(**report)


@app.get("/sessions/{session_id}/memory-context", response_model=MemoryContextResponse, tags=["Memory"])
def get_session_memory_context(session_id: str):
    """Return the memory/RAG context snapshot used for decision debugging."""
    session = session_store.get(session_id)
    if session and hasattr(session.game, "memory_manager"):
        snapshot = session.game.memory_manager.memory_context_snapshot()
    else:
        snapshot = PokerMemoryManager(session_id=session_id).memory_context_snapshot()
    return MemoryContextResponse(**snapshot)
