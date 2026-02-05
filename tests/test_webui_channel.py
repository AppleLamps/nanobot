import asyncio
import json

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.webui import WebUIChannel
from nanobot.config.schema import WebUIConfig


@pytest.mark.asyncio
async def test_webui_channel_receives_inbound_and_sends_outbound() -> None:
    import websockets

    bus = MessageBus()
    cfg = WebUIConfig(enabled=True, host="127.0.0.1", port=0)
    ch = WebUIChannel(cfg, bus)

    task = asyncio.create_task(ch.start())
    try:
        await ch.wait_started()
        assert ch.bound_port is not None and ch.bound_port > 0

        uri = f"ws://127.0.0.1:{ch.bound_port}/ws?chat_id=testchat&sender_id=testsender"
        async with websockets.connect(uri) as ws:
            # Server sends a session announcement first.
            hello = json.loads(await ws.recv())
            assert hello["type"] == "session"
            assert hello["chat_id"] == "testchat"
            assert hello["sender_id"] == "testsender"

            await ws.send(json.dumps({"type": "message", "content": "hello"}))
            inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=2.0)
            assert inbound.channel == "webui"
            assert inbound.chat_id == "testchat"
            assert inbound.sender_id == "testsender"
            assert inbound.content == "hello"

            await ch.send(OutboundMessage(channel="webui", chat_id="testchat", content="hi there"))
            out = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))
            assert out["type"] == "assistant"
            assert out["chat_id"] == "testchat"
            assert out["content"] == "hi there"
    finally:
        await ch.stop()
        await asyncio.wait_for(task, timeout=2.0)


@pytest.mark.asyncio
async def test_webui_channel_auth_token_blocks_unauthorized_clients() -> None:
    import websockets

    bus = MessageBus()
    cfg = WebUIConfig(enabled=True, host="127.0.0.1", port=0, auth_token="secret")
    ch = WebUIChannel(cfg, bus)

    task = asyncio.create_task(ch.start())
    try:
        await ch.wait_started()
        assert ch.bound_port is not None and ch.bound_port > 0

        # No token: server should send an error then close.
        uri = f"ws://127.0.0.1:{ch.bound_port}/ws?chat_id=x&sender_id=y"
        async with websockets.connect(uri) as ws:
            msg = json.loads(await ws.recv())
            assert msg["type"] == "error"
            assert "unauthorized" in (msg["error"] or "")

        # With token: should succeed.
        uri2 = f"ws://127.0.0.1:{ch.bound_port}/ws?chat_id=x&sender_id=y&token=secret"
        async with websockets.connect(uri2) as ws:
            msg2 = json.loads(await ws.recv())
            assert msg2["type"] == "session"
            assert msg2["chat_id"] == "x"
    finally:
        await ch.stop()
        await asyncio.wait_for(task, timeout=2.0)

