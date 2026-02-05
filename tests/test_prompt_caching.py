import os
from pathlib import Path

from nanobot.agent.context import ContextBuilder
from nanobot.utils.helpers import today_date


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


def test_memory_context_caches_expensive_reads(tmp_path, monkeypatch) -> None:
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)

    (mem_dir / "MEMORY.md").write_text("long-term", encoding="utf-8")
    (mem_dir / f"{today_date()}.md").write_text("today", encoding="utf-8")

    builder = ContextBuilder(tmp_path)
    first = builder._get_memory_context()
    assert "long-term" in first
    assert "today" in first

    def _boom(*args, **kwargs) -> str:  # pragma: no cover
        raise AssertionError("get_memory_context should not be called on cache hit")

    monkeypatch.setattr(builder.memory, "get_memory_context", _boom, raising=True)
    second = builder._get_memory_context()
    assert second == first


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

