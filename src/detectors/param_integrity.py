"""Parameter integrity detection — catches proxy tampering with request params.

Detects: max_tokens reduction, reasoning_effort downgrade, temperature
locking, tool definition stripping, and system prompt injection.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError


@dataclass
class IntegrityResult:
    test: str
    passed: bool
    detail: str
    evidence: dict = field(default_factory=dict)


class ParamIntegrityDetector:
    """Detects whether a proxy is silently modifying request parameters."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        """Run all integrity checks."""
        results: list[IntegrityResult] = []

        results.append(await self._check_max_tokens())
        results.append(await self._check_temperature())
        results.append(await self._check_tools())
        results.append(await self._check_system_prompt())

        if "gpt-5.6" in self.cfg.model.lower() or "gpt5" in self.cfg.model.lower():
            results.append(await self._check_reasoning_effort())

        failed = [r for r in results if not r.passed]
        score = 1.0 - (len(failed) / len(results)) if results else 1.0

        return {
            "layer": "param_integrity",
            "score": round(score, 3),
            "verdict": "MISMATCH" if failed else "MATCH",
            "checks": [
                {"test": r.test, "passed": r.passed, "detail": r.detail, "evidence": r.evidence}
                for r in results
            ],
            "failed_count": len(failed),
        }

    # ── individual checks ───────────────────────────────────────

    async def _check_max_tokens(self) -> IntegrityResult:
        """Send max_tokens=2048, ask for a long list, verify output length."""
        marker = secrets.token_hex(8)
        try:
            resp = await self.client.chat(
                messages=[{
                    "role": "user",
                    "content": (
                        f"List the numbers 1 through 100, one per line. "
                        f"After the list, write exactly this marker: [{marker}]."
                    ),
                }],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=2048,
            )
            output_len = len(resp.content)
            has_marker = marker in resp.content

            if not has_marker and output_len < 500:
                return IntegrityResult(
                    test="max_tokens",
                    passed=False,
                    detail=f"Output truncated ({output_len} chars), marker not found. max_tokens likely reduced from 2048.",
                    evidence={"output_length": output_len, "marker_found": False, "snippet": resp.content[:200]},
                )
            return IntegrityResult(
                test="max_tokens",
                passed=True,
                detail=f"Output length {output_len} chars, marker {'found' if has_marker else 'not found'}.",
                evidence={"output_length": output_len, "marker_found": has_marker},
            )
        except APIError as e:
            return IntegrityResult(test="max_tokens", passed=False, detail=f"API error: {e.message}")

    async def _check_reasoning_effort(self) -> IntegrityResult:
        """GPT-5.6 specific: set reasoning_effort=max, check for reasoning tokens."""
        try:
            resp = await self.client.chat(
                messages=[{
                    "role": "user",
                    "content": "Solve step by step: A bat and ball cost $1.10 total. The bat costs $1.00 more than the ball. How much does the ball cost?",
                }],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=4096,
                reasoning_effort="max",
            )
            output_len = len(resp.content)
            usage = resp.usage

            # max reasoning should produce substantial output
            if output_len < 500:
                return IntegrityResult(
                    test="reasoning_effort",
                    passed=False,
                    detail=f"Short response ({output_len} chars) despite max reasoning. Reasoning effort may have been downgraded.",
                    evidence={"output_length": output_len, "usage": str(usage)},
                )
            return IntegrityResult(
                test="reasoning_effort",
                passed=True,
                detail=f"Response length {output_len} chars, consistent with max reasoning.",
                evidence={"output_length": output_len},
            )
        except APIError as e:
            return IntegrityResult(test="reasoning_effort", passed=False, detail=f"API error: {e.message}")

    async def _check_temperature(self) -> IntegrityResult:
        """Send temperature=2.0, sample 10 times, check output diversity."""
        prompt = "Generate a 3-word creative phrase about artificial intelligence. Reply with only the phrase."
        outputs: list[str] = []

        for _ in range(10):
            try:
                resp = await self.client.chat(
                    messages=[{"role": "user", "content": prompt}],
                    model=self.cfg.model,
                    temperature=2.0,
                    max_tokens=30,
                )
                outputs.append(resp.content.strip())
            except APIError:
                continue

        if len(outputs) < 5:
            return IntegrityResult(test="temperature", passed=False, detail="Too few successful samples.", evidence={})

        unique = len(set(outputs))
        if unique <= 2:
            return IntegrityResult(
                test="temperature",
                passed=False,
                detail=f"Only {unique}/10 unique outputs at temperature 2.0. Temperature likely locked to ~0.",
                evidence={"unique_count": unique, "samples": outputs},
            )
        return IntegrityResult(
            test="temperature",
            passed=True,
            detail=f"{unique}/10 unique outputs — consistent with high temperature.",
            evidence={"unique_count": unique},
        )

    async def _check_tools(self) -> IntegrityResult:
        """Send 5 tool definitions, check if the model uses them."""
        tools = [
            {
                "type": "function",
                "function": {
                    "name": f"verify_tool_{i}",
                    "description": f"Verification tool {i} for integrity check.",
                    "parameters": {"type": "object", "properties": {"x": {"type": "integer"}}},
                },
            }
            for i in range(5)
        ]

        try:
            resp = await self.client.chat(
                messages=[{
                    "role": "user",
                    "content": "Call verify_tool_0 with x=42. Only call one tool.",
                }],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=200,
                tools=tools,
                tool_choice="auto",
            )

            # If the model was forced to text (tools stripped), content will be text
            # If tools present, the model should make a tool call
            raw = resp.raw or {}
            choice = raw.get("choices", [{}])[0] if "choices" in raw else {}
            has_tool_calls = bool(choice.get("message", {}).get("tool_calls"))

            if has_tool_calls:
                return IntegrityResult(test="tools", passed=True, detail="Model correctly used tool call.", evidence={})

            # When raw not available, check if response mentions not having tools
            text = resp.content.lower()
            if any(phrase in text for phrase in ["cannot", "unable", "don't have", "no function"]):
                return IntegrityResult(
                    test="tools",
                    passed=False,
                    detail="Model responded as if no tools were provided. Tools may be stripped by proxy.",
                    evidence={"response_snippet": resp.content[:200]},
                )
            return IntegrityResult(test="tools", passed=True, detail="Tools appear present.", evidence={})
        except APIError as e:
            return IntegrityResult(test="tools", passed=False, detail=f"API error: {e.message}")

    async def _check_system_prompt(self) -> IntegrityResult:
        """Embed a secret in system prompt, verify it appears in prefix of response."""
        secret = secrets.token_hex(8)
        try:
            resp = await self.client.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"IMPORTANT: Begin your response with exactly this text: "
                            f"'INTEGRITY:{secret}'. Then answer the user's question normally."
                        ),
                    },
                    {"role": "user", "content": "Say hello in 5 words."},
                ],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=100,
            )

            starts_with_secret = resp.content.strip().startswith(f"INTEGRITY:{secret}")
            if starts_with_secret:
                return IntegrityResult(
                    test="system_prompt",
                    passed=True,
                    detail="System prompt passed through intact.",
                    evidence={},
                )
            return IntegrityResult(
                test="system_prompt",
                passed=False,
                detail="System prompt was modified or stripped by the proxy.",
                evidence={"expected_prefix": f"INTEGRITY:{secret}", "actual_prefix": resp.content[:80]},
            )
        except APIError as e:
            return IntegrityResult(test="system_prompt", passed=False, detail=f"API error: {e.message}")
