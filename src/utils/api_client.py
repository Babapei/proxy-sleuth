"""Unified API client supporting OpenAI and Anthropic protocols."""

from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

import httpx


class Protocol(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    RESPONSES = "responses"
    GEMINI = "gemini"


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> TokenUsage:
        return cls(
            prompt_tokens=data.get("prompt_tokens", 0),
            completion_tokens=data.get("completion_tokens", 0),
            total_tokens=data.get("total_tokens", 0),
        )


@dataclass
class ChatResponse:
    content: str
    model: str
    finish_reason: str = "stop"
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: dict | None = None
    duration_ms: float = 0.0


@dataclass
class StreamChunk:
    content: str = ""
    finish_reason: str | None = None
    usage: TokenUsage | None = None


class APIError(Exception):
    """Raised when the upstream API returns an error."""

    def __init__(self, status_code: int, message: str, raw: dict | None = None):
        self.status_code = status_code
        self.message = message
        self.raw = raw
        super().__init__(f"[{status_code}] {message}")


class APIClient:
    """Unified client for OpenAI-compatible and Anthropic chat APIs.

    Usage::

        client = APIClient("https://api.openai.com/v1", "sk-xxx")
        resp = await client.chat([{"role": "user", "content": "Hello"}], "gpt-5.6-sol")
        print(resp.content, resp.usage)

        # Streaming
        async for chunk in client.chat_stream(messages, model):
            print(chunk.content, end="")
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        protocol: Protocol | str = Protocol.OPENAI,
        timeout: float = 120.0,
        max_retries: int = 3,
        extra_headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.protocol = Protocol(protocol) if isinstance(protocol, str) else protocol
        self.timeout = timeout
        self.max_retries = max_retries
        self.extra_headers = extra_headers or {}

    # ── public API ──────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
    ) -> ChatResponse:
        body = self._build_body(
            messages, model, temperature, max_tokens, top_p,
            reasoning_effort, tools, tool_choice, extra_body, stream=False,
        )
        if self.protocol == Protocol.ANTHROPIC:
            return await self._anthropic_chat(body)
        if self.protocol == Protocol.RESPONSES:
            return await self._responses_chat(messages, model, temperature, max_tokens, tools, extra_body)
        if self.protocol == Protocol.GEMINI:
            return await self._gemini_chat(messages, model, temperature, max_tokens)
        return await self._openai_chat(body)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        top_p: float | None = None,
        reasoning_effort: str | None = None,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
        extra_body: dict | None = None,
    ) -> AsyncIterator[StreamChunk]:
        body = self._build_body(
            messages, model, temperature, max_tokens, top_p,
            reasoning_effort, tools, tool_choice, extra_body, stream=True,
        )
        if self.protocol == Protocol.ANTHROPIC:
            async for chunk in self._anthropic_stream(body):
                yield chunk
            return
        async for chunk in self._openai_stream(body):
            yield chunk

    # ── internal: OpenAI ────────────────────────────────────────

    def _chat_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    async def _openai_chat(self, body: dict) -> ChatResponse:
        t0 = asyncio.get_event_loop().time()
        for attempt in range(self.max_retries):
            try:
                async with self._client() as client:
                    resp = await client.post(self._chat_url(), json=body)
                    data = resp.json()
                    if resp.status_code >= 400:
                        raise APIError(resp.status_code, _extract_error(data), data)
                    choice = data["choices"][0]
                    return ChatResponse(
                        content=choice["message"].get("content") or "",
                        model=data.get("model", body["model"]),
                        finish_reason=choice.get("finish_reason", "stop"),
                        usage=TokenUsage.from_dict(data.get("usage", {})),
                        raw=data,
                        duration_ms=(asyncio.get_event_loop().time() - t0) * 1000,
                    )
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt == self.max_retries - 1:
                    raise APIError(0, f"Connection failed after {self.max_retries} retries: {e}") from e
                await asyncio.sleep(2 ** attempt)
        raise APIError(0, "Unexpected: all retries exhausted")

    async def _openai_stream(self, body: dict) -> AsyncIterator[StreamChunk]:
        for attempt in range(self.max_retries):
            try:
                async with self._client() as client:
                    async with client.stream("POST", self._chat_url(), json=body) as resp:
                        if resp.status_code >= 400:
                            data = await resp.aread()
                            raise APIError(resp.status_code, _extract_error(json.loads(data)))
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                return
                            try:
                                chunk_data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            choice = chunk_data.get("choices", [{}])[0]
                            yield StreamChunk(
                                content=choice.get("delta", {}).get("content") or "",
                                finish_reason=choice.get("finish_reason"),
                                usage=TokenUsage.from_dict(chunk_data.get("usage", {})) if chunk_data.get("usage") else None,
                            )
                return
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    # ── internal: OpenAI Responses API ───────────────────────────

    def _responses_url(self) -> str:
        return f"{self.base_url}/responses"

    async def _responses_chat(
        self, messages: list, model: str, temperature: float,
        max_tokens: int, tools: list | None, extra_body: dict | None,
    ) -> ChatResponse:
        """OpenAI Responses API format (native Codex CLI protocol)."""
        t0 = asyncio.get_event_loop().time()
        input_msgs = [{"role": m["role"], "content": m["content"]} for m in messages]
        body: dict[str, Any] = {
            "model": model, "input": input_msgs,
            "max_output_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if extra_body:
            body.update(extra_body)

        for attempt in range(self.max_retries):
            try:
                async with self._client() as client:
                    resp = await client.post(self._responses_url(), json=body)
                    data = resp.json()
                    if resp.status_code >= 400:
                        raise APIError(resp.status_code, _extract_error(data), data)
                    output = data.get("output", [])
                    text = ""
                    for item in output:
                        if item.get("type") == "message":
                            for content in item.get("content", []):
                                if content.get("type") == "output_text":
                                    text += content.get("text", "")
                    usage = data.get("usage", {})
                    return ChatResponse(
                        content=text, model=data.get("model", model),
                        finish_reason=data.get("status", "completed"),
                        usage=TokenUsage.from_dict({
                            "prompt_tokens": usage.get("input_tokens", 0),
                            "completion_tokens": usage.get("output_tokens", 0),
                            "total_tokens": usage.get("total_tokens", 0),
                        }),
                        raw=data,
                        duration_ms=(asyncio.get_event_loop().time() - t0) * 1000,
                    )
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt == self.max_retries - 1:
                    raise APIError(0, f"Connection failed: {e}") from e
                await asyncio.sleep(2 ** attempt)
        raise APIError(0, "Unexpected")

    # ── internal: Google Gemini ──────────────────────────────────

    async def _gemini_chat(
        self, messages: list, model: str, temperature: float, max_tokens: int,
    ) -> ChatResponse:
        """Google Gemini generateContent API."""
        t0 = asyncio.get_event_loop().time()

        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})

        base = self.base_url.rstrip("/")
        url = f"{base}/v1beta/models/{model}:generateContent"

        # Google official uses ?key= query param; proxies use Bearer auth
        if "generativelanguage.googleapis.com" in base:
            url += f"?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
        else:
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        body = {
            "contents": contents,
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout), headers=headers) as client:
                    resp = await client.post(url, json=body)
                    data = resp.json()
                    if resp.status_code >= 400:
                        raise APIError(resp.status_code, _extract_error(data), data)
                    candidates = data.get("candidates", [{}])
                    parts = candidates[0].get("content", {}).get("parts", [{}])
                    text = "".join(p.get("text", "") for p in parts)
                    usage = data.get("usageMetadata", {})
                    return ChatResponse(
                        content=text, model=model,
                        finish_reason=candidates[0].get("finishReason", "STOP"),
                        usage=TokenUsage.from_dict({
                            "prompt_tokens": usage.get("promptTokenCount", 0),
                            "completion_tokens": usage.get("candidatesTokenCount", 0),
                            "total_tokens": usage.get("totalTokenCount", 0),
                        }),
                        raw=data, duration_ms=(asyncio.get_event_loop().time() - t0) * 1000,
                    )
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt == self.max_retries - 1:
                    raise APIError(0, f"Connection failed: {e}") from e
                await asyncio.sleep(2 ** attempt)
        raise APIError(0, "Unexpected")

    # ── internal: Anthropic Messages ────────────────────────────

    def _anthropic_url(self) -> str:
        if "/v1" in self.base_url:
            return f"{self.base_url}/messages"
        return f"{self.base_url}/v1/messages"

    async def _anthropic_chat(self, body: dict) -> ChatResponse:
        t0 = asyncio.get_event_loop().time()
        anthropic_body = _to_anthropic(body)
        for attempt in range(self.max_retries):
            try:
                async with self._anthropic_client() as client:
                    resp = await client.post(self._anthropic_url(), json=anthropic_body)
                    data = resp.json()
                    if resp.status_code >= 400:
                        raise APIError(resp.status_code, _extract_error(data), data)
                    content_blocks = data.get("content", [])
                    text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                    usage = data.get("usage", {})
                    return ChatResponse(
                        content=text,
                        model=data.get("model", body["model"]),
                        finish_reason=data.get("stop_reason", "end_turn"),
                        usage=TokenUsage(
                            prompt_tokens=usage.get("input_tokens", 0),
                            completion_tokens=usage.get("output_tokens", 0),
                            total_tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                        ),
                        raw=data,
                        duration_ms=(asyncio.get_event_loop().time() - t0) * 1000,
                    )
            except (httpx.TimeoutException, httpx.ConnectError) as e:
                if attempt == self.max_retries - 1:
                    raise APIError(0, str(e)) from e
                await asyncio.sleep(2 ** attempt)
        raise APIError(0, "Unexpected")

    async def _anthropic_stream(self, body: dict) -> AsyncIterator[StreamChunk]:
        anthropic_body = _to_anthropic(body)
        for attempt in range(self.max_retries):
            try:
                async with self._anthropic_client() as client:
                    async with client.stream("POST", self._anthropic_url(), json=anthropic_body) as resp:
                        if resp.status_code >= 400:
                            data = await resp.aread()
                            raise APIError(resp.status_code, _extract_error(json.loads(data)))
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:].strip()
                            try:
                                event = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue
                            if event.get("type") == "content_block_delta":
                                yield StreamChunk(
                                    content=event.get("delta", {}).get("text", ""),
                                )
                            elif event.get("type") == "message_delta":
                                usage = event.get("usage", {})
                                yield StreamChunk(
                                    finish_reason=event.get("delta", {}).get("stop_reason"),
                                    usage=TokenUsage(
                                        completion_tokens=usage.get("output_tokens", 0),
                                    ),
                                )
                return
            except (httpx.TimeoutException, httpx.ConnectError):
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

    # ── helpers ─────────────────────────────────────────────────

    def _build_body(
        self, messages, model, temperature, max_tokens, top_p,
        reasoning_effort, tools, tool_choice, extra_body, stream,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if top_p is not None:
            body["top_p"] = top_p
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        if tools is not None:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        if extra_body:
            body.update(extra_body)
        return body

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                **self.extra_headers,
            },
        )

    def _anthropic_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout),
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
                **self.extra_headers,
            },
        )


# ── helpers ─────────────────────────────────────────────────────

def _extract_error(data: dict) -> str:
    if "error" in data:
        err = data["error"]
        if isinstance(err, dict):
            return err.get("message", str(err))
        return str(err)
    return data.get("message", str(data))


def _to_anthropic(openai_body: dict) -> dict:
    """Convert OpenAI-format request body to Anthropic Messages format."""
    system_prompts = []
    messages: list[dict] = []

    for msg in openai_body.get("messages", []):
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            system_prompts.append({"type": "text", "text": content})
        elif role == "assistant":
            messages.append({"role": "assistant", "content": [{"type": "text", "text": content}]})
        elif role == "tool":
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id", "unknown"), "content": content}],
            })
        else:
            messages.append({"role": "user", "content": [{"type": "text", "text": content}]})

    body: dict[str, Any] = {
        "model": openai_body["model"],
        "messages": messages,
        "max_tokens": openai_body.get("max_tokens", 1024),
        "stream": openai_body.get("stream", False),
    }
    if system_prompts:
        body["system"] = system_prompts
    if openai_body.get("temperature") is not None:
        body["temperature"] = openai_body["temperature"]
    if openai_body.get("top_p") is not None:
        body["top_p"] = openai_body["top_p"]

    return body
