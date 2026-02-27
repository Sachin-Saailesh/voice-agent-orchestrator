"""
Voice Activity Detection (VAD)
Energy-based VAD for:
  1. End-of-utterance detection (triggers STT)
  2. Barge-in detection (user speaks while agent is talking)
"""

import struct
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math


class VADState(Enum):
    SILENCE = "silence"
    SPEECH = "speech"
    END_OF_UTTERANCE = "end_of_utterance"


@dataclass
class VADResult:
    state: VADState
    rms: float = 0.0
    speech_duration_ms: float = 0.0
    silence_duration_ms: float = 0.0
    in_utterance: bool = False


class VADProcessor:
    """
    Energy-based Voice Activity Detection.
    
    Configuration:
      - SPEECH_THRESHOLD: RMS value above which audio is considered speech
      - SILENCE_THRESHOLD_MS: Silence duration to declare end-of-utterance
      - MIN_SPEECH_MS: Minimum speech duration to avoid false triggers
      - BARGE_IN_MS: Speech duration to declare barge-in while TTS playing
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        speech_threshold: float = 0.01,        # RMS level for speech detection
        silence_threshold_ms: float = 10000.0, # ms of silence → end of utterance
        min_speech_ms: float = 150.0,          # ms of speech → confirmed speech
        barge_in_ms: float = 200.0,            # ms of speech → barge-in signal
        chunk_size: int = 512,                 # samples per chunk (32ms at 16kHz)
    ):
        self.sample_rate = sample_rate
        self.speech_threshold = speech_threshold
        self.silence_threshold_ms = silence_threshold_ms
        self.min_speech_ms = min_speech_ms
        self.barge_in_ms = barge_in_ms
        self.chunk_size = chunk_size

        # State tracking
        self._in_speech = False
        self._speech_start: Optional[float] = None
        self._silence_start: Optional[float] = None
        self._speech_duration_ms: float = 0.0
        self._silence_duration_ms: float = 0.0

    def _rms(self, pcm_bytes: bytes) -> float:
        """Compute RMS of signed 16-bit PCM samples."""
        if len(pcm_bytes) < 2:
            return 0.0
        # unpack as signed 16-bit little-endian
        n = len(pcm_bytes) // 2
        samples = struct.unpack_from(f"<{n}h", pcm_bytes, 0)
        if n == 0:
            return 0.0
        mean_sq = sum(s * s for s in samples) / n
        return math.sqrt(mean_sq) / 32768.0  # normalize to [0, 1]

    def process_chunk(self, pcm_bytes: bytes) -> VADResult:
        """
        Process one PCM audio chunk.
        Returns VADResult indicating current state.
        """
        rms = self._rms(pcm_bytes)
        now = time.time()
        is_speech = rms >= self.speech_threshold

        if is_speech:
            if not self._in_speech:
                self._in_speech = True
                self._speech_start = now
                self._silence_start = None
                self._silence_duration_ms = 0.0

            # Accumulate speech time
            if self._speech_start is not None:
                self._speech_duration_ms = (now - self._speech_start) * 1000.0

            return VADResult(
                state=VADState.SPEECH,
                rms=rms,
                speech_duration_ms=self._speech_duration_ms,
                in_utterance=self._in_speech,
            )
        else:
            # Silence
            if self._in_speech:
                if self._silence_start is None:
                    self._silence_start = now

                if self._silence_start is not None:
                    self._silence_duration_ms = (now - self._silence_start) * 1000.0

                if self._silence_duration_ms >= self.silence_threshold_ms:
                    # End of utterance — only if we had enough speech
                    if self._speech_duration_ms >= self.min_speech_ms:
                        self._reset()
                        return VADResult(
                            state=VADState.END_OF_UTTERANCE,
                            rms=rms,
                            speech_duration_ms=self._speech_duration_ms,
                            silence_duration_ms=self._silence_duration_ms,
                            in_utterance=False,
                        )
                    else:
                        # Too short — was probably noise
                        self._reset()

            return VADResult(
                state=VADState.SILENCE,
                rms=rms,
                silence_duration_ms=self._silence_duration_ms,
                in_utterance=self._in_speech,
            )

    def is_barge_in(self, rms: float) -> bool:
        """
        Check if current RMS constitutes a barge-in.
        True when: speech detected while agent is supposed to be speaking.
        """
        return rms >= self.speech_threshold

    def _reset(self):
        self._in_speech = False
        self._speech_start = None
        self._silence_start = None
        self._speech_duration_ms = 0.0
        self._silence_duration_ms = 0.0

    def reset(self):
        """Public reset — call at start of each new turn."""
        self._reset()
