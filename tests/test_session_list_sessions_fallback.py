import json
import pathlib

from nanobot.session.manager import SessionManager


def test_list_sessions_fallback_does_not_corrupt_underscores(tmp_path, monkeypatch) -> None:
    # Ensure sessions are stored under the temp home.
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))

    mgr = SessionManager(workspace=tmp_path)

    # Create a "legacy/corrupt" metadata line missing the key.
    path = mgr.sessions_dir / "telegram_user_123.jsonl"
    meta = {"_type": "metadata", "created_at": "2026-02-06T00:00:00", "updated_at": "2026-02-06T00:00:00"}
    path.write_text(json.dumps(meta) + "\n", encoding="utf-8")

    sessions = mgr.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["key"] == "telegram_user_123"

