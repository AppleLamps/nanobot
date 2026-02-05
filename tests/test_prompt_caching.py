import os
from pathlib import Path

from nanobot.agent.context import ContextBuilder


def test_bootstrap_prompt_caches_file_reads(tmp_path, monkeypatch) -> None:
    (tmp_path / "AGENTS.md").write_text("v1", encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    builder.BOOTSTRAP_FILES = ["AGENTS.md"]

    first = builder._get_bootstrap_content()
    assert "v1" in first

    def _boom(self: Path, *args, **kwargs) -> str:  # pragma: no cover
        raise AssertionError("read_text should not be called on cache hit")

    monkeypatch.setattr(Path, "read_text", _boom, raising=True)
    second = builder._get_bootstrap_content()
    assert second == first


def test_bootstrap_cache_invalidates_on_mtime_change(tmp_path) -> None:
    p = tmp_path / "AGENTS.md"
    p.write_text("old", encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    builder.BOOTSTRAP_FILES = ["AGENTS.md"]

    first = builder._get_bootstrap_content()
    assert "old" in first

    p.write_text("new", encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))

    second = builder._get_bootstrap_content()
    assert "new" in second
    assert second != first


def test_memory_context_caches_expensive_reads(tmp_path) -> None:
    # Memory is retrieved per request (query-time) and scoped by session to avoid cross-chat leakage.
    mem_dir = tmp_path / "memory"
    (mem_dir / "MEMORY.md").parent.mkdir(parents=True, exist_ok=True)

    # Global memory (shared workspace).
    (mem_dir / "MEMORY.md").write_text("Global: likes apples and coffee.", encoding="utf-8")

    # Session-scoped memories.
    a_dir = mem_dir / "sessions" / "cli_cA"
    b_dir = mem_dir / "sessions" / "cli_cB"
    a_dir.mkdir(parents=True, exist_ok=True)
    b_dir.mkdir(parents=True, exist_ok=True)
    (a_dir / "MEMORY.md").write_text("Alpha: project name is Zorbulator.", encoding="utf-8")
    (b_dir / "MEMORY.md").write_text("Beta: secret token is QUUX-12345.", encoding="utf-8")

    builder = ContextBuilder(tmp_path)

    prompt_a = builder.build_system_prompt(session_key="cli:cA", current_message="Zorbulator", history=[])
    assert "Zorbulator" in prompt_a
    assert "QUUX-12345" not in prompt_a

    # Ensure the prompt clearly shows the session memory path (scoping).
    assert str(a_dir / "MEMORY.md") in prompt_a


def test_skills_summary_cache_invalidates_on_skill_mtime(tmp_path) -> None:
    skill_file = tmp_path / "skill1.md"
    skill_file.write_text("x", encoding="utf-8")

    class _StubSkills:
        def __init__(self, path: Path):
            self._path = path
            self.calls = 0

        def list_skills(self, filter_unavailable: bool = True):
            return [{"name": "skill1", "path": str(self._path), "source": "workspace"}]

        def build_skills_summary(self) -> str:
            self.calls += 1
            return f"summary-{self.calls}"

    builder = ContextBuilder(tmp_path)
    builder.skills = _StubSkills(skill_file)

    s1 = builder._get_skills_summary()
    assert s1 == "summary-1"
    s2 = builder._get_skills_summary()
    assert s2 == "summary-1"
    assert builder.skills.calls == 1

    st = skill_file.stat()
    os.utime(skill_file, (st.st_atime, st.st_mtime + 10))

    s3 = builder._get_skills_summary()
    assert s3 == "summary-2"
    assert builder.skills.calls == 2
