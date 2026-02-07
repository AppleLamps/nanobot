from nanobot.agent.context import ContextBuilder


def test_requested_skills_are_loaded_into_prompt(tmp_path) -> None:
    # Create a workspace skill and ensure it is only inlined when explicitly requested.
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: my-skill
description: "Test skill"
---

# My Skill

UniqueBodyLine: do-not-accidentally-match-summary
""",
        encoding="utf-8",
    )

    builder = ContextBuilder(tmp_path)

    prompt_without = builder.build_system_prompt(
        session_key="cli:test",
        current_message="hello",
        history=[],
    )
    assert "UniqueBodyLine: do-not-accidentally-match-summary" not in prompt_without

    prompt_with = builder.build_system_prompt(
        ["my-skill"],
        session_key="cli:test",
        current_message="hello",
        history=[],
    )
    assert "UniqueBodyLine: do-not-accidentally-match-summary" in prompt_with

