"""
Transfer Router
Detects transfer intent from user input before calling LLM
"""

import re
from typing import Optional, Dict


class TransferRouter:
    """Detects and routes agent transfer requests"""
    
    def __init__(self):
        # Explicit transfer patterns (checked first, highest priority)
        self.transfer_patterns = {
            "alice": [
                r"transfer.*alice",
                r"let me talk to alice",
                r"switch.*alice",
                r"bring.*alice",
                r"connect.*alice",
                r"put.*alice.*on",
                r"speak.*alice",
                r"can i talk to alice",
                r"i want alice",
                r"i need alice",
            ],
            "bob": [
                r"transfer.*bob",
                r"let me talk to bob",
                r"switch.*bob",
                r"bring.*bob",
                r"go back.*bob",
                r"back to bob",
                r"return.*bob",
                r"put.*bob.*on",
                r"speak.*bob",
                r"can i talk to bob",
                r"i want bob",
                r"i need bob",
            ]
        }
        
        # Compile patterns for efficiency
        self.compiled_patterns = {
            agent: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for agent, patterns in self.transfer_patterns.items()
        }
    
    def detect_transfer(self, user_input: str) -> Optional[Dict[str, str]]:
        """
        Detect if user wants to transfer to a different agent
        
        Args:
            user_input: User's message text
        
        Returns:
            Dict with 'target_agent' if transfer detected, else None
        """
        if not user_input or not user_input.strip():
            return None
        
        text = user_input.lower().strip()
        
        # Check explicit transfer patterns first
        for agent, patterns in self.compiled_patterns.items():
            for pattern in patterns:
                if pattern.search(text):
                    return {
                        "target_agent": agent,
                        "confidence": "explicit",
                        "matched_pattern": pattern.pattern
                    }
        
        # No transfer detected
        return None
    




