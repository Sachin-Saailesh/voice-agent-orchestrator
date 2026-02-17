"""
Audio I/O Module
Handles microphone recording and audio playback
"""

import io
import wave
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
from typing import Optional


class AudioManager:
    """Manages audio recording and playback"""
    
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        dtype: str = 'int16'
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.dtype = dtype
        self.recording = []
        
        # Check available devices
        try:
            devices = sd.query_devices()
            print(f"[Audio] Found {len(devices)} audio devices")
        except Exception as e:
            print(f"[Audio] Warning: Could not query audio devices: {e}")
    
    def record(self, max_duration: int = 30) -> Optional[bytes]:
        """
        Record audio from microphone until user stops (Ctrl+C) or max duration
        Returns WAV bytes or None on failure
        """
        try:
            print(f"[Audio] Recording started (max {max_duration}s)...")
            self.recording = []
            
            # Stream callback
            def callback(indata, frames, time_info, status):
                if status:
                    print(f"[Audio] Status: {status}")
                self.recording.append(indata.copy())
            
            # Record with stream
            with sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype=self.dtype,
                callback=callback
            ):
                # Wait for user interrupt or timeout
                start_time = time.time()
                try:
                    while time.time() - start_time < max_duration:
                        sd.sleep(100)
                except KeyboardInterrupt:
                    pass
            
            if not self.recording:
                print("[Audio] No audio data recorded")
                return None
            
            # Concatenate all chunks
            audio_data = np.concatenate(self.recording, axis=0)
            
            # Convert to WAV bytes
            wav_bytes = self._to_wav_bytes(audio_data)
            
            duration = len(audio_data) / self.sample_rate
            print(f"[Audio] Recorded {duration:.1f}s of audio")
            
            return wav_bytes
            
        except Exception as e:
            print(f"[Audio] Recording error: {e}")
            return None
    
    def _to_wav_bytes(self, audio_data: np.ndarray) -> bytes:
        """Convert numpy array to WAV bytes"""
        try:
            # Create in-memory WAV file
            wav_buffer = io.BytesIO()
            
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(2)  # 16-bit
                wav_file.setframerate(self.sample_rate)
                wav_file.writeframes(audio_data.tobytes())
            
            wav_buffer.seek(0)
            return wav_buffer.read()
            
        except Exception as e:
            print(f"[Audio] WAV conversion error: {e}")
            return b""
    
    def play(self, audio_data: bytes) -> bool:
        """
        Play audio from bytes (supports WAV or raw PCM)
        Returns True on success
        """
        try:
            # Try to parse as WAV first
            try:
                wav_buffer = io.BytesIO(audio_data)
                data, sample_rate = sf.read(wav_buffer)
            except:
                # If not WAV, try as raw audio
                print("[Audio] Not WAV format, trying raw playback...")
                # Assume bytes are int16 PCM
                data = np.frombuffer(audio_data, dtype=np.int16)
                sample_rate = self.sample_rate
            
            # Play audio
            sd.play(data, sample_rate)
            sd.wait()
            
            return True
            
        except Exception as e:
            print(f"[Audio] Playback error: {e}")
            return False
    

