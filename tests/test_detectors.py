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
            if "fibonacci" in content.lower():
                return _resp("def fib(n): return n if n<=1 else fib(n-1)+fib(n-2)")
            if "right triangle" in content.lower():
                return _resp("13")
            if "probability" in content.lower():
                return _resp("3/28")
            if "画蛇添足" in content:
                return _resp("画蛇添足意思是做了多余的事。")
            if "床前明月光" in content:
                return _resp("疑是地上霜。李白。")
            if "7^100 mod 13" in content:
                return _resp("9")
            if "knights" in content.lower():
                return _resp("A is a knave, B is a knight.")
            if "5L jug" in content.lower():
                return _resp("Fill 5L, pour into 3L... get 4L.")
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
