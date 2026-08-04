"""Capability benchmark — tests actual reasoning/coding/math/chinese ability."""

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


# High-discrimination benchmark problems
BENCHMARKS = {
    "coding": [
        {
            "id": "code_recursive_fib",
            "prompt": "Write Python code for a function that returns the nth Fibonacci number using recursion. Include the function only, no explanation.",
            "check": lambda resp: "def " in resp and "return" in resp and ("fib" in resp.lower() or "n-1" in resp or "n-2" in resp),
        },
        {
            "id": "code_async_fetch",
            "prompt": "Write Python code using asyncio and aiohttp to fetch two URLs concurrently and return their status codes. Only the function, no explanation.",
            "check": lambda resp: "async" in resp and ("aiohttp" in resp or "httpx" in resp or "await" in resp),
        },
        {
            "id": "code_pandas_groupby",
            "prompt": "Write Python pandas code to read a CSV, group by column 'category', and compute mean of column 'value'. Only the code, no explanation.",
            "check": lambda resp: "groupby" in resp and "mean" in resp and ("pd" in resp or "pandas" in resp or "read_csv" in resp),
        },
    ],
    "math": [
        {
            "id": "math_probability",
            "prompt": "A bag has 3 red balls and 5 blue balls. You draw 2 balls without replacement. What is the probability both are red? Answer with just the fraction or decimal.",
            "check": lambda resp: _number_in(resp, ["3/28", "0.107", "10.7%", "0.11"]),
        },
        {
            "id": "math_modular",
            "prompt": "What is 7^100 mod 13? Show your work briefly, then give the final answer.",
            "check": lambda resp: "9" in _last_number(resp) if _last_number(resp) else False,
        },
        {
            "id": "math_geometry",
            "prompt": "A right triangle has legs of length 5 and 12. What is the length of the hypotenuse? Answer with just the number.",
            "check": lambda resp: "13" in resp,
        },
    ],
    "reasoning": [
        {
            "id": "reason_knights",
            "prompt": "On an island, knights always tell the truth and knaves always lie. A says 'B is a knave.' B says 'We are both knights.' What are A and B? Answer briefly.",
            "check": lambda resp: "knave" in resp.lower() or ("a is" in resp.lower() and "b is" in resp.lower()),
        },
        {
            "id": "reason_wine",
            "prompt": "You have a 5L jug and a 3L jug. How do you measure exactly 4 liters? Describe the steps concisely.",
            "check": lambda resp: "4" in resp and ("fill" in resp.lower() or "pour" in resp.lower() or "jug" in resp.lower()),
        },
    ],
    "chinese": [
        {
            "id": "zh_idiom",
            "prompt": "请解释成语'画蛇添足'的含义，并用一句话举例。",
            "check": lambda resp: "蛇" in resp and ("多余" in resp or "多此一举" in resp or "不必要" in resp),
        },
        {
            "id": "zh_poem",
            "prompt": "'床前明月光'的下一句是什么？并说出这首诗的作者。",
            "check": lambda resp: ("疑是地上霜" in resp or "李白" in resp),
        },
    ],
}


class CapabilityDetector:
    """Tests model reasoning, coding, math, and Chinese capability."""

    def __init__(self, cfg: RunConfig):
        self.cfg = cfg
        self.client = APIClient(
            base_url=cfg.endpoint,
            api_key=cfg.resolve_api_key(),
            protocol=cfg.protocol,
            timeout=cfg.timeout,
        )

    async def run(self) -> dict[str, Any]:
        """Run all benchmark categories."""
        all_results: list[BenchResult] = []
        by_category: dict[str, list[BenchResult]] = {}

        for category, problems in BENCHMARKS.items():
            cat_results: list[BenchResult] = []
            for prob in problems:
                result = await self._run_problem(prob, category)
                cat_results.append(result)
                all_results.append(result)
            by_category[category] = cat_results

        passed = sum(1 for r in all_results if r.passed)
        total = len(all_results)
        score = passed / total if total > 0 else 0

        return {
            "layer": "capability",
            "score": round(score, 3),
            "verdict": "MATCH" if score >= 0.6 else "MISMATCH",
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
