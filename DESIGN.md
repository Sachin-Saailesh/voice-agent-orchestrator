# Design Document: Bob ↔ Alice Voice Assistant

## Overview

This document describes the architecture, design decisions, and tradeoffs for a production-grade voice assistant with seamless agent transfer capabilities.

## System Architecture

### High-Level Flow

```
┌─────────────┐
│    User     │
│  (Voice)    │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────────┐
│                    Main Loop (main.py)                  │
│  ┌──────────────────────────────────────────────────┐  │
│  │  1. Record Audio (audio.py)                      │  │
│  │     - Push-to-talk microphone capture            │  │
│  │     - Convert to WAV bytes                       │  │
│  └──────────────────────────────────────────────────┘  │
│                          │                              │
│                          ▼                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  2. Speech-to-Text (stt.py)                      │  │
│  │     - OpenAI Whisper API                         │  │
│  │     - Returns transcript text                    │  │
│  └──────────────────────────────────────────────────┘  │
│                          │                              │
│                          ▼                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  3. Transfer Detection (router.py)               │  │
│  │     - Check for transfer commands FIRST          │  │
│  │     - Regex pattern matching                     │  │
│  │     - Returns target agent or None               │  │
│  └──────────────────────────────────────────────────┘  │
│                          │                              │
│              ┌───────────┴───────────┐                  │
│              ▼                       ▼                  │
│  ┌────────────────────┐   ┌──────────────────────┐    │
│  │   If Transfer      │   │   If Normal Turn     │    │
│  │   - Generate       │   │   - Get agent        │    │
│  │     handoff note   │   │     response         │    │
│  │   - Switch agent   │   │   - Update state     │    │
│  │   - Get response   │   │                      │    │
│  └────────────────────┘   └──────────────────────┘    │
│                          │                              │
│                          ▼                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  4. LLM Response Generation (llm.py)             │  │
│  │     - OpenAI GPT API                             │  │
│  │     - System prompt (Bob or Alice)               │  │
│  │     - Full context (state + summary + tail)      │  │
│  └──────────────────────────────────────────────────┘  │
│                          │                              │
│                          ▼                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  5. Text-to-Speech (tts.py)                      │  │
│  │     - OpenAI TTS API                             │  │
│  │     - Returns audio bytes                        │  │
│  └──────────────────────────────────────────────────┘  │
│                          │                              │
│                          ▼                              │
│  ┌──────────────────────────────────────────────────┐  │
│  │  6. Play Audio (audio.py)                        │  │
│  │     - Stream to speakers                         │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
                    ┌──────────┐
                    │   User   │
                    │ (Hears)  │
                    └──────────┘

Supporting Components:
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   agents.py      │  │    state.py      │  │   router.py      │
│                  │  │                  │  │                  │
│  Bob & Alice     │  │  Structured      │  │  Transfer        │
│  System Prompts  │  │  State Tracking  │  │  Detection       │
│                  │  │  - Project info  │  │  - Regex         │
│  Handoff Notes   │  │  - Summary       │  │  - Auto-suggest  │
│                  │  │  - Transcript    │  │                  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### Module Responsibilities

#### main.py
- **Purpose**: Orchestration and main conversation loop
- **Key responsibilities**:
  - Initialize all components
  - Run conversation loop
  - Handle user input (voice or text)
  - Coordinate turn processing
  - Implement circuit breaker for API failures
  - Logging with timestamps

#### audio.py
- **Purpose**: Audio I/O abstraction
- **Key responsibilities**:
  - Record microphone input with push-to-talk
  - Convert audio to WAV bytes
  - Play audio through speakers
  - Handle audio device errors gracefully

#### stt.py
- **Purpose**: Speech-to-Text adapter
- **Key responsibilities**:
  - Wrap OpenAI Whisper API
  - Retry logic with exponential backoff
  - Error handling and logging
  - Swappable provider interface

#### tts.py
- **Purpose**: Text-to-Speech adapter
- **Key responsibilities**:
  - Wrap OpenAI TTS API
  - Retry logic with exponential backoff
  - Error handling and logging
  - Swappable provider interface

#### llm.py
- **Purpose**: LLM adapter
- **Key responsibilities**:
  - Wrap OpenAI Chat API
  - Retry logic with exponential backoff
  - Error handling and logging
  - Swappable provider interface

#### agents.py
- **Purpose**: Agent personality and behavior management
- **Key responsibilities**:
  - Define Bob and Alice system prompts
  - Manage agent switching
  - Generate handoff notes
  - Build context for LLM (state + summary + transcript + handoff)
  - Ensure context continuity across transfers

#### router.py
- **Purpose**: Transfer intent detection
- **Key responsibilities**:
  - Detect explicit transfer commands via regex
  - Optional auto-transfer suggestions (bonus feature)
  - Pattern matching before LLM processing

#### state.py
- **Purpose**: Conversation state management
- **Key responsibilities**:
  - Maintain structured project state
  - Generate rolling summary
  - Keep transcript tail (last N turns)
  - Extract information from conversation
  - Provide formatted context for agents

## Transfer Detection Approach

### Critical Design Decision: Pre-LLM Detection

**Why detect transfers BEFORE calling the LLM?**

1. **Deterministic behavior**: Transfer commands must always work, regardless of agent state
2. **Latency reduction**: No LLM call needed for simple routing
3. **Cost efficiency**: Save API costs on transfer-only turns
4. **Reliability**: Regex matching is fail-safe; LLM might refuse or misinterpret

### Implementation Strategy

```python
# Step 1: User input received
transcript = "Transfer me to Alice"

# Step 2: Check for transfer FIRST (router.py)
transfer = router.detect_transfer(transcript)

if transfer:
    # Step 3a: Execute transfer immediately
    agent_manager.transfer_to(transfer['target_agent'])
    
    # Step 4a: Generate handoff message
    handoff_msg = "Bringing Alice in..."
    
    # Step 5a: Get new agent's continuation
    response = agent_manager.get_response(
        transcript, state, llm, 
        is_transfer_continuation=True
    )
else:
    # Step 3b: Normal turn processing
    response = agent_manager.get_response(
        transcript, state, llm
    )
```

### Transfer Patterns

Explicit patterns (high confidence):
- `"transfer.*alice"` → Transfer to Alice
- `"let me talk to alice"` → Transfer to Alice
- `"go back.*bob"` → Transfer to Bob
- `"back to bob"` → Transfer to Bob

Auto-suggest patterns (optional, lower confidence):
- Keywords like "permit", "code", "structural" → Suggest Alice
- Keywords like "checklist", "next steps", "plan" → Suggest Bob

## State & Memory Strategy

### Three-Tier Context System

#### 1. Structured State (state.structured_state)
```json
{
  "project": {
    "room": "kitchen",
    "budget": "$25k",
    "goals": ["new cabinets", "countertops", "open up wall"],
    "constraints": [],
    "timeline": "3 months",
    "diy_or_contractor": "contractor"
  },
  "open_questions": ["Is wall load-bearing?"],
  "risks": ["load-bearing check needed"],
  "decisions": [],
  "materials_discussed": []
}
```

**Purpose**: Machine-readable project state, incrementally updated

#### 2. Rolling Summary (state.summary)
```
Renovating kitchen, budget $25k, wants: new cabinets, countertops, 
open up wall. risks: load-bearing check needed.
```

**Purpose**: Concise human-readable overview for LLM context

#### 3. Transcript Tail (state.transcript_tail)
```
USER: I want to remodel my kitchen. Budget is around $25k.
BOB: Great! Do you know if that wall is load-bearing?
USER: Not sure about the wall. Timeline is 3 months.
BOB: Perfect. Want Alice to dive into the wall situation?
```

**Purpose**: Recent conversational context (last 6 turns / 3 exchanges)

### Context Assembly for LLM

When generating a response, the agent receives:

```
SYSTEM: [Bob or Alice system prompt]

SYSTEM: [Context block containing:]
  - PROJECT STATE: [structured_state as JSON]
  - CONVERSATION SUMMARY: [rolling summary]
  - RECENT CONVERSATION: [transcript_tail]
  - HANDOFF NOTE: [only on transfers]

USER: [current user input]
```

### Handoff Note Generation

On transfer, a handoff note is generated:

```
HANDOFF NOTE:

WHAT WE KNOW:
- Room: kitchen
- Budget: $25k
- Goals: new cabinets, countertops, open up wall

OPEN QUESTIONS: Is wall load-bearing?

KNOWN RISKS: load-bearing check needed

LAST USER MESSAGE: Transfer me to Alice

RECOMMENDED FOCUS: Address technical concerns, risks, 
permits/codes (if relevant), sequencing, or material trade-offs.
```

**Purpose**: Brief incoming agent on context and suggested focus

### Why This Approach?

1. **Structured state**: Prevents information loss, enables downstream features (cost estimation, scheduling)
2. **Rolling summary**: Compact representation for LLM context window efficiency
3. **Transcript tail**: Preserves conversational flow and natural language context
4. **Handoff notes**: Explicit knowledge transfer between agents

## Error Handling & Reliability

### Retry Strategy

All API clients implement retry with exponential backoff:

```python
max_retries = 3
base_delay = 1.0

for attempt in range(max_retries):
    try:
        result = api_call()
        return result
    except RateLimitError:
        delay = base_delay * (2 ** (attempt + 2))  # Longer for rate limits
        time.sleep(delay)
    except APIError:
        delay = base_delay * (2 ** attempt)
        time.sleep(delay)
```

### Circuit Breaker

Main loop tracks consecutive failures:

```python
circuit_breaker_failures = 0
max_failures = 3

if error:
    circuit_breaker_failures += 1
    if circuit_breaker_failures >= max_failures:
        # Degrade gracefully
        speak("I'm having technical difficulties...")
        exit()
else:
    circuit_breaker_failures = 0  # Reset on success
```

### Graceful Degradation

- **Audio fails** → Fall back to text mode (`--text` flag)
- **STT fails** → Prompt user to repeat
- **TTS fails** → Display text response only
- **LLM fails** → Use generic fallback response
- **Circuit breaker trips** → Clean shutdown with message

### Idempotency

State updates are designed to be idempotent:
- Adding same goal twice → No duplicates
- Updating budget → Last write wins
- Retries don't corrupt state

## Tradeoffs & Design Choices

### 1. Regex vs. LLM for Transfer Detection

**Chosen**: Regex (pre-LLM detection)

**Pros**:
- Deterministic, always works
- Fast, no API call
- Cost-free
- Reliable

**Cons**:
- Requires explicit commands
- Limited to predefined patterns
- Less flexible

**Alternative**: LLM-based intent classification
- More flexible, handles variations
- But: slower, costs money, can fail or misinterpret
- Could add as fallback for unclear cases

### 2. Three-Tier Context vs. Full Transcript

**Chosen**: Structured state + summary + tail

**Pros**:
- Efficient use of context window
- Faster LLM processing
- Lower costs
- Structured state enables features

**Cons**:
- Information may be lost in summarization
- Extraction heuristics are imperfect

**Alternative**: Pass full transcript
- No information loss
- But: long context, higher cost, slower

**Mitigation**: Could use LLM to extract state more accurately

### 3. Push-to-Talk vs. Continuous Listening

**Chosen**: Push-to-talk (Ctrl+C to stop)

**Pros**:
- Simple, reliable
- No false triggers
- User controls exactly what's recorded
- Works on all platforms

**Cons**:
- Less natural interaction
- Requires keyboard access

**Alternative**: Voice Activity Detection (VAD)
- More natural
- But: complex, can miss words, needs tuning

### 4. Single File Artifact vs. Modular Architecture

**Chosen**: Modular (7 separate files)

**Pros**:
- Clear separation of concerns
- Easy to test individual components
- Swappable providers
- Maintainable

**Cons**:
- More files to navigate
- Slightly more complex setup

**Alternative**: Monolithic single file
- Simpler deployment
- But: harder to maintain, test, extend

### 5. OpenAI vs. Other Providers

**Chosen**: OpenAI (Whisper, GPT, TTS)

**Pros**:
- Single provider, one API key
- High quality across all modalities
- Well-documented
- Reliable

**Cons**:
- Vendor lock-in
- Cost can add up
- Latency (non-streaming)

**Mitigation**: Adapter pattern allows swapping providers

## Performance Metrics

### Latency Breakdown (Typical Turn)

```
┌─────────────────────┬──────────┐
│ Component           │ Time     │
├─────────────────────┼──────────┤
│ Audio Recording     │ ~3s      │ (user-dependent)
│ STT (Whisper)       │ ~1-2s    │
│ Transfer Detection  │ ~0.001s  │
│ LLM (GPT-4o-mini)   │ ~1-3s    │
│ TTS (OpenAI)        │ ~1-2s    │
│ Audio Playback      │ ~2-5s    │ (response-dependent)
├─────────────────────┼──────────┤
│ TOTAL               │ ~8-15s   │
└─────────────────────┴──────────┘
```

### Cost Estimate (per conversation)

```
Assuming 10-turn conversation:
- Whisper STT: 10 calls × ~$0.006/min = ~$0.06
- GPT-4o-mini: 10 calls × ~$0.01 = ~$0.10
- TTS: 10 calls × ~$0.015/1K chars = ~$0.15
─────────────────────────────────────
TOTAL: ~$0.31 per conversation
```

## Future Improvements

### Short Term (Next Sprint)

1. **Streaming STT/TTS**
   - Reduce latency by streaming audio
   - Start playing response before it's fully generated
   - Use WebSockets for real-time communication

2. **Voice Activity Detection (VAD)**
   - Automatic speech detection
   - No need for push-to-talk
   - Libraries: webrtcvad, silero-vad

3. **Better State Extraction**
   - Use LLM to extract structured state more accurately
   - Structured outputs with JSON mode
   - Validation and error correction

4. **Conversation Persistence**
   - Save conversations to database
   - Resume previous conversations
   - Export transcripts

### Medium Term (Next Month)

5. **Improved Transfer Logic**
   - LLM-based intent classification as fallback
   - Confidence scoring
   - Confirm ambiguous transfers with user

6. **Multi-Turn Planning**
   - Agents can plan ahead
   - Set goals for conversation
   - Track progress toward goals

7. **Context Window Management**
   - Smart summarization with LLM
   - Hierarchical summaries (turn → exchange → phase)
   - Semantic search over history

8. **Enhanced Error Recovery**
   - Retry with different models on failure
   - Fallback to simpler prompts
   - User-friendly error messages

### Long Term (Production)

9. **Real-Time Dialogue Features**
   - Barge-in support (interrupt agent)
   - Clarification questions mid-response
   - Dynamic conversation flow

10. **Observability & Monitoring**
    - Structured logging (JSON)
    - Distributed tracing
    - Metrics dashboard (latency, errors, costs)
    - Conversation analytics

11. **Scale & Performance**
    - Async/await for concurrent requests
    - Request queuing
    - Load balancing
    - Caching frequent responses

12. **Advanced Features**
    - Multi-modal input (images of spaces)
    - Document upload (floor plans, quotes)
    - Integration with contractor databases
    - Cost estimation tools

## Testing Strategy

### Unit Tests
- Each module has standalone test function
- Run: `python module_name.py`
- Tests API connectivity and basic functionality

### Integration Tests
- Test full conversation flows
- Verify transfer continuity
- Validate state updates

### Acceptance Tests
- Test scenarios from requirements doc:
  1. Intake with Bob
  2. Transfer to Alice
  3. Transfer back to Bob
- Verify natural conversation flow

### Load Tests (Future)
- Concurrent user handling
- Rate limit behavior
- Circuit breaker activation

## Security Considerations

### Current Implementation
- API keys via environment variables
- No data persistence (no user data stored)
- No authentication required

### Production Requirements
- User authentication and authorization
- Encrypted data storage
- Rate limiting per user
- Input sanitization
- PII detection and handling
- Audit logging

## Conclusion

This implementation demonstrates production-grade engineering for a voice assistant with seamless agent transfer. The key innovations are:

1. **Pre-LLM transfer detection** for deterministic routing
2. **Three-tier context system** for efficient state management
3. **Comprehensive error handling** with retries and circuit breakers
4. **Modular architecture** for maintainability and testing

The system successfully achieves the core requirement: **seamless context transfer** between agents without user repetition.

Future improvements focus on reducing latency, improving state extraction, and adding production-grade observability and scale.
