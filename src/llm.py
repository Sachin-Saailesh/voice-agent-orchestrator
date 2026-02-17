"""
LLM Client
Wrapper around OpenAI Chat API with fallback logic
"""

import os
import time
import openai
from typing import Optional, List, Dict


class LLMClient:
    """LLM client with automatic fallback for speed/reliability"""
    
    def __init__(self, default_model: str = "gpt-4o-mini", temperature: float = 0.7):
        """
        Initialize LLM client
        """
        self.default_model = default_model
        self.temperature = temperature
        self.client = None
        self.max_retries = 2
        
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            try:
                self.client = openai.OpenAI(api_key=api_key)
                print(f"[LLM] Client initialized (Default: {self.default_model})")
            except Exception as e:
                print(f"[LLM] Failed to initialize OpenAI: {e}")
        else:
             print("[LLM] Warning: OPENAI_API_KEY not found. LLM features disabled.")

    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        max_tokens: int = 500,
        temperature: Optional[float] = None
    ) -> Optional[str]:
        """
        Send chat completion request with fallback
        """
        if not self.client:
            print("[LLM] Error: Client not initialized")
            return None

        target_model = model or self.default_model
        temp = temperature if temperature is not None else self.temperature

        # Try primary model
        response = self._try_chat(target_model, messages, max_tokens, temp)
        
        # If primary failed and it wasn't the fallback already, try fallback
        if not response and target_model != "gpt-4o-mini":
            print(f"[LLM] ⚠️ Primary model {target_model} failed, falling back to gpt-4o-mini...")
            response = self._try_chat("gpt-4o-mini", messages, max_tokens, temp)
            
        return response

    def _try_chat(self, model: str, messages: list, max_tokens: int, temperature: float) -> Optional[str]:
        """Internal method to try a specific model"""
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                response = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature
                )
                duration = (time.time() - start_time) * 1000
                
                content = response.choices[0].message.content
                if content:
                    return content.strip()
                
            except Exception as e:
                print(f"[LLM] Error with {model} (attempt {attempt+1}): {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(0.5)
                    
        return None



