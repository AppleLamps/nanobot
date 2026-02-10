"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import FirecrawlScrapeTool, WebFetchTool, WebSearchTool
from nanobot.agent.utils import is_tool_error
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider

if TYPE_CHECKING:
    from nanobot.agent.context import ContextBuilder
    from nanobot.config.schema import ExecToolConfig


class SubagentManager:
    """
    Manages background subagent execution.
    
    Subagents are lightweight agent instances that run in the background
    to handle specific tasks. They share the same LLM provider but have
    isolated context and a focused system prompt.
    """
    
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        model: str | None = None,
        brave_api_key: str | None = None,
        firecrawl_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        progress_interval_s: int = 15,
        context_builder: ContextBuilder | None = None,
        subagent_timeout_s: int = 300,
        tool_error_backoff: int = 3,
        subagent_bootstrap_chars: int = 3000,
        subagent_context_chars: int = 3000,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.brave_api_key = brave_api_key
        self.firecrawl_api_key = firecrawl_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_meta: dict[str, dict[str, Any]] = {}
        self.progress_interval_s = max(int(progress_interval_s), 0)
        self._context_builder = context_builder
        self.subagent_timeout_s = max(int(subagent_timeout_s), 0)
        self.tool_error_backoff = max(int(tool_error_backoff), 0)
        self._max_completed_tasks = 50
        self.subagent_bootstrap_chars = max(int(subagent_bootstrap_chars), 500)
        self.subagent_context_chars = max(int(subagent_context_chars), 500)
    
    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        context: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        task_id, display_label = await self._spawn_with_id(
            task=task,
            label=label,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            context=context,
        )
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def spawn_task(
        self,
        task: str,
        label: str | None,
        origin_channel: str,
        origin_chat_id: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """Spawn a subagent and return structured task info for control surfaces."""
        task_id, display_label = await self._spawn_with_id(
            task=task,
            label=label,
            origin_channel=origin_channel,
            origin_chat_id=origin_chat_id,
            context=context,
        )
        meta = self._task_meta.get(task_id)
        return {
            "message": f"Subagent [{display_label}] started (id: {task_id}).",
            "task": meta or {"id": task_id, "label": display_label, "task": task},
        }

    async def _spawn_with_id(
        self,
        *,
        task: str,
        label: str | None,
        origin_channel: str,
        origin_chat_id: str,
        context: str | None = None,
    ) -> tuple[str, str]:
        """Internal spawn helper that returns the task id and display label."""
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        origin = {
            "channel": origin_channel,
            "chat_id": origin_chat_id,
        }

        started_at = time.time()
        self._task_meta[task_id] = {
            "id": task_id,
            "label": display_label,
            "task": task,
            "origin": origin,
            "status": "running",
            "started_at": started_at,
        }

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, context)
        )
        self._running_tasks[task_id] = bg_task
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

        logger.info(f"Spawned subagent [{task_id}]: {display_label}")
        return task_id, display_label

    def list_running(self) -> list[dict[str, Any]]:
        """List currently running subagent tasks."""
        items: list[dict[str, Any]] = []
        for task_id in list(self._running_tasks.keys()):
            meta = self._task_meta.get(task_id)
            if meta:
                items.append({
                    "id": task_id,
                    "label": meta.get("label") or "",
                    "task": meta.get("task") or "",
                    "status": meta.get("status") or "running",
                    "started_at": meta.get("started_at") or 0,
                })
        return items

    def cancel(self, task_id: str) -> bool:
        """Cancel a running subagent task."""
        task = self._running_tasks.get(task_id)
        if not task:
            return False
        task.cancel()
        meta = self._task_meta.get(task_id)
        if meta:
            meta["status"] = "cancelled"
            meta["finished_at"] = time.time()
        return True

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Return full metadata for a task (including result and usage), or None."""
        return self._task_meta.get(task_id)

    def list_all(self) -> list[dict[str, Any]]:
        """List all tasks including completed ones."""
        return list(self._task_meta.values())

    def _prune_completed_meta(self) -> None:
        """Evict oldest completed task metadata when count exceeds limit."""
        completed = [
            (tid, m)
            for tid, m in self._task_meta.items()
            if m.get("status") not in ("running", None)
        ]
        if len(completed) <= self._max_completed_tasks:
            return
        completed.sort(key=lambda x: x[1].get("finished_at", 0))
        to_remove = len(completed) - self._max_completed_tasks
        for tid, _ in completed[:to_remove]:
            self._task_meta.pop(tid, None)
    
    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        context: str | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(f"Subagent [{task_id}] starting task: {label}")

        started_at = time.monotonic()
        stop_status = asyncio.Event()
        status_task: asyncio.Task[None] | None = None
        if self.progress_interval_s > 0:
            status_task = asyncio.create_task(
                self._status_loop(label, origin, stop_status, started_at)
            )
        
        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            tools.register(ReadFileTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ))
            tools.register(WriteFileTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ))
            tools.register(EditFileTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ))
            tools.register(ListDirTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ))
            tools.register(ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            ))
            tools.register(WebSearchTool(api_key=self.brave_api_key))
            tools.register(WebFetchTool())
            tools.register(FirecrawlScrapeTool(api_key=self.firecrawl_api_key))

            # Build messages with subagent-specific prompt
            system_prompt = self._build_subagent_prompt(task, context=context)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            # Run agent loop (limited iterations), with overall timeout
            timeout = self.subagent_timeout_s if self.subagent_timeout_s > 0 else None
            final_result = await asyncio.wait_for(
                self._run_tool_loop(task_id, messages, tools),
                timeout=timeout,
            )

            logger.info(f"Subagent [{task_id}] completed successfully")
            meta = self._task_meta.get(task_id)
            if meta:
                meta["status"] = "ok"
                meta["finished_at"] = time.time()
                meta["result"] = final_result[:2000]
                usage = meta.get("usage")
                if usage:
                    logger.info(
                        f"Subagent [{task_id}] usage: "
                        f"prompt={usage.get('prompt_tokens', 0)} "
                        f"completion={usage.get('completion_tokens', 0)}"
                        + (f" cost=${usage['cost']:.4f}" if usage.get("cost") else "")
                    )
            await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except asyncio.TimeoutError:
            timeout_s = self.subagent_timeout_s
            error_msg = f"Error: Subagent timed out after {timeout_s}s"
            logger.error(f"Subagent [{task_id}] timed out after {timeout_s}s")
            meta = self._task_meta.get(task_id)
            if meta:
                meta["status"] = "timeout"
                meta["finished_at"] = time.time()
                meta["result"] = error_msg[:2000]
            await self._announce_result(task_id, label, task, error_msg, origin, "error")

        except asyncio.CancelledError:
            logger.info(f"Subagent [{task_id}] cancelled")
            meta = self._task_meta.get(task_id)
            if meta:
                meta["status"] = "cancelled"
                meta["finished_at"] = time.time()
                meta["result"] = "Task was cancelled."
            await self._announce_result(
                task_id, label, task, "Task was cancelled.", origin, "error"
            )

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.error(f"Subagent [{task_id}] failed: {e}")
            meta = self._task_meta.get(task_id)
            if meta:
                meta["status"] = "error"
                meta["finished_at"] = time.time()
                meta["result"] = error_msg[:2000]
            await self._announce_result(task_id, label, task, error_msg, origin, "error")
        finally:
            self._prune_completed_meta()
            stop_status.set()
            if status_task is not None:
                try:
                    await status_task
                except Exception:
                    pass
    
    async def _run_tool_loop(
        self,
        task_id: str,
        messages: list[dict[str, Any]],
        tools: ToolRegistry,
    ) -> str:
        """Inner tool loop for a subagent. Returns the final text result."""
        max_iterations = 15
        iteration = 0
        final_result: str | None = None
        tool_error_streak = 0
        nudged_for_response = False

        # Usage accumulator
        total_usage: dict[str, float] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost": 0,
        }

        # Tool log for progress tracking
        meta = self._task_meta.get(task_id)
        if meta:
            meta["tool_log"] = []

        while iteration < max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=tools.get_definitions(),
                model=self.model,
            )

            # Accumulate usage
            if response.usage:
                total_usage["prompt_tokens"] += response.usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += response.usage.get("completion_tokens", 0)
                cost = response.usage.get("cost")
                if cost is not None:
                    total_usage["cost"] += cost
            if meta:
                meta["usage"] = total_usage

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in response.tool_calls
                ]
                messages.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tool_call_dicts,
                })

                results = await tools.execute_calls(response.tool_calls, allow_parallel=True)
                abort_loop = False
                for tool_call, result in zip(response.tool_calls, results):
                    logger.debug(f"Subagent [{task_id}] executing: {tool_call.name}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": result,
                    })

                    # Tool log entry
                    if meta is not None:
                        meta["tool_log"].append({
                            "tool": tool_call.name,
                            "timestamp": time.time(),
                            "result_preview": result[:100] if result else "",
                        })

                    # Error backoff tracking
                    if self.tool_error_backoff > 0:
                        if is_tool_error(result):
                            tool_error_streak += 1
                        else:
                            tool_error_streak = 0

                        if tool_error_streak >= self.tool_error_backoff:
                            final_result = (
                                "Task aborted: too many consecutive tool errors."
                            )
                            abort_loop = True
                            break

                if abort_loop:
                    break

                continue

            # No tool calls — check for content
            final_result = response.content

            # Nudge once if the LLM returned empty content without tool calls
            if (
                not nudged_for_response
                and (final_result is None or not final_result.strip())
                and iteration < max_iterations
            ):
                nudged_for_response = True
                messages.append({
                    "role": "user",
                    "content": "Please reply with a brief summary of what you did.",
                })
                final_result = None
                continue

            break

        if final_result is None:
            final_result = (
                f"Task completed but no final response was generated "
                f"(reached {iteration}/{max_iterations} iterations)."
            )

        return final_result

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"
        
        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""
        
        # Inject as system message to trigger main agent
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
        )
        
        await self.bus.publish_inbound(msg)
        logger.debug(f"Subagent [{task_id}] announced result to {origin['channel']}:{origin['chat_id']}")

    async def _status_loop(
        self,
        label: str,
        origin: dict[str, str],
        stop_event: asyncio.Event,
        started_at: float,
    ) -> None:
        """Periodically emit status updates while a subagent is running."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.progress_interval_s)
                break
            except asyncio.TimeoutError:
                pass

            if stop_event.is_set():
                break

            elapsed_s = int(time.monotonic() - started_at)
            msg = OutboundMessage(
                channel=origin["channel"],
                chat_id=origin["chat_id"],
                content=self._format_status_message(label, elapsed_s),
                metadata={"type": "status"},
            )
            try:
                await self.bus.publish_outbound(msg)
            except Exception:
                pass

    def _format_status_message(self, label: str, elapsed_s: int) -> str:
        minutes, seconds = divmod(max(elapsed_s, 0), 60)
        if minutes:
            elapsed = f"{minutes}m {seconds}s"
        else:
            elapsed = f"{seconds}s"
        return f"Background task '{label}' still running ({elapsed})."
    
    def _build_subagent_prompt(self, task: str, *, context: str | None = None) -> str:
        """Build a focused system prompt for the subagent.

        When a ``ContextBuilder`` is available, the prompt is enriched with
        bootstrap files, memory retrieval, and skills summary so the subagent
        has the same workspace knowledge as the main agent.  When no builder
        is set (backward-compat), a minimal identity-only prompt is returned.
        """
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")

        # --- 1. Identity (always present) ---
        identity = f"""# Subagent

You are a subagent spawned by the main agent to complete a specific task.
Follow the project conventions described below.

Current time: {now}

## Your Task
{task}

## Rules
1. Stay focused — complete only the assigned task, nothing else
2. Your final response will be reported back to the main agent
3. Do not initiate conversations or take on side tasks
4. Be concise but informative in your findings

## What You Can Do
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages
- Complete the task thoroughly

## What You Cannot Do
- Send messages directly to users (no message tool available)
- Spawn other subagents

## Workspace
Your workspace is at: {self.workspace}

When you have completed the task, provide a clear summary of your findings or actions."""

        if self._context_builder is None:
            return identity

        sections: list[str] = [identity]

        bootstrap_budget = self.subagent_bootstrap_chars
        context_budget = self.subagent_context_chars

        # --- 2. Bootstrap files ---
        try:
            bootstrap = self._context_builder._get_bootstrap_content()
            if bootstrap:
                sections.append(bootstrap[:bootstrap_budget])
        except Exception:
            pass

        # --- 3. Memory ---
        try:
            memory = self._context_builder._get_memory_section(
                session_key=None,
                memory_scope="global",
                memory_key=None,
                current_message=task,
                history=[],
            )
            if memory:
                sections.append(memory[:bootstrap_budget])
        except Exception:
            pass

        # --- 4. Skills summary (budget: 3000 chars) ---
        try:
            skills = self._context_builder._get_skills_summary()
            if skills:
                skills_section = (
                    "# Skills\n\n"
                    "You can read a skill's SKILL.md file to learn how to use it.\n\n"
                    + skills
                )
                sections.append(skills_section[:3000])
        except Exception:
            pass

        # --- 5. Memory write instruction ---
        memory_dir = self.workspace / "memory"
        sections.append(
            "## Memory\n\n"
            f"You can write durable findings to `{memory_dir}/MEMORY.md` "
            "using the `write_file` tool. This persists across sessions."
        )

        # --- 6. Spawn context ---
        if context:
            sections.append("# Conversation Context\n\n" + context[:context_budget])

        return "\n\n---\n\n".join(sections)
    
    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)
