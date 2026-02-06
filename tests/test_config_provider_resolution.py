from nanobot.config.schema import (
    AgentsConfig,
    ChannelsConfig,
    Config,
    GatewayConfig,
    ProviderConfig,
    ProvidersConfig,
    ToolsConfig,
)


def _base_config(*, providers: ProvidersConfig) -> Config:
    # Build a minimal Config without relying on env.
    return Config(
        agents=AgentsConfig(),
        channels=ChannelsConfig(),
        providers=providers,
        gateway=GatewayConfig(),
        tools=ToolsConfig(),
    )


def test_get_api_key_and_base_do_not_mismatch_anthropic_vs_vllm() -> None:
    cfg = _base_config(
        providers=ProvidersConfig(
            anthropic=ProviderConfig(api_key="anthropic-key"),
            vllm=ProviderConfig(api_key="", api_base="http://127.0.0.1:8000/v1"),
        )
    )

    assert cfg.get_api_key() == "anthropic-key"
    # api_base must correspond to the selected provider (anthropic), not vllm.
    assert cfg.get_api_base() is None


def test_openrouter_api_base_defaults_when_key_set() -> None:
    cfg = _base_config(
        providers=ProvidersConfig(
            openrouter=ProviderConfig(api_key="sk-or-test", api_base=None),
        )
    )
    assert cfg.get_api_key() == "sk-or-test"
    assert cfg.get_api_base() == "https://openrouter.ai/api/v1"


def test_vllm_selected_when_only_base_configured() -> None:
    cfg = _base_config(
        providers=ProvidersConfig(
            vllm=ProviderConfig(api_key="", api_base="http://127.0.0.1:8000/v1"),
        )
    )
    assert cfg.get_api_key() is None
    assert cfg.get_api_base() == "http://127.0.0.1:8000/v1"

