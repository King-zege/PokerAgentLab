"""Session management for poker games."""

from dataclasses import dataclass, field
from typing import Dict, Optional
import time

from engine.game import Game


@dataclass
class GameSession:
    """Holds all state for an active game session."""
    session_id: str
    game: Game
    status: str = "created"  # created | running | completed | error
    created_at: float = field(default_factory=time.time)
    current_hand: int = 0
    num_hands: int | None = None
    mode: str = "fixed"
    error: Optional[str] = None

    @property
    def created_at_str(self) -> str:
        """Return creation time as ISO string."""
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.created_at))


class SessionStore:
    """Manages all active game sessions."""

    def __init__(self):
        self._sessions: Dict[str, GameSession] = {}

    def create(self, session_id: str, game: Game, mode: str = "fixed", num_hands: int | None = None) -> GameSession:
        """Create a new game session."""
        session = GameSession(
            session_id=session_id,
            game=game,
            mode=mode,
            num_hands=num_hands,
            status="created",
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> GameSession | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def remove(self, session_id: str) -> bool:
        """Remove a session."""
        return self._sessions.pop(session_id, None) is not None

    def list_active(self) -> list[GameSession]:
        """List all active sessions."""
        return list(self._sessions.values())

    def update_status(self, session_id: str, status: str, error: str | None = None):
        """Update session status."""
        session = self._sessions.get(session_id)
        if session:
            session.status = status
            if error:
                session.error = error

    def update_progress(self, session_id: str, current_hand: int | None = None, status: str | None = None):
        """Update live progress fields from a runner."""
        session = self._sessions.get(session_id)
        if not session:
            return
        if current_hand is not None:
            session.current_hand = current_hand
        if status is not None:
            session.status = status


# Global session store instance
session_store = SessionStore()
