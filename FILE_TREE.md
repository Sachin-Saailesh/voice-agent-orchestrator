# Project File Tree

```
bob-alice-voice-assistant/
│
├── src/                          # Source code
│   ├── main.py                   # Main application loop & CLI (243 lines)
│   ├── audio.py                  # Audio I/O with sounddevice (149 lines)
│   ├── stt.py                    # Speech-to-Text client wrapper (115 lines)
│   ├── tts.py                    # Text-to-Speech client wrapper (113 lines)
│   ├── llm.py                    # LLM client wrapper (112 lines)
│   ├── agents.py                 # Bob & Alice agents (293 lines)
│   ├── router.py                 # Transfer detection (149 lines)
│   └── state.py                  # Conversation state management (195 lines)
│
├── README.md                     # Setup and usage guide
├── DESIGN.md                     # Architecture and design decisions
├── requirements.txt              # Python dependencies
├── .env.example                  # Environment variables template
├── .gitignore                    # Git ignore rules
├── setup.sh                      # Setup script (executable)
└── test_all.py                   # Test runner (executable)

Total: 1,369 lines of production code (excluding docs)
```

## File Descriptions

### Core Application (`src/`)

**main.py** (243 lines)
- Main conversation loop
- Voice/text mode handling
- Turn processing orchestration
- Circuit breaker for API failures
- Logging and error handling

**audio.py** (149 lines)
- Push-to-talk recording
- WAV format conversion
- Audio playback
- Device error handling

**stt.py** (115 lines)
- OpenAI Whisper API wrapper
- Exponential backoff retry logic
- Connection testing
- Error handling

**tts.py** (113 lines)
- OpenAI TTS API wrapper
- Multiple voice support
- Retry logic
- Error handling

**llm.py** (112 lines)
- OpenAI Chat API wrapper
- Message formatting
- Retry logic
- Error handling

**agents.py** (293 lines)
- Bob system prompt (intake & planning)
- Alice system prompt (specialist & risk)
- Agent transfer logic
- Handoff note generation
- Context assembly for LLM

**router.py** (149 lines)
- Regex-based transfer detection
- Pattern matching (explicit commands)
- Auto-transfer suggestions (bonus)
- Intent classification

**state.py** (195 lines)
- Structured project state tracking
- Rolling summary generation
- Transcript tail management
- State extraction from conversation
- Context formatting

### Documentation & Setup

**README.md**
- Quick start guide
- Setup instructions
- Usage examples
- Test scenarios
- Troubleshooting

**DESIGN.md**
- Architecture diagrams
- Design decisions
- Tradeoffs analysis
- Performance metrics
- Future improvements

**requirements.txt**
- Python dependencies
- Version specifications

**.env.example**
- Environment variable template
- API key placeholder

**.gitignore**
- Python artifacts
- Environment files
- IDE files

**setup.sh**
- Automated setup script
- Dependency installation
- Environment configuration

**test_all.py**
- Module test runner
- Unit test orchestration
- API test suite

## Key Statistics

- **Total Lines of Code**: 1,369 (excluding docs)
- **Number of Modules**: 8 core modules
- **Test Coverage**: All modules have test functions
- **Documentation**: 2 comprehensive docs (README + DESIGN)
- **Production Ready**: ✓ Error handling, ✓ Retry logic, ✓ Circuit breaker

## Architecture Highlights

```
Audio I/O ─────┐
               ├──> Main Loop ──> State Manager
STT/TTS ───────┤                      ↓
               │                 Agent Manager
LLM ───────────┤                      ↓
               └──> Router ────> Transfer Logic
```

All components are:
- Modular and swappable
- Production-grade error handling
- Well-tested with unit tests
- Documented with inline comments
