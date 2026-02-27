"""
TTS Client
Wraps OpenAI TTS for text-to-speech synthesis.
"""

import io
import os
import logging
from typing import Optional

import openai

log = logging.getLogger(__name__)

_VOICE_MAP = {
    "bob": os.getenv("TTS_VOICE_BOB", "alloy"),
    "alice": os.getenv("TTS_VOICE_ALICE", "shimmer"),
}
_TTS_MODEL = os.getenv("TTS_MODEL", "tts-1")


class TTSClient:
    """Synchronous OpenAI TTS wrapper."""

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        self._client = openai.OpenAI(api_key=api_key) if api_key else None
        if not self._client:
            log.warning("OPENAI_API_KEY not set — TTS disabled")

    def synthesize(self, text: str, agent: str = "bob") -> Optional[bytes]:
        """
        Synthesize text to speech.
        Returns MP3 audio bytes or None on failure.
        """
        if not self._client or not text.strip():
            return None
        try:
            voice = _VOICE_MAP.get(agent.lower(), "alloy")
            response = self._client.audio.speech.create(
                model=_TTS_MODEL,
                voice=voice,
                input=text[:4096],
                response_format="mp3",
            )
            return response.content
        except Exception as e:
            log.error("TTS error: %s", e)
            return None
