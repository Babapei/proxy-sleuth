"""Knowledge boundary probe engine — the most reliable detection layer."""

from __future__ import annotations

import json
import re
import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import PROMPTS_DIR, RunConfig
from src.utils.api_client import APIClient, ChatResponse, APIError

PROBES_PATH = PROMPTS_DIR / "knowledge_probes.json"


@dataclass
class ProbeResult:
    probe_id: str
    group: str
    question: str
    response: str
    keywords_matched: list[str]
    score: float  # 0.0 ~ 1.0
    duration_ms: float
    error: str | None = None


@dataclass
class GroupResult:
    group: str
    description: str
    score: float
    expected: bool
    probes: list[ProbeResult] = field(default_factory=list)


class KnowledgeProbeEngine:
    """Runs knowledge boundary probes and scores model identification confidence."""

    REPEAT_COUNT = 3
    MATCH_THRESHOLD = 0.70
    MISMATCH_THRESHOLD = 0.40

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )
        with open(PROBES_PATH) as f:
            self.probes_data = json.load(f)

    async def run(self) -> dict[str, Any]:
        """Run all knowledge probe groups and return a structured report."""
        group_results: list[GroupResult] = []
        total_start = asyncio.get_event_loop().time()

        for group_name, group_data in self.probes_data["probe_groups"].items():
            expected = self._should_model_know(group_name)
            result = await self._run_group(group_name, group_data, expected)
            group_results.append(result)

        overall = self._compute_overall(group_results)
        duration = (asyncio.get_event_loop().time() - total_start) * 1000
        total_probes = sum(len(g.probes) for g in group_results) * self.REPEAT_COUNT

        return {
            "layer": "knowledge_probes",
            "claimed_model": self.cfg.model,
            "groups": [self._group_to_dict(g) for g in group_results],
            "overall_score": round(overall, 3),
            "verdict": self._verdict_label(overall),
            "total_requests": total_probes,
            "total_duration_ms": round(duration, 0),
        }

    async def _run_group(self, name: str, data: dict, expected: bool) -> GroupResult:
        result = GroupResult(group=name, description=data["description"], expected=expected, score=0.0)
        tasks = [self._probe_with_retries(p, name) for p in data["probes"]]
        result.probes = await asyncio.gather(*tasks)
        valid = [p.score for p in result.probes if p.error is None]
        result.score = sum(valid) / len(valid) if valid else 0.0
        return result

    async def _probe_with_retries(self, probe: dict, group: str) -> ProbeResult:
        scores: list[float] = []
        all_keywords: list[str] = []
        response_text = ""
        duration = 0.0
        error = None

        for attempt in range(self.REPEAT_COUNT):
            question_variant = self._variant(probe["question"], attempt)
            try:
                resp = await self.client.chat(
                    messages=[
                        {"role": "system", "content": "Answer concisely and factually. If you don't know, say so."},
                        {"role": "user", "content": question_variant},
                    ],
                    model=self.cfg.model,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                )
                keywords = probe["keywords"]
                matched = [kw for kw in keywords if _match_keyword(kw, resp.content)]
                score = len(matched) / len(keywords) if keywords else 0.0
                scores.append(score)
                all_keywords.extend(matched)
                response_text = resp.content
                duration = resp.duration_ms
            except APIError as e:
                error = str(e)
                scores.append(0.0)

        avg_score = sum(scores) / len(scores) if scores else 0.0
        return ProbeResult(
            probe_id=probe["id"],
            group=group,
            question=probe["question"],
            response=response_text,
            keywords_matched=list(set(all_keywords)),
            score=round(avg_score * probe.get("weight", 1.0), 3),
            duration_ms=duration,
            error=error,
        )

    def _should_model_know(self, group_name: str) -> bool:
        """Determine whether the claimed model should know this probe group."""
        model = self.cfg.model.lower()
        if group_name == "gpt56_only":
            return "gpt-5.6" in model or "gpt5.6" in model
        if group_name == "fable5":
            return "fable" in model
        if group_name == "deepseek":
            return "deepseek" in model and "v4" in model
        if group_name == "reverse":
            return True
        # llm-verify groups: identity and capability for all models
        if group_name.startswith("llm_verify_"):
            return True
        # "self" groups: only the named model should know, others → suspicious if they do
        if group_name == "kimi_self":
            return "kimi" in model
        if group_name == "qwen_self":
            return "qwen" in model
        if group_name == "glm_self":
            return "glm" in model
        if group_name == "grok_self":
            return "grok" in model
        if group_name == "minimax_self":
            return "minimax" in model
        if group_name == "gemini_self":
            return "gemini" in model
        if group_name == "mistral_self":
            return "mistral" in model
        if group_name == "llama_self":
            return "llama" in model
        if group_name == "hunyuan_self":
            return "hunyuan" in model or "tencent" in model
        return False

    def _variant(self, question: str, attempt: int) -> str:
        if attempt == 0:
            return question
        prefixes = ["Please tell me: ", "I need to know: ", "Can you answer this: "]
        return prefixes[min(attempt - 1, len(prefixes) - 1)] + question

    def _compute_overall(self, group_results: list[GroupResult]) -> float:
        """Weighted score with penalty for knowing things the model shouldn't.

        Expected groups (reverse, llm_verify_*): high score = good (0.35 weight)
        Unexpected groups with HIGH score → penalty (overknowledge is suspicious)
        Unexpected groups with LOW score → normal (0.15 weight)
        """
        if not group_results:
            return 0.0
        total_w, weighted_sum = 0.0, 0.0

        for g in group_results:
            if g.expected:
                w = 0.35
                weighted_sum += g.score * w
            else:
                if g.score > 0.5:
                    penalty = (g.score - 0.5) * 0.3
                    weighted_sum -= penalty
                w = 0.15
                weighted_sum += g.score * w
            total_w += w

        base = weighted_sum / total_w if total_w > 0 else 0.0
        return max(0.0, min(1.0, base))

    def _verdict_label(self, score: float) -> str:
        if score >= self.MATCH_THRESHOLD:
            return "MATCH"
        if score <= self.MISMATCH_THRESHOLD:
            return "MISMATCH"
        return "INCONCLUSIVE"

    def _group_to_dict(self, g: GroupResult) -> dict:
        return {
            "group": g.group,
            "description": g.description,
            "expected": g.expected,
            "score": round(g.score, 3),
            "probes": [
                {
                    "id": p.probe_id,
                    "score": p.score,
                    "keywords_matched": p.keywords_matched,
                    "response_snippet": p.response[:200],
                    "error": p.error,
                }
                for p in g.probes
            ],
        }


def _match_keyword(keyword: str, text: str) -> bool:
    """Match keyword against text — all space-separated words must appear in the text.

    Args:
        keyword: Keyword to match, e.g. "may 2024" requires both words present.
        text: Model's response text.
    Returns:
        True if all words from keyword are found somewhere in text.
    """
    text_lower = text.lower()
    for word in keyword.lower().split():
        if not re.search(re.escape(word), text_lower):
            return False
    return True
