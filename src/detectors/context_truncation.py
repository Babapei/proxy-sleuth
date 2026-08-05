"""Context truncation detection — Needle-in-Haystack at realistic token depths.

Detects whether a proxy is silently trimming conversation history.
Uses longer, naturalistic filler to reach meaningful token counts,
and tests needle recall at beginning/middle positions of the context.
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError


# Realistic filler — a paragraph of plausible conversation content
FILLER_TEMPLATES = [
    "Let's continue working on the data pipeline for the analytics dashboard. "
    "The ETL process needs to handle incremental updates from the CDC logs. "
    "We should also add validation for the schema changes in the upstream tables. "
    "The monitoring dashboard should show throughput and error rates per partition.",
    "I've reviewed the deployment configuration for the staging environment. "
    "The Kubernetes manifests need the resource limits updated for the worker pods. "
    "The ingress controller rate limiting thresholds should be raised from 100 to 500 "
    "requests per second during the migration window.",
    "The user authentication flow needs to support both OAuth 2.0 and SAML 2.0 simultaneously. "
    "The session management should use rotating refresh tokens with a 15-minute absolute timeout. "
    "We need to audit the existing refresh token revocation logic for edge cases.",
    "Database migration plan: we need to split the users table into users and user_profiles. "
    "The foreign key relationships to orders and subscriptions need to be preserved. "
    "We'll use a dual-write strategy during the migration to avoid downtime.",
    "Frontend performance audit showed the bundle size increased by 40% after the last release. "
    "The charting library tree-shaking isn't working correctly for the date-fns imports. "
    "We should also implement code splitting for the admin routes.",
    "The CI pipeline now runs the integration tests in parallel across 4 shards. "
    "Test flakiness dropped from 12% to 3% after adding retry logic for the database fixtures. "
    "The e2e tests still need the video recording artifact size reduced.",
    "DataDog metrics show p99 latency increased from 200ms to 450ms after the caching layer change. "
    "The Redis cluster connection pooling needs adjustment for the new workload pattern. "
    "We should also investigate the increased garbage collection pauses in the JVM.",
    "Security audit findings: the API gateway needs CORS headers tightened for the admin endpoints. "
    "The JWT token validation doesn't check the 'aud' claim consistently across services. "
    "Rate limiting should be applied per-user rather than per-IP for the authenticated endpoints.",
    "The recommendation engine A/B test reached statistical significance after 14 days. "
    "The collaborative filtering model improved click-through rate by 8.3% against the baseline. "
    "We should start the gradual rollout to 25% of users next week.",
    "Infrastructure cost optimization: the reserved instances for the GPU cluster expire next month. "
    "The spot instance strategy for the batch processing jobs saved 40% last quarter. "
    "We need to evaluate the new G5 instances for the inference workloads.",
]


@dataclass
class NeedleResult:
    depth: int
    position_desc: str  # "beginning" | "middle" | "end"
    recalled: bool
    response: str = ""
    error: str | None = None


class ContextTruncationDetector:
    """Tests whether proxy preserves context at realistic token depths.

    Default: ~25K tokens (200 rounds of realistic dialogue).
    Deep mode: ~50K tokens (400 rounds) for detecting subtle truncation.
    """

    DEFAULT_ROUNDS = 200
    DEEP_ROUNDS = 400

    def __init__(self, cfg: RunConfig, deep: bool = False):
        self.cfg = cfg
        self.deep = deep
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        """Test needle recall at key positions in a conversation."""
        total_rounds = self.DEEP_ROUNDS if self.deep else self.DEFAULT_ROUNDS
        needle = f"NEEDLE_{secrets.token_hex(4).upper()}"
        positions = {
            total_rounds // 20: "beginning",      # 5% into conversation
            total_rounds // 2: "middle",          # 50% mark
            int(total_rounds * 0.95): "end",       # 95% mark
        }

        messages = self._build_conversation(total_rounds, needle, list(positions.keys()))

        results: list[NeedleResult] = []
        for pos, desc in positions.items():
            messages.append({
                "role": "user",
                "content": f"What was the secret code I asked you to remember earlier? "
                           f"If you recall it, reply with just the code. If not, say 'unknown'.",
            })
            try:
                resp = await self.client.chat(
                    messages=messages, model=self.cfg.model,
                    temperature=0.0, max_tokens=30,
                )
                recalled = needle in resp.content
                results.append(NeedleResult(depth=pos, position_desc=desc, recalled=recalled, response=resp.content[:100]))
            except APIError as e:
                results.append(NeedleResult(depth=pos, position_desc=desc, recalled=False, error=str(e)))
            # Remove the recall question for the next test
            messages.pop()

        recalled_count = sum(1 for r in results if r.recalled)
        # If needle at position 10 (beginning) is NOT recalled → context is clearly truncated
        # If all positions recalled → full context preserved
        beginning_recalled = any(r.position_desc == "beginning" and r.recalled for r in results)
        truncated = not beginning_recalled or recalled_count < len(results)

        return {
            "layer": "context_truncation",
            "score": round(recalled_count / len(results), 3) if results else 1.0,
            "verdict": "MISMATCH" if truncated else "MATCH",
            "truncated": truncated,
            "needles": [
                {"position": r.position_desc, "depth": r.depth, "recalled": r.recalled,
                 "response": r.response, "error": r.error}
                for r in results
            ],
            "total_conversation_rounds": len(messages),
        }

    def _build_conversation(self, total_rounds: int, needle: str, needle_positions: list[int]) -> list[dict]:
        """Build a naturalistic conversation with needles at specified positions."""
        messages: list[dict] = []
        needle_set = set(needle_positions)

        for i in range(total_rounds):
            if i in needle_set:
                filler_idx = i % len(FILLER_TEMPLATES)
                messages.append({
                    "role": "user",
                    "content": f"{FILLER_TEMPLATES[filler_idx]}\n\nBy the way, please remember this code: {needle}",
                })
                messages.append({
                    "role": "assistant",
                    "content": f"Noted. Let me address the points you raised about the data pipeline and deployment configuration. "
                               f"The ETL process changes look good, and I've noted the K8s resource limits. "
                               f"I'll prepare a summary of the action items for the next sprint planning.",
                })
            else:
                filler_idx = i % len(FILLER_TEMPLATES)
                messages.append({"role": "user", "content": FILLER_TEMPLATES[filler_idx]})
                messages.append({
                    "role": "assistant",
                    "content": f"Thanks for the update. I've reviewed item {i} and it looks good. "
                               f"I'll incorporate these changes into the next iteration.",
                })

        return messages
