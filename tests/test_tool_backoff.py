import pathlib
from typing import Any

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _SeqProvider(LLMProvider):
    def __init__(self, responses: list[LLMResponse]):
        super().__init__(api_key=None, api_base=None)
        self._responses = responses
        self.calls: int = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        if self.calls >= len(self._responses):
            raise AssertionError("provider.chat called more times than expected")
        resp = self._responses[self.calls]
        self.calls += 1
        return resp

    def get_default_model(self) -> str:
        return "test-model"


class _AlwaysErrorTool(Tool):
    @property
    def name(self) -> str:
        return "always_error"

    @property
    def description(self) -> str:
        return "returns an error string"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "Error: tool failed"


class _ToggleTool(Tool):
    @property
    def name(self) -> str:
        return "toggle"

    @property
    def description(self) -> str:
        return "returns ok or error"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }

    async def execute(self, ok: bool, **kwargs: Any) -> str:
        return "ok" if ok else "Error: tool failed"

class _WarnTool(Tool):
    @property
    def name(self) -> str:
        return "warn_tool"

    @property
    def description(self) -> str:
        return "returns a warning string"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "Warning: ambiguous input"


async def test_tool_error_backoff_aborts_after_streak(tmp_path, monkeypatch) -> None:
    # Avoid writing sessions under the real home directory during tests.
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    bus = MessageBus()
    provider = _SeqProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t1", name="always_error", arguments={})],
            ),
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t2", name="always_error", arguments={})],
            ),
        ]
    )
    cfg = AgentDefaults(max_tool_iterations=10, tool_error_backoff=2)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, agent_config=cfg)
    loop.tools.register(_AlwaysErrorTool())

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    out = await loop._process_message(msg)

    assert provider.calls == 2
    assert out is not None
    assert out.content == (
        "I'm hitting repeated tool errors. "
        "Please rephrase or provide more specific inputs."
        "\n\nLast tool error (always_error): Error: tool failed"
    )


async def test_tool_error_backoff_resets_on_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    bus = MessageBus()
    provider = _SeqProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t1", name="toggle", arguments={"ok": False})],
            ),
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t2", name="toggle", arguments={"ok": True})],
            ),
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t3", name="toggle", arguments={"ok": False})],
            ),
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t4", name="toggle", arguments={"ok": False})],
            ),
        ]
    )
    cfg = AgentDefaults(max_tool_iterations=10, tool_error_backoff=2)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, agent_config=cfg)
    loop.tools.register(_ToggleTool())

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    out = await loop._process_message(msg)

    assert provider.calls == 4
    assert out is not None
    assert out.content == (
        "I'm hitting repeated tool errors. "
        "Please rephrase or provide more specific inputs."
        "\n\nLast tool error (toggle): Error: tool failed"
    )


async def test_system_message_tool_backoff_uses_system_wording(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    bus = MessageBus()
    provider = _SeqProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t1", name="always_error", arguments={})],
            ),
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t2", name="always_error", arguments={})],
            ),
        ]
    )
    cfg = AgentDefaults(max_tool_iterations=10, tool_error_backoff=2)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, agent_config=cfg)
    loop.tools.register(_AlwaysErrorTool())

    msg = InboundMessage(
        channel="system",
        sender_id="subagent",
        chat_id="cli:direct",
        content="announce",
    )
    out = await loop._process_message(msg)

    assert provider.calls == 2
    assert out is not None
    assert out.content == (
        "Background task hit repeated tool errors. "
        "Please rephrase or provide more specific inputs."
        "\n\nLast tool error (always_error): Error: tool failed"
    )


async def test_tool_error_backoff_counts_warnings(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    bus = MessageBus()
    provider = _SeqProvider(
        [
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t1", name="warn_tool", arguments={})],
            ),
            LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest(id="t2", name="warn_tool", arguments={})],
            ),
        ]
    )
    cfg = AgentDefaults(max_tool_iterations=10, tool_error_backoff=2)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, agent_config=cfg)
    loop.tools.register(_WarnTool())

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="c", content="hi")
    out = await loop._process_message(msg)

    assert provider.calls == 2
    assert out is not None
    assert out.content == (
        "I'm hitting repeated tool errors. "
        "Please rephrase or provide more specific inputs."
        "\n\nLast tool error (warn_tool): Warning: ambiguous input"
    )
