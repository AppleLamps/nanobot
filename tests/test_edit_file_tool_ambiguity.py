from nanobot.agent.tools.filesystem import EditFileTool


async def test_edit_file_tool_ambiguous_match_is_error(tmp_path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("foo\nbar\nfoo\n", encoding="utf-8")

    tool = EditFileTool(workspace_root=tmp_path, restrict_to_workspace=True)
    res = await tool.execute(path=str(p), old_text="foo", new_text="baz")
    assert res.startswith("Error: old_text appears")

