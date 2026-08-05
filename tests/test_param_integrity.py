"""Tests for parameter integrity and context truncation detectors."""

import pytest
from unittest.mock import AsyncMock

from src.config import RunConfig
from src.detectors.param_integrity import ParamIntegrityDetector
from src.detectors.context_truncation import ContextTruncationDetector
from src.utils.api_client import ChatResponse, TokenUsage


def _resp(content):
    return ChatResponse(content=content, model="gpt-5.6-sol", finish_reason="stop",
                        usage=TokenUsage(prompt_tokens=100, completion_tokens=len(content), total_tokens=100+len(content)))


def _cfg(**kw):
    return RunConfig(endpoint="https://test.example.com/v1", api_key="sk-test", **kw)


class TestParamIntegrity:
    @pytest.fixture
    def detector(self):
        return ParamIntegrityDetector(_cfg(model="gpt-5.6-sol"))

    @pytest.mark.asyncio
    async def test_temperature_locked(self, detector):
        detector.client.chat = AsyncMock(side_effect=[_resp("same phrase") for _ in range(20)])
        r = await detector._check_temperature()
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_temperature_diverse(self, detector):
        detector.client.chat = AsyncMock(side_effect=[_resp(f"phrase {i}") for i in range(20)])
        r = await detector._check_temperature()
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_system_prompt_passed(self, detector):
        async def chat(messages, **kw):
            import re; m = re.search(r"'(INTEGRITY:[a-f0-9]+)'", messages[0]["content"])
            secret = m.group(1) if m else "INTEGRITY:test"
            return _resp(f"{secret} Hello!")
        detector.client.chat = AsyncMock(side_effect=chat)
        r = await detector._check_system_prompt()
        assert r.passed is True

    @pytest.mark.asyncio
    async def test_system_prompt_stripped(self, detector):
        detector.client.chat = AsyncMock(return_value=_resp("Hello! Nice to meet you."))
        r = await detector._check_system_prompt()
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_tools_stripped(self, detector):
        detector.client.chat = AsyncMock(return_value=_resp("I cannot use any tools."))
        r = await detector._check_tools()
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_max_tokens_truncated(self, detector):
        detector.client.chat = AsyncMock(return_value=_resp("short"))
        r = await detector._check_max_tokens()
        assert r.passed is False

    @pytest.mark.asyncio
    async def test_full_run_structure(self, detector):
        detector.client.chat = AsyncMock(return_value=_resp("OK response."))
        r = await detector.run()
        assert r["layer"] == "param_integrity"
        assert "score" in r
        assert "checks" in r
        assert len(r["checks"]) >= 4


class TestContextTruncation:
    @pytest.fixture
    def detector(self):
        return ContextTruncationDetector(_cfg(model="gpt-5.6-sol"))

    def test_build_filler_contains_needle(self, detector):
        msgs = detector._build_filler(depth=5, needle="NEEDLE_ABC")
        assert any("NEEDLE_ABC" in m.get("content", "") for m in msgs)

    @pytest.mark.asyncio
    async def test_needle_recalled(self, detector, monkeypatch):
        import secrets
        monkeypatch.setattr(secrets, "token_hex", lambda n: "XYZ")
        detector.client.chat = AsyncMock(return_value=_resp("The code was NEEDLE_XYZ."))
        r = await detector._needle_test(depth=10)
        assert r.recalled is True

    @pytest.mark.asyncio
    async def test_needle_forgotten(self, detector):
        detector.client.chat = AsyncMock(return_value=_resp("I don't remember."))
        r = await detector._needle_test(depth=50)
        assert r.recalled is False

    @pytest.mark.asyncio
    async def test_full_run(self, detector):
        detector.client.chat = AsyncMock(return_value=_resp("NEEDLE_FOUND"))
        r = await detector.run()
        assert r["layer"] == "context_truncation"
        assert len(r["needles"]) == len(ContextTruncationDetector.TEST_DEPTHS)
