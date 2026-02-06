import pytest

from nanobot.heartbeat.service import HeartbeatService


@pytest.mark.asyncio
async def test_heartbeat_tick_calls_callback_when_file_has_tasks(tmp_path) -> None:
    (tmp_path / "HEARTBEAT.md").write_text("Do the thing\n", encoding="utf-8")

    calls: list[str] = []

    async def on_heartbeat(prompt: str) -> str:
        calls.append(prompt)
        return "HEARTBEAT_OK"

    svc = HeartbeatService(workspace=tmp_path, on_heartbeat=on_heartbeat, interval_s=999999)
    await svc._tick()

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_heartbeat_tick_skips_when_file_empty(tmp_path) -> None:
    # Only headers/checkboxes count as empty.
    (tmp_path / "HEARTBEAT.md").write_text("# Tasks\n\n- [ ]\n", encoding="utf-8")

    called = False

    async def on_heartbeat(prompt: str) -> str:
        nonlocal called
        called = True
        return "HEARTBEAT_OK"

    svc = HeartbeatService(workspace=tmp_path, on_heartbeat=on_heartbeat, interval_s=999999)
    await svc._tick()

    assert called is False

