"""Context truncation detection — Needle-in-Haystack tests.

Detects whether a proxy is silently trimming conversation history
to save on input token costs.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError


@dataclass
class NeedleResult:
    depth: int  # How many rounds deep the needle was
    total_rounds: int
    recalled: bool
    response: str = ""
    error: str | None = None


class ContextTruncationDetector:
    """Tests whether the proxy preserves full conversation context."""

    # Test depths: 10, 20, 50, 100 rounds
    TEST_DEPTHS = [10, 20, 50, 100]

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        """Run context truncation tests at multiple depths."""
        results: list[NeedleResult] = []

        for depth in self.TEST_DEPTHS:
            result = await self._needle_test(depth)
            results.append(result)

        # Find the truncation point
        max_recalled = 100
        min_forgotten = 100
        for r in results:
            if r.recalled and r.depth > max_recalled:
                max_recalled = r.depth
            if not r.recalled and r.depth < min_forgotten:
                min_forgotten = r.depth

        truncated = min_forgotten < 100
        estimated_cutoff = max_recalled if truncated else 100

        passed_count = sum(1 for r in results if r.recalled)
        score = passed_count / len(results) if results else 1.0

        return {
            "layer": "context_truncation",
            "score": round(score, 3),
            "verdict": "MISMATCH" if truncated else "MATCH",
            "truncated": truncated,
            "estimated_context_rounds": estimated_cutoff,
            "needles": [
                {
                    "depth": r.depth,
                    "total_rounds": r.total_rounds,
                    "recalled": r.recalled,
                    "error": r.error,
                    "response_snippet": r.response[:100] if r.response else "",
                }
                for r in results
            ],
        }

    async def _needle_test(self, depth: int) -> NeedleResult:
        """Insert a secret at a specific depth and test recall."""
        needle = f"NEEDLE_{secrets.token_hex(6).upper()}"
        messages = self._build_filler(depth, needle)

        # Final round: ask for the needle
        messages.append({
            "role": "user",
            "content": "What was the secret code I asked you to remember earlier? Reply with just the code or 'unknown'.",
        })

        try:
            resp = await self.client.chat(
                messages=messages,
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=50,
            )
            recalled = needle in resp.content
            return NeedleResult(depth=depth, total_rounds=len(messages), recalled=recalled, response=resp.content)
        except APIError as e:
            return NeedleResult(depth=depth, total_rounds=len(messages), recalled=False, error=str(e))

    def _build_filler(self, depth: int, needle: str) -> list[dict]:
        """Build conversation with filler turns and one needle at the specified depth.

        The needle is placed in a user message at position `depth`. All other
        turns are harmless filler content about counting and lists.
        """
        messages: list[dict] = []
        needle_inserted = False

        for i in range(depth + 5):  # 5 extra rounds after needle
            is_needle_round = (i == depth) and not needle_inserted

            if is_needle_round:
                needle_inserted = True
                user_content = (
                    f"Please remember this secret code for later: {needle}. "
                    f"Just reply 'OK, I remember the code.' and nothing else."
                )
                assistant_content = "OK, I remember the code."
            else:
                topics = [
                    f"Count from 1 to {i+5}.",
                    f"List {i+3} common fruits.",
                    f"Name {i+2} programming languages.",
                    f"List the first {i+4} prime numbers.",
                    f"What is {i+1} plus {i+2} times 3?",
                    f"Name {i+3} capital cities.",
                ]
                user_content = topics[i % len(topics)]
                assistant_content = f"Here are the results for round {i}: done."

            messages.append({"role": "user", "content": user_content})
            messages.append({"role": "assistant", "content": assistant_content})

        return messages[: (depth + 5) * 2]  # Cap at requested depth
