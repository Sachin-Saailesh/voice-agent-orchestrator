# Quick Start Guide


### Step 1: Setup (2 minutes)

```bash
# Navigate to project
cd bob-alice-voice-assistant

# Run automated setup
./setup.sh

# Or manual setup:
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Configure API Key (1 minute)

```bash
# Copy template
cp .env.example .env

# Edit and add your OpenAI API key
# Get key from: https://platform.openai.com/api-keys
nano .env  # or use your preferred editor
```

Your `.env` should look like:
```
OPENAI_API_KEY=sk-proj-abc123...your-actual-key
```

### Step 3: Run! (30 seconds)

```bash
# Activate environment (if not already active)
source venv/bin/activate

# Run in voice mode
cd src
python main.py

# OR run in text mode (if audio issues)
python main.py --text
```

## First Conversation

**Voice Mode:**
1. See "🎤 Recording..."
2. Speak: "Hi Bob, I want to remodel my kitchen. Budget is $25k."
3. Press `Ctrl+C` when done
4. Listen to Bob's response
5. Continue conversation

**Transfer Example:**
```
YOU: Transfer me to Alice
ALICE: [Takes over with full context]

YOU: Go back to Bob  
BOB: [Resumes with full context]
```

**Text Mode:**
1. Type your message
2. Press Enter
3. Read response
4. Continue

## Test It Works

```bash
# Run all tests
python test_all.py

# Test individual modules
cd src
python state.py    # Test state management
python router.py   # Test transfer detection
python llm.py      # Test LLM (requires API key)
```

## ⚡ Common Commands

```bash
# Voice mode (default)
python main.py

# Text mode (fallback)
python main.py --text

# Exit conversation
Type: exit, quit, or bye
Or press: Ctrl+C twice
```

## Test Scenarios

Try these to verify the system works:

**Scenario 1: Basic intake**
```
"Hi Bob, I want to remodel my kitchen with a budget of $25k. 
I want new cabinets and countertops, and maybe open up a wall."
```

**Scenario 2: Transfer to specialist**
```
"Transfer me to Alice"
```

**Scenario 3: Return to planner**
```
"Go back to Bob"
```

## Troubleshooting

**"Module not found"**
```bash
pip install -r requirements.txt
```

**"OPENAI_API_KEY not set"**
```bash
# Make sure .env exists and has your key
cat .env
export OPENAI_API_KEY=sk-your-key
```

**Audio doesn't work**
```bash
# Use text mode instead
python main.py --text
```

**Rate limit errors**
```bash
# Wait 1 minute, the system will retry automatically
# Or check your API usage limits at platform.openai.com
```

## Next Steps

- Read [README.md](README.md) for detailed documentation
- Read [DESIGN.md](DESIGN.md) for architecture details
- Check [FILE_TREE.md](FILE_TREE.md) for code structure
- Review original requirements in PDF

## Tips

- **Push-to-talk**: Speak naturally, press Ctrl+C when done
- **Transfer anytime**: Say "transfer to Alice" or "go back to Bob"
- **Context preserved**: Never need to repeat information after transfer
- **Ask anything**: Budget questions, permit guidance, material choices
- **Stay general**: System won't give licensed professional advice

## You're Ready!

The system is production-grade with:
- ✅ Retry logic and error handling
- ✅ Circuit breaker for failures
- ✅ Seamless agent transfers
- ✅ Perfect context continuity
- ✅ Logging and observability

Happy renovating!
