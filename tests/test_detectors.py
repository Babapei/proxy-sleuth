"""Tests for capability and mixed routing detectors."""

import pytest
from unittest.mock import AsyncMock

from src.config import RunConfig
from src.detectors.capability import CapabilityDetector, _number_in, _last_number
from src.detectors.mixed_routing import MixedRoutingDetector
from src.utils.api_client import ChatResponse, TokenUsage


def _resp(content):
    return ChatResponse(content=content, model="gpt-5.6-sol", finish_reason="stop",
                        usage=TokenUsage(prompt_tokens=100, completion_tokens=len(content), total_tokens=100+len(content)))


def _cfg():
    return RunConfig(endpoint="https://test.example.com/v1", api_key="sk-test", model="gpt-5.6-sol")


class TestCapability:
    def test_number_in(self):
        assert _number_in("answer is 13", ["13", "14"])
        assert not _number_in("answer is 7", ["13", "14"])

    def test_last_number(self):
        assert _last_number("the answer is 13") == "13"
        assert _last_number("step: 5 then 9") == "9"

    @pytest.mark.asyncio
    async def test_all_correct(self):
        detector = CapabilityDetector(_cfg())

        async def smart_chat(**kw):
            content = str(kw.get("messages", [{}])[-1].get("content", ""))
            if "dining philosophers" in content.lower():
                return _resp("import asyncio\nforks = [asyncio.Lock() for _ in range(5)]\nasync def philosopher(i):\n  while True:\n    async with forks[i]:\n      async with forks[(i+1)%5]:\n        pass")
            if "LRU" in content or "lru" in content:
                return _resp("from collections import OrderedDict\nimport threading\nclass LRUCache:\n  def __init__(self, capacity):\n    self.cache = OrderedDict()\n    self.lock = threading.Lock()")
            if "Monty Hall" in content.lower() or "game show" in content.lower():
                return _resp("yes, 2/3")
            if "birthday" in content.lower() and "23" in content:
                return _resp("50%")
            if "knights" in content.lower() and "A" in content and "B" in content:
                return _resp("A is a knight, B is a knave, C is a knight")
            if "poisoned" in content.lower() or "binary" in content.lower():
                return _resp("Binary encoding: number bottles 0-999, use 10 prisoners as bits. Since 2^10 = 1024 > 1000...")
            if "君子之交" in content or "淡如水" in content:
                return _resp("出自庄子。君子之交淡如水，小人之交甘若醴...")
            if "七十二" in content or "三十六" in content:
                return _resp("72 × 36 = 2592, + 480 = 3072")
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
