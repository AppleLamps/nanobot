"""Tool for managing running subagents."""

import json
from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


class SubagentControlTool(Tool):
    """Manage running subagents (list, cancel)."""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "subagent_control"

    @property
    def description(self) -> str:
        return (
            "List or cancel running subagents. "
            "Use this to track background tasks or stop one by id."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "cancel"],
                    "description": "Action to perform",
                },
                "task_id": {
                    "type": "string",
                    "description": "Subagent task id (required for cancel)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, task_id: str | None = None, **kwargs: Any) -> str:
        action = (action or "").strip().lower()
        if action == "list":
            data = {"tasks": self._manager.list_running()}
            return json.dumps(data)
        if action == "cancel":
            if not task_id:
                return "Error: task_id is required for cancel"
            ok = self._manager.cancel(task_id)
            return json.dumps({"ok": ok, "task_id": task_id})
        return "Error: unsupported action"
