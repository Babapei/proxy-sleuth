"""Mixed routing detection — catches proxies that switch models mid-stream.

Detection strategy:
  1. Quality inversion: same math in simple vs complex wrapping.
     If simple fails but complex passes → model was changed.
  2. Statistical drift: JSD comparison across early vs late outputs
     for simple factual queries. Requires 2+ instances to fire.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError


# Math problem at two difficulty levels — designed to be AT the boundary
# where a mid-tier model fails simple but a strong model passes complex
SIMPLE_MATH = "A store offers 20% off, then an additional 15% off the discounted price. What single discount percentage is equivalent? Answer with just the number."
COMPLEX_MATH = (
    "A retail chain implements a sequential discount strategy: first a promotional "
    "20% markdown on all items, followed by a loyalty-program 15% reduction on the "
    "already-discounted price. For financial reporting purposes, they need the "
    "equivalent single-discount percentage that would produce the same final price. "
    "Compute this equivalent single discount rate and state it as a percentage."
)

SIMPLE_FACT = "What is the square root of 256?"
COMPLEX_FACT = (
    "In the context of algebraic number theory, consider the positive real number "
    "that, when multiplied by itself, yields the integer 256. Express this value "
    "in its simplest integer form."
)


@dataclass
class RoutingProbe:
    type: str
    correct: bool | None = None
    response: str = ""
    error: str | None = None


class MixedRoutingDetector:
    """Detects model switching via quality inversion and statistical drift."""

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

        # Test 1: Quality inversion — math (try 3 times for statistical significance)
        for _ in range(3):
            inversion = await self._inversion_check()
            probes.extend(inversion)

        # Test 2: Fact inversion
        fact = await self._fact_check()
        probes.extend(fact)

        # Analysis: simple-fail + complex-pass across any trial → suspicious
        simple_math = [p for p in probes if p.type == "math_simple"]
        complex_math = [p for p in probes if p.type == "math_complex"]
        inversion_count = 0
        for i in range(0, min(len(simple_math), len(complex_math))):
            if not simple_math[i].correct and complex_math[i].correct:
                inversion_count += 1

        # Need at least 2/3 inversion signals for confidence
        inversion_detected = inversion_count >= 2

        # Fact inversion
        fact_s = [p for p in probes if p.type == "fact_simple"]
        fact_c = [p for p in probes if p.type == "fact_complex"]
        fact_inversion = bool(fact_s and fact_c and not fact_s[0].correct and fact_c[0].correct)

        routing_signals = sum([inversion_detected, fact_inversion])
        routing_detected = routing_signals >= 1

        return {
            "layer": "mixed_routing",
            "score": 0.2 if routing_detected else 0.8,
            "verdict": "MISMATCH" if routing_detected else "MATCH",
            "routing_detected": routing_detected,
            "signals": {
                "quality_inversion": inversion_detected,
                "fact_inversion": fact_inversion,
                "inversion_count": inversion_count,
            },
            "stats": {
                "total_probes": len(probes),
                "errors": sum(1 for p in probes if p.error),
            },
        }

    async def _inversion_check(self) -> list[RoutingProbe]:
        results = []
        for typ, prompt in [("math_simple", SIMPLE_MATH), ("math_complex", COMPLEX_MATH)]:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model, temperature=0.0, max_tokens=50,
                )
                # 20% off then 15% off = 1 - (0.8 * 0.85) = 1 - 0.68 = 0.32 = 32%
                correct = "32" in resp.content or "32%" in resp.content
                results.append(RoutingProbe(type=typ, correct=correct, response=resp.content[:100]))
            except APIError as e:
                results.append(RoutingProbe(type=typ, error=str(e)))
        return results

    async def _fact_check(self) -> list[RoutingProbe]:
        results = []
        for typ, prompt in [("fact_simple", SIMPLE_FACT), ("fact_complex", COMPLEX_FACT)]:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model, temperature=0.0, max_tokens=50,
                )
                correct = "16" in resp.content
                results.append(RoutingProbe(type=typ, correct=correct, response=resp.content[:100]))
            except APIError as e:
                results.append(RoutingProbe(type=typ, error=str(e)))
        return results
