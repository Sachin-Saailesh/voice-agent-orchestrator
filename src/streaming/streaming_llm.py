"""
Streaming LLM Client
Wraps OpenAI Chat with token-by-token async streaming.
Checks cancellation event between each token.
"""

import asyncio
import os
from typing import AsyncIterator, Optional, List, Dict

import openai


class StreamingLLMClient:
    """Async streaming LLM wrapper."""

    def __init__(self):
        self._client: Optional[openai.AsyncOpenAI] = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self._client = openai.AsyncOpenAI(api_key=api_key)

    async def stream_tokens(
        self,
        messages: List[Dict[str, str]],
        cancel_event: asyncio.Event,
        model: str = "gpt-4o-mini",
        max_tokens: int = 400,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """
        Yield LLM tokens one by one.
        Stops cleanly if cancel_event is set (barge-in).
        """
        if not self._client:
            return

        try:
            stream = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
            )
            async for chunk in stream:
                if cancel_event.is_set():
                    break
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta

        except asyncio.CancelledError:
            raise
        except Exception as e:
            import logging
            logging.getLogger("voice_agent.llm").error(f"LLM streaming error: {e}", exc_info=True)
            # Yield nothing on error — caller should handle empty response
            return

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str = "gpt-4o-mini",
        max_tokens: int = 200,
        temperature: float = 0.0,
    ) -> Optional[str]:
        """
        Non-streaming completion for utility calls (state extraction, guardrails).
        """
        if not self._client:
            return None
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=8.0,
            )
            content = resp.choices[0].message.content
            return content.strip() if content else None
        except Exception:
            return None
