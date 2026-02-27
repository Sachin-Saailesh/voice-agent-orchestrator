"""
STT Client
Wraps OpenAI Whisper for speech-to-text transcription.
"""

import io
import os
import wave
import logging
from typing import Optional

import openai

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """Wrap raw 16-bit PCM in a WAV container for Whisper."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


class STTClient:
    """Synchronous Whisper STT wrapper."""

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        self._client = openai.OpenAI(api_key=api_key) if api_key else None
        if not self._client:
            log.warning("OPENAI_API_KEY not set — STT disabled")

    def transcribe(self, audio_bytes: bytes, language: str = "en") -> Optional[str]:
        """
        Transcribe raw 16-bit PCM audio bytes.
        Returns transcript text or None on failure.
        """
        if not self._client or not audio_bytes:
            return None
        try:
            wav_bytes = _pcm_to_wav(audio_bytes)
            buf = io.BytesIO(wav_bytes)
            buf.name = "audio.wav"
            resp = self._client.audio.transcriptions.create(
                model="whisper-1",
                file=buf,
                language=language,
                response_format="text",
            )
            text = resp.strip() if isinstance(resp, str) else ""
            return text or None
        except Exception as e:
            log.error("STT error: %s", e)
            return None
