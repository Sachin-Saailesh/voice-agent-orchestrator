"""
Async Pipeline — STT → Guardrails → Transfer → LLM Stream → Guardrails → TTS Stream
Called once per END_OF_UTTERANCE or text_input event.
Checks cancellation events between every step for barge-in safety.
"""

import asyncio
import base64
import json
import os
import sys
import time
from typing import Optional

from streaming.guardrails import GuardrailFilter
from streaming.streaming_llm import StreamingLLMClient
from streaming.streaming_stt import StreamingSTTClient
from streaming.streaming_tts import StreamingTTSClient

# Lazy singletons (shared across sessions, thread-safe async clients)
_guardrail = GuardrailFilter()
_stt = StreamingSTTClient()
_llm = StreamingLLMClient()
_tts = StreamingTTSClient()


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _send(session, event: dict):
    """Put an event on the outbound queue. Drops stale turn_ids on the receiver."""
    await session.text_out_q.put(event)


async def _send_now(session, event: dict):
    """Immediately send a WS message (bypasses coalesce queue for urgent signals)."""
    try:
        await session.ws.send_json(event)
    except Exception:
        pass


# ─── Pipeline ────────────────────────────────────────────────────────────────

async def run_turn(session, transcript: str, turn_id: int):
    """
    Full pipeline for one conversation turn.
    
    Args:
        session: Session object (from session.py)
        transcript: Final STT transcript text
        turn_id: The turn_id for this pipeline run (used for stale-drop)
    """

    def cancelled() -> bool:
        """Check if this turn has been interrupted by a barge-in."""
        return session.pipeline_cancel.is_set()

    # ── Step 1: Guardrail on user input ──────────────────────────────────────
    result = await _guardrail.check(transcript)
    if not result.ok:
        await _send(session, {
            "type": "guardrail_blocked",
            "turn_id": turn_id,
            "reason": result.reason or "Content policy violation on your message",
        })
        return

    if cancelled():
        return

    # ── Step 2: Transfer detection (pre-LLM, reuses existing router) ─────────
    transfer = session.router.detect_transfer(transcript)
    if transfer:
        target = transfer["target_agent"]
        if target != session.agent_manager.current_agent:
            from_agent = session.agent_manager.current_agent
            handoff_msg = session.agent_manager.transfer_to(target, session.state)
            session.state.add_turn(speaker="system", text=f"[Transferred to {target}]")

            await _send(session, {
                "type": "agent_change",
                "agent": target,
                "from_agent": from_agent,
                "handoff_message": handoff_msg,
                "turn_id": turn_id,
            })

            # Synthesize the handoff announcement first
            async for chunk in _tts.stream_chunks(handoff_msg, from_agent, session.tts_cancel):
                if cancelled():
                    return
                await _send(session, {
                    "type": "tts_chunk",
                    "audio": base64.b64encode(chunk).decode(),
                    "turn_id": turn_id,
                })

            if cancelled():
                return

            # Now let the new agent respond
            await _send(session, {"type": "tts_done", "turn_id": turn_id})

    if cancelled():
        return

    # ── Step 3: Build LLM messages + inject checkpoint context ─────────────
    is_transfer = transfer is not None and transfer["target_agent"] != session.agent_manager.current_agent

    # If the previous turn was interrupted, prepend checkpoint context so the
    # LLM understands what was partially said before the user cut in.
    prior_partial = session.pop_checkpoint()
    if prior_partial:
        session.state.add_turn(
            speaker=session.agent_manager.current_agent,
            text=f"[INTERRUPTED — was saying: {prior_partial}]",
        )
        await _send(session, {
            "type": "checkpoint_restored",
            "partial": prior_partial,
            "turn_id": turn_id,
        })

    messages = session.agent_manager._build_messages(
        transcript,
        session.state,
        is_transfer,
    )

    # ── Step 4: Stream LLM tokens ─────────────────────────────────────────────
    full_response = ""
    token_batch = []
    last_flush = time.time()
    coalesce_ms = float(os.getenv("WS_COALESCE_MS", "25")) / 1000.0

    tts_buffer = ""  # accumulates tokens until sentence boundary for TTS
    tts_task: Optional[asyncio.Task] = None

    async def flush_tts_buffer(force: bool = False):
        """Start TTS for a sentence chunk if buffer is ready."""
        nonlocal tts_buffer, tts_task
        if not tts_buffer.strip():
            return
        # Wait for previous TTS task to complete first
        if tts_task and not tts_task.done():
            if not force:
                return
            await tts_task

        text_to_speak = tts_buffer.strip()
        tts_buffer = ""
        agent = session.agent_manager.current_agent

        async def _stream_tts():
            async for chunk in _tts.stream_chunks(text_to_speak, agent, session.tts_cancel):
                if cancelled():
                    return
                # Only activate barge-in detection when audio actually starts flowing
                session.tts_playing = True
                await _send(session, {
                    "type": "tts_chunk",
                    "audio": base64.b64encode(chunk).decode(),
                    "turn_id": turn_id,
                })

        tts_task = asyncio.create_task(_stream_tts())

    async for token in _llm.stream_tokens(
        messages,
        cancel_event=session.pipeline_cancel,
        model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
        max_tokens=400,
        temperature=0.7,
    ):
        if cancelled():
            if tts_task:
                tts_task.cancel()
            # ── Context checkpoint: save what was generated before barge-in ──
            spoken_so_far = full_response.strip()
            if spoken_so_far:
                session.checkpoint(spoken_so_far)
                await _send(session, {
                    "type": "checkpoint_saved",
                    "partial": spoken_so_far[:120],   # preview for UI
                    "turn_id": turn_id,
                })
            return

        full_response += token
        tts_buffer += token
        token_batch.append(token)

        # Coalesce token events every 25ms
        now = time.time()
        if now - last_flush >= coalesce_ms:
            if token_batch:
                await _send(session, {
                    "type": "llm_token",
                    "token": "".join(token_batch),
                    "turn_id": turn_id,
                })
                token_batch = []
            last_flush = now

        # TTS sentence boundary: start audio when we hit punctuation
        if any(tts_buffer.rstrip().endswith(p) for p in [".", "!", "?", "\n"]):
            await flush_tts_buffer()

    # Flush any remaining tokens
    if token_batch:
        await _send(session, {
            "type": "llm_token",
            "token": "".join(token_batch),
            "turn_id": turn_id,
        })

    if cancelled():
        if tts_task:
            tts_task.cancel()
        # Checkpoint anything that was buffered but not yet spoken
        if full_response.strip():
            session.checkpoint(full_response.strip())
            await _send(session, {
                "type": "checkpoint_saved",
                "partial": full_response.strip()[:120],
                "turn_id": turn_id,
            })
        return

    # ── Step 5: Guardrail on LLM output ──────────────────────────────────────
    if full_response:
        result = await _guardrail.check(full_response)
        if not result.ok:
            if tts_task:
                tts_task.cancel()
            session.tts_cancel.set()
            await _send(session, {
                "type": "guardrail_blocked",
                "turn_id": turn_id,
                "reason": result.reason or "Agent response was blocked by content policy",
            })
            return

    # Flush any remaining TTS buffer (last partial sentence)
    await flush_tts_buffer(force=True)

    # Wait for TTS to complete
    if tts_task:
        try:
            await tts_task
        except asyncio.CancelledError:
            pass

    if cancelled():
        return

    await _send(session, {"type": "tts_done", "turn_id": turn_id})

    # ── Step 6: Update conversation state ────────────────────────────────────
    if full_response:
        session.state.add_turn(speaker="user", text=transcript)
        session.state.add_turn(speaker=session.agent_manager.current_agent, text=full_response)

        # Run state update in background (non-blocking)
        asyncio.create_task(
            _update_state_async(session, transcript, full_response, turn_id)
        )


async def _update_state_async(session, user_text: str, agent_text: str, turn_id: int):
    """Update conversation state in background without blocking the pipeline."""
    try:
        # Use the streaming LLM client's complete() for state extraction
        prompt = f"""Analyze this conversation turn and update the JSON state.

CURRENT STATE:
{session.state.get_state_summary()}

TURN:
User: {user_text}
Agent: {agent_text}

OUTPUT ONLY JSON with keys to update from the existing schema."""

        messages = [{"role": "user", "content": prompt}]
        result = await _llm.complete(messages, model="gpt-4o-mini", max_tokens=200, temperature=0.0)

        if result:
            import json
            try:
                clean = result.replace("```json", "").replace("```", "").strip()
                updates = json.loads(clean)
                session.state._merge_updates(updates)
            except Exception:
                pass

        # Also update rolling summary
        session.state.summary = (
            session.state.summary + f" User: {user_text}. Agent: {agent_text}."
        )[-500:]

        # Send state update to client
        await session.text_out_q.put({
            "type": "state_update",
            "turn_id": turn_id,
            "state": session.state.structured_state,
        })

    except Exception:
        pass


# ─── STT Pipeline ────────────────────────────────────────────────────────────

async def run_stt(session, audio_bytes: bytes, turn_id: int) -> Optional[str]:
    """
    Run STT on buffered audio.
    Returns transcript text, or None on failure.
    Sends partial_transcript and final_transcript events.
    """
    if not audio_bytes:
        return None

    # Send a "processing" event so UI shows spinner
    await _send(session, {"type": "stt_processing", "turn_id": turn_id})

    transcript = await _stt.transcribe(audio_bytes)

    if transcript and transcript.strip():
        await _send(session, {
            "type": "final_transcript",
            "text": transcript,
            "turn_id": turn_id,
            "latency_ms": session.latency_ms(),
        })
        return transcript
    else:
        # If there's an active interrupted response, the server will auto-resume. Show graceful text.
        if getattr(session, 'partial_response', ""):
            await _send(session, {
                "type": "final_transcript",
                "text": "[Noise detected — resuming...]",
                "turn_id": turn_id,
            })
        else:
            await _send(session, {
                "type": "error",
                "message": "Could not transcribe audio. Please try again.",
                "turn_id": turn_id,
            })
        return None
