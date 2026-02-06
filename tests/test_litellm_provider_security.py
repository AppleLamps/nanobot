import os
from typing import Any

import pytest

import litellm

import nanobot.providers.litellm_provider as lp
from nanobot.providers.base import LLMError


@pytest.mark.asyncio
async def test_litellm_provider_does_not_mutate_process_env(monkeypatch) -> None:
    keys = [
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "ZHIPUAI_API_KEY",
        "GROQ_API_KEY",
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)

    before_env = dict(os.environ)
    before_api_base = getattr(litellm, "api_base", None)

    provider = lp.LiteLLMProvider(
        api_key="super-secret",
        api_base="https://openrouter.ai/api/v1",
        default_model="openrouter/anthropic/claude-3-5-sonnet",
    )
    assert provider.is_openrouter is True

    # Provider must not write secrets into environment variables.
    after_env = dict(os.environ)
    for k in keys:
        assert k not in after_env
    assert before_env == after_env

    # Provider must not mutate global litellm.api_base (pass it per call instead).
    assert getattr(litellm, "api_base", None) == before_api_base


@pytest.mark.asyncio
async def test_litellm_provider_passes_api_key_and_api_base_per_request(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any):
        captured.update(kwargs)

        class _Msg:
            content = "ok"
            tool_calls = None

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Resp:
            choices = [_Choice()]
            usage = None

        return _Resp()

    monkeypatch.setattr(lp, "acompletion", fake_acompletion)

    provider = lp.LiteLLMProvider(
        api_key="k1",
        api_base="http://127.0.0.1:8000/v1",
        default_model="llama-3.1-8b-instruct",
    )

    out = await provider.chat(messages=[{"role": "user", "content": "hi"}])
    assert out.content == "ok"
    assert captured.get("api_key") == "k1"
    assert captured.get("api_base") == "http://127.0.0.1:8000/v1"


@pytest.mark.asyncio
async def test_litellm_provider_wraps_litellm_errors(monkeypatch) -> None:
    class _FakeBadRequestError(Exception):
        def __init__(self, message: str, status_code: int | None = None):
            super().__init__(message)
            self.status_code = status_code

    async def fake_acompletion(**kwargs: Any):
        raise _FakeBadRequestError("boom", status_code=429)

    monkeypatch.setattr(lp, "acompletion", fake_acompletion)
    monkeypatch.setattr(lp.litellm, "BadRequestError", _FakeBadRequestError)

    provider = lp.LiteLLMProvider(
        api_key="k1",
        api_base="http://127.0.0.1:8000/v1",
        default_model="openai/gpt-4o",
    )

    with pytest.raises(LLMError) as excinfo:
        await provider.chat(messages=[{"role": "user", "content": "hi"}])

    err = excinfo.value
    assert err.status_code == 429
    assert err.retryable is True

