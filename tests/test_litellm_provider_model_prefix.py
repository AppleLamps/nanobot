from typing import Any

import pytest

import nanobot.providers.litellm_provider as lp


@pytest.mark.asyncio
async def test_litellm_provider_prefixes_openrouter_models(monkeypatch) -> None:
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
        api_key="sk-or-test",
        api_base="https://openrouter.ai/api/v1",
        default_model="anthropic/claude-3-5-sonnet",
    )
    assert provider.is_openrouter is True

    out = await provider.chat(messages=[{"role": "user", "content": "hi"}], model="anthropic/claude-3-5-sonnet")
    assert out.content == "ok"
    assert captured["model"].startswith("openrouter/")

