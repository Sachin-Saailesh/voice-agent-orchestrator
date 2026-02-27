"""
Guardrails Filter
Two-pass content safety check:
  Pass 1: Fast keyword blocklist (instant, zero latency)
  Pass 2: OpenAI Moderation API (async, ~100ms)

Applied to:
  - User input (before LLM call)
  - LLM output (before TTS)
"""

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Optional

import openai


# ─── Blocklist ──────────────────────────────────────────────────────────────

_BLOCKLIST_PATTERNS = [
    r"\b(how\s+to\s+(make|build|create|synthesize)\s+(a\s+)?(bomb|weapon|poison|drug)s?)\b",
    r"\b(kill\s+(yourself|myself|yourself|himself|herself|themselves))\b",
    r"\b(child\s+(pornography|abuse|exploitation|sexual))\b",
    r"\b(self[\-\s]harm|suicide\s+method)\b",
    r"\b(synthesize\s+(drugs?|methamphetamine|heroin|fentanyl))\b",
]

_compiled_blocklist = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in _BLOCKLIST_PATTERNS
]


def _blocklist_check(text: str) -> tuple[bool, Optional[str]]:
    """
    Returns (is_harmful, category).
    Extremely fast — just regex scan.
    """
    for pattern in _compiled_blocklist:
        if pattern.search(text):
            return True, "blocklist_match"
    return False, None


# ─── GuardrailFilter ────────────────────────────────────────────────────────

@dataclass
class GuardrailResult:
    ok: bool
    category: Optional[str] = None
    confidence: float = 0.0
    reason: Optional[str] = None


class GuardrailFilter:
    """
    Two-pass content filter.
    Pass 1 is synchronous (call in-thread).
    Pass 2 (moderation API) is async.
    """

    def __init__(self):
        self.enabled = os.getenv("GUARDRAIL_ENABLED", "true").lower() == "true"
        self._client: Optional[openai.AsyncOpenAI] = None
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            self._client = openai.AsyncOpenAI(api_key=api_key)

    async def check(self, text: str) -> GuardrailResult:
        """
        Full two-pass check. Always call this before TTS and before LLM.
        """
        if not self.enabled or not text or not text.strip():
            return GuardrailResult(ok=True)

        # Pass 1: Blocklist (instant)
        harmful, cat = _blocklist_check(text)
        if harmful:
            return GuardrailResult(
                ok=False,
                category=cat,
                confidence=1.0,
                reason="Content matched safety blocklist",
            )

        # Pass 2: OpenAI Moderation (async, fires in background)
        if self._client:
            try:
                resp = await asyncio.wait_for(
                    self._moderation_check(text), timeout=2.0
                )
                return resp
            except asyncio.TimeoutError:
                # Moderation API timed out — allow (don't block on infrastructure)
                pass
            except Exception:
                pass

        return GuardrailResult(ok=True)

    async def _moderation_check(self, text: str) -> GuardrailResult:
        """OpenAI Moderation API check."""
        resp = await self._client.moderations.create(input=text)
        result = resp.results[0]
        if result.flagged:
            # Find highest-scoring category
            cats = result.categories.model_dump()
            scores = result.category_scores.model_dump()
            flagged_cats = [k for k, v in cats.items() if v]
            top_cat = max(flagged_cats, key=lambda k: scores.get(k, 0)) if flagged_cats else "unknown"
            return GuardrailResult(
                ok=False,
                category=top_cat,
                confidence=scores.get(top_cat, 0.0),
                reason=f"OpenAI Moderation flagged: {', '.join(flagged_cats)}",
            )
        return GuardrailResult(ok=True)
