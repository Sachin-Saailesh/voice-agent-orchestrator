"""
FastAPI Streaming Voice Agent Server
====================================
  GET  /          → serves client/index.html
  GET  /static/*  → serves client/ assets
  WS   /ws/{session_id}

Message schema (client → server):
  { "type": "audio_chunk",  "data": "<base64 PCM/WebM>", "turn_id": N }
  { "type": "end_of_audio",  "turn_id": N }
  { "type": "barge_in",     "turn_id": N }
  { "type": "text_input",   "text": "...",  "turn_id": N }
  { "type": "ping" }

Event schema (server → client):
  { "type": "log", "level": "INFO|WARNING|ERROR|DEBUG", "logger": "...",
    "message": "...", "ts": "HH:MM:SS.mmm" }
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# ─── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent.parent  # voice-agent-orchestrator/
SRC = Path(__file__).parent.parent           # voice-agent-orchestrator/src/
CLIENT = ROOT / "client"

# Load .env from project root
load_dotenv(ROOT / ".env")
load_dotenv(SRC / ".env")

# Make src/ importable
sys.path.insert(0, str(SRC))

from streaming.session import Session, create_session, remove_session, get_session
from streaming.vad import VADProcessor, VADState
from streaming.pipeline import run_turn, run_stt
from streaming import webrtc as webrtc_mod

# ─── WebSocket Log Handler ────────────────────────────────────────────────────

class _WSLogHandler(logging.Handler):
    """
    Captures Python log records and fans them out to every active
    WebSocket session as a {"type":"log", ...} event.
    Queue puts are always non-blocking to avoid back-pressure.
    """
    def emit(self, record: logging.LogRecord):
        try:
            from streaming.session import _sessions  # lazy import avoids circular
            if not _sessions:
                return
            ts = self.formatTime(record, "%H:%M:%S") + f".{record.msecs:03.0f}"
            event = {
                "type": "log",
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "ts": ts,
            }
            # Optional: include exception traceback
            if record.exc_info:
                import traceback
                tb = "".join(traceback.format_exception(*record.exc_info))
                event["traceback"] = tb
            for session in list(_sessions.values()):
                try:
                    session.text_out_q.put_nowait(event)
                except Exception:
                    pass  # queue full or session gone
        except Exception:
            pass  # never let the log handler crash the server


def _setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Console handler (keeps terminal output)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    # WS handler (sends to browser)
    wsh = _WSLogHandler()
    wsh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(ch)
    root.addHandler(wsh)


_setup_logging()
log = logging.getLogger("voice_agent")

# ─── App ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Streaming Voice Agent",
    description="WebSocket low-latency voice agent with Bob & Alice",
    version="2.0.0",
)

# ── Security / permissions headers ───────────────────────────────────────────
# These headers are required so getUserMedia (microphone) and the AudioWorklet
# work correctly when the page is served over plain HTTP from any IP
# (not just localhost).  Without them Chrome/Safari block mic access.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class _PermissionsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Permissions-Policy"] = "microphone=*, camera=()"
        # Required for SharedArrayBuffer / AudioWorklet in cross-origin contexts
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        return response

app.add_middleware(_PermissionsMiddleware)

# Serve client static files if the directory exists
if CLIENT.exists():
    app.mount("/static", StaticFiles(directory=str(CLIENT)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the browser UI."""
    html_path = CLIENT / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(), status_code=200)
    return HTMLResponse(
        content="<h2>Voice Agent Server running. Open <code>/ws/{session_id}</code></h2>",
        status_code=200,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/sessions")
async def list_sessions():
    """Debug endpoint — list active session IDs."""
    from streaming.session import _sessions
    return {"active_sessions": list(_sessions.keys())}


# ─── WebSocket ───────────────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def ws_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    log.info("Session connected: %s", session_id)

    session = create_session(session_id, websocket)
    vad = VADProcessor(
        speech_threshold=float(os.getenv("VAD_SPEECH_THRESHOLD", "0.015")),
        silence_threshold_ms=float(os.getenv("VAD_SILENCE_MS", "500")),
    )
    TTS_DEAF_SECS = 0.7   # seconds to ignore barge-in after TTS ends
    # Startup deaf period: the greeting TTS plays for ~5-10s after connect.
    # Don't process any utterances until this window passes so mic echo
    # of the greeting doesn't cascade into false transcriptions.
    STARTUP_DEAF_SECS = 8.0   # wait this long after connect before processing audio
    session_start_time = time.time()

    # Min/max audio sanity bounds (16-bit mono 16kHz)
    MIN_AUDIO_BYTES = 8000    # < 0.25s — too short to be real speech
    MAX_AUDIO_BYTES = 400000  # > 12.5s — probably accumulated echo, not a utterance

    # Background sender task (coalesces outbound events)
    sender_task = asyncio.create_task(_sender_loop(session))
    session.add_task(sender_task)

    async def _inactivity_monitor():
        await asyncio.sleep(STARTUP_DEAF_SECS)
        while True:
            await asyncio.sleep(1.0)
            if session.peer_connection is None and session_id not in _sessions:
                break
                
            # If the pipeline is running or TTS is playing, they aren't inactive
            if session.pipeline_task and not session.pipeline_task.done():
                session.last_activity_time = time.time()
                session.inactivity_notified = False
                continue
                
            if session.tts_playing:
                session.last_activity_time = time.time()
                session.inactivity_notified = False
                continue
                
            elapsed = time.time() - session.last_activity_time
            if elapsed >= 30.0 and not session.inactivity_notified:
                log.info("Inactivity timeout reached (session=%s)", session_id)
                session.inactivity_notified = True
                
                # Proactively ask the user if they're still there
                turn_id = session.new_turn()
                prompt = "[System: The user has been completely silent for 30 seconds. Gently ask if they are still there or if they need more time.]"
                
                # Send a processing event so the UI knows we are thinking
                await session.text_out_q.put({"type": "stt_processing", "turn_id": turn_id})
                await session.text_out_q.put({
                    "type": "final_transcript", 
                    "text": "[User inactive for 30 seconds]", 
                    "turn_id": turn_id
                })
                
                session.pipeline_task = asyncio.create_task(run_turn(session, prompt, turn_id))

    inactivity_task = asyncio.create_task(_inactivity_monitor())
    session.add_task(inactivity_task)

    async def _process_pcm_chunk(chunk: bytes):
        session.audio_buffer.extend(chunk)
        vad_result = vad.process_chunk(chunk)
        
        if vad_result.in_utterance:
            session.last_activity_time = time.time()
            session.inactivity_notified = False
            
        # Enforce 300ms pre-roll silence buffer (9600 bytes at 16kHz 16-bit mono)
        # We only truncate if we are NOT in an active utterance or barge-in decay.
        if not vad_result.in_utterance and vad_result.state != VADState.END_OF_UTTERANCE:
            if len(session.audio_buffer) > 9600:
                session.audio_buffer = session.audio_buffer[-9600:]

        if (session.tts_playing
                and time.time() > session.tts_deaf_until
                and vad_result.rms >= 0.04
                and vad.is_barge_in(vad_result.rms)):
            log.info("Barge-in detected (session=%s, rms=%.4f)", session_id, vad_result.rms)
            _do_barge_in(session)
            session.tts_playing = False
            session.tts_deaf_until = time.time() + TTS_DEAF_SECS
            session.audio_buffer = bytearray()
            vad.reset()
            await session.text_out_q.put({
                "type": "barge_in_ack",
                "turn_id": session.turn_id,
            })
        elif vad_result.state == VADState.END_OF_UTTERANCE:
            audio_snapshot = bytes(session.audio_buffer)
            turn_id = session.new_turn()
            vad.reset()
            elapsed = time.time() - session_start_time
            if elapsed < STARTUP_DEAF_SECS:
                log.debug("Startup deaf: skipping EOU")
            elif len(audio_snapshot) < MIN_AUDIO_BYTES:
                log.debug(f"Skipping EOU: audio too short ({len(audio_snapshot)} bytes)")
            elif len(audio_snapshot) > MAX_AUDIO_BYTES:
                log.debug(f"Skipping EOU: audio too long ({len(audio_snapshot)} bytes)")
            else:
                log.info("End-of-utterance (session=%s, turn=%d, audio_len=%d)", session_id, turn_id, len(audio_snapshot))
                session.pipeline_task = asyncio.create_task(_process_audio_turn(session, audio_snapshot, turn_id))

    async def _webrtc_pump():
        while True:
            try:
                chunk = await session.webrtc_audio_q.get()
                await _process_pcm_chunk(chunk)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"webrtc_pump error: {e}")

    webrtc_pump_task = asyncio.create_task(_webrtc_pump())
    session.add_task(webrtc_pump_task)

    # Send welcome event
    await session.text_out_q.put({
        "type": "connected",
        "session_id": session_id,
        "agent": session.agent_manager.current_agent,
    })

    # Play Bob's greeting on connect
    greeting = (
        "Hi! I'm Bob, your renovation planning assistant. "
        "I'm here to help you think through your project. "
        "What room are you looking to renovate?"
    )
    await session.text_out_q.put({
        "type": "llm_token",
        "token": greeting,
        "turn_id": session.turn_id,
    })
    asyncio.create_task(_stream_greeting(session, greeting))

    # Main receive loop
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")
            client_turn_id = msg.get("turn_id", session.turn_id)

            # ── ping / pong ────────────────────────────────────────────────
            if msg_type == "ping":
                await session.text_out_q.put({"type": "pong"})

            # ── audio chunk from microphone (WebSocket legacy) ─────────────
            elif msg_type == "audio_chunk":
                # Only use WebSocket audio if WebRTC is not active
                if session.peer_connection is not None:
                    continue

                audio_b64 = msg.get("data", "")
                if not audio_b64:
                    continue
                try:
                    chunk = base64.b64decode(audio_b64)
                except Exception:
                    continue

                await _process_pcm_chunk(chunk)

            # ── manual end-of-audio signal ─────────────────────────────────
            elif msg_type == "end_of_audio":
                # The server-side VAD strictly manages endpointing for both WebRTC
                # and WebSocket fallback streams now, ensuring proper pre-rolls.
                # We can safely ignore the client VAD's spurious manual trigger.
                continue

            # ── manual barge-in button ─────────────────────────────────────
            elif msg_type == "barge_in":
                _do_barge_in(session)
                await session.text_out_q.put({
                    "type": "barge_in_ack",
                    "turn_id": session.turn_id,
                })

            # ── text input (dev/debug or text mode) ────────────────────────
            elif msg_type == "text_input":
                text = msg.get("text", "").strip()
                if text:
                    turn_id = session.new_turn()
                    vad.reset()
                    log.info("Text input (session=%s, turn=%d): %s", session_id, turn_id, text)
                    await session.text_out_q.put({
                        "type": "final_transcript",
                        "text": text,
                        "turn_id": turn_id,
                    })
                    asyncio.create_task(run_turn(session, text, turn_id))
                    tts_playing = True

            # ── WebRTC offer (SDP exchange) ────────────────────────────────
            elif msg_type == "webrtc_offer":
                offer_sdp = msg.get("sdp", "")
                if offer_sdp:
                    answer_sdp = await webrtc_mod.setup_peer_connection(session, offer_sdp)
                    if answer_sdp:
                        await session.text_out_q.put({
                            "type": "webrtc_answer",
                            "sdp":  answer_sdp,
                        })
                        log.info("WebRTC: sent SDP answer (session=%s)", session_id)

            # ── WebRTC ICE candidate (relay from browser) ──────────────────
            elif msg_type == "ice_candidate":
                candidate = msg.get("candidate")
                if candidate:
                    await webrtc_mod.add_ice_candidate(session, candidate)

            # ── TTS done acknowledgement ───────────────────────────────────
            elif msg_type == "tts_playback_done":
                session.tts_playing = False
                # Start dead period so room echo doesn’t immediately trigger barge-in
                session.tts_deaf_until = time.time() + TTS_DEAF_SECS
                log.debug("TTS playback done — barge-in suppressed for %.1fs", TTS_DEAF_SECS)

    except WebSocketDisconnect:
        log.info("Session disconnected: %s", session_id)
    except Exception as e:
        log.error("Session error (session=%s): %s", session_id, e, exc_info=True)
    finally:
        await webrtc_mod.close_peer_connection(session)
        remove_session(session_id)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _do_barge_in(session: Session):
    """Signal cancellation of current TTS and pipeline."""
    log.info("Barge-in: cancelling turn %d for session %s", session.turn_id, session.session_id)
    session.tts_cancel.set()
    session.pipeline_cancel.set()


async def _process_audio_turn(session: Session, audio_bytes: bytes, turn_id: int):
    """STT → pipeline in one async task."""
    transcript = await run_stt(session, audio_bytes, turn_id)
    
    if transcript and not session.pipeline_cancel.is_set() and session.turn_id == turn_id:
        await run_turn(session, transcript, turn_id)
    elif not transcript and getattr(session, 'partial_response', "") and not session.pipeline_cancel.is_set() and session.turn_id == turn_id:
        # Agent was interrupted by noise (empty transcript), resume previous thought
        prompt = "[System: The user accidentally interrupted with background noise. Please naturally continue your previous sentence exactly from where you left off.]"
        await run_turn(session, prompt, turn_id)


async def _stream_greeting(session: Session, text: str):
    """Stream the greeting TTS on connect."""
    from streaming.streaming_tts import StreamingTTSClient
    tts = StreamingTTSClient()
    async for chunk in tts.stream_chunks(text, "bob", session.tts_cancel):
        await session.text_out_q.put({
            "type": "tts_chunk",
            "audio": base64.b64encode(chunk).decode(),
            "turn_id": session.turn_id,
        })
    await session.text_out_q.put({"type": "tts_done", "turn_id": session.turn_id})


async def _sender_loop(session: Session):
    """
    Dedicated task that reads from text_out_q and sends to WS.
    Coalesces multiple events into one send every WS_COALESCE_MS milliseconds
    to avoid flooding the network with tiny messages.
    """
    coalesce_ms = float(os.getenv("WS_COALESCE_MS", "25")) / 1000.0
    batch: list[dict] = []

    async def flush():
        nonlocal batch
        if not batch:
            return
        to_send = batch
        batch = []
        # If single event, send as plain dict; else as array
        payload = to_send[0] if len(to_send) == 1 else to_send
        try:
            await session.ws.send_json(payload)
        except Exception:
            pass  # WS already closed

    try:
        while True:
            # Drain queue with timeout for coalescing
            deadline = asyncio.get_event_loop().time() + coalesce_ms
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(session.text_out_q.get(), timeout=remaining)
                    batch.append(event)
                    session.text_out_q.task_done()
                except asyncio.TimeoutError:
                    break

            await flush()

    except asyncio.CancelledError:
        # Send any remaining events before exiting
        try:
            while not session.text_out_q.empty():
                event = session.text_out_q.get_nowait()
                batch.append(event)
            await flush()
        except Exception:
            pass
        raise


# ─── CORS (for local dev) ────────────────────────────────────────────────────
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "streaming.server:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
        log_level="info",
    )
