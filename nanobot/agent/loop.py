"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.subagent_control import SubagentControlTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import SessionManager

if TYPE_CHECKING:
    from nanobot.config.schema import AgentDefaults, ExecToolConfig
    from nanobot.session.manager import Session


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int | None = None,
        brave_api_key: str | None = None,
        exec_config: ExecToolConfig | None = None,
        allowed_tools: list[str] | None = None,
        agent_config: AgentDefaults | None = None,
    ):
        from nanobot.config.schema import AgentDefaults, ExecToolConfig

        self.bus = bus
        self.provider = provider
        self.workspace = workspace

        cfg = agent_config or AgentDefaults()

        self.model = model or cfg.model or provider.get_default_model()
        self.max_iterations = (
            max_iterations if max_iterations is not None else cfg.max_tool_iterations
        )
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.allowed_tools = allowed_tools

        self.max_tokens = cfg.max_tokens
        self.temperature = cfg.temperature
        self.memory_scope = (getattr(cfg, "memory_scope", None) or "session").strip().lower()
        self.max_concurrent_messages = max(
            int(getattr(cfg, "max_concurrent_messages", 1) or 1),
            1,
        )

        self.tool_error_backoff = max(int(cfg.tool_error_backoff), 0)
        self.auto_tune_max_tokens = bool(cfg.auto_tune_max_tokens)
        self.auto_tune_initial_max_tokens = cfg.initial_max_tokens
        self.auto_tune_step = max(int(cfg.auto_tune_step), 1)
        self.auto_tune_threshold = float(cfg.auto_tune_threshold)
        self.auto_tune_streak = max(int(cfg.auto_tune_streak), 1)

        self.context = ContextBuilder(
            workspace,
            memory_max_chars=cfg.memory_max_chars,
            skills_max_chars=cfg.skills_max_chars,
            bootstrap_max_chars=cfg.bootstrap_max_chars,
        )
        self.sessions = SessionManager(workspace)

        # This registry is the "base" tool set. Each request gets its own request-scoped
        # ToolRegistry to avoid cross-chat state leaks.
        self.tools = ToolRegistry()

        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            brave_api_key=brave_api_key,
            exec_config=self.exec_config,
        )

        self._running = False
        self._register_default_tools()

    def _session_key_for_inbound(self, msg: InboundMessage) -> str:
        """Return the session key used for ordering/sharding work."""
        if msg.channel != "system":
            return msg.session_key

        # System messages route back to the origin session; keep ordering consistent.
        if ":" in msg.chat_id:
            origin_channel, origin_chat_id = msg.chat_id.split(":", 1)
        else:
            origin_channel, origin_chat_id = "cli", msg.chat_id
        return f"{origin_channel}:{origin_chat_id}"

    def _build_tools_for_request(
        self,
        *,
        channel: str,
        chat_id: str,
        allowed_tools: list[str] | None,
        restrict_workspace: bool | None,
    ) -> ToolRegistry:
        """Build a request-scoped tool registry (no shared mutable per-chat state)."""
        reg = ToolRegistry()

        if isinstance(restrict_workspace, bool):
            reg.register(
                ReadFileTool(
                    workspace_root=self.workspace,
                    restrict_to_workspace=restrict_workspace,
                )
            )
            reg.register(
                WriteFileTool(
                    workspace_root=self.workspace,
                    restrict_to_workspace=restrict_workspace,
                )
            )
            reg.register(
                EditFileTool(
                    workspace_root=self.workspace,
                    restrict_to_workspace=restrict_workspace,
                )
            )
            reg.register(
                ListDirTool(
                    workspace_root=self.workspace,
                    restrict_to_workspace=restrict_workspace,
                )
            )
            reg.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=restrict_workspace,
                )
            )
            reg.register(SubagentControlTool(manager=self.subagents))
            reg.register(WebSearchTool(api_key=self.brave_api_key))
            reg.register(WebFetchTool())
        else:
            # Copy all registered tools, but always override message/spawn with request-scoped instances.
            for tool in self.tools.iter_tools():
                if tool.name in ("message", "spawn"):
                    continue
                reg.register(tool)

        msg_tool = MessageTool(
            send_callback=self.bus.publish_outbound,
            default_channel=channel,
            default_chat_id=chat_id,
        )
        reg.register(msg_tool)

        spawn_tool = SpawnTool(manager=self.subagents)
        spawn_tool.set_context(channel, chat_id)
        reg.register(spawn_tool)

        reg.set_allowed_tools(allowed_tools if isinstance(allowed_tools, list) else None)
        return reg

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools
        self.tools.register(
            ReadFileTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            )
        )
        self.tools.register(
            WriteFileTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            )
        )
        self.tools.register(
            EditFileTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            )
        )
        self.tools.register(
            ListDirTool(
                workspace_root=self.workspace,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            )
        )

        # Shell tool
        self.tools.register(
            ExecTool(
                working_dir=str(self.workspace),
                timeout=self.exec_config.timeout,
                restrict_to_workspace=self.exec_config.restrict_to_workspace,
            )
        )

        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        self.tools.register(SubagentControlTool(manager=self.subagents))

        # Note: message/spawn tools are NOT registered on the base registry.
        # They are always created per-request in _build_tools_for_request() to avoid
        # cross-chat state leaks (default channel/chat_id context).

    def _is_tool_error(self, result: str) -> bool:
        s = result.strip().lower()
        return s.startswith("error:") or s.startswith("warning:")

    def _get_session_max_tokens(self, session: Session) -> int:
        if not self.auto_tune_max_tokens:
            return self.max_tokens
        override = session.metadata.get("max_tokens_override")
        if isinstance(override, int) and override > 0:
            return min(override, self.max_tokens)
        initial = self.auto_tune_initial_max_tokens or self.max_tokens
        return min(int(initial), self.max_tokens)

    def _record_usage(self, session: Session, usage: dict[str, int], max_tokens_used: int) -> None:
        if not usage:
            return
        record = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "max_tokens": max_tokens_used,
            "cost": usage.get("cost"),
        }
        history = session.metadata.get("usage_history") or []
        if not isinstance(history, list):
            history = []
        history.append(record)
        if len(history) > 20:
            history = history[-20:]
        session.metadata["usage_history"] = history

        cost = usage.get("cost")
        if cost is not None:
            session.metadata["session_cost"] = (session.metadata.get("session_cost") or 0) + cost

        prompt_tokens = usage.get("prompt_tokens") or 0
        peak = session.metadata.get("prompt_tokens_peak") or 0
        if prompt_tokens > peak:
            session.metadata["prompt_tokens_peak"] = prompt_tokens
            if peak and prompt_tokens > int(peak * 1.5) and prompt_tokens > 2000:
                logger.warning(f"Prompt tokens spike: {prompt_tokens} (previous peak {peak})")

    def _maybe_autotune_max_tokens(
        self,
        session: Session,
        completion_tokens: int | None,
        max_tokens_used: int,
    ) -> None:
        if not self.auto_tune_max_tokens or not completion_tokens:
            return
        threshold = int(max_tokens_used * self.auto_tune_threshold)
        streak = session.metadata.get("token_tune_streak") or 0
        if completion_tokens >= threshold:
            streak += 1
        else:
            streak = 0

        if streak >= self.auto_tune_streak:
            new_max = min(self.max_tokens, max_tokens_used + self.auto_tune_step)
            if new_max > max_tokens_used:
                session.metadata["max_tokens_override"] = new_max
            streak = 0

        session.metadata["token_tune_streak"] = streak

    async def _emit_status(self, channel: str, chat_id: str, content: str) -> None:
        if not content:
            return
        try:
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=content,
                    metadata={"type": "status"},
                )
            )
        except Exception:
            pass

    def _format_tool_status(self, tool_name: str, args: dict[str, Any]) -> str:
        if tool_name == "web_fetch":
            url = str(args.get("url") or "").strip()
            host = ""
            try:
                from urllib.parse import urlparse

                host = urlparse(url).netloc
            except Exception:
                host = ""
            return f"Fetching {host or 'a web page'}..."
        if tool_name == "web_search":
            q = str(args.get("query") or "").strip()
            return f"Searching the web{': ' + q if q else '...'}"
        if tool_name == "read_file":
            p = str(args.get("path") or args.get("filePath") or "").strip()
            return f"Reading {p or 'a file'}..."
        if tool_name == "write_file":
            p = str(args.get("path") or args.get("filePath") or "").strip()
            return f"Writing {p or 'a file'}..."
        if tool_name == "edit_file":
            p = str(args.get("path") or args.get("filePath") or "").strip()
            return f"Editing {p or 'a file'}..."
        if tool_name == "list_dir":
            p = str(args.get("path") or "").strip()
            return f"Listing {p or 'a folder'}..."
        if tool_name == "exec":
            return "Running a command..."
        if tool_name == "message":
            return "Sending a message..."
        if tool_name == "spawn":
            return "Starting a background task..."
        return "Working on it..."

    async def _maybe_emit_tool_status(
        self,
        channel: str,
        chat_id: str,
        tool_name: str,
        args: dict[str, Any],
        last_status_ts: float,
        min_interval_s: float = 2.0,
    ) -> float:
        now = time.monotonic()
        if now - last_status_ts < min_interval_s:
            return last_status_ts
        msg = self._format_tool_status(tool_name, args)
        await self._emit_status(channel, chat_id, msg)
        return now

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        sem = asyncio.Semaphore(max(int(self.max_concurrent_messages), 1))
        tails: dict[str, asyncio.Task[None]] = {}
        tasks: set[asyncio.Task[None]] = set()

        async def _process_one(msg: InboundMessage, session_key: str) -> None:
            prev = tails.get(session_key)
            if prev is not None:
                try:
                    await prev
                except Exception:
                    # If a previous message failed, still allow new messages to proceed.
                    pass

            async with sem:
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            channel=msg.channel,
                            chat_id=msg.chat_id,
                            content=f"Sorry, I encountered an error: {str(e)}",
                        )
                    )

        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                session_key = self._session_key_for_inbound(msg)
                t = asyncio.create_task(_process_one(msg, session_key))
                tails[session_key] = t
                tasks.add(t)

                def _cleanup(done: asyncio.Task[None], *, sk: str = session_key) -> None:
                    tasks.discard(done)
                    if tails.get(sk) is done:
                        tails.pop(sk, None)

                t.add_done_callback(_cleanup)
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _run_tool_loop(
        self,
        *,
        session: Session,
        messages: list[dict[str, Any]],
        tools: ToolRegistry,
        tool_error_backoff_message: str,
        no_response_message: str,
        channel: str,
        chat_id: str,
        verbosity: str,
        model: str | None = None,
    ) -> str:
        iteration = 0
        final_content: str | None = None
        tool_error_streak = 0
        last_status_ts = 0.0
        nudged_for_response = False

        chosen_model = (model or "").strip() or self.model

        while iteration < self.max_iterations:
            iteration += 1

            max_tokens_used = self._get_session_max_tokens(session)

            response = await self.provider.chat(
                messages=messages,
                tools=tools.get_definitions(),
                model=chosen_model,
                max_tokens=max_tokens_used,
                temperature=self.temperature,
            )

            self._record_usage(session, response.usage, max_tokens_used)

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
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )

                abort_loop = False
                for tool_call in response.tool_calls:
                    logger.debug(
                        f"Executing tool: {tool_call.name} with arguments: {tool_call.arguments}"
                    )
                    if channel and chat_id:
                        v = (verbosity or "normal").strip().lower()
                        min_interval = 2.0
                        if v == "low":
                            min_interval = 5.0
                        elif v == "high":
                            min_interval = 0.8
                        last_status_ts = await self._maybe_emit_tool_status(
                            channel,
                            chat_id,
                            tool_call.name,
                            tool_call.arguments,
                            last_status_ts,
                            min_interval_s=min_interval,
                        )
                results = await tools.execute_calls(response.tool_calls, allow_parallel=True)
                for tool_call, result in zip(response.tool_calls, results):
                    messages = self.context.add_tool_result(
                        messages,
                        tool_call.id,
                        tool_call.name,
                        result,
                    )

                    if self.tool_error_backoff > 0:
                        if self._is_tool_error(result):
                            tool_error_streak += 1
                        else:
                            tool_error_streak = 0

                        if tool_error_streak >= self.tool_error_backoff:
                            final_content = tool_error_backoff_message
                            abort_loop = True
                            break

                if abort_loop:
                    break

                continue

            final_content = response.content
            self._maybe_autotune_max_tokens(
                session,
                response.usage.get("completion_tokens") if response.usage else None,
                max_tokens_used,
            )

            # Some models return content=None without tool calls when they
            # consider the task "done".  Nudge once to get a text summary.
            if not nudged_for_response and (final_content is None or not final_content.strip()) and iteration < self.max_iterations:
                nudged_for_response = True
                messages.append({
                    "role": "user",
                    "content": "Please reply with a brief summary of what you did.",
                })
                final_content = None
                continue

            break

        if final_content is None:
            final_content = no_response_message

        return final_content

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key_override: str | None = None,
    ) -> OutboundMessage | None:
        """Process a single inbound message."""
        # Handle system messages (subagent announces).
        # The chat_id contains the original "channel:chat_id" to route back to.
        if msg.channel == "system":
            return await self._process_system_message(msg)

        control_response = await self._process_control_message(msg)
        if control_response is not None:
            return control_response

        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")

        meta_override = None
        try:
            meta_override = msg.metadata.get("session_key") if isinstance(msg.metadata, dict) else None
        except Exception:
            meta_override = None

        session_key = session_key_override or (meta_override if isinstance(meta_override, str) and meta_override else None) or msg.session_key
        session = self.sessions.get_or_create(session_key)
        allowed_tools = session.metadata.get("allowed_tools", self.allowed_tools)
        restrict_workspace = session.metadata.get("restrict_workspace")

        # Per-session model override (set by WebUI model switcher, etc.).
        chosen_model: str | None = None
        try:
            m = session.metadata.get("model")
            if isinstance(m, str) and m.strip():
                chosen_model = m.strip()
        except Exception:
            chosen_model = None
        if not chosen_model:
            try:
                if isinstance(msg.metadata, dict):
                    m2 = msg.metadata.get("model")
                    if isinstance(m2, str) and m2.strip():
                        chosen_model = m2.strip()
            except Exception:
                chosen_model = None

        tools = self._build_tools_for_request(
            channel=msg.channel,
            chat_id=msg.chat_id,
            allowed_tools=allowed_tools if isinstance(allowed_tools, list) else None,
            restrict_workspace=restrict_workspace if isinstance(restrict_workspace, bool) else None,
        )

        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            session_key=session_key,
            memory_scope=self.memory_scope,
            memory_key=(f"{msg.channel}:{msg.sender_id}" if self.memory_scope == "user" else session_key),
            media=msg.media if msg.media else None,
        )

        final_content = await self._run_tool_loop(
            session=session,
            messages=messages,
            tools=tools,
            tool_error_backoff_message=(
                "I'm hitting repeated tool errors. "
                "Please rephrase or provide more specific inputs."
            ),
            no_response_message="I've completed processing but have no response to give.",
            channel=msg.channel,
            chat_id=msg.chat_id,
            verbosity=str(session.metadata.get("verbosity") or "normal"),
            model=chosen_model,
        )

        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        await self.sessions.save_async(session)

        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
        )

    async def _process_control_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """Handle non-LLM control messages (WebUI subagent controls)."""
        if not isinstance(msg.metadata, dict):
            return None
        control = msg.metadata.get("control")
        if not isinstance(control, dict):
            return None
        if msg.channel != "webui":
            return None

        action = str(control.get("action") or "").strip().lower()
        if action == "subagent_list":
            tasks = self.subagents.list_running()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={"type": "subagents", "data": {"tasks": tasks}},
            )
        if action == "subagent_spawn":
            task = str(control.get("task") or "").strip()
            if not task:
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content="Missing task.",
                    metadata={"type": "subagent_event", "data": {"ok": False}},
                )
            label = str(control.get("label") or "").strip() or None
            result = await self.subagents.spawn_task(
                task=task,
                label=label,
                origin_channel=msg.channel,
                origin_chat_id=msg.chat_id,
            )
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=result.get("message") or "",
                metadata={"type": "subagent_event", "data": {"ok": True, **result}},
            )
        if action == "subagent_cancel":
            task_id = str(control.get("task_id") or "").strip()
            ok = self.subagents.cancel(task_id) if task_id else False
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="",
                metadata={
                    "type": "subagent_event",
                    "data": {"ok": ok, "task_id": task_id},
                },
            )

        return None

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """Process a system message (e.g., subagent announce)."""
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "channel:chat_id").
        if ":" in msg.chat_id:
            origin_channel, origin_chat_id = msg.chat_id.split(":", 1)
        else:
            origin_channel, origin_chat_id = "cli", msg.chat_id

        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        allowed_tools = session.metadata.get("allowed_tools", self.allowed_tools)
        restrict_workspace = session.metadata.get("restrict_workspace")

        chosen_model: str | None = None
        try:
            m = session.metadata.get("model")
            if isinstance(m, str) and m.strip():
                chosen_model = m.strip()
        except Exception:
            chosen_model = None

        tools = self._build_tools_for_request(
            channel=origin_channel,
            chat_id=origin_chat_id,
            allowed_tools=allowed_tools if isinstance(allowed_tools, list) else None,
            restrict_workspace=restrict_workspace if isinstance(restrict_workspace, bool) else None,
        )

        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            session_key=session_key,
            # System messages should not attribute memory to the subagent sender_id.
            memory_scope="session",
            memory_key=session_key,
        )

        final_content = await self._run_tool_loop(
            session=session,
            messages=messages,
            tools=tools,
            tool_error_backoff_message=(
                "Background task hit repeated tool errors. "
                "Please rephrase or provide more specific inputs."
            ),
            no_response_message="Background task completed.",
            channel=origin_channel,
            chat_id=origin_chat_id,
            verbosity=str(session.metadata.get("verbosity") or "normal"),
            model=chosen_model,
        )

        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        await self.sessions.save_async(session)

        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        *,
        media: list[str] | None = None,
    ) -> str:
        """Process a message directly (for CLI usage)."""
        msg = InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content=content,
            media=media or [],
        )

        response = await self._process_message(msg, session_key_override=session_key)
        return response.content if response else ""
