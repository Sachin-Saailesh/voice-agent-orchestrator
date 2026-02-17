# Bob ↔ Alice Voice Assistant: Design Overview

## Architecture

This system uses a modular, state-driven architecture to manage multi-turn conversations and seamless agent handoffs.

```mermaid
graph TD
    User([User Voice]) --> STT[Whisper STT]
    STT --> Router{Transfer Router}
    
    Router -->|Transfer Detected| Handoff[Transfer Logic]
    Router -->|No Transfer| Agent[Agent Manager]
    
    Handoff -->|Generating Message| TTS_From[TTS (From Agent)]
    Handoff -->|Switching Context| State[Conversation State]
    
    Agent -->|Query| LLM[GPT-4o-mini]
    State -->|Context Injection| LLM
    
    LLM --> Response[Text Response]
    Response --> TTS_To[TTS (To Agent)]
    TTS_From --> AudioOut([Audio Output])
    TTS_To --> AudioOut
```

## Core Components

### 1. **Transfer Router** (`src/router.py`)
- **Responsibility**: Detects user intent to switch agents *before* sending the request to the LLM.
- **Why Rule-Based?**: Regex patterns (e.g., `transfer.*alice`, `switch.*bob`) provide near-zero latency and 100% reliability for explicit commands, avoiding the overhead and potential ambiguity of an LLM classification step for critical control signals.

### 2. **Agent Manager** (`src/agents.py`)
- **Responsibility**: Manages agent personas (System Prompts) and executes transfers.
- **Transfer Logic**:
    1.  **Detection**: Router flags a transfer.
    2.  **Announcement**: The system uses the *current* agent's voice to say "Transferring you now..." (e.g., Bob says "Bringing Alice in").
    3.  **Context Injection**: The *new* agent receives the full conversation history + a structured summary + a specific "Handoff Note".
    4.  **Continuation**: The new agent is instructed via system prompt: *DO NOT introduce yourself again. Continue immediately.*

### 3. **Conversation State** (`src/state.py`)
- **Responsibility**: Maintains context across the session.
- **Structure**:
    - `structured_state`: JSON object tracking project details (Room, Budget, Goals).
    - `transcript_tail`: Recent message history for immediate context.
    - `summary`: Rolling summary of the entire conversation.
    - `agent_seen`: Tracks which agents have already introduced themselves to prevent repetitive greetings.

### 4. **Voice Pipeline** (`src/main.py`)
- **STT**: OpenAI Whisper (reliable transcription).
- **TTS**: OpenAI TTS (`tts-1`) with dynamic voice selection (`alloy` vs `nova`).
- **Latency**: Minimized by using `gpt-4o-mini` and efficient state updates.

## Key Design Decisions

### **Seamless Transfer UX**
Instead of a jarring switch, the transferring agent announces the handoff. This provides audio continuity. We also suppress re-introductions (e.g., "Hi I'm Alice") on transfer to keep the conversation natural.

### **Structured State**
We maintain a JSON state of the project (e.g., `{"budget": "$25k", "room": "Kitchen"}`). This ensures that when switching from Bob to Alice, Alice immediately knows the constraints without asking the user to repeat themselves.

### **Safety & Scope**
Both agents have strict system instructions to avoid providing professional legal or engineering advice, deferring to licensed professionals for code/structural issues.

## Future Improvements for Production

1.  **Streaming Audio**: Implement WebSocket-based streaming for STT and TTS to reduce latency to <500ms.
2.  **Barge-In**: Allow users to interrupt the agent using Voice Activity Detection (VAD).
3.  **Local Wake Word**: Use a local model (e.g., Porcupine) to trigger listening without manual input.
4.  **Guardrails**: Implement specific guardrail models to filter harmful content before TTS generation.
