"""Tests for capability and mixed routing detectors."""

import pytest
from unittest.mock import AsyncMock

from src.config import RunConfig
from src.detectors.capability import CapabilityDetector, _has_approx
from src.detectors.mixed_routing import MixedRoutingDetector
from src.utils.api_client import ChatResponse, TokenUsage


def _resp(content):
    return ChatResponse(content=content, model="gpt-5.6-sol", finish_reason="stop",
                        usage=TokenUsage(prompt_tokens=100, completion_tokens=len(content), total_tokens=100+len(content)))


def _cfg():
    return RunConfig(endpoint="https://test.example.com/v1", api_key="sk-test", model="gpt-5.6-sol")


class TestCapability:
    def test_has_approx(self):
        assert _has_approx("answer is 8.8%", 8.8, 1.0)
        assert _has_approx("about 9 percent", 8.8, 1.0)
        assert not _has_approx("answer is 50", 8.8, 1.0)

    @pytest.mark.asyncio
    async def test_all_correct(self):
        detector = CapabilityDetector(_cfg())

        async def smart_chat(**kw):
            content = str(kw.get("messages", [{}])[-1].get("content", ""))
            if "fib_overflow" in content.lower() or "Fibonacci" in content:
                return _resp("def fib_overflow(n, memo={}):\n  if n in memo: return memo[n]\n  if n<=1: return n\n  result = fib_overflow(n-1)+fib_overflow(n-2)\n  if result > 10**18: return -1\n  memo[n]=result\n  return result")
            if "parse_dollars" in content.lower() or "dollar amounts" in content:
                return _resp("import re\ndef parse_dollars(s):\n  return [float(m.replace('$','').replace(',','')) for m in re.findall(r'\\\\$[\\\\d,.]+', s)]")
            if "Monty Hall" in content.lower() or "3 doors" in content.lower():
                return _resp("yes, 2/3")
            if "disease" in content.lower() and "1%" in content:
                return _resp("8.8%")
            if ("hats" in content.lower() and "prisoner" in content.lower()) or "five prisoners" in content.lower():
                return _resp("Parity strategy: the last prisoner says the parity of black hats. Others deduce from this.")
            if "counterfeit" in content.lower() or "12 identical" in content.lower():
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
