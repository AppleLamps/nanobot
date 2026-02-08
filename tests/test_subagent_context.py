"""Tests for subagent context enrichment (bootstrap, memory, skills, context)."""

from pathlib import Path
from unittest.mock import AsyncMock

from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.bus.queue import MessageBus


def _make_manager(
    workspace: Path, context_builder: ContextBuilder | None = None
) -> SubagentManager:
    provider = AsyncMock()
    provider.get_default_model.return_value = "test-model"
    bus = MessageBus()
    return SubagentManager(
        provider=provider,
        workspace=workspace,
        bus=bus,
        context_builder=context_builder,
    )


def test_subagent_prompt_includes_bootstrap(tmp_path: Path) -> None:
    """Bootstrap file content (e.g. TOOLS.md) should appear in the subagent prompt."""
    (tmp_path / "TOOLS.md").write_text(
        "Use pytest for testing. Always run ruff before committing.\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(tmp_path)
    mgr = _make_manager(tmp_path, context_builder=builder)
    prompt = mgr._build_subagent_prompt("Do something")

    assert "Use pytest for testing" in prompt
    assert "Always run ruff before committing" in prompt


def test_subagent_prompt_includes_memory(tmp_path: Path) -> None:
    """Global memory hits should appear when the task matches a stored keyword."""
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text(
        "GlobalFact: The deploy target is staging-west.\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(tmp_path)
    mgr = _make_manager(tmp_path, context_builder=builder)
    prompt = mgr._build_subagent_prompt("Deploy to staging")

    assert "deploy target is staging-west" in prompt


def test_subagent_prompt_includes_skills_summary(tmp_path: Path) -> None:
    """Skill names from the workspace should appear in the subagent prompt."""
    skill_dir = tmp_path / "skills" / "code-review"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Reviews code for issues\n---\nReview instructions here.\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(tmp_path)
    mgr = _make_manager(tmp_path, context_builder=builder)
    prompt = mgr._build_subagent_prompt("Review the PR")

    assert "code-review" in prompt
    assert "SKILL.md" in prompt


def test_subagent_prompt_includes_spawn_context(tmp_path: Path) -> None:
    """The optional context parameter should appear under a Conversation Context header."""
    builder = ContextBuilder(tmp_path)
    mgr = _make_manager(tmp_path, context_builder=builder)
    prompt = mgr._build_subagent_prompt(
        "Apply the user's preferences", context="user wants dark theme with rounded corners"
    )

    assert "# Conversation Context" in prompt
    assert "user wants dark theme with rounded corners" in prompt


def test_subagent_prompt_without_context_builder(tmp_path: Path) -> None:
    """Without a ContextBuilder the prompt should still be valid (backward compat)."""
    mgr = _make_manager(tmp_path, context_builder=None)
    prompt = mgr._build_subagent_prompt("Do something simple")

    assert "# Subagent" in prompt
    assert "Do something simple" in prompt
    # Should NOT contain enrichment sections
    assert "# Skills" not in prompt
    assert "# Conversation Context" not in prompt
