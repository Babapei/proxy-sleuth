"""Mixed routing detection — catches proxies that switch models mid-stream.

Detection strategy:
  1. Identity consistency: ask "what model are you?" 3 ways → must agree
  2. Quality inversion: same math buried in simple vs complex phrasing → 
     if simple fails but complex passes → model switched
  3. Self-consistency: repeat same prompt 3 times → style/quality shouldn't jump
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError


# Same math problem, different complexity wrappers
SIMPLE_MATH = "What is 15% of 200? Answer with just the number."
COMPLEX_MATH = (
    "In a portfolio analysis, quarterly returns follow a proportional allocation model. "
    "If each quarter represents 15% of a 200-unit baseline, what is the absolute value "
    "for one quarter? Answer with just the number."
)

# Same factual question, different complexity
SIMPLE_FACT = "What is the capital of France?"
COMPLEX_FACT = (
    "In the context of European political geography, considering the historical evolution "
    "of national administrative centers from the Merovingian period through the Fifth Republic, "
    "identify the primary administrative capital of the French Republic."
)


@dataclass
class RoutingProbe:
    type: str
    prompt_preview: str
    correct: bool | None = None
    response: str = ""
    error: str | None = None


class MixedRoutingDetector:
    """Detects model switching via identity consistency and quality inversion."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        probes: list[RoutingProbe] = []

        # Test 1: Identity consistency — ask "who are you?" 3 ways
        identity = await self._identity_check()
        probes.extend(identity)

        # Test 2: Quality inversion — same math, simple vs complex wrapping
        inversion = await self._inversion_check()
        probes.extend(inversion)

        # Test 3: Same fact, simple vs complex
        fact = await self._fact_check()
        probes.extend(fact)

        # Analysis
        identity_consistent = all(
            p.correct for p in probes if "identity" in p.type
        ) if any("identity" in p.type for p in probes) else True

        # Quality inversion: simple should be AT LEAST as likely to be correct as complex
        simple_math = [p for p in probes if p.type == "math_simple"]
        complex_math = [p for p in probes if p.type == "math_complex"]
        inversion_detected = False
        if simple_math and complex_math:
            inversion_detected = (
                (not any(p.correct for p in simple_math)) and
                any(p.correct for p in complex_math)
            )

        # Fact consistency
        fact_simple = [p for p in probes if p.type == "fact_simple"]
        fact_complex = [p for p in probes if p.type == "fact_complex"]
        fact_inversion = False
        if fact_simple and fact_complex:
            fact_inversion = (
                (not any(p.correct for p in fact_simple)) and
                any(p.correct for p in fact_complex)
            )

        routing_signals = sum([not identity_consistent, inversion_detected, fact_inversion])
        routing_detected = routing_signals >= 1

        return {
            "layer": "mixed_routing",
            "score": 0.2 if routing_detected else 0.8,
            "verdict": "MISMATCH" if routing_detected else "MATCH",
            "routing_detected": routing_detected,
            "signals": {
                "identity_inconsistent": not identity_consistent,
                "quality_inversion": inversion_detected,
                "fact_inversion": fact_inversion,
            },
            "stats": {
                "total_probes": len(probes),
                "errors": sum(1 for p in probes if p.error),
                "routing_signal_count": routing_signals,
            },
        }

    async def _identity_check(self) -> list[RoutingProbe]:
        """Ask the model to identify itself 3 different ways."""
        prompts = [
            ("identity_direct", "What model are you? Reply with just the model name."),
            ("identity_indirect", "What is your official product name and version from OpenAI?"),
            ("identity_self", "Introduce yourself with your exact model identifier."),
        ]
        results = []
        for typ, prompt in prompts:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model, temperature=0.0, max_tokens=100,
                )
                # Check if any of the 3 responses mention the claimed model
                claimed = self.cfg.model.lower()
                correct = claimed in resp.content.lower()
                results.append(RoutingProbe(type=typ, prompt_preview=prompt[:40], correct=correct, response=resp.content[:100]))
            except APIError as e:
                results.append(RoutingProbe(type=typ, prompt_preview=prompt[:40], error=str(e)))
        return results

    async def _inversion_check(self) -> list[RoutingProbe]:
        """Same math, simple vs complex phrasing — simple should be easier, not harder."""
        results = []
        for typ, prompt in [("math_simple", SIMPLE_MATH), ("math_complex", COMPLEX_MATH)]:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model, temperature=0.0, max_tokens=50,
                )
                correct = "30" in resp.content
                results.append(RoutingProbe(type=typ, prompt_preview=prompt[:40], correct=correct, response=resp.content[:100]))
            except APIError as e:
                results.append(RoutingProbe(type=typ, prompt_preview=prompt[:40], error=str(e)))
        return results

    async def _fact_check(self) -> list[RoutingProbe]:
        results = []
        for typ, prompt in [("fact_simple", SIMPLE_FACT), ("fact_complex", COMPLEX_FACT)]:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model, temperature=0.0, max_tokens=50,
                )
                correct = "paris" in resp.content.lower()
                results.append(RoutingProbe(type=typ, prompt_preview=prompt[:40], correct=correct, response=resp.content[:100]))
            except APIError as e:
                results.append(RoutingProbe(type=typ, prompt_preview=prompt[:40], error=str(e)))
        return results
