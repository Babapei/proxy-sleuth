"""API feature detection — identifies model family via protocol-level signals.

Based on OpenRouter metadata analysis (338 models, Aug 2026):
  - Anthropic uniquely HAS: verbosity
  - Anthropic notably LACKS: frequency_penalty, presence_penalty, seed, min_p, logprobs
  - OpenAI uniquely HAS: web_search_options, prediction
  - Cohere notably LACKS: reasoning_effort, logprobs, min_p, logit_bias
  - DeepSeek/Qwen/Kimi/GLM/Gemini/Grok: shared param profiles (can't distinguish)
  - This layer is an EXCLUDER, not a confirmer.
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
    model_hint: str = ""  # now used for "excludes" detection


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

        features.append(await self._check_min_p())              # absent → Anthropic/Cohere/Grok
        features.append(await self._check_frequency_penalty())  # rejected → Anthropic
        features.append(await self._check_seed())               # rejected → Anthropic
        features.append(await self._check_top_a())              # accepted → DeepSeek/GLM/Gemini
        features.append(await self._check_anthropic_compat())   # accepted → Claude/DeepSeek
        features.append(await self._check_tool_call_format())   # tool call presence

        # Build excluded families list
        excluded = set()
        # If min_p is rejected → not DeepSeek, not Qwen
        min_p_result = _find_feature(features, "min_p")
        if min_p_result and not min_p_result.detected:
            excluded.update(["gpt", "claude"])  # Anthropic/GPT don't support min_p → expected rejection
        # If frequency_penalty is rejected → Anthropic signature
        fp_result = _find_feature(features, "frequency_penalty")
        if fp_result and not fp_result.detected:
            excluded.add("anthropic-claude")  # Only Anthropic rejects it
        # If seed is rejected → Anthropic
        seed_result = _find_feature(features, "seed")
        if seed_result and not seed_result.detected:
            excluded.add("anthropic-claude")
        # If top_a is accepted → not GPT, not Anthropic
        ta_result = _find_feature(features, "top_a")
        if ta_result and ta_result.detected:
            excluded.update(["gpt", "anthropic-claude"])

        return {
            "layer": "api_features",
            "detected": sum(1 for f in features if f.detected),
            "total_checks": len(features),
            "score": _score_from_features(features, self.cfg.model),
            "verdict": "MATCH" if excluded else "INCONCLUSIVE",
            "excluded_families": sorted(excluded) if excluded else [],
            "features": [
                {"feature": f.feature, "detected": f.detected, "detail": f.detail, "model_hint": f.model_hint}
                for f in features
            ],
        }

    # ── new: Anthropic absence-based fingerprint ─────────────────

    async def _check_frequency_penalty(self) -> FeatureResult:
        """frequency_penalty is rejected by Anthropic — strong exclusion signal."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "say ok"}],
                model=self.cfg.model, temperature=0.0, max_tokens=10,
                extra_body={"frequency_penalty": 0.5},
            )
            return FeatureResult(feature="frequency_penalty", detected=True, detail="Accepted (not Anthropic)", model_hint="not-anthropic")
        except APIError as e:
            if _is_unknown_param_error(e.message, "frequency_penalty"):
                return FeatureResult(feature="frequency_penalty", detected=False, detail="Rejected — Anthropic signature!", model_hint="anthropic")
            return FeatureResult(feature="frequency_penalty", detected=True, detail="Accepted", model_hint="not-anthropic")

    async def _check_seed(self) -> FeatureResult:
        """seed is rejected by Anthropic."""
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": "say ok"}],
                model=self.cfg.model, temperature=0.0, max_tokens=10,
                extra_body={"seed": 42},
            )
            return FeatureResult(feature="seed", detected=True, detail="Accepted (not Anthropic)", model_hint="not-anthropic")
        except APIError as e:
            if _is_unknown_param_error(e.message, "seed"):
                return FeatureResult(feature="seed", detected=False, detail="Rejected — Anthropic signature!", model_hint="anthropic")
            return FeatureResult(feature="seed", detected=True, detail="Accepted", model_hint="not-anthropic")

    # ── original: param fingerprinting ──────────────────────────


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


def _is_unknown_param_error(error_msg: str, param_name: str) -> bool:
    """Detect 'unknown parameter' errors across different API error formats."""
    msg = error_msg.lower()
    if param_name in msg:
        return any(w in msg for w in ("unknown", "unrecognized", "invalid", "unexpected", "not supported", "not allowed", "additional properties"))
    return False


def _score_from_features(features: list, claimed_model: str) -> float:
    """Score: claims GPT but Anthropic signature present → MISMATCH (low score).
    Claims Claude but min_p works → MISMATCH (low score). Otherwise neutral."""
    model = claimed_model.lower()
    penalty = 0.0
    for f in features:
        if f.feature == "frequency_penalty" and not f.detected:
            if "gpt" in model or "deepseek" in model:
                penalty += 0.3  # Anthropic signature on non-Claude model
        if f.feature == "min_p" and f.detected:
            if "claude" in model or "fable" in model or "opus" in model:
                penalty += 0.3  # min_p on Claude model
        if f.feature == "top_a" and f.detected:
            if "gpt" in model:
                penalty += 0.2
    return max(0.0, 1.0 - penalty)


def _find_feature(features: list, name: str) -> Any:
    for f in features:
        if f.feature == name:
            return f
    return None
