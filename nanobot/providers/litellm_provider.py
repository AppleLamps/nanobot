"""LiteLLM provider implementation for multi-provider support."""

import json
from typing import Any

import litellm
from litellm import acompletion

from nanobot.providers.base import LLMError, LLMProvider, LLMResponse, ToolCallRequest


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.
    
    Supports OpenRouter, Anthropic, OpenAI, Gemini, and many other providers through
    a unified interface.
    """

    _NON_OPENAI_COMPATIBLE_PREFIXES = (
        "anthropic/",
        "gemini/",
        "zhipu/",
        "zai/",
        "groq/",
        "bedrock/",
        "openrouter/",
    )

    _PROVIDER_PREFIXES = {
        "openrouter": "openrouter/",
        "openai": "openai/",
        "anthropic": "anthropic/",
        "gemini": "gemini/",
        "groq": "groq/",
        "zhipu": "zhipu/",
        "zai": "zai/",
        "bedrock": "bedrock/",
        "vllm": "hosted_vllm/",
    }
    
    def __init__(
        self, 
        api_key: str | None = None, 
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        provider: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.provider = (provider or "").strip().lower() or None
        
        # Detect OpenRouter by explicit provider or fallback heuristics.
        self.is_openrouter = False
        if self.provider:
            self.is_openrouter = self.provider == "openrouter"
        else:
            self.is_openrouter = (
                (api_key and api_key.startswith("sk-or-")) or
                (api_base and "openrouter" in api_base) or
                default_model.startswith("openrouter/")
            )
        
        # Track if using a custom OpenAI-compatible endpoint (vLLM, etc.).
        # Do not infer "vLLM" from api_base alone, because other providers can also have api_base.
        self.is_vllm = False
        if self.provider:
            self.is_vllm = self.provider == "vllm"
        else:
            self.is_vllm = default_model.startswith("hosted_vllm/")
        
        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True

    def _is_openai_compatible_model(self, model: str) -> bool:
        model = (model or "").strip()
        if model.startswith("hosted_vllm/"):
            return True
        lower = model.lower()
        if any(lower.startswith(p) for p in self._NON_OPENAI_COMPATIBLE_PREFIXES):
            return False
        # Models without a provider prefix are treated as OpenAI-compatible for custom endpoints.
        if "/" not in lower:
            return True
        return lower.startswith("openai/")

    def _apply_provider_prefix(self, model: str) -> str:
        if not model:
            return model
        provider = self.provider or ("openrouter" if self.is_openrouter else None)
        if not provider:
            return model
        prefix = self._PROVIDER_PREFIXES.get(provider)
        if not prefix:
            return model
        if model.startswith(prefix):
            return model
        return f"{prefix}{model}"

    def _build_llm_error(self, exc: Exception) -> LLMError:
        status_code = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        retryable = False
        timeout_exc = getattr(litellm, "Timeout", None)
        if timeout_exc and isinstance(exc, timeout_exc):
            retryable = True
        if isinstance(status_code, int) and status_code in (429, 503):
            retryable = True
        return LLMError(message=str(exc), status_code=status_code, retryable=retryable)
    
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.
        
        Returns:
            LLMResponse with content and/or tool calls.
        """
        model = model or self.default_model

        model = self._apply_provider_prefix(model)
        
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "num_retries": 2,
        }
        
        # Pass credentials per-request to avoid global os.environ mutation and reduce secret leakage
        # (e.g., to subprocesses spawned by tools).
        if self.api_key:
            kwargs["api_key"] = self.api_key

        # Pass api_base directly for custom endpoints or provider overrides.
        if self.api_base:
            kwargs["api_base"] = self.api_base
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        
        try:
            response = await acompletion(**kwargs)
            return self._parse_response(response)
        except tuple(
            exc
            for exc in (
                getattr(litellm, "BadRequestError", None),
                getattr(litellm, "AuthenticationError", None),
                getattr(litellm, "PermissionDeniedError", None),
                getattr(litellm, "NotFoundError", None),
                getattr(litellm, "RateLimitError", None),
                getattr(litellm, "ServiceUnavailableError", None),
                getattr(litellm, "Timeout", None),
                getattr(litellm, "APIError", None),
            )
            if exc
        ) as e:
            raise self._build_llm_error(e) from e
    
    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message
        
        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"raw": args}
                
                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))
        
        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
        
        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
        )
    
    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
