"""
Streaming TTS Client
Wraps OpenAI TTS with chunked streaming output.
Yields audio bytes as they arrive so the browser can start playing
before the full synthesis is done.

Strategy: chunk the full response text into sentences, then
synthesize sentence by sentence for minimum time-to-first-audio.
"""

import asyncio
import os
import re
from typing import AsyncIterator, Optional

import openai


# ─── Sentence splitter ──────────────────────────────────────────────────────

_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+')


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for per-sentence TTS."""
    parts = _SENTENCE_BOUNDARY.split(text.strip())
    # Filter very short fragments and merge them with the next sentence
    merged = []
    buf = ""
    for part in parts:
        buf = (buf + " " + part).strip()
        if len(buf) >= 20:  # Minimum chars for a TTS call
            merged.append(buf)
            buf = ""
    if buf:
        if merged:
            merged[-1] = merged[-1] + " " + buf
        else:
            merged.append(buf)
    return merged or [text.strip()]


# ─── StreamingTTSClient ──────────────────────────────────────────────────────

class StreamingTTSClient:
    """Async TTS wrapper that yields audio chunks."""

    VOICE_MAP = {
        "bob": os.getenv("TTS_VOICE_BOB", "alloy"),
        "alice": os.getenv("TTS_VOICE_ALICE", "shimmer"),
    }
    DEFAULT_MODEL = os.getenv("TTS_MODEL", "tts-1")
    CHUNK_SIZE = int(os.getenv("TTS_CHUNK_SIZE", "4096"))

    def __init__(self):
        self._client: Optional[openai.AsyncOpenAI] = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self._client = openai.AsyncOpenAI(api_key=api_key)

    def _voice_for(self, agent: str) -> str:
        return self.VOICE_MAP.get(agent.lower(), "alloy")

    async def stream_chunks(
        self,
        text: str,
        agent: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        """
        Synthesize `text` and yield audio bytes as chunks arrive.
        Checks cancel_event between chunks; returns immediately on barge-in.
        """
        if not self._client or not text.strip():
            return

        voice = self._voice_for(agent)
        sentences = _split_sentences(text)

        for sentence in sentences:
            if cancel_event.is_set():
                return
            async for chunk in self._synthesize_sentence(sentence, voice, cancel_event):
                if cancel_event.is_set():
                    return
                yield chunk

    async def _synthesize_sentence(
        self,
        text: str,
        voice: str,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[bytes]:
        """Synthesize one sentence and stream its bytes."""
        try:
            async with self._client.audio.speech.with_streaming_response.create(
                model=self.DEFAULT_MODEL,
                voice=voice,
                input=text[:4096],
                response_format="mp3",
            ) as response:
                async for chunk in response.iter_bytes(chunk_size=self.CHUNK_SIZE):
                    if cancel_event.is_set():
                        return
                    yield chunk
        except asyncio.CancelledError:
            raise
        except Exception:
            return
