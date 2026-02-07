"""Tests for the OpenRouter httpx-based provider."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from nanobot.providers.base import LLMError, LLMResponse
from nanobot.providers.openrouter_provider import OpenRouterProvider


class FakeResponse:
    """Fake httpx.Response for testing."""

    def __init__(self, status_code: int, data: dict[str, Any]):
        self.status_code = status_code
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data

    @property
    def text(self) -> str:
        return json.dumps(self._data)


def _success_response(content: str = "hello", tool_calls=None, cost=None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage: dict[str, Any] = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if cost is not None:
        usage["cost"] = cost
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": usage,
    }


@pytest.mark.asyncio
async def test_chat_returns_llm_response(monkeypatch):
    provider = OpenRouterProvider(api_key="k1", default_model="test/model")

    async def fake_post(self, url, **kwargs):
        return FakeResponse(200, _success_response("ok"))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert isinstance(resp, LLMResponse)
    assert resp.content == "ok"
    assert resp.finish_reason == "stop"
    assert resp.usage["total_tokens"] == 15
    await provider.close()


@pytest.mark.asyncio
async def test_chat_parses_tool_calls(monkeypatch):
    tool_calls = [{
        "id": "call_123",
        "type": "function",
        "function": {
            "name": "read_file",
            "arguments": '{"path": "/tmp/x.txt"}'
        }
    }]

    async def fake_post(self, url, **kwargs):
        return FakeResponse(200, _success_response(content=None, tool_calls=tool_calls))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="test/model")
    resp = await provider.chat(messages=[{"role": "user", "content": "read a file"}])
    assert resp.has_tool_calls
    assert len(resp.tool_calls) == 1
    tc = resp.tool_calls[0]
    assert tc.id == "call_123"
    assert tc.name == "read_file"
    assert tc.arguments == {"path": "/tmp/x.txt"}
    await provider.close()


@pytest.mark.asyncio
async def test_model_passed_as_is(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return FakeResponse(200, _success_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="anthropic/claude-3-5-sonnet")
    await provider.chat(
        messages=[{"role": "user", "content": "hi"}],
        model="google/gemini-2.5-flash",
    )
    assert captured["body"]["model"] == "google/gemini-2.5-flash"
    await provider.close()


@pytest.mark.asyncio
async def test_default_model_used(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return FakeResponse(200, _success_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="my/default-model")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert captured["body"]["model"] == "my/default-model"
    await provider.close()


@pytest.mark.asyncio
async def test_tools_included_in_request(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return FakeResponse(200, _success_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
    provider = OpenRouterProvider(api_key="k1", default_model="m")
    await provider.chat(messages=[{"role": "user", "content": "hi"}], tools=tools)
    assert captured["body"]["tools"] == tools
    assert captured["body"]["tool_choice"] == "auto"
    await provider.close()


@pytest.mark.asyncio
async def test_rate_limit_error_is_retryable(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return FakeResponse(429, {"error": {"code": 429, "message": "Rate limited"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="m", max_retries=0)
    with pytest.raises(LLMError) as exc_info:
        await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 429
    assert exc_info.value.retryable is True
    await provider.close()


@pytest.mark.asyncio
async def test_auth_error_is_not_retryable(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return FakeResponse(401, {"error": {"code": 401, "message": "Invalid key"}})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="bad", default_model="m", max_retries=0)
    with pytest.raises(LLMError) as exc_info:
        await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 401
    assert exc_info.value.retryable is False
    await provider.close()


@pytest.mark.asyncio
async def test_retries_on_502_then_recovers(monkeypatch):
    call_count = 0

    async def fake_post(self, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return FakeResponse(502, {"error": {"message": "Provider error"}})
        return FakeResponse(200, _success_response("recovered"))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", lambda _: asyncio.ensure_future(_noop()))

    provider = OpenRouterProvider(api_key="k1", default_model="m", max_retries=2)
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.content == "recovered"
    assert call_count == 3
    await provider.close()


@pytest.mark.asyncio
async def test_provider_does_not_mutate_env():
    import os

    env_keys = ["OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    before = {k: os.environ.get(k) for k in env_keys}
    _ = OpenRouterProvider(api_key="secret", default_model="test/model")
    after = {k: os.environ.get(k) for k in env_keys}
    assert before == after


@pytest.mark.asyncio
async def test_fallback_models_in_request(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return FakeResponse(200, _success_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(
        api_key="k1",
        default_model="google/gemini-3-flash",
        fallback_models=["google/gemini-3-flash", "anthropic/claude-sonnet-4-5"],
    )
    await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert captured["body"]["models"] == ["google/gemini-3-flash", "anthropic/claude-sonnet-4-5"]
    assert captured["body"]["route"] == "fallback"
    await provider.close()


@pytest.mark.asyncio
async def test_no_fallback_when_empty(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return FakeResponse(200, _success_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="m")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert "models" not in captured["body"]
    assert "route" not in captured["body"]
    await provider.close()


@pytest.mark.asyncio
async def test_response_healing_plugin_always_sent(monkeypatch):
    captured: dict[str, Any] = {}

    async def fake_post(self, url, **kwargs):
        captured["body"] = kwargs.get("json", {})
        return FakeResponse(200, _success_response())

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="m")
    await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert captured["body"]["plugins"] == [{"id": "response-healing"}]
    await provider.close()


@pytest.mark.asyncio
async def test_cost_included_in_usage(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return FakeResponse(200, _success_response("ok", cost=0.0042))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="m")
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.usage["cost"] == 0.0042
    await provider.close()


@pytest.mark.asyncio
async def test_cost_none_when_not_returned(monkeypatch):
    async def fake_post(self, url, **kwargs):
        return FakeResponse(200, _success_response("ok"))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    provider = OpenRouterProvider(api_key="k1", default_model="m")
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert resp.usage.get("cost") is None
    await provider.close()


async def _noop():
    pass
