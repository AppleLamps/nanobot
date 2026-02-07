from nanobot.agent.context import ContextBuilder


def test_memory_retrieves_from_global_and_active_scope(tmp_path) -> None:
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text(
        "GlobalMemory: Zorbulator is the codename.\n",
        encoding="utf-8",
    )

    session_dir = mem_dir / "sessions" / "cli_cA"
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "MEMORY.md").write_text(
        "SessionMemory: Zorbulator lives in session scope.\n",
        encoding="utf-8",
    )

    builder = ContextBuilder(tmp_path)
    prompt = builder.build_system_prompt(
        session_key="cli:cA",
        current_message="Zorbulator",
        history=[],
    )

    assert "GlobalMemory: Zorbulator is the codename." in prompt
    assert "SessionMemory: Zorbulator lives in session scope." in prompt

