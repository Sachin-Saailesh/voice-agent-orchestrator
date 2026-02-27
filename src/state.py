"""
Conversation State Management
Maintains structured state, rolling summary, and transcript across agent transfers.
"""

import json
from typing import Dict, List, Any, Optional
from datetime import datetime


class ConversationState:
    """Manages conversation state with perfect context continuity."""

    def __init__(self):
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
        self.summary: str = ""
        self.transcript_tail: List[Dict[str, str]] = []
        self.max_tail_length = 12  # 6 exchanges
        self.full_transcript: List[Dict[str, str]] = []
        self.session_start = datetime.now()
        self.turn_count = 0
        self.agent_seen = {"bob": False, "alice": False}

    def add_turn(self, speaker: str, text: str):
        """Add a turn to the conversation."""
        turn = {
            "speaker": speaker,
            "text": text,
            "timestamp": datetime.now().isoformat(),
        }
        self.full_transcript.append(turn)
        self.transcript_tail.append(turn)
        if len(self.transcript_tail) > self.max_tail_length:
            self.transcript_tail = self.transcript_tail[-self.max_tail_length:]
        self.turn_count += 1

    def get_state_summary(self) -> str:
        """Get formatted state summary for LLM context."""
        return json.dumps(self.structured_state, indent=2)

    def _merge_updates(self, updates: Dict):
        """Merge LLM-extracted updates into structured state."""
        p_up = updates.get("project", {})
        p_curr = self.structured_state["project"]
        for k, v in p_up.items():
            if v and k in p_curr:
                if isinstance(p_curr[k], list):
                    items = v if isinstance(v, list) else [v]
                    p_curr[k].extend(x for x in items if x not in p_curr[k])
                else:
                    p_curr[k] = v

        for key in ["open_questions", "risks", "decisions"]:
            if key in updates and updates[key]:
                current = self.structured_state[key]
                for item in updates[key]:
                    if item not in current:
                        current.append(item)
