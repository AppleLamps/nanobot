from nanobot.agent.context import ContextBuilder
from nanobot.agent.skills import SkillsLoader


def test_skills_summary_cache_invalidates_on_env_change(tmp_path, monkeypatch) -> None:
    # Create a skill that depends on an env var so availability can flip without touching mtimes.
    skill_dir = tmp_path / "skills" / "env-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: env-skill
description: "Env-gated skill"
metadata: {"nanobot":{"requires":{"env":["NANOBOT_TEST_REQUIRED_ENV"]}}}
---

# Env Skill
""",
        encoding="utf-8",
    )

    builder = ContextBuilder(tmp_path)
    # Disable builtin skills so this test only depends on the skill we created above.
    builder.skills = SkillsLoader(tmp_path, builtin_skills_dir=tmp_path / "no-builtin-skills-here")

    monkeypatch.delenv("NANOBOT_TEST_REQUIRED_ENV", raising=False)
    s1 = builder._get_skills_summary()
    s2 = builder._get_skills_summary()
    assert s2 == s1

    monkeypatch.setenv("NANOBOT_TEST_REQUIRED_ENV", "1")
    s3 = builder._get_skills_summary()
    assert s3 != s1

