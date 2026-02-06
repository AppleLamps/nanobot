import os

from nanobot.agent.tools.shell import ExecTool


def test_exec_tool_strips_common_secret_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("MY_SECRET_TOKEN", "t")
    monkeypatch.setenv("NORMAL_VAR", "ok")

    tool = ExecTool()
    env = tool._build_subprocess_env()

    assert "NORMAL_VAR" in env
    assert env["NORMAL_VAR"] == "ok"
    assert "OPENAI_API_KEY" not in env
    assert "MY_SECRET_TOKEN" not in env

    # Ensure we didn't accidentally mutate the process env.
    assert os.environ.get("OPENAI_API_KEY") == "k"
    assert os.environ.get("MY_SECRET_TOKEN") == "t"

