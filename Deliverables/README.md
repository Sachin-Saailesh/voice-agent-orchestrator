# Bob ↔ Alice Voice Assistant (Run Instructions)

A dual-agent voice assistant for home renovation planning, featuring seamless context-aware transfers between **Bob** (Planner) and **Alice** (Technical Specialist).

##  Quick Setup

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

2.  **Configure Environment**:
    copy `.env.example` to `.env` and add your OpenAI API Key.
    ```bash
    cp .env.example .env
    # Edit .env to add OPENAI_API_KEY
    ```

## How to Run

### **Voice Mode** (Default)
Requires microphone and speakers (uses OpenAI Whisper & TTS).
```bash
python3 src/main.py
```
*   **Speak clearly into your microphone.**
*   **Press Ctrl+C to stop recording.**

### **Text Mode** (Fallback)
If you don't have a microphone or prefer typing:
```bash
python3 src/main.py --text
```

## Demo Phrases

**Planning with Bob:**
*   "I want to remodel my kitchen with a $25k budget."
*   "What are the first steps for a bathroom renovation?"

**Transferring to Alice (Technical):**
*   "Transfer me to Alice."
*   "Let me talk to Alice about permit requirements."
*   "Can you bring Alice in to discuss structural risks?"

**Switching back to Bob:**
*   "Transfer me back to Bob."
*   "Let's go back to planning with Bob."

**Exiting:**
*   "Exit", "Quit", or "Goodbye".

##  Models Used
*   **LLM**: `gpt-4o-mini` (Optimized for low latency)
*   **STT**: `whisper-1`
*   **TTS**: `tts-1` (Voices: `alloy` for Bob, `nova` for Alice)
