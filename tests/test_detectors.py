"""Tests for capability and mixed routing detectors."""

import pytest
from unittest.mock import AsyncMock

from src.config import RunConfig
from src.detectors.capability import CapabilityDetector
from src.detectors.mixed_routing import MixedRoutingDetector
from src.utils.api_client import ChatResponse, TokenUsage


def _resp(content):
    return ChatResponse(content=content, model="gpt-5.6-sol", finish_reason="stop",
                        usage=TokenUsage(prompt_tokens=100, completion_tokens=len(content), total_tokens=100+len(content)))


def _cfg():
    return RunConfig(endpoint="https://test.example.com/v1", api_key="sk-test", model="gpt-5.6-sol")


class TestCapability:
    @pytest.mark.asyncio
    async def test_all_correct(self):
        detector = CapabilityDetector(_cfg())

        async def smart_chat(**kw):
            content = str(kw.get("messages", [{}])[-1].get("content", ""))
            if "longest" in content:
                return _resp("from typing import List, Optional\ndef longest(strings: List[str]) -> Optional[str]:\n  if not strings:\n    return None\n  maxlen = max(len(s) for s in strings)\n  for s in strings:\n    if len(s) == maxlen:\n      return s")
            if "find_zero" in content:
                return _resp("import math\ndef find_zero(xs):\n  begin, end = -1.0, 1.0\n  while poly(xs, begin) * poly(xs, end) > 0:\n    begin *= 2.0\n    end *= 2.0\n  while end - begin > 1e-10:\n    center = (begin + end) / 2.0\n    if poly(xs, center) * poly(xs, begin) > 0:\n      begin = center\n    else:\n      end = center\n  return (begin + end) / 2.0")
            if "get_odd_collatz" in content:
                return _resp("def get_odd_collatz(n):\n  odd = [n] if n%2 else []\n  while n > 1:\n    n = n//2 if n%2==0 else n*3+1\n    if n%2: odd.append(n)\n  odd.append(1)\n  return sorted(odd)")
            if "divisors" in content and "196" in content:
                return _resp("9")
            if "multiple of 30" in content and "0 and 2" in content:
                return _resp("2220")
            if "proper divisors" in content and "284" in content:
                return _resp("284")
            if "hats" in content.lower() or "parity" in content.lower():
                return _resp("Parity strategy: the last prisoner says the parity of black hats. Others deduce from this.")
            if "counterfeit" in content.lower() or "12" in content and "coin" in content.lower():
                return _resp("Weigh 4 vs 4 first. If equal, counterfeit is in remaining 4.")
            if "塞翁失马" in content:
                return _resp("出自《淮南子·人间训》，寓意福祸相依。")
            if "纸老虎" in content:
                return _resp("比喻义。形容外强中干的人或事物。")
            return _resp("OK")

        detector.client.chat = AsyncMock(side_effect=smart_chat)
        r = await detector.run()
        assert r["layer"] == "capability"
        assert r["score"] > 0.5

    @pytest.mark.asyncio
    async def test_all_wrong(self):
        detector = CapabilityDetector(_cfg())
        detector.client.chat = AsyncMock(return_value=_resp("I don't know."))
        r = await detector.run()
        assert r["score"] < 0.4


class TestMixedRouting:
    @pytest.mark.asyncio
    async def test_alternating_structure(self):
        detector = MixedRoutingDetector(_cfg())
        detector.client.chat = AsyncMock(return_value=_resp("OK"))
        r = await detector.run()
        assert r["layer"] == "mixed_routing"
        assert "routing_detected" in r
        assert "stats" in r
