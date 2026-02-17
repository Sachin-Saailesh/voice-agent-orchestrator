"""
Text-to-Speech (TTS) Client
Wrapper around OpenAI TTS API
"""

import os
import openai
from typing import Optional


class TTSClient:
    """Text-to-Speech client using OpenAI TTS"""
    
    def __init__(self, voice: str = "alloy", model: str = "tts-1"):
        self.client = None
        self.voice = voice
        self.model = model
        
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            try:
                self.client = openai.OpenAI(api_key=api_key)
                print(f"[TTS] Client initialized (Voice: {self.voice})")
            except Exception as e:
                print(f"[TTS] Failed to initialize OpenAI: {e}")
        else:
            print("[TTS] Warning: OPENAI_API_KEY not found. TTS disabled.")
            
    def synthesize(self, text: str, agent_name: str = "bob") -> Optional[bytes]:
        """
        Synthesize text to speech
        """
        if not self.client:
            return None

        if not text or not text.strip():
            return None
        
        # Determine voice based on agent
        voice = self.voice # Default
        if agent_name.lower() == "alice":
            voice = os.getenv("TTS_VOICE_ALICE", "shimmer")
        else:
            voice = os.getenv("TTS_VOICE_BOB", "alloy")
            
        print(f"[TTS] Synthesizing for {agent_name}: {voice}")
            
        try:
            response = self.client.audio.speech.create(
                model=self.model,
                voice=voice,
                input=text[:4096]
            )
            
            return response.read()
                
        except Exception as e:
            print(f"[TTS] Error: {e}")
            return None



