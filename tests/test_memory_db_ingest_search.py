from nanobot.agent.memory_db import MemoryDB


def test_memory_db_ingest_and_search(tmp_path) -> None:
    db = MemoryDB(tmp_path / "memory.sqlite3")
    note = tmp_path / "note.md"
    note.write_text(
        "Cats are great.\n\nDogs are also great.\n\nThis is a longer paragraph about cats.\n",
        encoding="utf-8",
    )

    db.ingest_file_if_changed(scope="s1", source_key="note", path=note)
    hits = db.search(scope="s1", query_text="cats", limit=5)

    assert hits, "expected at least one hit"
    assert any("cat" in h.content.lower() for h in hits)

