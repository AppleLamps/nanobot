"""Tests for the 6 improvements: history compression, subagent timeout,
provider validation, tool descriptions, session retry, and structured logging."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.config.schema import Config


# ---------------------------------------------------------------------------
# 1. History compression
# ---------------------------------------------------------------------------


def test_history_trim_drops_oldest_messages(tmp_path: Path) -> None:
    """When history exceeds budget, oldest messages are dropped with a note."""
    builder = ContextBuilder(tmp_path, history_max_chars=100)
    history = [
        {"role": "user", "content": "A" * 60},
        {"role": "assistant", "content": "B" * 60},
        {"role": "user", "content": "C" * 30},
    ]
    trimmed = builder._trim_history(history)

    # The first message(s) should be dropped, a note prepended.
    assert trimmed[0]["content"].startswith("[System note:")
    assert "omitted" in trimmed[0]["content"]
    # The last message must survive.
    assert any("C" * 30 in m["content"] for m in trimmed)


def test_history_trim_noop_when_under_budget(tmp_path: Path) -> None:
    builder = ContextBuilder(tmp_path, history_max_chars=10000)
    history = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]
    trimmed = builder._trim_history(history)
    assert trimmed == history


def test_history_trim_zero_budget_returns_all(tmp_path: Path) -> None:
    """history_max_chars=0 disables trimming entirely."""
    builder = ContextBuilder(tmp_path, history_max_chars=0)
    history = [{"role": "user", "content": "X" * 9999}]
    assert builder._trim_history(history) == history


# ---------------------------------------------------------------------------
# 2. Subagent timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_timeout_announces_error(tmp_path: Path) -> None:
    """A subagent that exceeds its timeout should announce a timeout error."""
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    provider = AsyncMock()
    provider.get_default_model.return_value = "test-model"

    # Simulate an LLM call that never finishes.
    async def _hang(*a, **kw):
        await asyncio.sleep(999)

    provider.chat = _hang

    bus = MessageBus()
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        subagent_timeout_s=1,  # 1-second timeout
        progress_interval_s=0,
    )

    await mgr.spawn(task="Hang forever", origin_channel="test", origin_chat_id="c1")

    # Wait for the timeout to fire and the announce message to arrive.
    msg = await asyncio.wait_for(bus.consume_inbound(), timeout=5)
    assert "timed out" in msg.content.lower() or "timeout" in msg.content.lower()


# ---------------------------------------------------------------------------
# 3. Provider validation
# ---------------------------------------------------------------------------


def test_validate_provider_no_key_warns() -> None:
    config = Config()
    # All keys empty by default.
    warnings = config.validate_provider()
    assert any("No API key" in w for w in warnings)


def test_validate_provider_mismatch_warns() -> None:
    config = Config()
    config.providers.openai.api_key = "sk-test"
    config.agents.defaults.model = "anthropic/claude-3-haiku"
    warnings = config.validate_provider()
    assert any("anthropic" in w.lower() for w in warnings)


def test_validate_provider_openrouter_no_warn() -> None:
    """OpenRouter can serve any model prefix, so no mismatch warning."""
    config = Config()
    config.providers.openrouter.api_key = "sk-or-test"
    config.agents.defaults.model = "anthropic/claude-3-haiku"
    warnings = config.validate_provider()
    assert not warnings


# ---------------------------------------------------------------------------
# 4. Tool descriptions (spot checks)
# ---------------------------------------------------------------------------


def test_edit_file_description_mentions_unique() -> None:
    from nanobot.agent.tools.filesystem import EditFileTool

    tool = EditFileTool()
    assert "unique" in tool.description.lower()


def test_exec_description_mentions_timeout() -> None:
    from nanobot.agent.tools.shell import ExecTool

    tool = ExecTool(timeout=30)
    assert "30s" in tool.description


def test_web_search_description_mentions_brave() -> None:
    from nanobot.agent.tools.web import WebSearchTool

    tool = WebSearchTool()
    assert "Brave" in tool.description


# ---------------------------------------------------------------------------
# 5. Session save retry + load validation
# ---------------------------------------------------------------------------


def test_session_load_skips_malformed_lines(tmp_path: Path) -> None:
    from nanobot.session.manager import SessionManager

    mgr = SessionManager(tmp_path)
    path = mgr._get_session_path("test:key")
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        # Valid metadata
        f.write(json.dumps({"_type": "metadata", "key": "test:key", "metadata": {}}) + "\n")
        # Valid message
        f.write(json.dumps({"role": "user", "content": "Hello"}) + "\n")
        # Malformed line
        f.write("this is not json\n")
        # Missing role
        f.write(json.dumps({"content": "orphan"}) + "\n")
        # Valid message
        f.write(json.dumps({"role": "assistant", "content": "Hi"}) + "\n")

    session = mgr._load("test:key")
    assert session is not None
    assert len(session.messages) == 2
    assert session.messages[0]["content"] == "Hello"
    assert session.messages[1]["content"] == "Hi"


# ---------------------------------------------------------------------------
# 6. Structured logging (just verify no crash; actual log output is best
#    tested manually)
# ---------------------------------------------------------------------------


def test_context_builder_trim_logs(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """History trimming should produce a log message."""
    builder = ContextBuilder(tmp_path, history_max_chars=50)
    history = [
        {"role": "user", "content": "X" * 100},
        {"role": "assistant", "content": "Y" * 100},
    ]
    with caplog.at_level("DEBUG", logger="nanobot.agent.context"):
        builder._trim_history(history)
    # loguru doesn't integrate with caplog by default, so just verify no crash.
    # The log line is verified manually via `nanobot agent -v`.
