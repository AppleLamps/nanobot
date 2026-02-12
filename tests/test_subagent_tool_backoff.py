"""Tests for subagent tool loop resilience: error backoff, response nudge, usage tracking."""

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.base import Tool
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SeqProvider(LLMProvider):
    """Provider that returns a pre-defined sequence of responses."""

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
        use_fallbacks: bool = True,
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


class _OkTool(Tool):
    @property
    def name(self) -> str:
        return "ok_tool"

    @property
    def description(self) -> str:
        return "returns ok"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def _make_manager(
    workspace: Path,
    provider: LLMProvider,
    tool_error_backoff: int = 3,
) -> SubagentManager:
    bus = MessageBus()
    return SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=bus,
        tool_error_backoff=tool_error_backoff,
        progress_interval_s=0,
        subagent_timeout_s=30,
    )


# ---------------------------------------------------------------------------
# Phase 1A: Tool error backoff
# ---------------------------------------------------------------------------

async def test_subagent_tool_error_backoff_aborts(tmp_path: Path) -> None:
    """Subagent tool loop should abort after N consecutive tool errors."""
    provider = _SeqProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="always_error", arguments={})],
        ),
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t2", name="always_error", arguments={})],
        ),
    ])
    mgr = _make_manager(tmp_path, provider, tool_error_backoff=2)

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_AlwaysErrorTool())

    result = await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    assert provider.calls == 2
    assert "too many consecutive tool errors" in result


async def test_subagent_tool_error_streak_resets_on_success(tmp_path: Path) -> None:
    """A successful tool call should reset the error streak."""
    provider = _SeqProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="always_error", arguments={})],
        ),
        # Success resets streak
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t2", name="ok_tool", arguments={})],
        ),
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t3", name="always_error", arguments={})],
        ),
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t4", name="always_error", arguments={})],
        ),
    ])
    mgr = _make_manager(tmp_path, provider, tool_error_backoff=2)

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_AlwaysErrorTool())
    tools.register(_OkTool())

    result = await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    # Should take 4 calls: error, ok (reset), error, error (abort)
    assert provider.calls == 4
    assert "too many consecutive tool errors" in result


async def test_subagent_tool_error_backoff_disabled(tmp_path: Path) -> None:
    """With backoff=0, errors should not cause early abort."""
    provider = _SeqProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="always_error", arguments={})],
        ),
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t2", name="always_error", arguments={})],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])
    mgr = _make_manager(tmp_path, provider, tool_error_backoff=0)

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_AlwaysErrorTool())

    result = await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    assert provider.calls == 3
    assert result == "done"


# ---------------------------------------------------------------------------
# Phase 1B: Response nudge
# ---------------------------------------------------------------------------

async def test_subagent_nudge_on_empty_content(tmp_path: Path) -> None:
    """When LLM returns content=None without tool calls, nudge once, then accept."""
    provider = _SeqProvider([
        # First: tool call
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="ok_tool", arguments={})],
        ),
        # Second: empty content, no tool calls → nudge
        LLMResponse(content=None, tool_calls=[]),
        # Third: real content after nudge
        LLMResponse(content="Here are my findings.", tool_calls=[]),
    ])
    mgr = _make_manager(tmp_path, provider)

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_OkTool())

    result = await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    assert provider.calls == 3
    assert result == "Here are my findings."


async def test_subagent_nudge_only_once(tmp_path: Path) -> None:
    """If content is still None after nudge, should break with fallback."""
    provider = _SeqProvider([
        # First: empty → nudge
        LLMResponse(content=None, tool_calls=[]),
        # Second: still empty → accept None and fall through
        LLMResponse(content=None, tool_calls=[]),
    ])
    mgr = _make_manager(tmp_path, provider)

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()

    result = await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    assert provider.calls == 2
    # Second empty response should produce fallback (iteration limit message)
    assert "no final response was generated" in result


# ---------------------------------------------------------------------------
# Phase 2B: Usage tracking
# ---------------------------------------------------------------------------

async def test_subagent_usage_accumulation(tmp_path: Path) -> None:
    """Usage stats should be accumulated across iterations."""
    provider = _SeqProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="ok_tool", arguments={})],
            usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 0.01},
        ),
        LLMResponse(
            content="done",
            tool_calls=[],
            usage={"prompt_tokens": 200, "completion_tokens": 80, "cost": 0.02},
        ),
    ])
    mgr = _make_manager(tmp_path, provider)

    # Pre-populate task meta so the loop can track usage
    mgr._task_meta["test-task"] = {"id": "test-task", "status": "running"}

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_OkTool())

    await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    meta = mgr._task_meta["test-task"]
    usage = meta["usage"]
    assert usage["prompt_tokens"] == 300
    assert usage["completion_tokens"] == 130
    assert abs(usage["cost"] - 0.03) < 0.001


# ---------------------------------------------------------------------------
# Phase 4B: Iteration limit warning
# ---------------------------------------------------------------------------

async def test_subagent_iteration_limit_message(tmp_path: Path) -> None:
    """When all iterations are tool calls with no final content, the fallback message
    should include the iteration count."""
    # 15 tool calls (max_iterations) with no final content
    responses = [
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id=f"t{i}", name="ok_tool", arguments={})],
        )
        for i in range(15)
    ]
    provider = _SeqProvider(responses)
    mgr = _make_manager(tmp_path, provider, tool_error_backoff=0)

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_OkTool())

    result = await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    assert provider.calls == 15
    assert "15/15" in result
    assert "no final response" in result


# ---------------------------------------------------------------------------
# Phase 4D: Tool log
# ---------------------------------------------------------------------------

async def test_subagent_tool_log(tmp_path: Path) -> None:
    """Tool log should record tool calls with previews."""
    provider = _SeqProvider([
        LLMResponse(
            content=None,
            tool_calls=[ToolCallRequest(id="t1", name="ok_tool", arguments={})],
        ),
        LLMResponse(content="done", tool_calls=[]),
    ])
    mgr = _make_manager(tmp_path, provider)
    mgr._task_meta["test-task"] = {"id": "test-task", "status": "running"}

    from nanobot.agent.tools.registry import ToolRegistry
    tools = ToolRegistry()
    tools.register(_OkTool())

    await mgr._run_tool_loop("test-task", [
        {"role": "system", "content": "test"},
        {"role": "user", "content": "do something"},
    ], tools)

    meta = mgr._task_meta["test-task"]
    assert "tool_log" in meta
    assert len(meta["tool_log"]) == 1
    assert meta["tool_log"][0]["tool"] == "ok_tool"
    assert meta["tool_log"][0]["result_preview"] == "ok"
    assert "timestamp" in meta["tool_log"][0]


# ---------------------------------------------------------------------------
# Phase 2A: Metadata pruning
# ---------------------------------------------------------------------------

async def test_metadata_pruning(tmp_path: Path) -> None:
    """Completed task metadata should be pruned when exceeding limit."""
    provider = _SeqProvider([])
    mgr = _make_manager(tmp_path, provider)
    mgr._max_completed_tasks = 3

    # Add 5 completed tasks
    import time
    for i in range(5):
        tid = f"task-{i}"
        mgr._task_meta[tid] = {
            "id": tid,
            "status": "ok",
            "finished_at": time.time() + i,
        }

    mgr._prune_completed_meta()

    # Should keep the 3 most recent (task-2, task-3, task-4)
    assert len(mgr._task_meta) == 3
    assert "task-0" not in mgr._task_meta
    assert "task-1" not in mgr._task_meta
    assert "task-4" in mgr._task_meta


# ---------------------------------------------------------------------------
# Phase 2C: get_task / list_all
# ---------------------------------------------------------------------------

def test_get_task_returns_meta(tmp_path: Path) -> None:
    """get_task should return full metadata for a known task."""
    provider = _SeqProvider([])
    mgr = _make_manager(tmp_path, provider)
    mgr._task_meta["abc"] = {"id": "abc", "status": "ok", "result": "hello"}

    result = mgr.get_task("abc")
    assert result is not None
    assert result["result"] == "hello"


def test_get_task_returns_none_for_unknown(tmp_path: Path) -> None:
    provider = _SeqProvider([])
    mgr = _make_manager(tmp_path, provider)

    assert mgr.get_task("nonexistent") is None


def test_list_all_includes_completed(tmp_path: Path) -> None:
    provider = _SeqProvider([])
    mgr = _make_manager(tmp_path, provider)
    mgr._task_meta["a"] = {"id": "a", "status": "ok"}
    mgr._task_meta["b"] = {"id": "b", "status": "running"}

    all_tasks = mgr.list_all()
    assert len(all_tasks) == 2
    statuses = {t["status"] for t in all_tasks}
    assert statuses == {"ok", "running"}
