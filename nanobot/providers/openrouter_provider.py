"""OpenRouter provider — direct httpx implementation."""

import asyncio
import json
from typing import Any

import httpx

from nanobot.providers.base import LLMError, LLMProvider, LLMResponse, ToolCallRequest

_RETRYABLE_STATUS_CODES = frozenset({408, 429, 502, 503})

_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    """
    LLM provider that calls the OpenRouter chat completions API directly
    via httpx, without the litellm intermediary.

    The OpenRouter API is OpenAI-compatible, so the request/response format
    follows the standard chat completions schema.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "openai/gpt-oss-120b:exacto",
        provider: str | None = None,
        max_retries: int = 2,
        timeout: float = 120.0,
        fallback_models: list[str] | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.max_retries = max_retries
        self.timeout = timeout
        self.fallback_models = fallback_models or []
        base = (api_base or _DEFAULT_BASE_URL).rstrip("/")
        self._completions_url = f"{base}/chat/completions"
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers: dict[str, str] = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers=headers,
            )
        return self._client

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_fallbacks: bool = True,
    ) -> LLMResponse:
        model = model or self.default_model

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "plugins": [{"id": "response-healing"}],
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # Model fallbacks: if the primary model errors, OpenRouter tries the next.
        if use_fallbacks:
            fallbacks = [m for m in self.fallback_models if m != model]
            if fallbacks:
                body["models"] = [model] + fallbacks
                body["route"] = "fallback"

        client = self._get_client()
        last_error: LLMError | None = None

        for attempt in range(1 + self.max_retries):
            try:
                resp = await client.post(self._completions_url, json=body)

                if resp.status_code >= 400:
                    try:
                        error_body = resp.json()
                    except Exception:
                        error_body = resp.text
                    llm_err = self._build_llm_error(resp.status_code, error_body)
                    if llm_err.retryable and attempt < self.max_retries:
                        last_error = llm_err
                        await asyncio.sleep(2**attempt)
                        continue
                    raise llm_err

                return self._parse_response(resp.json())

            except LLMError:
                raise

            except httpx.TimeoutException as exc:
                last_error = LLMError(
                    message=f"Request timed out: {exc}",
                    status_code=408,
                    retryable=True,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise last_error from exc

            except httpx.HTTPError as exc:
                last_error = LLMError(
                    message=f"HTTP error: {exc}",
                    status_code=None,
                    retryable=True,
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(2**attempt)
                    continue
                raise last_error from exc

        raise last_error or LLMError(
            "Unknown error after retries", status_code=None, retryable=False
        )

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            return LLMResponse(content=None, tool_calls=[], finish_reason="stop", usage={})

        choice = choices[0]
        message = choice.get("message") or {}

        tool_calls: list[ToolCallRequest] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments", "{}")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {"raw": args}
            tool_calls.append(
                ToolCallRequest(
                    id=tc.get("id", ""),
                    name=fn.get("name", ""),
                    arguments=args,
                )
            )

        usage_raw = data.get("usage") or {}
        usage = {}
        if usage_raw:
            usage = {
                "prompt_tokens": usage_raw.get("prompt_tokens", 0),
                "completion_tokens": usage_raw.get("completion_tokens", 0),
                "total_tokens": usage_raw.get("total_tokens", 0),
                "cost": usage_raw.get("cost"),
            }

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason") or "stop",
            usage=usage,
        )

    @staticmethod
    def _build_llm_error(status_code: int, body: dict[str, Any] | str) -> LLMError:
        if isinstance(body, dict):
            error_obj = body.get("error", {})
            message = (
                error_obj.get("message", str(body))
                if isinstance(error_obj, dict)
                else str(error_obj)
            )
        else:
            message = body
        return LLMError(
            message=str(message),
            status_code=status_code,
            retryable=status_code in _RETRYABLE_STATUS_CODES,
        )

    def get_default_model(self) -> str:
        return self.default_model

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
