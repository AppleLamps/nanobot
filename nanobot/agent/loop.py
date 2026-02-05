"""Agent loop: the core processing engine."""

import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMProvider
from nanobot.agent.context import ContextBuilder
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.filesystem import ReadFileTool, WriteFileTool, EditFileTool, ListDirTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebSearchTool, WebFetchTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.subagent import SubagentManager
from nanobot.session.manager import SessionManager


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
        exec_config: "ExecToolConfig | None" = None,
        allowed_tools: list[str] | None = None,
        agent_config: "AgentDefaults | None" = None,
    ):
        from nanobot.config.schema import ExecToolConfig, AgentDefaults
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        cfg = agent_config or AgentDefaults()
        self.model = model or cfg.model or provider.get_default_model()
        self.max_iterations = max_iterations if max_iterations is not None else cfg.max_tool_iterations
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.allowed_tools = allowed_tools
        self.max_tokens = cfg.max_tokens
        self.temperature = cfg.temperature
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
    
    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        # File tools
        self.tools.register(ReadFileTool(
            workspace_root=self.workspace,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        self.tools.register(WriteFileTool(
            workspace_root=self.workspace,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        self.tools.register(EditFileTool(
            workspace_root=self.workspace,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        self.tools.register(ListDirTool(
            workspace_root=self.workspace,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        
        # Shell tool
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.exec_config.restrict_to_workspace,
        ))
        
        # Web tools
        self.tools.register(WebSearchTool(api_key=self.brave_api_key))
        self.tools.register(WebFetchTool())
        
        # Message tool
        message_tool = MessageTool(send_callback=self.bus.publish_outbound)
        self.tools.register(message_tool)
        
        # Spawn tool (for subagents)
        spawn_tool = SpawnTool(manager=self.subagents)
        self.tools.register(spawn_tool)

    def _is_tool_error(self, result: str) -> bool:
        return result.strip().lower().startswith("error:")

    def _get_session_max_tokens(self, session: "Session") -> int:
        if not self.auto_tune_max_tokens:
            return self.max_tokens
        override = session.metadata.get("max_tokens_override")
        if isinstance(override, int) and override > 0:
            return min(override, self.max_tokens)
        initial = self.auto_tune_initial_max_tokens or self.max_tokens
        return min(int(initial), self.max_tokens)

    def _record_usage(self, session: "Session", usage: dict[str, int], max_tokens_used: int) -> None:
        if not usage:
            return
        record = {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "max_tokens": max_tokens_used,
        }
        history = session.metadata.get("usage_history") or []
        if not isinstance(history, list):
            history = []
        history.append(record)
        if len(history) > 20:
            history = history[-20:]
        session.metadata["usage_history"] = history

        prompt_tokens = usage.get("prompt_tokens") or 0
        peak = session.metadata.get("prompt_tokens_peak") or 0
        if prompt_tokens > peak:
            session.metadata["prompt_tokens_peak"] = prompt_tokens
            if peak and prompt_tokens > int(peak * 1.5) and prompt_tokens > 2000:
                logger.warning(
                    f"Prompt tokens spike: {prompt_tokens} (previous peak {peak})"
                )

    def _maybe_autotune_max_tokens(
        self,
        session: "Session",
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
    
    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")
        
        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(
                    self.bus.consume_inbound(),
                    timeout=1.0
                )
                
                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel,
                        chat_id=msg.chat_id,
                        content=f"Sorry, I encountered an error: {str(e)}"
                    ))
            except asyncio.TimeoutError:
                continue
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(
        self,
        msg: InboundMessage,
        session_key_override: str | None = None
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.
        
        Args:
            msg: The inbound message to process.
        
        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        if msg.channel == "system":
            return await self._process_system_message(msg)
        
        logger.info(f"Processing message from {msg.channel}:{msg.sender_id}")
        
        # Get or create session
        session_key = session_key_override or msg.session_key
        session = self.sessions.get_or_create(session_key)
        allowed_tools = session.metadata.get("allowed_tools", self.allowed_tools)
        self.tools.set_allowed_tools(allowed_tools)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(msg.channel, msg.chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(msg.channel, msg.chat_id)
        
        # Build initial messages (use get_history for LLM-formatted messages)
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
        )
        
        # Agent loop
        iteration = 0
        final_content = None
        tool_error_streak = 0
        
        while iteration < self.max_iterations:
            iteration += 1

            max_tokens_used = self._get_session_max_tokens(session)

            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                max_tokens=max_tokens_used,
                temperature=self.temperature,
            )

            self._record_usage(session, response.usage, max_tokens_used)
            
            # Handle tool calls
            if response.has_tool_calls:
                # Tool loop: append assistant tool calls, execute tools, add tool results, then continue.
                # Add assistant message with tool calls
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)  # Must be JSON string
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                # Execute tools (parallelize consecutive parallel-safe calls)
                abort_loop = False
                results = await self.tools.execute_calls(response.tool_calls, allow_parallel=True)
                for tool_call, result in zip(response.tool_calls, results):
                    logger.debug(
                        f"Executing tool: {tool_call.name} with arguments: {tool_call.arguments}"
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    if self.tool_error_backoff > 0:
                        if self._is_tool_error(result):
                            tool_error_streak += 1
                        else:
                            tool_error_streak = 0
                        if tool_error_streak >= self.tool_error_backoff:
                            final_content = (
                                "I'm hitting repeated tool errors. "
                                "Please rephrase or provide more specific inputs."
                            )
                            abort_loop = True
                            break
                if abort_loop:
                    break
            else:
                # No tool calls, we're done
                final_content = response.content
                self._maybe_autotune_max_tokens(
                    session,
                    response.usage.get("completion_tokens") if response.usage else None,
                    max_tokens_used,
                )
                break
        
        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        
        # Save to session
        session.add_message("user", msg.content)
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content
        )
    
    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).
        
        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")
        
        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id
        
        # Use the origin session for context
        session_key = f"{origin_channel}:{origin_chat_id}"
        session = self.sessions.get_or_create(session_key)
        allowed_tools = session.metadata.get("allowed_tools", self.allowed_tools)
        self.tools.set_allowed_tools(allowed_tools)
        
        # Update tool contexts
        message_tool = self.tools.get("message")
        if isinstance(message_tool, MessageTool):
            message_tool.set_context(origin_channel, origin_chat_id)
        
        spawn_tool = self.tools.get("spawn")
        if isinstance(spawn_tool, SpawnTool):
            spawn_tool.set_context(origin_channel, origin_chat_id)
        
        # Build messages with the announce content
        messages = self.context.build_messages(
            history=session.get_history(),
            current_message=msg.content
        )
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        tool_error_streak = 0
        
        while iteration < self.max_iterations:
            iteration += 1
            
            max_tokens_used = self._get_session_max_tokens(session)
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
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
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts
                )
                
                abort_loop = False
                results = await self.tools.execute_calls(response.tool_calls, allow_parallel=True)
                for tool_call, result in zip(response.tool_calls, results):
                    logger.debug(
                        f"Executing tool: {tool_call.name} with arguments: {tool_call.arguments}"
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                    if self.tool_error_backoff > 0:
                        if self._is_tool_error(result):
                            tool_error_streak += 1
                        else:
                            tool_error_streak = 0
                        if tool_error_streak >= self.tool_error_backoff:
                            final_content = (
                                "Background task hit repeated tool errors. "
                                "Please rephrase or provide more specific inputs."
                            )
                            abort_loop = True
                            break
                if abort_loop:
                    break
            else:
                final_content = response.content
                self._maybe_autotune_max_tokens(
                    session,
                    response.usage.get("completion_tokens") if response.usage else None,
                    max_tokens_used,
                )
                break
        
        if final_content is None:
            final_content = "Background task completed."
        
        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)
        
        return OutboundMessage(
            channel=origin_channel,
            chat_id=origin_chat_id,
            content=final_content
        )
    
    async def process_direct(self, content: str, session_key: str = "cli:direct") -> str:
        """
        Process a message directly (for CLI usage).
        
        Args:
            content: The message content.
            session_key: Session identifier.
        
        Returns:
            The agent's response.
        """
        msg = InboundMessage(
            channel="cli",
            sender_id="user",
            chat_id="direct",
            content=content
        )
        
        response = await self._process_message(msg, session_key_override=session_key)
        return response.content if response else ""
