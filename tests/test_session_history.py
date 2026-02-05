from nanobot.session.manager import Session


def test_get_history_trims_old_messages() -> None:
    session = Session(key="test")

    for i in range(6):
        session.add_message("user", f"msg-{i}")

    history = session.get_history(max_messages=2)

    assert [item["content"] for item in history] == ["msg-4", "msg-5"]
    assert [item["content"] for item in session.messages] == [
        "msg-2",
        "msg-3",
        "msg-4",
        "msg-5",
    ]


def test_get_history_with_zero_max_messages_returns_empty() -> None:
    session = Session(key="test")

    session.add_message("user", "msg-0")

    assert session.get_history(max_messages=0) == []
    assert [item["content"] for item in session.messages] == ["msg-0"]


def test_get_history_boundary_does_not_trim() -> None:
    session = Session(key="test")

    for i in range(4):
        session.add_message("user", f"msg-{i}")

    history = session.get_history(max_messages=2)

    assert [item["content"] for item in history] == ["msg-2", "msg-3"]
    assert [item["content"] for item in session.messages] == [
        "msg-0",
        "msg-1",
        "msg-2",
        "msg-3",
    ]
