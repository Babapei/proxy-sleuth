"""Mixed routing detection — catches proxies that switch models by request complexity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError

SIMPLE_PROMPTS = [
    "Say hello.",
    "What is 1+1?",
    "Repeat: 'test'.",
    "What color is the sky?",
    "What is 2+2?",
    "Say 'ok'.",
    "What day comes after Monday?",
    "Count to 3.",
]

COMPLEX_PROMPTS = [
    "Write a Python function to find the longest palindromic substring in O(n^2) time. Include time complexity analysis.",
    "Explain the difference between TCP and UDP, their use cases, and how QUIC protocol improves upon both.",
    "Design a database schema for a multi-tenant SaaS application with users, organizations, roles, and subscriptions.",
    "Implement a thread-safe LRU cache in Python with O(1) get and put operations.",
    "Write a detailed explanation of how the Linux kernel handles memory paging and swap, including page replacement algorithms.",
    "You are designing a distributed key-value store. Explain your approach to consistency, partitioning, and replication.",
    "Analyze the time and space complexity of sorting algorithms: merge sort, quicksort, heap sort. Compare their practical performance.",
    "Describe how OAuth 2.0 with PKCE works step by step. Include the threat model and why each step exists.",
]

SAME_QUESTION_SIMPLE = "What is 15% of 200? Give just the number."
SAME_QUESTION_COMPLEX = (
    "In a financial analysis context, a portfolio manager needs to calculate the expected quarterly return. "
    "If a stock's annual return is structured such that quarterly compounding reflects a proportional allocation "
    "where each quarter represents 15% of a baseline 200-unit investment pool, what absolute value corresponds "
    "to a single quarter's baseline contribution? Compute this step by step."
)

LONG_NEEDLE = "LONG_NEEDLE_{marker}"


@dataclass
class RoutingProbe:
    type: str  # "simple" | "complex"
    prompt: str
    response_len: int = 0
    quality_score: float = 0.0  # 0-1 subjective quality
    duration_ms: float = 0.0
    error: str | None = None


class MixedRoutingDetector:
    """Detects model switching based on request complexity patterns."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        """Run mixed routing detection battery."""
        probes: list[RoutingProbe] = []

        # Test 1: Alternating simple/complex
        alt_results = await self._alternating_test()
        probes.extend(alt_results)

        # Test 2: Same question, different phrasing
        same_results = await self._same_question_test()
        probes.extend(same_results)

        # Test 3: Consistency within categories
        simple_scores = [p.response_len for p in probes if p.type == "simple" and not p.error]
        complex_scores = [p.response_len for p in probes if p.type == "complex" and not p.error]

        avg_simple = sum(simple_scores) / len(simple_scores) if simple_scores else 0
        avg_complex = sum(complex_scores) / len(complex_scores) if complex_scores else 0

        # Suspicious: simple responses are better than complex (impossible for real model)
        suspicious = avg_simple > avg_complex * 0.3 and len(complex_scores) >= 3

        # Suspicious: high variance in simple responses
        simple_variance = 0.0
        if simple_scores:
            simple_variance = sum((s - avg_simple) ** 2 for s in simple_scores) / len(simple_scores)
        high_variance = simple_variance > 100 and len(simple_scores) >= 5

        routing_detected = suspicious or high_variance

        return {
            "layer": "mixed_routing",
            "score": 0.2 if routing_detected else 0.8,
            "verdict": "MISMATCH" if routing_detected else "MATCH",
            "routing_detected": routing_detected,
            "signals": {
                "suspicious_simple_better_than_complex": suspicious,
                "high_simple_variance": high_variance,
            },
            "stats": {
                "avg_simple_response_len": round(avg_simple, 0),
                "avg_complex_response_len": round(avg_complex, 0),
                "simple_variance": round(simple_variance, 0),
                "total_probes": len(probes),
                "errors": sum(1 for p in probes if p.error),
            },
        }

    async def _alternating_test(self) -> list[RoutingProbe]:
        """Alternate 3 simple + 3 complex requests."""
        results: list[RoutingProbe] = []
        alternating = []
        for i in range(6):
            if i % 2 == 0:
                alternating.append(("simple", SIMPLE_PROMPTS[i // 2]))
            else:
                alternating.append(("complex", COMPLEX_PROMPTS[i // 2]))

        for rtype, prompt in alternating:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model,
                    temperature=0.0,
                    max_tokens=512,
                )
                results.append(RoutingProbe(
                    type=rtype, prompt=prompt[:50],
                    response_len=len(resp.content), duration_ms=resp.duration_ms,
                ))
            except APIError as e:
                results.append(RoutingProbe(type=rtype, prompt=prompt[:50], error=str(e)))

        return results

    async def _same_question_test(self) -> list[RoutingProbe]:
        """Same math question, asked simply and complexly."""
        results: list[RoutingProbe] = []

        for rtype, prompt in [("simple", SAME_QUESTION_SIMPLE), ("complex", SAME_QUESTION_COMPLEX)]:
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model,
                    temperature=0.0,
                    max_tokens=512,
                )
                correct = "30" in resp.content or "0.3" in resp.content
                results.append(RoutingProbe(
                    type=rtype, prompt=prompt[:50],
                    response_len=len(resp.content),
                    quality_score=1.0 if correct else 0.0,
                    duration_ms=resp.duration_ms,
                ))
            except APIError as e:
                results.append(RoutingProbe(type=rtype, prompt=prompt[:50], error=str(e)))

        return results
