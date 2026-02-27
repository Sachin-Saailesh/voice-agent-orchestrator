"""
LLM Client
Wraps OpenAI Chat Completions for synchronous response generation.
"""

import os
import logging
from typing import Optional

import openai

log = logging.getLogger(__name__)


class LLMClient:
    """Synchronous OpenAI Chat wrapper."""

    def __init__(
        self,
        default_model: str = "gpt-4o-mini",
        temperature: float = 0.7,
    ):
        self.default_model = default_model
        self.temperature = temperature
        api_key = os.getenv("OPENAI_API_KEY")
        self._client = openai.OpenAI(api_key=api_key) if api_key else None
        if not self._client:
            log.warning("OPENAI_API_KEY not set — LLM disabled")

    def chat(
        self,
        messages: list,
        model: Optional[str] = None,
        max_tokens: int = 400,
        temperature: Optional[float] = None,
    ) -> Optional[str]:
        """
        Send messages to OpenAI Chat and return the reply text.
        Returns None on failure.
        """
        if not self._client:
            return None
        try:
            resp = self._client.chat.completions.create(
                model=model or self.default_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature if temperature is not None else self.temperature,
            )
            content = resp.choices[0].message.content
            return content.strip() if content else None
        except Exception as e:
            log.error("LLM error: %s", e)
            return None
