"""
Session Management
Per-session state container: turn_id, queues, cancellation, and components.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from fastapi import WebSocket

from agents import AgentManager
from state import ConversationState
from router import TransferRouter


@dataclass
class Session:
    """Encapsulates all state and resources for one WebSocket session."""
    session_id: str
    ws: WebSocket

    # Reuse existing domain modules (no changes needed)
    agent_manager: AgentManager = field(default_factory=AgentManager)
    state: ConversationState = field(default_factory=ConversationState)
    router: TransferRouter = field(default_factory=TransferRouter)

    # Per-turn generation id — clients drop events with stale turn_ids
    turn_id: int = field(default=0)

    # Audio buffer: collects PCM chunks for the current utterance
    audio_buffer: bytearray = field(default_factory=bytearray)

    # Async queues
    audio_in_q: asyncio.Queue = field(default_factory=asyncio.Queue)     # raw PCM from client (WebSocket path)
    text_out_q: asyncio.Queue = field(default_factory=asyncio.Queue)     # events to send to client
    webrtc_audio_q: asyncio.Queue = field(default_factory=asyncio.Queue) # raw PCM from WebRTC mic track

    # WebRTC peer connection (set by webrtc.setup_peer_connection)
    peer_connection: object = field(default=None)
    agent_track: object = field(default=None)  # AgentAudioTrack

    # Barge-in / TTS tracking
    tts_playing: bool = field(default=False)
    tts_deaf_until: float = field(default=0.0)

    # The single active pipeline task — cancelled explicitly on new utterance
    pipeline_task: Optional[asyncio.Task] = field(default=None)

    # Cancellation signals
    pipeline_cancel: asyncio.Event = field(default_factory=asyncio.Event)
    tts_cancel: asyncio.Event = field(default_factory=asyncio.Event)
    pipeline_running: asyncio.Event = field(default_factory=asyncio.Event)

    # Running background tasks (kept to allow cancellation)
    _tasks: list = field(default_factory=list)

    # Metrics
    turn_start_time: float = field(default_factory=time.time)

    # ── Context checkpointing ──────────────────────────────────────────────
    # When a barge-in interrupts the LLM or TTS mid-response, we save what
    # was said so far so the next turn can continue with full context.
    partial_response: str = field(default="")

    # Inactivity tracking
    last_activity_time: float = field(default_factory=time.time)
    inactivity_notified: bool = field(default=False)

    def checkpoint(self, spoken_so_far: str):
        """Save a partial response that was interrupted by barge-in."""
        self.partial_response = spoken_so_far.strip()

    def pop_checkpoint(self) -> str:
        """Return and clear any saved partial response."""
        p = self.partial_response
        self.partial_response = ""
        return p

    def new_turn(self, cancel_pipeline: bool = True) -> int:
        """
        Increment turn_id and re-arm cancellation events.
        Explicitly cancels the previous pipeline task if one is running.
        """
        self.turn_id += 1
        # Signal old pipeline to stop (for barge-in checks inside the pipeline)
        self.pipeline_cancel.set()
        self.tts_cancel.set()
        # Cancel previous asyncio task — guaranteed to interrupt any await
        if cancel_pipeline and self.pipeline_task and not self.pipeline_task.done():
            self.pipeline_task.cancel()
        self.pipeline_task = None
        # Re-arm for the new turn
        self.pipeline_cancel.clear()
        self.tts_cancel.clear()
        # Reset audio buffer and TTS state for new turn
        self.audio_buffer = bytearray()
        self.tts_playing = False
        self.turn_start_time = time.time()
        return self.turn_id

    def add_task(self, task: asyncio.Task):
        """Register a background task for cleanup on disconnect."""
        self._tasks.append(task)

    def cancel_all(self):
        """Cancel all background tasks on disconnect."""
        self.pipeline_cancel.set()
        self.tts_cancel.set()
        for task in self._tasks:
            if not task.done():
                task.cancel()
        self._tasks.clear()

    def latency_ms(self) -> int:
        """Milliseconds since the current turn started."""
        return int((time.time() - self.turn_start_time) * 1000)


# Global session registry
_sessions: dict[str, Session] = {}


def get_session(session_id: str) -> Optional[Session]:
    return _sessions.get(session_id)


def create_session(session_id: str, ws: WebSocket) -> Session:
    session = Session(session_id=session_id, ws=ws)
    _sessions[session_id] = session
    return session


def remove_session(session_id: str):
    session = _sessions.pop(session_id, None)
    if session:
        session.cancel_all()
