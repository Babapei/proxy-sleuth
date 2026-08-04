"""API feature detection — identifies model via protocol-level characteristics.

Checks: Programmatic Tool Calling support, Anthropic API compatibility,
tool call format, streaming format, reasoning effort parameter support.
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
    model_hint: str = ""  # Which model(s) this feature points to


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
        """Run all feature checks."""
        features: list[FeatureResult] = []

        features.append(await self._check_ptc())
        features.append(await self._check_anthropic_compat())
        features.append(await self._check_tool_call_format())
        features.append(await self._check_reasoning_effort_param())
        features.append(await self._check_stream_format())
        features.append(await self._check_model_self_report())

        detected = sum(1 for f in features if f.detected)

        # Build model hints
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
