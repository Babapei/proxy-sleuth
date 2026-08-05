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

        # Parameter fingerprinting — the only reliable API-level signals
        features.append(await self._check_min_p())              # DeepSeek-exclusive
        features.append(await self._check_top_a())              # DeepSeek-exclusive
        features.append(await self._check_include_reasoning())  # DeepSeek/Qwen vs GPT/Claude
        features.append(await self._check_anthropic_compat())   # Claude/DeepSeek vs GPT
        features.append(await self._check_tool_call_format())   # Tool call presence
        features.append(await self._check_stream_format())      # Streaming capability

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
            return FeatureResult(feature="min_p", detected=True, detail="min_p accepted — DeepSeek signature.", model_hint="deepseek")
        except APIError as e:
            if _is_unknown_param_error(e.message, "min_p"):
                return FeatureResult(feature="min_p", detected=False, detail="min_p rejected (not DeepSeek)", model_hint="gpt-5.x/claude")
            return FeatureResult(feature="min_p", detected=True, detail=f"Accepted (other error: {e.message[:60]})", model_hint="deepseek")

    async def _check_top_a(self) -> FeatureResult:
        """top_a is exclusive to DeepSeek V4."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "say ok"}],
                model=self.cfg.model, temperature=1.0, max_tokens=10,
                extra_body={"top_a": 0.5},
            )
            return FeatureResult(feature="top_a", detected=True, detail="top_a accepted — DeepSeek signature.", model_hint="deepseek")
        except APIError as e:
            if _is_unknown_param_error(e.message, "top_a"):
                return FeatureResult(feature="top_a", detected=False, detail="top_a rejected (not DeepSeek)", model_hint="gpt-5.x/claude")
            return FeatureResult(feature="top_a", detected=True, detail=f"Accepted", model_hint="deepseek")

    async def _check_include_reasoning(self) -> FeatureResult:
        """include_reasoning is supported by DeepSeek V4 and Qwen, not GPT/Claude."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "1+1=?"}],
                model=self.cfg.model, temperature=0.0, max_tokens=50,
                extra_body={"include_reasoning": True},
            )
            return FeatureResult(feature="include_reasoning", detected=True, detail="include_reasoning accepted — DeepSeek or Qwen.", model_hint="deepseek/qwen")
        except APIError as e:
            if _is_unknown_param_error(e.message, "include_reasoning"):
                return FeatureResult(feature="include_reasoning", detected=False, detail="include_reasoning rejected (likely GPT/Claude)", model_hint="gpt-5.x/claude")
            return FeatureResult(feature="include_reasoning", detected=True, detail=f"Accepted", model_hint="deepseek/qwen")

    # ── anthropic compat ────────────────────────────────────────

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


def _is_unknown_param_error(error_msg: str, param_name: str) -> bool:
    """Detect 'unknown parameter' errors across different API error formats.

    OpenAI: "Unknown parameter: 'min_p'"
    Anthropic: "Unrecognized request argument: min_p"
    Generic: "Invalid parameter", "additional properties", etc.
    """
    msg = error_msg.lower()
    if param_name in msg:
        if any(w in msg for w in ("unknown", "unrecognized", "invalid", "unexpected", "not supported", "not allowed", "additional properties")):
            return True
    return False
