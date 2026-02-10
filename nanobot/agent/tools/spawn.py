"""Spawn tool for creating background subagents."""

from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.agent.subagent import SubagentManager


class SpawnTool(Tool):
    """
    Tool to spawn a subagent for background task execution.

    The subagent runs asynchronously and announces its result back
    to the main agent when complete. It always uses the same model
    as the main agent.
    """

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        self._origin_channel = "cli"
        self._origin_chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the origin context for subagent announcements."""
        self._origin_channel = channel
        self._origin_chat_id = chat_id

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Delegate a task to a background subagent. "
            "USE THIS FOR MOST TASKS — any work requiring 2+ tool calls "
            "(web searches, file ops, commands, research, multi-step work). "
            "The subagent runs asynchronously with full tool access and reports back when done. "
            "This keeps you responsive for conversation while work happens in background. "
            "Use the 'context' parameter to pass relevant conversation details the subagent will need."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Relevant context from the conversation to help "
                        "the subagent understand the task"
                    ),
                },
            },
            "required": ["task"],
        }

    async def execute(
        self, task: str, label: str | None = None, context: str | None = None, **kwargs: Any
    ) -> str:
        """Spawn a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel,
            origin_chat_id=self._origin_chat_id,
            context=context,
        )
