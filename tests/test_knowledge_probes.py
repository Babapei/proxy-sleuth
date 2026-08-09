"""Tests for knowledge probe engine."""

import pytest
from unittest.mock import AsyncMock

from src.config import RunConfig
from src.detectors.knowledge_probes import (
    KnowledgeProbeEngine, GroupResult, _match_keyword,
)
from src.utils.api_client import ChatResponse, TokenUsage


def _resp(content, model="gpt-5.6-sol"):
    return ChatResponse(content=content, model=model, finish_reason="stop",
                        usage=TokenUsage(prompt_tokens=100, completion_tokens=len(content), total_tokens=100+len(content)))


class TestKeywordMatching:
    def test_exact_match(self):
        assert _match_keyword("sol", "GPT-5.6 Sol is the flagship")

    def test_case_insensitive(self):
        assert _match_keyword("SOL", "gpt-5.6 sol is great")

    def test_no_match(self):
        assert not _match_keyword("qwen", "This is GPT-5.6")

    def test_chinese_match(self):
        assert _match_keyword("李白", "这首诗的作者是李白")


class TestKnowledgeProbeEngine:
    @pytest.fixture
    def engine(self):
        cfg = RunConfig(endpoint="https://test.example.com/v1", api_key="sk-test", model="gpt-5.6-sol", protocol="openai")
        return KnowledgeProbeEngine(cfg)

    def test_should_model_know(self, engine):
        assert engine._should_model_know("gpt56_only") is True
        assert engine._should_model_know("reverse") is True
        assert engine._should_model_know("deepseek") is False

    def test_deepseek_model_know(self):
        cfg = RunConfig(endpoint="x", api_key="x", model="deepseek-v4")
        e = KnowledgeProbeEngine(cfg)
        assert e._should_model_know("deepseek") is True
        assert e._should_model_know("gpt56_only") is False

    def test_verdict_label(self, engine):
        assert engine._verdict_label(0.80) == "MATCH"
        assert engine._verdict_label(0.30) == "MISMATCH"
        assert engine._verdict_label(0.55) == "INCONCLUSIVE"

    def test_variant(self, engine):
        q = "What is the price?"
        assert engine._variant(q, 0) == q
        assert engine._variant(q, 1) != q

    def test_compute_overall(self, engine):
        g1 = GroupResult(group="gpt56_only", description="", expected=True, score=0.9)
        g2 = GroupResult(group="deepseek", description="", expected=False, score=0.1)
        score = engine._compute_overall([g1, g2])
        assert score == pytest.approx(0.66, abs=0.01)

    @pytest.mark.asyncio
    async def test_run_produces_valid_structure(self, engine):
        engine.client.chat = AsyncMock(return_value=_resp("GPT-5.6 Sol costs $5 input and $30 output per million tokens."))
        result = await engine.run()
        assert result["layer"] == "knowledge_probes"
        assert "overall_score" in result
        assert "verdict" in result
        assert len(result["groups"]) == 15  # all probe groups

    @pytest.mark.asyncio
    async def test_mismatch_on_ignorant_model(self, engine):
        engine.client.chat = AsyncMock(return_value=_resp("I don't know anything about that.", model="unknown"))
        result = await engine.run()
        assert result["verdict"] == "MISMATCH"
        assert result["overall_score"] < 0.5
