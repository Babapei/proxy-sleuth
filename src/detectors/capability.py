"""Capability benchmark — hard problems that discriminate between model tiers."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from src.config import RunConfig
from src.utils.api_client import APIClient, APIError


@dataclass
class BenchResult:
    problem_id: str
    category: str
    passed: bool
    response: str = ""
    error: str | None = None


# Problems designed to be:
#  - Easy for GPT-5.5/Claude Fable 5  (90%+)
#  - Medium for DeepSeek V4 Pro       (60-80%)
#  - Hard for Qwen/Mid-tier           (30-50%)
BENCHMARKS = {
    "coding": [
        {
            "id": "code_lock_ordering",
            "prompt": "Write a Python solution for the classic 'dining philosophers' problem using only asyncio locks (no semaphores, no queues). Five philosophers, five forks. Prevent deadlock. Only the code, no explanation.",
            "check": lambda resp: "async" in resp and "Lock" in resp and ("acquire" in resp or "lock" in resp.lower()) and len(resp) > 100,
        },
        {
            "id": "code_concurrent_lru",
            "prompt": "Write a thread-safe LRU cache in Python with O(1) get and put using only the stdlib (no functools.lru_cache). Both methods must be threadsafe. Only the code.",
            "check": lambda resp: "OrderedDict" in resp and "thread" in resp.lower() and ("Lock" in resp or "RLock" in resp) and len(resp) > 80,
        },
    ],
    "math": [
        {
            "id": "math_monty_hall",
            "prompt": "You're on a game show. There are 3 doors. Behind one is a car, behind the others goats. You pick door 1. The host, who knows what's behind the doors, opens door 3 to reveal a goat. He asks if you want to switch to door 2. Should you switch? What is the probability of winning if you switch? Answer with just 'yes' or 'no' and the probability as a fraction.",
            "check": lambda resp: "yes" in resp.lower() and "2/3" in resp,
        },
        {
            "id": "math_birthday_paradox",
            "prompt": "In a room of 23 people, what is the approximate probability that at least two share a birthday? (Assume 365 days, all equally likely.) Give just the percentage rounded to nearest whole number.",
            "check": lambda resp: any(v in resp.replace("%", "").strip() for v in ("50", "51", "50.7")),
        },
    ],
    "reasoning": [
        {
            "id": "reason_liar_truth",
            "prompt": "On an island of knights (always tell truth) and knaves (always lie), you meet three inhabitants: A, B, and C. A says 'B is a knave.' B says 'A and C are the same type.' What are A, B, and C? Answer concisely.",
            "check": lambda resp: ("a is a knight" in resp.lower() or "a is knight" in resp.lower() or "a knight" in resp.lower()) and ("b" in resp.lower()) and ("c" in resp.lower()),
        },
        {
            "id": "reason_poisoned_wine",
            "prompt": "A king has 1000 bottles of wine. One is poisoned. He has 10 prisoners to test the wine. The poison takes 24 hours to kill. He needs to find the poisoned bottle within 24 hours. How can he do it? Explain the binary encoding approach concisely.",
            "check": lambda resp: "binary" in resp.lower() and ("prisoner" in resp.lower() or "10" in resp) and ("2^10" in resp or "1024" in resp or "1000" in resp),
        },
    ],
    "chinese": [
        {
            "id": "zh_classical",
            "prompt": "请解释'君子之交淡如水，小人之交甘若醴'的含义，并说明出处。",
            "check": lambda resp: "庄子" in resp and ("君子" in resp or "小人" in resp),
        },
        {
            "id": "zh_number_system",
            "prompt": "计算 (七十二 × 三十六) + 四百八十 等于多少？请写出计算过程然后给结果。",
            "check": lambda resp: "3072" in resp or "三千零七十二" in resp,
        },
    ],
}


class CapabilityDetector:
    """Tests model reasoning, coding, math, and Chinese at discriminating difficulty levels."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        all_results: list[BenchResult] = []
        by_category: dict[str, list[BenchResult]] = {}

        for category, problems in BENCHMARKS.items():
            cat_results = [await self._run_problem(p, category) for p in problems]
            by_category[category] = cat_results
            all_results.extend(cat_results)

        passed = sum(1 for r in all_results if r.passed)
        total = len(all_results)
        score = passed / total if total > 0 else 0

        return {
            "layer": "capability",
            "score": round(score, 3),
            "verdict": "MATCH" if score >= 0.5 else "MISMATCH",
            "total_passed": passed,
            "total_problems": total,
            "categories": {
                cat: {
                    "passed": sum(1 for r in results if r.passed),
                    "total": len(results),
                    "score": round(sum(1 for r in results if r.passed) / len(results), 2) if results else 0,
                }
                for cat, results in by_category.items()
            },
            "problems": [
                {"id": r.problem_id, "category": r.category, "passed": r.passed, "snippet": r.response[:150]}
                for r in all_results
            ],
        }

    async def _run_problem(self, prob: dict, category: str) -> BenchResult:
        try:
            resp = await self.client.chat(
                messages=[{"role": "user", "content": prob["prompt"]}],
                model=self.cfg.model,
                temperature=0.0,
                max_tokens=512,
            )
            passed = prob["check"](resp.content)
            return BenchResult(problem_id=prob["id"], category=category, passed=passed, response=resp.content)
        except APIError as e:
            return BenchResult(problem_id=prob["id"], category=category, passed=False, error=str(e))


def _number_in(text: str, values: list[str]) -> bool:
    return any(v in text for v in values)


def _last_number(text: str) -> str:
    nums = re.findall(r'\d+', text)
    return nums[-1] if nums else ""
