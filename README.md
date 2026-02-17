# Bob ↔ Alice Voice Assistant for Home Renovation

A production-grade voice assistant that helps homeowners plan renovations with seamless agent transfer between Bob (Intake & Planning) and Alice (Technical Specialist).

## Overview

This system demonstrates:
- **Voice conversation** with speech-to-text and text-to-speech
- **Two distinct AI agents** with different personalities and expertise
- **Seamless context transfer** between agents without user repetition
- **Production-grade error handling** with retries and circuit breakers
- **Structured state management** for perfect conversation continuity

### The Agents

**Bob** (Intake & Planning)
- Friendly, concise, asks clarifying questions
- Gathers requirements: room, budget, timeline, scope
- Creates checklists and rough plans
- Transfers to Alice for technical questions

**Alice** (Specialist & Risk Analysis)
- Structured, risk-aware, technical
- Handles permits, codes, sequencing, material trade-offs
- Provides cost breakdowns and identifies pitfalls
- Transfers back to Bob for execution planning

## Requirements

- Python 3.11 or higher
- OpenAI API key (for GPT, Whisper, TTS)
- Microphone and speakers (for voice mode)
- macOS, Linux, or Windows with audio support

## Quick Start

### 1. Clone and Setup

```bash
# Clone the repository
git clone <repository-url>
cd bob-alice-voice-assistant

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API Keys

```bash
# Copy example environment file
cp .env.example .env

# Edit .env and add your OpenAI API key
# Get your key from: https://platform.openai.com/api-keys
OPENAI_API_KEY=sk-your-actual-api-key-here
```

### 3. Run the Assistant

```bash
# Voice mode (default)
cd src
python main.py

# Text mode (fallback if audio issues)
python main.py --text
```

## 🎤 How to Use

### Voice Mode
1. Start the app - Bob greets you
2. When you see "🎤 Recording...", start speaking
3. Press `Ctrl+C` when done speaking
4. The assistant will respond with voice
5. Repeat for conversation

### Transfer Commands
Use these phrases to switch agents:
- **To Alice**: "Transfer me to Alice", "Let me talk to Alice", "Bring Alice in"
- **To Bob**: "Go back to Bob", "Let me talk to Bob", "Switch to Bob"

### Example Conversation Flow

```
YOU: Hi Bob, I want to remodel my kitchen. Budget is around $25k. 
     I want new cabinets and countertops, and maybe open up a wall.

BOB: Great! A few questions: Do you know if that wall is load-bearing? 
     What's your timeline? Are you doing any of this yourself or 
     hiring contractors?

YOU: Not sure about the wall. Timeline is 3 months. Hiring contractors.

BOB: Perfect. Here's a rough plan: 1) Get a structural assessment for 
     the wall, 2) Get contractor quotes, 3) Finalize design choices. 
     Want Alice to dive into the wall situation?

YOU: Transfer me to Alice

ALICE: I'm here. I see you're planning a $25k kitchen remodel with wall 
       removal. For opening up a wall: First, you'll need a structural 
       engineer to determine if it's load-bearing. If it is, you'll 
       likely need a beam installed and a building permit. Typical 
       sequence: 1) Engineer assessment ($300-500), 2) Apply for permit 
       if needed, 3) Contractor does demo and beam install, 4) 
       Inspection, 5) Drywall and finish. Permit timelines vary by 
       jurisdiction - check with your local building department.

YOU: Go back to Bob

BOB: I'm back! Based on what Alice covered, here's your action plan 
     for this week: 1) Call 2-3 structural engineers for quotes, 
     2) Start getting contractor bids, 3) Make cabinet and countertop 
     selections. Want to discuss anything else?
```

## Test Scenarios

The system is designed to pass these test scenarios:

### Test 1: Intake and Planning (Bob)
```
Say: "Hi Bob, I want to remodel my kitchen. Budget is around $25k. 
      I want new cabinets and countertops, and maybe open up a wall."

Expected: Bob asks 1-3 clarifying questions about scope, load-bearing 
          uncertainty, appliances, timeline.
```

### Test 2: Transfer to Specialist (Alice)
```
Say: "Transfer me to Alice"

Expected: Alice confirms takeover, references budget/scope, addresses 
          the wall risk with structural checks and permit guidance.
```

### Test 3: Transfer Back to Bob
```
Say: "Go back to Bob"

Expected: Bob resumes with full context, provides homeowner-friendly 
          next-steps list.
```

## Project Structure

```
bob-alice-voice-assistant/
├── src/
│   ├── main.py          # Main application loop & CLI
│   ├── audio.py         # Audio recording and playback
│   ├── stt.py           # Speech-to-text client (OpenAI Whisper)
│   ├── tts.py           # Text-to-speech client (OpenAI TTS)
│   ├── llm.py           # LLM client (OpenAI GPT)
│   ├── agents.py        # Bob & Alice system prompts
│   ├── router.py        # Transfer intent detection
│   └── state.py         # Conversation state management
├── requirements.txt     # Python dependencies
├── .env.example        # Environment variables template
├── README.md           # This file
└── DESIGN.md           # Architecture and design decisions
```

## Configuration

### Environment Variables

```bash
# Required
OPENAI_API_KEY=sk-...        # Your OpenAI API key

# Optional overrides
LLM_MODEL=gpt-4o-mini        # Default: gpt-4o-mini
TTS_VOICE=alloy              # Options: alloy, echo, fable, onyx, nova, shimmer
TTS_MODEL=tts-1              # Default: tts-1
```

### Audio Settings

Edit `src/audio.py` to adjust:
- `sample_rate`: Default 16000 Hz
- `channels`: Default 1 (mono)
- `max_duration`: Max recording length (default 30s)

## Troubleshooting

### "No module named 'sounddevice'"
```bash
pip install sounddevice soundfile
```

### "OPENAI_API_KEY not set"
```bash
# Make sure .env file exists and has your key
cat .env

# Or export directly
export OPENAI_API_KEY=sk-your-key
```

### Audio device issues
```bash
# Test audio system
cd src
python audio.py

# Use text mode as fallback
python main.py --text
```

### Rate limit errors
- OpenAI has rate limits on API usage
- The system will retry with exponential backoff
- If persistent, check your API usage at platform.openai.com

## Architecture

See [DESIGN.md](DESIGN.md) for detailed architecture, transfer detection approach, state management strategy, and future improvements.

Key design decisions:
- **Transfer detection BEFORE LLM**: Explicit commands detected via regex before agent processes turn
- **Structured state + summary + tail**: Maintains project state, rolling summary, and recent transcript
- **Handoff notes**: Generated on transfer to brief new agent on context
- **Circuit breaker**: Fails gracefully after repeated API errors

## Important Constraints

This system:
- **Does NOT provide professional legal/engineering advice**
- **Recommends consulting licensed professionals** for structural, electrical, plumbing work
- **Keeps permit/code guidance general** - always check with local authorities
- Is designed for **planning and guidance**, not professional consultation

## Development

### Running Tests
```bash
# Test individual modules
cd src
python audio.py    # Test audio recording/playback
python stt.py      # Test speech-to-text
python tts.py      # Test text-to-speech
python llm.py      # Test LLM client
python agents.py   # Test agent manager
python router.py   # Test transfer detection
python state.py    # Test state management
```

### Adding New Agents
1. Add system prompt to `agents.py`
2. Update transfer patterns in `router.py`
3. Add agent name to valid agents list

### Swapping Providers
The code uses adapter pattern - you can swap providers by:
1. Implementing same interface in respective client (stt.py, tts.py, llm.py)
2. Updating credentials in .env

## Reference

For the original requirements, see the attached PDF: [Take-Home__Bob___Alice_Voice_Assistant_for_Home_Renovation__Agent_Transfer_.pdf]

## License

This is a take-home implementation project.

## Contributing

This is a demonstration project. For production use:
- Add authentication and user management
- Implement conversation persistence
- Add streaming STT/TTS for lower latency
- Implement voice activity detection (VAD)
- Add observability and monitoring
- Scale with queuing system for concurrent users

---

**Built with**: OpenAI GPT-4o-mini, Whisper, and TTS • Python 3.11+ • Production-grade error handling
