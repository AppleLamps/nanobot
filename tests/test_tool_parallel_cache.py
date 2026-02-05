import asyncio
import os
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.filesystem import ReadFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.providers.base import ToolCallRequest


class _BarrierTool(Tool):
    """Returns ok only if two calls are running concurrently."""

    parallel_safe = True

    @property
    def name(self) -> str:
        return "barrier"

    @property
    def description(self) -> str:
        return "barrier tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"idx": {"type": "integer"}},
            "required": ["idx"],
        }

    def __init__(self) -> None:
        self._started = 0
        self._both_started = asyncio.Event()

    async def execute(self, idx: int, **kwargs: Any) -> str:
        self._started += 1
        if self._started >= 2:
            self._both_started.set()
        try:
            await asyncio.wait_for(self._both_started.wait(), timeout=0.75)
        except asyncio.TimeoutError:
            return "Error: did not run in parallel"
        return f"ok-{idx}"


class _CountingTool(Tool):
    cacheable = True
    cache_ttl_s = 60.0

    @property
    def name(self) -> str:
        return "counting"

    @property
    def description(self) -> str:
        return "counts executions"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }

    def __init__(self) -> None:
        self.calls = 0

    def cache_key(self, params: dict[str, Any]) -> str | None:
        x = params.get("x")
        return str(x) if isinstance(x, int) else None

    async def execute(self, x: int, **kwargs: Any) -> str:
        self.calls += 1
        return f"v{self.calls}:{x}"


class _InFlightTool(Tool):
    cacheable = True
    cache_ttl_s = 60.0

    @property
    def name(self) -> str:
        return "inflight"

    @property
    def description(self) -> str:
        return "waits until released"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }

    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def cache_key(self, params: dict[str, Any]) -> str | None:
        x = params.get("x")
        return str(x) if isinstance(x, int) else None

    async def execute(self, x: int, **kwargs: Any) -> str:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return f"ok:{x}"


async def test_registry_parallelizes_consecutive_parallel_safe_calls() -> None:
    reg = ToolRegistry()
    reg.register(_BarrierTool())

    calls = [
        ToolCallRequest(id="t1", name="barrier", arguments={"idx": 1}),
        ToolCallRequest(id="t2", name="barrier", arguments={"idx": 2}),
    ]
    results = await reg.execute_calls(calls, allow_parallel=True)
    assert results == ["ok-1", "ok-2"]


async def test_registry_caches_tool_results() -> None:
    reg = ToolRegistry()
    tool = _CountingTool()
    reg.register(tool)

    r1 = await reg.execute("counting", {"x": 1})
    r2 = await reg.execute("counting", {"x": 1})
    assert r1 == r2
    assert tool.calls == 1

    r3 = await reg.execute("counting", {"x": 2})
    assert r3 != r2
    assert tool.calls == 2


async def test_registry_dedupes_in_flight_calls_for_same_cache_key() -> None:
    reg = ToolRegistry()
    tool = _InFlightTool()
    reg.register(tool)

    t1 = asyncio.create_task(reg.execute("inflight", {"x": 1}))
    await asyncio.wait_for(tool.started.wait(), timeout=0.75)
    t2 = asyncio.create_task(reg.execute("inflight", {"x": 1}))

    # Let the second task reach the registry and observe the in-flight entry.
    await asyncio.sleep(0)
    assert tool.calls == 1

    tool.release.set()
    assert await t1 == "ok:1"
    assert await t2 == "ok:1"
    assert tool.calls == 1


async def test_read_file_tool_uses_registry_cache_on_repeat(tmp_path, monkeypatch) -> None:
    (tmp_path / "a.txt").write_text("v1", encoding="utf-8")

    reg = ToolRegistry()
    reg.register(ReadFileTool(workspace_root=tmp_path, restrict_to_workspace=True))

    r1 = await reg.execute("read_file", {"path": "a.txt"})
    assert r1 == "v1"

    def _boom(self: Path, *args, **kwargs) -> str:  # pragma: no cover
        raise AssertionError("read_text should not be called on cache hit")

    monkeypatch.setattr(Path, "read_text", _boom, raising=True)
    r2 = await reg.execute("read_file", {"path": "a.txt"})
    assert r2 == "v1"


async def test_read_file_cache_invalidates_on_mtime_change(tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("old", encoding="utf-8")

    reg = ToolRegistry()
    reg.register(ReadFileTool(workspace_root=tmp_path, restrict_to_workspace=True))

    r1 = await reg.execute("read_file", {"path": "a.txt"})
    assert r1 == "old"

    p.write_text("new", encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))

    r2 = await reg.execute("read_file", {"path": "a.txt"})
    assert r2 == "new"
    assert r2 != r1

