"""
Streaming STT Client
Wraps OpenAI Whisper for async transcription.
Feeds collected audio buffer → returns final transcript.

IMPORTANT: The browser's AudioWorklet sends raw 16-bit PCM (little-endian)
at 16kHz, mono. Whisper requires a proper audio container (WAV/MP3/etc.).
We wrap the raw PCM in a WAV header before sending — this is the fix for
the 400 Bad Request errors from the Whisper API.
"""

import asyncio
import io
import logging
import os
import time
import wave
from typing import Optional

import openai

log = logging.getLogger(__name__)

# Matches AudioContext options in app.js and pcm-processor.js
_SAMPLE_RATE  = int(os.getenv("STT_SAMPLE_RATE", "16000"))
_CHANNELS     = 1
_SAMPLE_WIDTH = 2   # 16-bit = 2 bytes


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:
    """
    Wrap raw 16-bit PCM bytes in a WAV container.
    pcm_bytes  : little-endian signed int16, mono, 16kHz (matches AudioWorklet output)
    Returns    : bytes of a valid .wav file readable by Whisper
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(_CHANNELS)
        wf.setsampwidth(_SAMPLE_WIDTH)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _is_silence(pcm_bytes: bytes, threshold: float = 0.002) -> bool:
    """
    Quick RMS check — skip Whisper call if audio is near-silent.
    Avoids wasting API quota on empty recordings.
    """
    if len(pcm_bytes) < 2:
        return True
    import struct
    n = len(pcm_bytes) // 2
    samples = struct.unpack_from(f"<{n}h", pcm_bytes, 0)
    rms = (sum(s * s for s in samples) / n) ** 0.5 / 32768.0
    return rms < threshold


class StreamingSTTClient:
    """Async Whisper STT wrapper."""

    def __init__(self):
        self._client: Optional[openai.AsyncOpenAI] = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self._client = openai.AsyncOpenAI(api_key=api_key)
        else:
            log.warning("OPENAI_API_KEY not set — STT disabled")

    async def transcribe(
        self,
        audio_bytes: bytes,
        language: str = "en",
    ) -> Optional[str]:
        """
        Transcribe a full audio buffer.
        audio_bytes should be raw 16-bit PCM (from AudioWorklet → server).
        Returns final text or None on error.
        """
        if not self._client or not audio_bytes:
            return None

        if _is_silence(audio_bytes):
            log.debug("Skipping STT — audio is silent")
            return None

        try:
            # Wrap raw PCM in WAV container so Whisper accepts it
            wav_bytes = _pcm_to_wav(audio_bytes)
            buf = io.BytesIO(wav_bytes)
            buf.name = "audio.wav"   # ← must have a recognised extension

            if os.getenv("DEBUG_STT_WAV"):
                # Save up to 5 seconds of audio for debugging exactly what ASR receives
                debug_pcm = audio_bytes[:16000 * 2 * 5]
                debug_wav = _pcm_to_wav(debug_pcm)
                with open("/tmp/debug_stt_5s.wav", "wb") as f:
                    f.write(debug_wav)

            t0 = time.time()
            resp = await asyncio.wait_for(
                self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=buf,
                    language=language,
                    response_format="text",
                ),
                timeout=15.0,
            )
            elapsed = int((time.time() - t0) * 1000)
            text = resp.strip() if isinstance(resp, str) else ""
            log.info("STT transcribed in %dms: %r", elapsed, text[:80])
            if not text:
                with open("/tmp/debug_stt_error.log", "w") as f:
                    f.write("Empty text returned from whisper\n")
            return text or None

        except asyncio.TimeoutError:
            log.warning("STT timed out after 15s")
            with open("/tmp/debug_stt_error.log", "w") as f:
                    f.write("Timeout error\n")
            return None
        except Exception as e:
            log.error("STT error: %s", e)
            with open("/tmp/debug_stt_error.log", "w") as f:
                f.write(f"Exception: {e}\n")
            return None

    async def transcribe_with_words(
        self,
        audio_bytes: bytes,
        language: str = "en",
    ) -> tuple[Optional[str], list[dict]]:
        """
        Transcribe with word-level timestamps.
        Returns (full_text, [{"word": ..., "start": ..., "end": ...}])
        """
        if not self._client or not audio_bytes:
            return None, []

        if _is_silence(audio_bytes):
            return None, []

        try:
            wav_bytes = _pcm_to_wav(audio_bytes)
            buf = io.BytesIO(wav_bytes)
            buf.name = "audio.wav"

            resp = await asyncio.wait_for(
                self._client.audio.transcriptions.create(
                    model="whisper-1",
                    file=buf,
                    language=language,
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                ),
                timeout=15.0,
            )

            text = resp.text.strip() if resp.text else ""
            words = [
                {"word": w.word, "start": w.start, "end": w.end}
                for w in (resp.words or [])
            ]
            return text or None, words

        except asyncio.TimeoutError:
            return None, []
        except Exception as e:
            log.error("STT (with words) error: %s", e)
            return None, []
