"""
Speech-to-Text (STT) Client
Wrapper around OpenAI Whisper API
"""

import os

import io
import openai
from typing import Optional


class STTClient:
    """Speech-to-Text client using OpenAI Whisper"""
    
    def __init__(self):
        self.client = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            try:
                self.client = openai.OpenAI(api_key=api_key)
                print("[STT] Client initialized (Whisper)")
            except Exception as e:
                print(f"[STT] Failed to initialize OpenAI: {e}")
        else:
           print("[STT] Warning: OPENAI_API_KEY not found. STT disabled.")
            
    def transcribe(self, audio_bytes: bytes, language: str = "en") -> Optional[str]:
        """
        Transcribe audio bytes to text
        """
        if not self.client:
            return None

        try:
            # Create a file-like object from bytes
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "audio.wav"
            
            # Call Whisper API
            response = self.client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                language=language,
                response_format="text"
            )
            
            return response.strip() if response else None
                
        except Exception as e:
            # print(f"[STT] Error: {e}") 
            return None



