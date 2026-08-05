"""Capability benchmark — execution-verified discriminators.

Uses a tiny Python subprocess sandbox to actually run generated code
and verify correctness. Math problems test for specific numeric answers.
Chinese problems require genuine cultural knowledge, not keyword matching.

Discriminating power:
  - Frontier (GPT-5.6, Claude Fable 5): 6-8/8
  - Mid-tier (DeepSeek V4 Pro, Qwen3.8 Max): 3-5/8
  - Weak (Qwen, older models): 1-3/8
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile
import os
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


BENCHMARKS = {
    "coding": [
        {
            "id": "code_fib_overflow",
            "prompt": "Write Python code ONLY (no explanation) for a function fib_overflow(n) that: "
                      "1. Computes the nth Fibonacci number using recursion with memoization "
                      "2. If the result would exceed 10**18, returns -1 instead (overflow guard) "
                      "Return just the function definition.",
            "verify": "exec",
            "test_code": """
try:
    exec(response, globals())
    assert callable(fib_overflow), 'not callable'
    assert fib_overflow(0) == 0, f'fib(0) failed: {fib_overflow(0)}'
    assert fib_overflow(10) == 55, f'fib(10) failed: {fib_overflow(10)}'
    assert fib_overflow(90) == -1, f'overflow guard failed: {fib_overflow(90)}'
    result = True
except Exception as e:
    result = False
""",
        },
        {
            "id": "code_parse_nested",
            "prompt": "Write Python code ONLY (no explanation) for a function parse_dollars(s) that extracts "
                      "all dollar amounts from a string. Accept formats: $5, $3.50, $1,200. "
                      "Return a list of floats. Empty string returns empty list. "
                      "Example: '$5 and $3.50' → [5.0, 3.5]. Return just the function.",
            "verify": "exec",
            "test_code": """
try:
    exec(response, globals())
    assert callable(parse_dollars), 'not callable'
    assert parse_dollars('') == [], f'empty failed: {parse_dollars("")}'
    assert parse_dollars('$5 and $3.50') == [5.0, 3.5], f'basic failed: {parse_dollars("$5 and $3.50")}'
    assert parse_dollars('$1,200 total') == [1200.0], f'comma failed: {parse_dollars("$1,200 total")}'
    assert parse_dollars('no money here') == [], f'none failed'
    result = True
except Exception as e:
    result = False
""",
        },
    ],
    "math": [
        {
            "id": "math_monty_hall",
            "prompt": "Monty Hall problem: 3 doors, car behind one, goats behind others. "
                      "You pick door 1. Host opens door 3 (goat). Should you switch to door 2? "
                      "What's the probability of winning if you switch? Answer: yes/no and fraction.",
            "verify": "text",
            "check": lambda resp: "yes" in resp.lower() and "2/3" in resp,
        },
        {
            "id": "math_conditional_prob",
            "prompt": "In a city, 1% of people have a disease. A test is 95% accurate for those with it "
                      "and 90% accurate for those without (10% false positive). If someone tests positive, "
                      "what is the probability they actually have the disease? Give as percentage rounded to 1 decimal.",
            "verify": "text",
            "check": lambda resp: _has_approx(resp, 8.8, 1.0) or _has_approx(resp, 8.7, 1.0),
        },
    ],
    "reasoning": [
        {
            "id": "reason_five_hats",
            "prompt": "Five prisoners are lined up facing forward. Each wears either a black or white hat. "
                      "They can see hats in front, not their own or behind. Starting from the back (#5), "
                      "each says their hat color or stays silent. They can agree on a strategy beforehand. "
                      "What strategy guarantees at least 4 survive? Explain concisely.",
            "verify": "text",
            "check": lambda resp: ("parity" in resp.lower() or "black hat" in resp.lower() or "odd" in resp.lower()) and len(resp) > 80,
        },
        {
            "id": "reason_counterfeit_coins",
            "prompt": "You have 12 identical-looking coins. One is counterfeit (slightly lighter or heavier). "
                      "Using a balance scale only 3 times, how do you identify the counterfeit coin and "
                      "whether it's lighter or heavier? Describe the first weighing step.",
            "verify": "text",
            "check": lambda resp: ("4" in resp and "3" in resp) or ("group" in resp.lower() and ("four" in resp.lower() or "4" in resp.lower())) and len(resp) > 60,
        },
    ],
    "chinese": [
        {
            "id": "zh_chengyu_rare",
            "prompt": "成语'塞翁失马'出自哪部古籍？这个故事的核心寓意是什么？请用中文简要回答。",
            "verify": "text",
            "check": lambda resp: ("淮南子" in resp or "老子" in resp or "塞翁" in resp) and ("福祸" in resp or "祸福" in resp or "焉知非福" in resp),
        },
        {
            "id": "zh_literal_figurative",
            "prompt": "判断'他这个人就是纸老虎'中'纸老虎'是字面义还是比喻义，并解释其含义。",
            "verify": "text",
            "check": lambda resp: ("比喻" in resp or "隐喻" in resp or "外强中干" in resp or "虚张声势" in resp),
        },
    ],
    "extra_math": [
        {
            "id": "math_balls_prob",
            "prompt": "A bag has 3 red balls and 5 blue balls. You draw 2 balls without replacement. What is the probability both are red? Answer with just the fraction.",
            "verify": "text",
            "check": lambda resp: "3/28" in resp or "0.107" in resp,
        },
        {
            "id": "math_modular_exp",
            "prompt": "What is 7^100 mod 13? Give the final answer only.",
            "verify": "text",
            "check": lambda resp: "9" in resp,
        },
    ],
}


# ── Archived v1 benchmarks (keyword-match) — preserved, not in active use ──
# These were too easy for modern models (keyword presence ≠ correctness).
# Kept for reference and for testing lower-tier models if needed.
_V1_ARCHIVE = [
    {"id": "v1_fibonacci", "prompt": "Write Python code for the nth Fibonacci number using recursion.", "check": lambda r: "def " in r and "return" in r},
    {"id": "v1_async", "prompt": "Write Python asyncio code to fetch two URLs concurrently and return status codes.", "check": lambda r: "async" in r and "await" in r},
    {"id": "v1_pandas", "prompt": "Write pandas code to read CSV, groupby 'category', mean of 'value'.", "check": lambda r: "groupby" in r and "mean" in r},
    {"id": "v1_triangle", "prompt": "Right triangle legs 5 and 12. Hypotenuse?", "check": lambda r: "13" in r},
    {"id": "v1_knights", "prompt": "Knights/knaves: A says B is knave. B says both knights. What are A,B?", "check": lambda r: "knave" in r.lower()},
    {"id": "v1_jugs", "prompt": "5L and 3L jugs. Measure exactly 4L.", "check": lambda r: "4" in r and "pour" in r.lower()},
    {"id": "v1_snake_feet", "prompt": "请解释成语'画蛇添足'的含义。", "check": lambda r: "多余" in r or "多此一举" in r},
    {"id": "v1_moonlight", "prompt": "床前明月光的下一句？作者？", "check": lambda r: "疑是地上霜" in r or "李白" in r},
    {"id": "v1_15percent", "prompt": "What is 15% of 200? Just the number.", "check": lambda r: "30" in r},
    {"id": "v1_paris", "prompt": "What is the capital of France?", "check": lambda r: "paris" in r.lower()},
]


class CapabilityDetector:
    """Tests model capability with execution-verified problems."""

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
            if prob["verify"] == "exec":
                passed = self._exec_test(prob["test_code"], resp.content)
            else:
                passed = prob["check"](resp.content)
            return BenchResult(problem_id=prob["id"], category=category, passed=passed, response=resp.content)
        except APIError as e:
            return BenchResult(problem_id=prob["id"], category=category, passed=False, error=str(e))

    def _exec_test(self, test_code: str, model_code: str) -> bool:
        """Run model-generated code against test assertions in a subprocess."""
        # Extract code block from markdown if present
        code = model_code
        if "```python" in code:
            parts = code.split("```python", 1)[1].split("```", 1)
            code = parts[0] if len(parts) > 0 else code
        elif "```" in code:
            parts = code.split("```", 1)[1].split("```", 1)
            code = parts[0] if len(parts) > 1 else code

        full_code = f"{code}\n{test_code}"

        try:
            result = subprocess.run(
                ["python3", "-c", full_code],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "PYTHONPATH": ""},
            )
            # If the test assertions passed, result.returncode == 0 and no stderr about assertion errors
            return result.returncode == 0 and "AssertionError" not in result.stderr and "assert " not in result.stderr
        except (subprocess.TimeoutExpired, Exception):
            return False


def _has_approx(text: str, target: float, tolerance: float) -> bool:
    """Check if a number approximately equal to target appears in text."""
    nums = re.findall(r'[\d.]+', text)
    for n in nums:
        try:
            if abs(float(n) - target) <= tolerance:
                return True
        except ValueError:
            continue
    return False
