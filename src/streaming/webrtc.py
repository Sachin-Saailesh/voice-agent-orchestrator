"""
WebRTC peer connection management for the Voice Agent.

Roles
-----
- AgentAudioTrack  : MediaStreamTrack that streams TTS PCM frames to the browser.
- setup_peer_connection(session, offer_sdp) → answer_sdp
    Creates an aiortc RTCPeerConnection for one session, wires the
    microphone track coming from the browser into the STT pipeline, and
    attaches an AgentAudioTrack so TTS audio goes back via DTLS-SRTP.

Signal flow (ICE candidates are exchanged over the existing WebSocket)
----------------------------------------------------------------------
Client                          Server
  |-- {type:"webrtc_offer"} --> ws_endpoint --> setup_peer_connection()
  |<- {type:"webrtc_answer"} --
  |-- {type:"ice_candidate"} --> pc.addIceCandidate()
  |<- {type:"ice_candidate"} -- (via session.text_out_q)
  |==== DTLS-SRTP audio ======>  _mic_receiver() → STT pipeline
  |<=== DTLS-SRTP audio ======  AgentAudioTrack.push_frame(pcm)
"""

import asyncio
import fractions
import logging
import time
from typing import Optional

import numpy as np

log = logging.getLogger("voice_agent.webrtc")

try:
    from aiortc import (
        RTCPeerConnection,
        RTCSessionDescription,
        RTCIceCandidate,
        MediaStreamTrack,
    )
    from aiortc.contrib.media import MediaBlackhole
    import av
    AIORTC_AVAILABLE = True
except ImportError:
    AIORTC_AVAILABLE = False
    log.warning("aiortc not installed — WebRTC audio disabled. "
                "Run: pip install aiortc")


# ── Constants ────────────────────────────────────────────────────────────────
SAMPLE_RATE   = 16_000   # Hz — must match STT / TTS
CHANNELS      = 1
FRAME_SAMPLES = 960      # 60 ms at 16 kHz (Opus default frame size)
TIME_BASE     = fractions.Fraction(1, SAMPLE_RATE)


# ─────────────────────────────────────────────────────────────────────────────
# AgentAudioTrack
# ─────────────────────────────────────────────────────────────────────────────

if AIORTC_AVAILABLE:
    class AgentAudioTrack(MediaStreamTrack):
        """
        Outbound audio track: TTS synthesiser pushes raw PCM int16 bytes via
        push_pcm().  recv() packages them into av.AudioFrame objects that
        aiortc sends to the browser over the WebRTC audio channel.

        If no frames are available recv() emits silence so the track stays
        alive and the browser does not drop it.
        """
        kind = "audio"

        def __init__(self):
            super().__init__()
            self._queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
            self._timestamp = 0
            self._leftover = b""          # carry-over from previous push

        def push_pcm(self, pcm_int16: bytes) -> None:
            """Non-blocking: called from TTS coroutine to enqueue raw PCM."""
            self._queue.put_nowait(pcm_int16)

        def flush(self) -> None:
            """Signal end of a TTS utterance (drain queue gracefully)."""
            self._queue.put_nowait(None)

        async def recv(self) -> "av.AudioFrame":
            """Called by aiortc once per frame interval."""
            # Accumulate enough samples for one frame
            buf = self._leftover
            target = FRAME_SAMPLES * 2  # 2 bytes per int16 sample

            while len(buf) < target:
                try:
                    chunk = await asyncio.wait_for(self._queue.get(), timeout=0.02)
                except asyncio.TimeoutError:
                    # Emit silence to keep the track alive
                    chunk = bytes(target - len(buf))
                if chunk is None:
                    # Flush marker — pad with silence and emit
                    chunk = bytes(target - len(buf))
                buf += chunk

            # Slice exactly one frame; keep the rest for next call
            frame_bytes = buf[:target]
            self._leftover = buf[target:]

            # Build av.AudioFrame
            samples = np.frombuffer(frame_bytes, dtype=np.int16)
            frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
            frame.planes[0].update(samples.tobytes())
            frame.sample_rate = SAMPLE_RATE
            frame.pts         = self._timestamp
            frame.time_base   = TIME_BASE
            self._timestamp  += FRAME_SAMPLES
            return frame

else:
    # Stub when aiortc is not installed
    class AgentAudioTrack:  # type: ignore[no-redef]
        kind = "audio"
        def push_pcm(self, pcm: bytes) -> None: pass
        def flush(self) -> None: pass


# ─────────────────────────────────────────────────────────────────────────────
# Peer connection lifecycle
# ─────────────────────────────────────────────────────────────────────────────

async def setup_peer_connection(session, offer_sdp: str) -> Optional[str]:
    """
    Create an RTCPeerConnection for *session*, wire up audio tracks, and
    return the SDP answer string (to be sent back to the browser).

    The session object must expose:
        session.text_out_q         – asyncio.Queue for outbound WS events
        session.webrtc_audio_q     – asyncio.Queue[bytes] for incoming mic PCM
        session.agent_track        – will be set to AgentAudioTrack instance
        session.peer_connection    – will be set to RTCPeerConnection instance
    """
    if not AIORTC_AVAILABLE:
        log.error("aiortc not available — cannot set up WebRTC peer connection")
        return None

    pc = RTCPeerConnection()
    session.peer_connection = pc
    agent_track = AgentAudioTrack()
    session.agent_track = agent_track

    # Add outbound TTS audio track
    pc.addTrack(agent_track)

    # Relay ICE candidates to the browser via the WebSocket control channel
    @pc.on("icecandidate")
    async def on_ice_candidate(candidate):
        if candidate:
            await session.text_out_q.put({
                "type": "ice_candidate",
                "candidate": {
                    "candidate":     candidate.to_sdp(),
                    "sdpMid":        candidate.sdpMid,
                    "sdpMLineIndex": candidate.sdpMLineIndex,
                },
            })

    # Receive mic audio from the browser
    @pc.on("track")
    def on_track(track):
        if track.kind == "audio":
            log.info("WebRTC: browser mic track received")
            asyncio.ensure_future(_mic_receiver(track, session))

    @pc.on("connectionstatechange")
    async def on_state_change():
        log.info("WebRTC connection state: %s", pc.connectionState)
        await session.text_out_q.put({
            "type": "webrtc_state",
            "state": pc.connectionState,
        })

    # Process SDP offer / answer
    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)
    return pc.localDescription.sdp


async def add_ice_candidate(session, candidate_dict: dict) -> None:
    """Add an ICE candidate received from the browser."""
    if not AIORTC_AVAILABLE:
        return
    pc = getattr(session, "peer_connection", None)
    if pc is None:
        return
    try:
        from aiortc.sdp import candidate_from_sdp
        candidate_str = candidate_dict.get("candidate", "")
        if "candidate:" in candidate_str:
            candidate_str = candidate_str.split("candidate:", 1)[1]
            
        candidate = candidate_from_sdp(candidate_str)
        candidate.sdpMid = candidate_dict.get("sdpMid")
        candidate.sdpMLineIndex = candidate_dict.get("sdpMLineIndex")
        
        await pc.addIceCandidate(candidate)
    except Exception as exc:
        log.warning("Failed to add ICE candidate: %s", exc)


async def close_peer_connection(session) -> None:
    """Gracefully close the RTCPeerConnection when the WS session ends."""
    pc = getattr(session, "peer_connection", None)
    if pc:
        await pc.close()
        session.peer_connection = None


# ─────────────────────────────────────────────────────────────────────────────
# Mic audio receiver
# ─────────────────────────────────────────────────────────────────────────────

async def _mic_receiver(track, session) -> None:
    """
    Continuously reads audio frames from the browser's mic track and
    pushes raw int16 PCM bytes into session.webrtc_audio_q.
    Maintains a per-receiver format resampler.
    """
    log.info("WebRTC mic receiver started for session %s", session.session_id)
    q: asyncio.Queue = session.webrtc_audio_q
    
    import av
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    
    # Instrumentation stats
    frames_received = 0
    frames_dropped = 0
    last_log_time = time.time()
    
    try:
        while True:
            frame = await track.recv()
            frames_received += 1
            
            # Resample to 16kHz mono s16 PCM
            resampled_frames = resampler.resample(frame)
            pcm = b"".join(f.to_ndarray().tobytes() for f in resampled_frames)
            
            if pcm:
                if q.qsize() > 50:  # bounding the queue (50 frames is approx 1 second)
                    frames_dropped += 1
                    try:
                        q.get_nowait()  # Drop oldest frame
                    except asyncio.QueueEmpty:
                        pass
                await q.put(pcm)
                
            # Log instrumentation once per second
            now = time.time()
            if now - last_log_time >= 1.0:
                # Calculate basic RMS of the frame for logging
                import struct
                import math
                if len(pcm) >= 2:
                    n = len(pcm) // 2
                    samples = struct.unpack_from(f"<{n}h", pcm, 0)
                    rms = math.sqrt(sum(s * s for s in samples) / n) if n > 0 else 0
                else:
                    rms = 0
                
                log.info(
                    "WebRTC Audio [session=%s] - %d Hz, %s channels | RMS: %.1f | Queue Depth: %d | Dropped: %d",
                    session.session_id,
                    frame.sample_rate,
                    len(frame.layout.channels),
                    rms,
                    q.qsize(),
                    frames_dropped
                )
                last_log_time = now
                frames_dropped = 0

    except Exception as exc:
        log.info("WebRTC mic receiver ended: %s", exc)
