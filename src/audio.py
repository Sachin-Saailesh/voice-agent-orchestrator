"""
Audio Manager
Handles microphone recording with VAD and speaker playback.
"""

import io
import time
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
from typing import Optional

SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "int16"

# RMS threshold to detect speech (tune if needed)
_SPEECH_THRESHOLD = 0.02
_SILENCE_TIMEOUT = 1.5   # seconds of silence before ending recording
_MAX_DURATION = 30.0     # max recording length in seconds


class AudioManager:
    """Handles microphone recording with VAD and audio playback."""

    def record_with_vad(self, pre_roll: Optional[bytes] = None) -> Optional[bytes]:
        """
        Record from the microphone until the user stops speaking.
        Returns raw 16-bit PCM bytes, or None on failure.
        """
        print("🎙  Listening...", flush=True)

        frames: list[bytes] = []
        if pre_roll:
            frames.append(pre_roll)

        silence_start: Optional[float] = None
        speech_detected = False
        start_time = time.time()

        def callback(indata, _frames, _time, _status):
            nonlocal silence_start, speech_detected
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / 32768.0)

            if rms > _SPEECH_THRESHOLD:
                speech_detected = True
                silence_start = None
                frames.append(bytes(indata))
            elif speech_detected:
                frames.append(bytes(indata))
                if silence_start is None:
                    silence_start = time.time()

        stop_event = threading.Event()

        def _run():
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=1600,
                callback=callback,
            ):
                while not stop_event.is_set():
                    time.sleep(0.05)

        t = threading.Thread(target=_run, daemon=True)
        t.start()

        try:
            while True:
                time.sleep(0.05)
                elapsed = time.time() - start_time
                if speech_detected and silence_start and (time.time() - silence_start) >= _SILENCE_TIMEOUT:
                    break
                if elapsed >= _MAX_DURATION:
                    break
        finally:
            stop_event.set()
            t.join(timeout=1.0)

        if not frames or not speech_detected:
            return None

        return b"".join(frames)

    def play(self, audio_bytes: bytes, interrupt_event: Optional[threading.Event] = None):
        """Play audio bytes (MP3 or WAV). Stops early if interrupt_event is set."""
        try:
            buf = io.BytesIO(audio_bytes)
            data, rate = sf.read(buf, dtype="float32")
            blocksize = int(rate * 0.05)  # 50ms blocks

            idx = 0
            with sd.OutputStream(samplerate=rate, channels=data.ndim if data.ndim > 1 else 1, dtype="float32") as stream:
                while idx < len(data):
                    if interrupt_event and interrupt_event.is_set():
                        break
                    chunk = data[idx: idx + blocksize]
                    stream.write(chunk)
                    idx += blocksize
        except Exception as e:
            print(f"[WARN] Audio playback error: {e}")
