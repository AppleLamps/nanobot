"""Configuration schema using Pydantic."""

import os
import re
import warnings
from pathlib import Path, PureWindowsPath

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings


def _default_workspace() -> str:
    from nanobot.utils.helpers import get_data_path

    return str(get_data_path() / "workspace")


def _normalize_path_for_platform(raw: str) -> str:
    """
    Normalize Windows absolute paths when running on POSIX (e.g. WSL).

    This allows configs that contain "C:\\Users\\..." to work when nanobot is
    launched from a POSIX environment that can access Windows mounts at /mnt/<drive>.
    """
    s = (raw or "").strip()
    if not s:
        return raw

    if os.name != "nt" and re.match(r"^[A-Za-z]:\\\\", s):
        try:
            wp = PureWindowsPath(s)
            drive = (wp.drive or "")[:1].lower()
            if drive and (Path("/mnt") / drive).exists():
                mapped = Path("/mnt") / drive
                for part in wp.parts[1:]:
                    mapped = mapped / part
                return str(mapped)
        except Exception:
            return raw

    return raw


class WhatsAppConfig(BaseModel):
    """WhatsApp channel configuration."""

    enabled: bool = False
    bridge_url: str = "ws://localhost:3001"
    allow_from: list[str] = Field(default_factory=list)  # Allowed phone numbers
    rate_limit_s: int = 0  # Minimum seconds between messages from the same sender


class TelegramConfig(BaseModel):
    """Telegram channel configuration."""

    enabled: bool = False
    token: str = ""  # Bot token from @BotFather
    allow_from: list[str] = Field(default_factory=list)  # Allowed user IDs or usernames
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    rate_limit_s: int = 0  # Minimum seconds between messages from the same sender


class FeishuConfig(BaseModel):
    """Feishu/Lark channel configuration using WebSocket long connection."""

    enabled: bool = False
    app_id: str = ""  # App ID from Feishu Open Platform
    app_secret: str = ""  # App Secret from Feishu Open Platform
    encrypt_key: str = ""  # Encrypt Key for event subscription (optional)
    verification_token: str = ""  # Verification Token for event subscription (optional)
    allow_from: list[str] = Field(default_factory=list)  # Allowed user open_ids
    rate_limit_s: int = 0  # Minimum seconds between messages from the same sender


class WebUIConfig(BaseModel):
    """Local web UI channel configuration."""

    enabled: bool = False
    host: str = "127.0.0.1"  # Bind address. Prefer loopback for safety.
    port: int = 18791
    auth_token: str = ""  # If set, requires `?token=...` for both HTTP and WS.
    allow_from: list[str] = Field(default_factory=list)  # Allowed sender IDs (advanced)
    rate_limit_s: int = 0  # Minimum seconds between messages from the same sender
    allow_unrestricted_workspace: bool = False  # Allow per-session restrict_workspace=false


class ChannelsConfig(BaseModel):
    """Configuration for chat channels."""

    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    feishu: FeishuConfig = Field(default_factory=FeishuConfig)
    webui: WebUIConfig = Field(default_factory=WebUIConfig)


class AgentDefaults(BaseModel):
    """Default agent configuration."""

    workspace: str = Field(default_factory=_default_workspace)
    provider: str = ""
    model: str = "openai/gpt-oss-120b:exacto"
    fallback_models: list[str] = Field(default_factory=list)
    max_tokens: int = 8192
    temperature: float = 0.7
    max_tool_iterations: int = 20
    # Memory scoping:
    # - session: per chat (channel:chat_id)
    # - user: per user (channel:sender_id)
    memory_scope: str = "session"
    # Concurrency: maximum number of different chats/sessions processed in parallel.
    # Messages from the same session are still processed sequentially.
    max_concurrent_messages: int = 4
    # Backpressure: cap queued messages to prevent unbounded memory growth.
    max_pending_messages_per_session: int = 20
    max_pending_messages_total: int = 200
    session_idle_timeout_s: int = 60
    trusted_session_override_channels: list[str] = Field(default_factory=lambda: ["webui"])
    # Prompt budgets (characters; sliding-window truncation)
    memory_max_chars: int = 6000
    skills_max_chars: int = 12000
    bootstrap_max_chars: int = 4000
    history_max_chars: int = 80000
    # Subagent prompt budgets (characters)
    subagent_bootstrap_chars: int = 3000
    subagent_context_chars: int = 3000
    subagent_announce_chars: int = 4000
    subagent_max_running: int = 4
    subagent_use_fallbacks: bool = True
    # Tool error backoff
    tool_error_backoff: int = 3
    tool_error_backoff_max_chars: int = 1200
    # Auto-tune response length
    auto_tune_max_tokens: bool = False
    initial_max_tokens: int | None = None
    auto_tune_step: int = 512
    auto_tune_threshold: float = 0.85
    auto_tune_streak: int = 3


class AgentsConfig(BaseModel):
    """Agent configuration."""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    api_key: str = ""
    api_base: str | None = None


class ProvidersConfig(BaseModel):
    """Configuration for LLM providers."""

    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)


class GatewayConfig(BaseModel):
    """Gateway/server configuration."""

    host: str = "0.0.0.0"
    port: int = 18790


class WebSearchConfig(BaseModel):
    """Web search tool configuration."""

    api_key: str = ""  # Brave Search API key
    max_results: int = 5


class FirecrawlConfig(BaseModel):
    """Firecrawl scrape tool configuration."""

    api_key: str = ""  # Firecrawl API key


class WebToolsConfig(BaseModel):
    """Web tools configuration."""

    search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    firecrawl: FirecrawlConfig = Field(default_factory=FirecrawlConfig)


class ExecToolConfig(BaseModel):
    """Shell exec tool configuration."""

    timeout: int = 60
    restrict_to_workspace: bool = True  # If true, block commands accessing paths outside workspace
    allow_unrestricted_workspace: bool = False


class ToolsConfig(BaseModel):
    """Tools configuration."""

    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    allowed_tools: list[str] | None = None  # Optional allowlist of tool names


class Config(BaseSettings):
    """Root configuration for nanobot."""

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)

    @model_validator(mode="after")
    def _validate_provider_model(self) -> "Config":
        provider = (self.agents.defaults.provider or "").strip().lower()
        model = (self.agents.defaults.model or "").strip()
        if not provider or not model or "/" not in model:
            return self

        model_prefix = model.split("/", 1)[0].strip().lower()
        provider_prefixes = {
            "openrouter": {"openrouter"},
            "openai": {"openai"},
            "anthropic": {"anthropic"},
            "gemini": {"gemini"},
            "groq": {"groq"},
            "zhipu": {"zhipu", "zai"},
            "vllm": {"hosted_vllm"},
            "bedrock": {"bedrock"},
        }
        known_prefixes = {p for prefixes in provider_prefixes.values() for p in prefixes}
        if model_prefix in known_prefixes and model_prefix not in provider_prefixes.get(
            provider, {provider}
        ):
            warnings.warn(
                f"Provider '{provider}' does not match model prefix '{model_prefix}'.",
                RuntimeWarning,
                stacklevel=2,
            )
        return self

    def _select_provider(self) -> tuple[str | None, ProviderConfig | None]:
        """Select the configured provider in priority order.

        Important: selection must be shared by get_api_key() and get_api_base() to avoid
        mismatched (api_key, api_base) pairs.
        """
        explicit = (self.agents.defaults.provider or "").strip().lower()
        if explicit:
            cfg = getattr(self.providers, explicit, None)
            if cfg is not None:
                return explicit, cfg
            return None, None

        if self.providers.openrouter.api_key:
            return "openrouter", self.providers.openrouter
        if self.providers.anthropic.api_key:
            return "anthropic", self.providers.anthropic
        if self.providers.openai.api_key:
            return "openai", self.providers.openai
        if self.providers.gemini.api_key:
            return "gemini", self.providers.gemini
        if self.providers.zhipu.api_key:
            return "zhipu", self.providers.zhipu
        if self.providers.groq.api_key:
            return "groq", self.providers.groq

        # vLLM/custom endpoint (lowest priority). If only a base URL is configured, this still
        # selects vLLM so get_api_base() stays consistent; get_api_key() may still be None.
        if self.providers.vllm.api_key or self.providers.vllm.api_base:
            return "vllm", self.providers.vllm

        return None, None

    @property
    def workspace_path(self) -> Path:
        """Get expanded workspace path."""
        raw = _normalize_path_for_platform(self.agents.defaults.workspace)
        return Path(raw).expanduser()

    def get_api_key(self) -> str | None:
        """Get the API key for the selected provider (see _select_provider())."""
        _name, cfg = self._select_provider()
        if not cfg:
            return None
        return cfg.api_key or None

    def get_api_base(self) -> str | None:
        """Get the API base URL for the selected provider (see _select_provider())."""
        name, cfg = self._select_provider()
        if not cfg:
            return None
        if name == "openrouter":
            return cfg.api_base or "https://openrouter.ai/api/v1"
        return cfg.api_base

    def validate_provider(self) -> list[str]:
        """Check for common provider misconfiguration. Returns a list of warnings."""
        warnings_list: list[str] = []
        model = (self.agents.defaults.model or "").strip()
        name, cfg = self._select_provider()

        if not name and not model.startswith("bedrock/"):
            warnings_list.append(
                "No API key configured for any provider. "
                "Set one via `nanobot onboard` or in config.json."
            )
            return warnings_list

        if not model:
            return warnings_list

        # Check model prefix vs selected provider.
        prefix_to_provider: dict[str, str] = {
            "openai": "openai",
            "anthropic": "anthropic",
            "gemini": "gemini",
            "groq": "groq",
            "zhipu": "zhipu",
            "zai": "zhipu",
            "hosted_vllm": "vllm",
            "bedrock": "bedrock",
        }
        if "/" in model:
            model_prefix = model.split("/", 1)[0].strip().lower()
            expected = prefix_to_provider.get(model_prefix)
            # Only warn for non-openrouter providers where the model prefix
            # suggests a direct provider but a different one was selected.
            if expected and name and name != "openrouter" and expected != name:
                warnings_list.append(
                    f"Model '{model}' looks like a {expected} model, "
                    f"but the active provider is '{name}'. "
                    f"This may cause errors. Check your API keys or model setting."
                )

        return warnings_list

    class Config:
        env_prefix = "NANOBOT_"
        env_nested_delimiter = "__"
