#!/usr/bin/env python3
"""
Bob ↔ Alice Voice Assistant — Terminal CLI (V1)
Continuous-listening mode: VAD detects end of speech automatically.
Barge-in stops playback and checkpoints the partial response for context continuity.

Usage:
    python main.py           # voice mode
    python main.py --text    # text mode (no microphone needed)
"""

import os
import re
import sys
import threading
import time
import queue
import argparse
from typing import Optional

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

from audio import AudioManager, _SPEECH_THRESHOLD
from stt import STTClient
from tts import TTSClient
from llm import LLMClient
from agents import AgentManager
from router import TransferRouter
from state import ConversationState


class VoiceAssistant:
    """Terminal voice assistant — continuous listening with barge-in support."""

    def __init__(self, text_mode: bool = False):
        self.text_mode = text_mode
        self.audio = AudioManager() if not text_mode else None
        self.stt = STTClient()
        self.tts = TTSClient()
        self.llm = LLMClient(
            default_model=os.getenv("LLM_MODEL", "gpt-4o-mini"),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
        )
        self.agents = AgentManager()
        self.router = TransferRouter()
        self.state = ConversationState()

        # Barge-in state
        self._barge_in = threading.Event()
        self._partial: str = ""
        self._barge_in_preroll: Optional[bytes] = None
        self._pending_stop: Optional[threading.Event] = None
        self._pending_listener: Optional[threading.Thread] = None

        self.failures = 0
        self.max_failures = 3

    def log(self, msg: str, level: str = "INFO"):
        print(f"[{time.strftime('%H:%M:%S')}] [{level}] {msg}")

    # ── Input ─────────────────────────────────────────────────────────────────

    def get_input(self) -> Optional[str]:
        if self.text_mode:
            try:
                return input("\n👤 You: ").strip() or None
            except (EOFError, KeyboardInterrupt):
                return None

        # Stop any lingering barge-in listener
        if self._pending_stop:
            self._pending_stop.set()
        if self._pending_listener:
            self._pending_listener.join(timeout=0.8)
        self._pending_stop = None
        self._pending_listener = None
        self._barge_in.clear()

        pre_roll = self._barge_in_preroll
        self._barge_in_preroll = None

        audio_data = self.audio.record_with_vad(pre_roll=pre_roll)
        if audio_data is None:
            return None

        t0 = time.time()
        transcript = self.stt.transcribe(audio_data)
        ms = int((time.time() - t0) * 1000)
        if transcript:
            self.log(f"📝 ({ms}ms): {transcript}")
            return transcript
        self.log("Could not transcribe audio", "WARN")
        return None

    # ── Speech output with barge-in ───────────────────────────────────────────

    def speak(self, text: str, agent: str) -> tuple[bool, str]:
        """
        Speak text sentence-by-sentence with pipelined TTS and real-time barge-in.
        Returns (interrupted, unspoken_remainder).
        """
        if not text.strip() or self.text_mode:
            return False, ""

        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
        if not sentences:
            return False, ""

        # Pre-synthesize next sentence while current plays
        synth_q: queue.Queue = queue.Queue(maxsize=2)

        def _synthesize():
            for i, s in enumerate(sentences):
                if self._barge_in.is_set():
                    break
                try:
                    audio = self.tts.synthesize(s, agent)
                except Exception as e:
                    self.log(f"TTS error: {e}", "ERROR")
                    audio = None
                synth_q.put((i, s, audio))
            synth_q.put(None)

        # Background mic listener for barge-in
        _stop_listener = threading.Event()

        def _listen_for_barge_in():
            post_chunks: list[bytes] = []

            def _cb(indata, _frames, _time, _status):
                if not self._barge_in.is_set():
                    rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / 32768.0)
                    if rms > _SPEECH_THRESHOLD:
                        self.log("✋ Barge-in detected")
                        self._barge_in.set()
                        post_chunks.append(bytes(indata))
                else:
                    post_chunks.append(bytes(indata))

            try:
                with sd.InputStream(samplerate=16000, channels=1, dtype="int16",
                                    blocksize=1600, callback=_cb):
                    while not _stop_listener.is_set():
                        time.sleep(0.01)
            except Exception:
                pass

            if post_chunks:
                self._barge_in_preroll = b"".join(post_chunks)

        self._barge_in.clear()
        synth_thread = threading.Thread(target=_synthesize, daemon=True)
        listener_thread = threading.Thread(target=_listen_for_barge_in, daemon=True)
        synth_thread.start()
        listener_thread.start()

        spoken: list[str] = []
        interrupted_at = len(sentences)

        try:
            while True:
                item = synth_q.get()
                if item is None:
                    break
                i, sentence, audio = item
                if self._barge_in.is_set():
                    interrupted_at = i
                    break
                if audio:
                    self.log(f"🔊 {agent.upper()} [{i+1}/{len(sentences)}]")
                    self.audio.play(audio, interrupt_event=self._barge_in)
                if self._barge_in.is_set():
                    interrupted_at = i + 1
                    break
                spoken.append(sentence)
        finally:
            if self._barge_in.is_set():
                self._pending_stop = _stop_listener
                self._pending_listener = listener_thread
            else:
                _stop_listener.set()
                listener_thread.join(timeout=0.5)

        if self._barge_in.is_set():
            return True, " ".join(sentences[interrupted_at:])
        return False, ""

    # ── Turn processing ───────────────────────────────────────────────────────

    def process_turn(self, user_text: str) -> bool:
        try:
            if user_text.lower() in ("exit", "quit", "bye", "goodbye"):
                return False

            had_partial = bool(self._partial)
            if had_partial:
                self.state.add_turn(
                    speaker=self.agents.current_agent,
                    text=f"[INTERRUPTED — was saying: {self._partial}]",
                )
                self.state.add_turn(speaker="user", text=f"[Interrupted, continuing: {user_text}]")
                self._partial = ""
            else:
                self.state.add_turn(speaker="user", text=user_text)

            # Transfer detection
            transfer = self.router.detect_transfer(user_text)
            if transfer:
                target = transfer["target_agent"]
                from_agent = self.agents.current_agent
                handoff = self.agents.transfer_to(target, self.state)
                self.log(f"🔄 Transfer → {target.upper()}")
                interrupted, unspoken = self.speak(handoff, from_agent)
                if interrupted:
                    self._partial = unspoken
                self.state.add_turn(speaker="system", text=f"[Transferred to {target}]")

            # LLM response
            messages = self.agents._build_messages(user_text, self.state, is_transfer=bool(transfer))
            t0 = time.time()
            response = self.llm.chat(messages, max_tokens=400, temperature=0.7)
            ms = int((time.time() - t0) * 1000)

            if not response:
                response = "I'm having trouble processing that right now. Could you try again?"

            self.log(f"🤖 {self.agents.current_agent.upper()} ({ms}ms): {response}")
            interrupted, unspoken = self.speak(response, self.agents.current_agent)

            if interrupted:
                self._partial = unspoken
                spoken = response.replace(unspoken, "").strip()
                if spoken:
                    self.state.add_turn(speaker=self.agents.current_agent, text=spoken)
            else:
                self.state.add_turn(speaker=self.agents.current_agent, text=response)

            self.failures = 0
            return True

        except Exception as e:
            self.log(f"Error: {e}", "ERROR")
            self.failures += 1
            if self.failures >= self.max_failures:
                self.speak("I'm having technical difficulties. Goodbye.", self.agents.current_agent)
                return False
            self.speak("Sorry, I hit an error. Try again?", self.agents.current_agent)
            return True

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        print("=" * 60)
        print("🏠  Bob ↔ Alice Voice Assistant for Home Renovation")
        print("=" * 60)
        print(f"Mode: {'TEXT' if self.text_mode else 'VOICE (continuous listening)'}")
        print("Say 'exit' or 'quit' to end the session.\n")

        greeting = (
            "Hi! I'm Bob, your renovation planning assistant. "
            "I'm here to help you think through your project. "
            "What room are you looking to renovate?"
        )
        self.log(f"🤖 BOB: {greeting}")
        self.speak(greeting, "bob")

        while True:
            try:
                user_input = self.get_input()
                if user_input is None:
                    continue
                if not self.process_turn(user_input):
                    break
            except KeyboardInterrupt:
                self.log("Session ended by user.")
                break

        print("\n" + "=" * 60)
        print("👋 Thanks for using Bob & Alice! Good luck with your renovation!")
        print("=" * 60)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Bob ↔ Alice Voice Assistant")
    parser.add_argument("--text", action="store_true", help="Text-only mode (no mic)")
    args = parser.parse_args()
    VoiceAssistant(text_mode=args.text).run()


if __name__ == "__main__":
    main()
