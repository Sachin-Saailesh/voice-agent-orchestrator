# Bob ↔ Alice Voice Assistant

A real-time voice agent with seamless agent transfer, barge-in support, and audio streaming. Bob handles intake & planning; Alice handles technical specialist questions.

## Status & Execution Modes

> **Mode 1: Python Terminal (V1)**
> Runs flawlessly in the terminal with continuous push-to-talk Voice Activity Detection.
> **Mode 2: Browser WebSocket/WebRTC (V2)**
> The browser UI implementation is still a work in progress. It runs, but currently experiences:
>
> - 🔶 **Audio latency** — voice response playback is slightly laggy over the WebSocket stream
> - 🔶 **STT accuracy** — speech-to-text transcription is less accurate compared to the terminal version
>   Both issues are actively being worked on.

## How to Run

### 1. Setup Environment

```bash
# Set up Python virtual environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create your .env file
cp src/.env.example src/.env
```

**CRITICAL: Open `src/.env` and add your OpenAI API Key at the top:**

```env
OPENAI_API_KEY=sk-your-actual-api-key-here
```

### 2. Run Terminal Mode (V1) - _Recommended_

Plays directly through your computer's microphone and speakers.

```bash
cd src
python3 main.py
```

_(Optionally run `python3 main.py --text` for a text-only interface)._

### 3. Run Browser UI Mode (V2) - _Beta_

Starts a FastAPI web server with a WebRTC/WebSocket frontend.

```bash
uvicorn src.streaming.server:app --reload
# Then open http://localhost:8000 in your browser
```

---

## What's New in Version 2

- **Unified Architecture**: Both the Terminal and Browser clients now share the exact same underlying state management (`state.py`) and agent routing core (`agents.py`).
- **WebRTC Audio**: Introduced `aiortc` for real-time bidirectional browser audio streaming without relying on hacky Web Audio API processor delays.
- **Asynchronous Pipeline**: The V2 UI uses an async STT → Guardrails → LLM → Guardrails → TTS pipeline (`pipeline.py`).
- **Codebase Clean-up**: Flattened directory structure to remove redundant CLI wrappers and deprecated V1 text analysis scripts, enforcing a single source of truth for the AI logic.

## Next Steps

1. **Reduce Browser Latency**: Transition the V2 server from OpenAI's sequential Web APIs to the newer OpenAI Realtime API (WebSocket) to dramatically reduce WebRTC turnaround time.
2. **Improve STT Accuracy**: Fine-tune the browser's PCM audio sample-rate conversion to ensure Whisper receives cleaner frequency bands, improving transcription accuracy.
3. **Persist State**: Add a Redis or SQLite layer so conversation contexts survive server reboots.

---

## Architecture

```
User Audio (Mic/WebRTC) → Input Handler
                              ├── vad.py         — side-car Voice Activity Detection
                              ├── pipeline.py    — STT → guardrails → LLM stream → TTS stream
                              ├── session.py     — per-session state, queues, cancellation
                              ├── agents.py      — Bob & Alice system prompts + context building
                              ├── state.py       — structured project state + transcript
                              ├── router.py      — transfer intent detection
                              └── guardrails.py  — content safety (blocklist + OpenAI moderation)
```

## Requirements

- Python 3.11+
- OpenAI API key (GPT-4o-mini, Whisper, TTS)

## Quick Start

```bash
# 1. Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r voice-agent-orchestrator/requirements.txt

# 3. Set environment variables
cp voice-agent-orchestrator/.env.example voice-agent-orchestrator/.env
# Edit .env and add: OPENAI_API_KEY=sk-...

# 4. Run the server
cd voice-agent-orchestrator
uvicorn src.streaming.server:app --host 0.0.0.0 --port 8000

# 5. Open browser at http://localhost:8000
```

## Environment Variables

| Variable               | Default       | Description                            |
| ---------------------- | ------------- | -------------------------------------- |
| `OPENAI_API_KEY`       | —             | **Required.** Your OpenAI API key      |
| `LLM_MODEL`            | `gpt-4o-mini` | LLM model to use                       |
| `TTS_VOICE_BOB`        | `alloy`       | TTS voice for Bob                      |
| `TTS_VOICE_ALICE`      | `shimmer`     | TTS voice for Alice                    |
| `TTS_MODEL`            | `tts-1`       | TTS model                              |
| `VAD_SPEECH_THRESHOLD` | `0.015`       | Voice activity detection sensitivity   |
| `VAD_SILENCE_MS`       | `500`         | Silence duration (ms) to end utterance |
| `GUARDRAIL_ENABLED`    | `true`        | Enable content safety checks           |

## Agent Transfer

Say these phrases to switch agents:

- **→ Alice**: `"Transfer me to Alice"`, `"Let me talk to Alice"`, `"Bring Alice in"`
- **→ Bob**: `"Go back to Bob"`, `"Let me talk to Bob"`, `"Switch to Bob"`

## Key Features

- **WebRTC audio** — low-latency bidirectional mic/speaker via `aiortc`
- **Server-side VAD** — automatic end-of-utterance detection (no push-to-talk)
- **Barge-in** — user can interrupt mid-sentence; response context is checkpointed
- **Streaming LLM + TTS** — tokens and audio stream in parallel for minimum latency
- **Guardrails** — blocklist + OpenAI Moderation API on input and output
- **Inactivity detection** — agent prompts after 30 seconds of silence
