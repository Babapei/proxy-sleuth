"""Capability benchmark — REAL HumanEval + MATH problems with actual test cases."""

from __future__ import annotations

import asyncio
import re
import subprocess
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
    # ── REAL HumanEval problems (from openai/human-eval) ────────
    "coding": [
        {
            "id": "heval_12",
            "source": "HumanEval/12 (easy, GPT-5.5 pass@1: ~95%, DS V4: ~90%)",
            "prompt": (
                "from typing import List, Optional\n\n"
                "def longest(strings: List[str]) -> Optional[str]:\n"
                '    """ Out of list of strings, return the longest one. Return the first one in case of multiple\n'
                "    strings of the same length. Return None in case the input list is empty.\n"
                "    >>> longest([])\n"
                "    >>> longest(['a', 'b', 'c'])\n"
                "    'a'\n"
                "    >>> longest(['a', 'bb', 'ccc'])\n"
                "    'ccc'\n"
                '    """\n'
            ),
            "entry": "longest",
            "verify": "exec",
            "test_code": (
                "assert candidate([]) == None\n"
                "assert candidate(['x', 'y', 'z']) == 'x'\n"
                "assert candidate(['x', 'yyy', 'zzzz', 'www', 'kkkk', 'abc']) == 'zzzz'\n"
            ),
        },
        {
            "id": "heval_32",
            "source": "HumanEval/32 (medium, GPT-5.5 pass@1: ~80%, DS V4: ~65%)",
            "prompt": (
                "import math\n\n"
                "def poly(xs: list, x: float):\n"
                '    """\n'
                "    Evaluates polynomial with coefficients xs at point x.\n"
                "    return xs[0] + xs[1] * x + xs[1] * x^2 + .... xs[n] * x^n\n"
                '    """\n'
                "    return sum([coeff * math.pow(x, i) for i, coeff in enumerate(xs)])\n\n\n"
                "def find_zero(xs: list):\n"
                '    """ xs are coefficients of a polynomial.\n'
                "    find_zero find x such that poly(x) = 0.\n"
                "    find_zero returns only only zero point, even if there are many.\n"
                "    Moreover, find_zero only takes list xs having even number of coefficients\n"
                "    and largest non zero coefficient as it guarantees a solution.\n"
                "    >>> round(find_zero([1, 2]), 2) # f(x) = 1 + 2x\n"
                "    -0.5\n"
                "    >>> round(find_zero([-6, 11, -6, 1]), 2)\n"
                "    1.0\n"
                '    """\n'
            ),
            "entry": "find_zero",
            "verify": "exec",
            "test_code": (
                "import math, random\n"
                "rng = random.Random(42)\n"
                "for _ in range(20):\n"
                "    ncoeff = 2 * rng.randint(1, 4)\n"
                "    coeffs = []\n"
                "    for _ in range(ncoeff):\n"
                "        coeff = rng.randint(-10, 10)\n"
                "        if coeff == 0:\n"
                "            coeff = 1\n"
                "        coeffs.append(coeff)\n"
                "    solution = candidate(coeffs[:])\n"
                "    assert math.fabs(poly(coeffs, solution)) < 1e-4\n"
            ),
        },
        {
            "id": "heval_123",
            "source": "HumanEval/123 (medium, GPT-5.5 pass@1: ~75%, DS V4: ~55%)",
            "prompt": (
                "def get_odd_collatz(n):\n"
                '    """\n'
                "    Given a positive integer n, return a sorted list that has the odd numbers in collatz sequence.\n"
                "    The Collatz conjecture: start with any positive integer n. Then each term is obtained from the\n"
                "    previous term as follows: if the previous term is even, the next term is one half of the\n"
                "    previous term. If the previous term is odd, the next term is 3 times the previous\n"
                "    term plus 1. The conjecture is that no matter what value of n, the sequence will always reach 1.\n"
                "    Note: 1. Collatz(1) is [1]. 2. returned list sorted in increasing order.\n"
                "    For example: get_odd_collatz(5) returns [1, 5]\n"
                '    """\n'
            ),
            "entry": "get_odd_collatz",
            "verify": "exec",
            "test_code": (
                "assert candidate(14) == [1, 5, 7, 11, 13, 17]\n"
                "assert candidate(5) == [1, 5]\n"
                "assert candidate(12) == [1, 3, 5]\n"
                "assert candidate(1) == [1]\n"
            ),
        },
        {
            "id": "heval_40",
            "source": "HumanEval/40 (medium, GPT-5.5 pass@1: ~78%, DS V4: ~62%)",
            "prompt": (
                "def triples_sum_to_zero(l: list):\n"
                '    """\n'
                "    triples_sum_to_zero takes a list of integers as an input.\n"
                "    it returns True if there are three distinct elements in the list that\n"
                "    sum to zero, and False otherwise.\n"
                "    >>> triples_sum_to_zero([1, 3, 5, 0])\n"
                "    False\n"
                "    >>> triples_sum_to_zero([1, 3, -2, 1])\n"
                "    True\n"
                "    >>> triples_sum_to_zero([1, 2, 3, 7])\n"
                "    False\n"
                "    >>> triples_sum_to_zero([1])\n"
                "    False\n"
                '    """\n'
            ),
            "entry": "triples_sum_to_zero",
            "verify": "exec",
            "test_code": (
                "assert candidate([1, 3, 5, 0]) == False\n"
                "assert candidate([1, 3, 5, -1]) == False\n"
                "assert candidate([1, 3, -2, 1]) == True\n"
                "assert candidate([1, 2, 3, 7]) == False\n"
                "assert candidate([1, 2, 5, 7]) == False\n"
                "assert candidate([2, 4, -5, 3, 9, 7]) == True\n"
                "assert candidate([1]) == False\n"
                "assert candidate([1, 3, 5, -100]) == False\n"
                "assert candidate([100, 3, 5, -100]) == False\n"
            ),
        },
        {
            "id": "heval_65",
            "source": "HumanEval/65 (medium, GPT-5.5 pass@1: ~72%, DS V4: ~55%)",
            "prompt": (
                "def circular_shift(x, shift):\n"
                '    """Circular shift the digits of the integer x, shift the digits right by shift\n'
                "    and return the result as a string.\n"
                "    If shift > number of digits, return digits reversed.\n"
                "    >>> circular_shift(12, 1)\n"
                '    "21"\n'
                "    >>> circular_shift(12, 2)\n"
                '    "12"\n'
                '    """\n'
            ),
            "entry": "circular_shift",
            "verify": "exec",
            "test_code": (
                "assert candidate(100, 2) == '001'\n"
                "assert candidate(12, 2) == '12'\n"
                "assert candidate(97, 8) == '79'\n"
                "assert candidate(12, 1) == '21'\n"
                "assert candidate(11, 101) == '11'\n"
            ),
        },
    ],
    # ── REAL MATH-500 problems (from HuggingFaceH4/MATH-500) ────
    "math": [
        {
            "id": "math500_number_theory_l3",
            "source": "MATH-500 Number Theory Level 3",
            "prompt": "How many positive whole-number divisors does 196 have?",
            "verify": "text",
            "check": lambda resp: "9" in resp and not any(n in resp for n in ("19","29","39","49","59","69","79","89","90","91","92","93","94","95","96","97","98","99")),
        },
        {
            "id": "math500_number_theory_l3b",
            "source": "MATH-500 Number Theory Level 3",
            "prompt": "What is the least positive integer multiple of 30 that can be written with only the digits 0 and 2?",
            "verify": "text",
            "check": lambda resp: "2220" in resp,
        },
        {
            "id": "math500_number_theory_l5",
            "source": "MATH-500 Number Theory Level 5",
            "prompt": "The proper divisors of 12 are 1, 2, 3, 4 and 6. A proper divisor of an integer N is a positive divisor of N that is less than N. What is the sum of the proper divisors of the sum of the proper divisors of 284?",
            "verify": "text",
            "check": lambda resp: "284" in resp,
        },
        {
            "id": "math500_prealgebra_l5",
            "source": "MATH-500 Prealgebra Level 5",
            "prompt": "The expression 2*3*4*5+1 is equal to 121, since multiplication is carried out before addition. However, we can obtain values other than 121 for this expression if we are allowed to change it by inserting parentheses. For example, we can obtain 144 by: (2*3)*(4*(5+1))=144. Including the trivial way, how many different values can be obtained for the expression 2*3*4*5+1 by inserting parentheses in all possible ways?",
            "verify": "text",
            "check": lambda resp: "4" in resp and not any(n in resp for n in ("14","24","34","40","41","42","43","44","45","46","47","48","49")),
        },
        {
            "id": "math500_algebra_l3",
            "source": "MATH-500 Intermediate Algebra Level 3",
            "prompt": "Let a be a positive real number such that all the roots of x^3 + a*x^2 + a*x + 1 = 0 are real. Find the smallest possible value of a.",
            "verify": "text",
            "check": lambda resp: "3" in resp and not any(n in resp for n in ("13","23","30","31","32","33","34","35","36","37","38","39")),
        },
    ],
    # ── Classic reasoning/strategy puzzles ──────────────────────
    "reasoning": [
        {
            "id": "reason_hats",
            "source": "5 Hats Parity Puzzle",
            "prompt": (
                "Five prisoners lined up facing forward. Each wears black or white hat. "
                "See hats in front only, not own or behind. Starting from the back (#5), "
                "each must say their color or stay silent. Can agree strategy beforehand. "
                "What strategy guarantees at least 4 survive? Explain parity approach concisely."
            ),
            "verify": "text",
            "check": lambda resp: ("parity" in resp.lower() or "odd" in resp.lower()) and len(resp) > 60,
        },
        {
            "id": "reason_12coins",
            "source": "12 Coins 3 Weighs Puzzle",
            "prompt": (
                "12 coins, one counterfeit (lighter OR heavier — you don't know which). "
                "Balance scale, maximum 3 weighings. Identify the fake and whether lighter/heavier. "
                "Describe first weighing step."
            ),
            "verify": "text",
            "check": lambda resp: "4" in resp and ("weigh" in resp.lower() or "group" in resp.lower()),
        },
    ],
    # ── Chinese language (discriminates non-Chinese models) ────
    "chinese": [
        {
            "id": "zh_idiom",
            "source": "Classical Chinese Allusion",
            "prompt": "成语'塞翁失马'出自哪部古籍？这个故事的核心寓意是什么？请用中文简要回答。",
            "verify": "text",
            "check": lambda resp: ("淮南子" in resp or "塞翁" in resp) and ("福祸" in resp or "祸福" in resp or "焉知非福" in resp),
        },
        {
            "id": "zh_figurative",
            "source": "Chinese Figurative Language",
            "prompt": "判断'他这个人就是纸老虎'中'纸老虎'是字面义还是比喻义，并解释其含义。",
            "verify": "text",
            "check": lambda resp: "比喻" in resp or "外强中干" in resp or "虚张声势" in resp,
        },
    ],
}


class CapabilityDetector:
    """Tests model capability with REAL HumanEval + MATH problems."""

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
                passed = self._exec_test(prob, resp.content)
            else:
                passed = prob["check"](resp.content)
            return BenchResult(problem_id=prob["id"], category=category, passed=passed, response=resp.content)
        except APIError as e:
            return BenchResult(problem_id=prob["id"], category=category, passed=False, error=str(e))

    def _exec_test(self, prob: dict, model_code: str) -> bool:
        """Run model-generated code against REAL HumanEval test cases."""
        code = model_code
        if "```python" in code:
            parts = code.split("```python", 1)[1].split("```", 1)
            code = parts[0] if parts else code
        elif "```" in code:
            parts = code.split("```", 1)[1].split("```", 1)
            code = parts[0] if len(parts) > 1 else code

        entry = prob.get("entry", "candidate")
        full_code = f"{code}\n\ncandidate = {entry}\n{prob['test_code']}\nresult = True"

        try:
            result = subprocess.run(
                ["python3", "-c", full_code],
                capture_output=True, text=True, timeout=10,
                env={**os.environ, "PYTHONPATH": ""},
            )
            return result.returncode == 0 and "AssertionError" not in result.stderr
        except (subprocess.TimeoutExpired, Exception):
            return False
