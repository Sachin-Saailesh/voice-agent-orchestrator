"""
Conversation State Management
Maintains structured state, summary, and transcript across agent transfers
"""

import json
from typing import Dict, List, Any, Optional
from datetime import datetime


class ConversationState:
    """Manages conversation state with perfect context continuity"""
    
    def __init__(self):
        # Structured project state (updated incrementally)
        self.structured_state: Dict[str, Any] = {
            "project": {
                "room": None,
                "budget": None,
                "goals": [],
                "constraints": [],
                "timeline": None,
                "diy_or_contractor": None,
            },
            "open_questions": [],
            "risks": [],
            "decisions": [],
            "materials_discussed": [],
        }
        
        # Rolling summary (updated each turn)
        self.summary: str = ""
        
        # Transcript tail (keep last N turns for context)
        self.transcript_tail: List[Dict[str, str]] = []
        self.max_tail_length = 12  # 6 exchanges
        
        # Full transcript (for debugging/logging)
        self.full_transcript: List[Dict[str, str]] = []
        
        # Session metadata
        self.session_start = datetime.now()
        self.turn_count = 0
        
        # Track if agent has historically introduced themselves
        self.agent_seen = {"bob": False, "alice": False}
    
    def add_turn(self, speaker: str, text: str):
        """
        Add a turn to the conversation
        
        Args:
            speaker: 'user', 'bob', 'alice', or 'system'
            text: What was said
        """
        turn = {
            "speaker": speaker,
            "text": text,
            "timestamp": datetime.now().isoformat()
        }
        
        self.full_transcript.append(turn)
        self.transcript_tail.append(turn)
        
        # Trim tail if needed
        if len(self.transcript_tail) > self.max_tail_length:
            self.transcript_tail = self.transcript_tail[-self.max_tail_length:]
        
        self.turn_count += 1
    
    def update_from_turn(self, user_input: str, agent_response: str, llm_client=None):
        """
        Update structured state using LLM (gpt-5-nano) if available, with heuristic fallback
        """
        # 1. Update rolling summary
        self.summary = f"{self.summary} User: {user_input}. Agent: {agent_response}."[-500:] # Keep last 500 chars roughly
        
        # 2. Extract structured state
        # 2. Extract structured state
        if llm_client:
            self._extract_with_llm(user_input, agent_response, llm_client)

    def get_state_summary(self) -> str:
        """Get formatted state summary for LLM context"""
        return json.dumps(self.structured_state, indent=2)

    def _extract_with_llm(self, user_input: str, agent_response: str, llm_client):
        """Use simple/fast model to extract state"""
        prompt = f"""
        Analyze this conversation turn and update the JSON state.
        
        CURRENT STATE:
        {json.dumps(self.structured_state)}
        
        TURN:
        User: {user_input}
        Agent: {agent_response}
        
        OUTPUT ONLY JSON with keys to update.
        """
        
        messages = [{"role": "user", "content": prompt}]
        
        # Request gpt-4o-mini (fast and reliable)
        response = llm_client.chat(messages, model="gpt-4o-mini", max_tokens=200, temperature=0.0)
        
        if response:
            try:
                # Naive cleanup of potential markdown
                clean_json = response.replace("```json", "").replace("```", "").strip()
                updates = json.loads(clean_json)
                self._merge_updates(updates)
            except:
                pass # Fail silently to heuristics? Or just ignore.

    def _merge_updates(self, updates: Dict):
        """Merge LLM updates into state"""
        p_up = updates.get("project", {})
        p_curr = self.structured_state["project"]
        
        for k, v in p_up.items():
            if v and k in p_curr:
                if isinstance(p_curr[k], list):
                    if isinstance(v, list):
                        p_curr[k].extend([x for x in v if x not in p_curr[k]])
                    else:
                        if v not in p_curr[k]:
                            p_curr[k].append(v)
                else:
                    p_curr[k] = v
        
        # Merge lists
        for key in ["open_questions", "risks", "decisions"]:
            if key in updates and updates[key]:
                current = self.structured_state[key]
                for item in updates[key]:
                    if item not in current:
                        current.append(item)


