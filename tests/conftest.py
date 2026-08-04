"""Shared test fixtures and helpers."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from src.config import RunConfig
from src.utils.api_client import ChatResponse, TokenUsage


@pytest.fixture
def run_config() -> RunConfig:
    return RunConfig(
        endpoint="https://test-proxy.example.com/v1",
        api_key="sk-test-key",
        model="gpt-5.6-sol",
        protocol="openai",
        temperature=0.0,
        max_tokens=512,
    )


def make_chat_response(content: str, model: str = "gpt-5.6-sol", tokens: int = 100) -> ChatResponse:
    return ChatResponse(
        content=content,
        model=model,
        finish_reason="stop",
        usage=TokenUsage(prompt_tokens=tokens, completion_tokens=len(content), total_tokens=tokens + len(content)),
        duration_ms=150.0,
    )
