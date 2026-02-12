import asyncio
import pathlib
from typing import Any

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class _BarrierProvider(LLMProvider):
    """Returns "ok" only if two chats hit chat() concurrently."""

    def __init__(self) -> None:
        super().__init__(api_key=None, api_base=None)
        self._started = 0
        self._both_started = asyncio.Event()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_fallbacks: bool = True,
    ) -> LLMResponse:
        self._started += 1
        if self._started >= 2:
            self._both_started.set()
        try:
            await asyncio.wait_for(self._both_started.wait(), timeout=0.75)
        except asyncio.TimeoutError:
            return LLMResponse(content="no-parallel")
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "test-model"


async def test_agent_run_processes_multiple_sessions_in_parallel(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    bus = MessageBus()
    provider = _BarrierProvider()
    cfg = AgentDefaults(max_concurrent_messages=2, max_tool_iterations=2)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, agent_config=cfg)

    agent_task = asyncio.create_task(loop.run())
    try:
        await bus.publish_inbound(InboundMessage(channel="cli", sender_id="u", chat_id="c1", content="hi-1"))
        await bus.publish_inbound(InboundMessage(channel="cli", sender_id="u", chat_id="c2", content="hi-2"))

        out1 = await asyncio.wait_for(bus.consume_outbound(), timeout=3.0)
        out2 = await asyncio.wait_for(bus.consume_outbound(), timeout=3.0)
        assert sorted([out1.content, out2.content]) == ["ok", "ok"]
    finally:
        loop.stop()
        await asyncio.wait_for(agent_task, timeout=3.0)


class _MessageToolRaceProvider(LLMProvider):
    """
    Provokes a race if MessageTool context is stored in a shared mutable instance.

    "A" waits until "B" has started, then triggers a message tool call without specifying
    channel/chat_id (so it uses defaults).
    """

    def __init__(self) -> None:
        super().__init__(api_key=None, api_base=None)
        self._b_started = asyncio.Event()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        use_fallbacks: bool = True,
    ) -> LLMResponse:
        # Find last user text (no media in this test).
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                c = m.get("content")
                user_text = c if isinstance(c, str) else ""
                break

        tag = "A" if " A" in f" {user_text}" else "B"
        saw_tool_result = any(m.get("role") == "tool" for m in messages)

        if not saw_tool_result:
            if tag == "A":
                await asyncio.wait_for(self._b_started.wait(), timeout=1.0)
            else:
                self._b_started.set()
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCallRequest(
                        id=f"t-{tag}",
                        name="message",
                        arguments={"content": f"hello-{tag}"},
                    )
                ],
            )

        return LLMResponse(content=f"final-{tag}")

    def get_default_model(self) -> str:
        return "test-model"


@pytest.mark.parametrize("concurrent", [True])
async def test_message_tool_context_is_request_scoped(tmp_path, monkeypatch, concurrent: bool) -> None:
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    bus = MessageBus()
    provider = _MessageToolRaceProvider()
    cfg = AgentDefaults(max_tool_iterations=5)
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, agent_config=cfg)

    msg_a = InboundMessage(channel="cli", sender_id="u", chat_id="cA", content="ping A")
    msg_b = InboundMessage(channel="cli", sender_id="u", chat_id="cB", content="ping B")

    if concurrent:
        t1 = asyncio.create_task(loop._process_message(msg_a))
        t2 = asyncio.create_task(loop._process_message(msg_b))
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=3.0)
    else:  # pragma: no cover
        await loop._process_message(msg_a)
        await loop._process_message(msg_b)

    # Two outbound messages from the message tool.
    # Filter out status messages emitted by _emit_status (metadata.type == "status").
    collected: list[tuple[str, str, str]] = []
    deadline = asyncio.get_event_loop().time() + 4.0
    while len(collected) < 2:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        m = await asyncio.wait_for(bus.consume_outbound(), timeout=remaining)
        if isinstance(m.metadata, dict) and m.metadata.get("type") == "status":
            continue
        collected.append((m.channel, m.chat_id, m.content))
    got = set(collected)
    assert got == {
        ("cli", "cA", "hello-A"),
        ("cli", "cB", "hello-B"),
    }

