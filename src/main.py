#!/usr/bin/env python3
"""
Bob ↔ Alice Voice Assistant - Main Application
Production-grade voice assistant with agent transfer
"""

import os
import sys
import time
import argparse
from typing import Optional

from audio import AudioManager
from stt import STTClient
from tts import TTSClient
from llm import LLMClient
from agents import AgentManager
from router import TransferRouter
from state import ConversationState
from dotenv import load_dotenv


class VoiceAssistant:
    """Main voice assistant orchestrator"""
    
    def __init__(self, text_mode: bool = False):
        self.text_mode = text_mode
        self.audio = AudioManager() if not text_mode else None
        self.stt = STTClient()
        self.tts = TTSClient()

        self.llm = LLMClient(
            default_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7"))
        )
        self.agent_manager = AgentManager()
        self.router = TransferRouter()
        self.state = ConversationState()
        self.circuit_breaker_failures = 0
        self.max_failures = 3
        
    def log(self, message: str, level: str = "INFO"):
        """Enhanced logging with timestamps"""
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] [{level}] {message}")
    
    def get_user_input(self) -> Optional[str]:
        """Get user input via voice or text"""
        if self.text_mode:
            try:
                user_input = input("\n👤 You: ").strip()
                return user_input if user_input else None
            except (EOFError, KeyboardInterrupt):
                return None
        else:
            try:
                self.log("🎤 Recording... (Press Ctrl+C when done)")
                audio_data = self.audio.record()
                
                if audio_data is None:
                    self.log("No audio recorded", "WARN")
                    return None
                
                # STT with timing
                stt_start = time.time()
                transcript = self.stt.transcribe(audio_data)
                stt_ms = int((time.time() - stt_start) * 1000)
                
                if transcript:
                    self.log(f"📝 Transcript ({stt_ms}ms): {transcript}")
                    return transcript
                else:
                    self.log("Could not transcribe audio", "WARN")
                    return None
                    
            except KeyboardInterrupt:
                self.log("\nRecording stopped by user")
                return None
            except Exception as e:
                self.log(f"Audio input error: {e}", "ERROR")
                return None
    
    def process_turn(self, user_transcript: str) -> bool:
        """
        Process a single conversation turn
        Returns False if should exit
        """
        try:
            # Check for exit commands
            if user_transcript.lower() in ['exit', 'quit', 'bye', 'goodbye']:
                return False
            
            # Check for transfer BEFORE calling LLM
            transfer_detected = self.router.detect_transfer(user_transcript)
            
            if transfer_detected:
                new_agent = transfer_detected['target_agent']
                
                # Capture current agent for handoff voice
                from_agent = self.agent_manager.current_agent
                
                handoff_message = self.agent_manager.transfer_to(
                    new_agent, 
                    self.state
                )
                
                self.log(f"🔄 Transferring to {new_agent.upper()}...")
                
                # Speak the handoff message using the FROM agent's voice
                self.speak_response(handoff_message, agent_name=from_agent)
                
                # Update state with transfer
                self.state.add_turn(
                    speaker="system",
                    text=f"[Transferred to {new_agent}]"
                )
                
                # Now get the new agent's greeting/continuation
                llm_start = time.time()
                agent_response = self.agent_manager.get_response(
                    user_transcript,
                    self.state,
                    self.llm,
                    is_transfer_continuation=True
                )
                llm_ms = int((time.time() - llm_start) * 1000)
                
                self.log(f"🤖 {self.agent_manager.current_agent.upper()} ({llm_ms}ms): {agent_response}")
                
                # Speak the continuation
                self.speak_response(agent_response, agent_name=self.agent_manager.current_agent)
                
                # Update state
                self.state.add_turn(
                    speaker=self.agent_manager.current_agent,
                    text=agent_response
                )
                self.state.update_from_turn(user_transcript, agent_response, self.llm)
                
                # Reset circuit breaker on success
                self.circuit_breaker_failures = 0
                return True
            
            # Normal turn (no transfer)
            llm_start = time.time()
            agent_response = self.agent_manager.get_response(
                user_transcript,
                self.state,
                self.llm
            )
            llm_ms = int((time.time() - llm_start) * 1000)
            
            self.log(f"🤖 {self.agent_manager.current_agent.upper()} ({llm_ms}ms): {agent_response}")
            
            # Speak response
            self.speak_response(agent_response, agent_name=self.agent_manager.current_agent)
            
            # Update state
            self.state.add_turn(speaker="user", text=user_transcript)
            self.state.add_turn(speaker=self.agent_manager.current_agent, text=agent_response)
            # Update state with extracted details (using gpt-5-nano if available)
            self.state.update_from_turn(user_transcript, agent_response, self.llm)
            
            # Reset circuit breaker on success
            self.circuit_breaker_failures = 0
            return True
            
        except Exception as e:
            self.log(f"Turn processing error: {e}", "ERROR")
            self.circuit_breaker_failures += 1
            
            if self.circuit_breaker_failures >= self.max_failures:
                self.log("Circuit breaker triggered - too many failures", "ERROR")
                fallback_msg = "I'm having technical difficulties. Please try again later."
                self.speak_response(fallback_msg)
                return False
            else:
                fallback_msg = "Sorry, I encountered an error. Could you try again?"
                self.speak_response(fallback_msg)
                return True
    
    def speak_response(self, text: str, agent_name: str = "bob"):
        """Speak response via TTS or print in text mode"""
        if self.text_mode:
            return  # Already printed to console
        
        try:
            tts_start = time.time()
            self.log(f"🎤 Requesting TTS for {agent_name}...")
            audio_data = self.tts.synthesize(text, agent_name)
            tts_ms = int((time.time() - tts_start) * 1000)
            
            if audio_data:
                self.log(f"🔊 {agent_name.upper()} Speaking ({tts_ms}ms)...")
                self.audio.play(audio_data)
            else:
                self.log("TTS failed, showing text only", "WARN")
                
        except Exception as e:
            self.log(f"TTS error: {e}", "ERROR")
            self.log(f"Response text: {text}", "INFO")
    
    def run(self):
        """Main conversation loop"""
        print("=" * 70)
        print("🏠 Bob ↔ Alice Voice Assistant for Home Renovation")
        print("=" * 70)
        print(f"\nMode: {'TEXT' if self.text_mode else 'VOICE'}")
        print(f"Active Agent: BOB (Intake & Planning)\n")
        print("Commands:")
        print("  - 'Transfer me to Alice' / 'Let me talk to Alice'")
        print("  - 'Go back to Bob' / 'Let me talk to Bob'")
        print("  - 'exit' / 'quit' to end\n")
        
        if not self.text_mode:
            print("🎤 Push-to-talk: Start speaking, then press Ctrl+C when done\n")
        
        # Initial greeting
        greeting = "Hi! I'm Bob, your renovation planning assistant. I'm here to help you think through your project. What room are you looking to renovate?"
        self.log(f"🤖 BOB: {greeting}")
        self.speak_response(greeting, agent_name="bob")
        
        # Main loop
        while True:
            try:
                # Get user input
                user_input = self.get_user_input()
                
                if user_input is None:
                    continue
                
                # Process turn
                should_continue = self.process_turn(user_input)
                
                if not should_continue:
                    break
                    
            except KeyboardInterrupt:
                self.log("\n\nExiting...", "INFO")
                break
            except Exception as e:
                self.log(f"Unexpected error: {e}", "ERROR")
                break
        
        print("\n" + "=" * 70)
        print("👋 Thanks for using Bob & Alice! Good luck with your renovation!")
        print("=" * 70)


def main():
    """Entry point"""
    load_dotenv()  # 🔥 Load .env from project root

    parser = argparse.ArgumentParser(
        description="Bob ↔ Alice Voice Assistant for Home Renovation"
    )
    parser.add_argument(
        "--text",
        action="store_true",
        help="Use text mode instead of voice (fallback mode)"
    )

    args = parser.parse_args()

    # Initialize and run
    assistant = VoiceAssistant(text_mode=args.text)
    assistant.run()


if __name__ == "__main__":
    main()

