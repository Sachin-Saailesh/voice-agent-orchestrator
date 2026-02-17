# Reflection on Development

I completed the core implementation within **2 hours**, and spent an additional **hour testing edge cases and multiple conversational scenarios**, including repeated transfers, context continuity validation, and error handling paths. The timebox constraint helped prioritize seamless transfer, structured state management, and reliability over premature optimization.

---

## Challenges & Solutions

### 1. Latency in Voice Interaction
**Challenge**: The full turn cycle (STT → LLM → TTS) introduces ~2–3 seconds of latency, which can reduce conversational fluidity.

**Solution**:
- Switched to `gpt-4o-mini` for faster inference.
- Implemented detailed timing logs (`stt_ms`, `llm_ms`, `tts_ms`) to measure latency precisely.
- Used `tts-1` (standard) instead of HD voices to reduce synthesis delay.

**Future Improvement**:  
A production system would use streaming STT/TTS and token-level streaming (e.g., WebSocket or Realtime API) to reduce perceived latency and enable more natural dialogue flow.

---

### 2. Seamless Agent Handover
**Challenge**: Initial transfers felt disjointed. Agents would reintroduce themselves (“Hi, I’m Alice”) and break conversational continuity.

**Solution**:
- Implemented `agent_seen` state tracking to suppress repeated greetings.
- Ensured transfer announcements are spoken by the current agent before switching.
- Added dynamic transfer instructions to prevent re-introductions and name repetition.

Resulting flow:
- Bob (Bob’s voice): “Bringing Alice in…”
- Alice (Alice’s voice): Immediately continues with context-aware response.

This ensures the user never has to repeat themselves and the transition feels natural.

---

### 3. Context Retention & Memory Design
**Challenge**: Ensuring Alice understands why she was brought in without rediscovering context.

**Solution**:
- Built a structured `ConversationState` object tracking:
  - Room
  - Budget
  - Goals
  - Constraints
  - Risk areas
- Added a lightweight **handoff note** summarizing the project state during transfer.
- Passed structured state into the agent prompt rather than replaying raw transcript.

This effectively gives both agents a shared memory while preserving distinct roles and tones.

---

## Trade-offs

Given the strict timebox, I prioritized:

- Deterministic state handling
- Explicit transfer routing
- Clean separation of agents
- Reliability and correctness

Over:

- Streaming concurrency
- Async architecture
- Real-time interruption (barge-in) handling

The current implementation uses synchronous HTTP requests, which are robust and easy to debug but introduce slight latency.

---

## Conclusion

The final architecture demonstrates a stateful, multi-agent voice assistant with:

- Distinct agent personalities
- Seamless context-preserving transfer
- Structured memory handoff
- Measurable latency instrumentation

The key architectural insight is structured state + controlled handoff, enabling multiple agents to operate over a shared conversational memory without breaking user flow.

With additional time, the next evolution would include streaming pipelines, improved observability, and guardrail-based transfer classification for greater reliability in real-time environments.
