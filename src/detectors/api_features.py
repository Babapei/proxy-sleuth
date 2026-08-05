"""API feature detection — identifies model via protocol-level characteristics.

Checks: unique API parameters (min_p, top_a: DeepSeek-only),
Programmatic Tool Calling, Anthropic compatibility, tool call format,
streaming format, reasoning effort support.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, Protocol, APIError


@dataclass
class FeatureResult:
    feature: str
    detected: bool
    detail: str
    model_hint: str = ""


class APIFeaturesDetector:
    """Identifies model family via API-level feature probing."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        features: list[FeatureResult] = []

        features.append(await self._check_min_p())           # DeepSeek-only
        features.append(await self._check_top_a())           # DeepSeek-only
        features.append(await self._check_include_reasoning())  # DeepSeek/Qwen
        features.append(await self._check_ptc())
        features.append(await self._check_anthropic_compat())
        features.append(await self._check_tool_call_format())
        features.append(await self._check_reasoning_effort_param())
        features.append(await self._check_stream_format())
        features.append(await self._check_model_self_report())

        detected = sum(1 for f in features if f.detected)
        hints: dict[str, int] = {}
        for f in features:
            if f.model_hint:
                hints[f.model_hint] = hints.get(f.model_hint, 0) + 1
        best_guess = max(hints, key=hints.get) if hints else "unknown"

        return {
            "layer": "api_features",
            "score": round(detected / len(features), 3) if features else 0,
            "verdict": "MATCH" if best_guess in self.cfg.model.lower() else "INCONCLUSIVE",
            "best_model_guess": best_guess,
            "features": [
                {"feature": f.feature, "detected": f.detected, "detail": f.detail, "model_hint": f.model_hint}
                for f in features
            ],
        }

    # ── new: parameter-based fingerprint (Aug 2026) ──────────────

    async def _check_min_p(self) -> FeatureResult:
        """min_p is exclusive to DeepSeek V4 — not supported by GPT or Claude."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "say ok"}],
                model=self.cfg.model, temperature=1.0, max_tokens=10,
                extra_body={"min_p": 0.05},
            )
            return FeatureResult(feature="min_p", detected=True, detail="min_p parameter accepted — DeepSeek signature.", model_hint="deepseek")
        except APIError as e:
            if "min_p" in str(e).lower() or "unknown" in str(e).lower() or "unrecognized" in str(e).lower():
                return FeatureResult(feature="min_p", detected=False, detail="min_p rejected (not DeepSeek)", model_hint="gpt-5.x/claude")
            return FeatureResult(feature="min_p", detected=True, detail=f"Accepted (or other error: {e.message[:60]})", model_hint="deepseek")

    async def _check_top_a(self) -> FeatureResult:
        """top_a is exclusive to DeepSeek V4."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "say ok"}],
                model=self.cfg.model, temperature=1.0, max_tokens=10,
                extra_body={"top_a": 0.5},
            )
            return FeatureResult(feature="top_a", detected=True, detail="top_a parameter accepted — DeepSeek signature.", model_hint="deepseek")
        except APIError as e:
            if "top_a" in str(e).lower() or "unknown" in str(e).lower() or "unrecognized" in str(e).lower():
                return FeatureResult(feature="top_a", detected=False, detail="top_a rejected (not DeepSeek)", model_hint="gpt-5.x/claude")
            return FeatureResult(feature="top_a", detected=True, detail=f"Accepted", model_hint="deepseek")

    async def _check_include_reasoning(self) -> FeatureResult:
        """include_reasoning is supported by DeepSeek V4 and Qwen3.8, not GPT/Claude."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "1+1=?"}],
                model=self.cfg.model, temperature=0.0, max_tokens=50,
                extra_body={"include_reasoning": True},
            )
            return FeatureResult(feature="include_reasoning", detected=True, detail="include_reasoning accepted — DeepSeek or Qwen.", model_hint="deepseek/qwen")
        except APIError as e:
            if "include_reasoning" in str(e).lower() or "unknown" in str(e).lower():
                return FeatureResult(feature="include_reasoning", detected=False, detail="include_reasoning rejected (likely GPT/Claude)", model_hint="gpt-5.x/claude")
            return FeatureResult(feature="include_reasoning", detected=True, detail=f"Accepted", model_hint="deepseek/qwen")

    # ── individual checks ───────────────────────────────────────

    async def _check_ptc(self) -> FeatureResult:
        """Check for Programmatic Tool Calling (GPT-5.6 exclusive)."""
        try:
            resp = await self.client.chat(
                messages=[{
                    "role": "user",
                    "content": "Write a Python program that calculates factorial of 5 and return the result.",
                }],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=200,
                extra_body={"tools": [{"type": "code_interpreter"}]},
            )
            # If the model understands code_interpreter tool, it's likely GPT-5.6
            raw = resp.raw or {}
            if "code_interpreter" in str(raw).lower() or "python" in resp.content.lower():
                return FeatureResult(feature="ptc", detected=True, detail="Model responds to code_interpreter tool.", model_hint="gpt-5.6")
            return FeatureResult(feature="ptc", detected=False, detail="No PTC evidence.", model_hint="not-gpt-5.6")
        except APIError:
            return FeatureResult(feature="ptc", detected=False, detail="API does not support code_interpreter tool.", model_hint="not-gpt-5.6")

    async def _check_anthropic_compat(self) -> FeatureResult:
        """Check if the endpoint accepts Anthropic-format requests."""
        try:
            anthropic_client = APIClient(
                base_url=self.cfg.endpoint,
                api_key=self.cfg.resolve_api_key(),
                protocol=Protocol.ANTHROPIC,
                timeout=self.cfg.timeout,
            )
            resp = await anthropic_client.chat(
                messages=[{"role": "user", "content": "Say hello."}],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=20,
            )
            if resp.content:
                return FeatureResult(feature="anthropic_compat", detected=True, detail="Endpoint accepts Anthropic Messages format.", model_hint="claude/deepseek")
            return FeatureResult(feature="anthropic_compat", detected=False, detail="Anthropic request failed.", model_hint="not-claude")
        except APIError:
            return FeatureResult(feature="anthropic_compat", detected=False, detail="Anthropic protocol not supported.", model_hint="gpt-5.6")

    async def _check_tool_call_format(self) -> FeatureResult:
        """Check tool call response format characteristics."""
        tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather for a city.",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }]
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "What's the weather in Beijing?"}],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=200,
                tools=tools,
                tool_choice="auto",
            )
            raw = resp.raw or {}
            choice = raw.get("choices", [{}])[0] if "choices" in raw else {}
            msg = choice.get("message", {})

            if msg.get("tool_calls"):
                tc = msg["tool_calls"][0]
                fn_name = tc.get("function", {}).get("name", "")
                return FeatureResult(
                    feature="tool_call_format",
                    detected=True,
                    detail=f"Tool call produced: {fn_name}",
                    model_hint="",
                )
            return FeatureResult(feature="tool_call_format", detected=True, detail="Model chose text over tool call.", model_hint="")
        except APIError:
            return FeatureResult(feature="tool_call_format", detected=False, detail="Tool calling not supported.", model_hint="")

    async def _check_reasoning_effort_param(self) -> FeatureResult:
        """Check if the endpoint respects the reasoning_effort parameter."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "1+1=?"}],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=200,
                reasoning_effort="low",
            )
            # If it accepted the param, it's likely GPT-5.x
            return FeatureResult(feature="reasoning_effort", detected=True, detail="Endpoint accepted reasoning_effort parameter.", model_hint="gpt-5.x")
        except APIError as e:
            if "reasoning_effort" in str(e).lower() or "unknown" in str(e).lower():
                return FeatureResult(feature="reasoning_effort", detected=False, detail="reasoning_effort rejected.", model_hint="not-gpt-5.x")
            return FeatureResult(feature="reasoning_effort", detected=False, detail=f"API error: {e.message}", model_hint="")

    async def _check_stream_format(self) -> FeatureResult:
        """Check streaming SSE format characteristics."""
        try:
            chunks: list[str] = []
            async for chunk in self.client.chat_stream(
                messages=[{"role": "user", "content": "Count from 1 to 5."}],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=100,
            ):
                chunks.append(chunk.content)
                if chunk.finish_reason:
                    break

            if chunks:
                return FeatureResult(feature="stream_format", detected=True, detail=f"Streaming works, received {len(chunks)} chunks.", model_hint="")
            return FeatureResult(feature="stream_format", detected=False, detail="No streaming chunks received.", model_hint="")
        except APIError:
            return FeatureResult(feature="stream_format", detected=False, detail="Streaming not supported.", model_hint="")

    async def _check_model_self_report(self) -> FeatureResult:
        """Ask the model to identify itself — unreliable but informative."""
        try:
            resp = await self.client.chat(
                messages=[{
                    "role": "system",
                    "content": "Reply only with the exact model name and version. No other text.",
                }, {
                    "role": "user",
                    "content": "What model are you?",
                }],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=50,
            )
            content = resp.content.strip().lower()
            hints: dict[str, str] = {
                "gpt": "gpt-5.6",
                "claude": "claude",
                "deepseek": "deepseek",
                "qwen": "qwen",
            }
            for key, hint in hints.items():
                if key in content:
                    return FeatureResult(feature="self_report", detected=True, detail=f"Self-reports as '{content[:60]}'", model_hint=hint)
            return FeatureResult(feature="self_report", detected=False, detail=f"No clear self-identification: '{content[:60]}'", model_hint="")
        except APIError:
            return FeatureResult(feature="self_report", detected=False, detail="API error.", model_hint="")
