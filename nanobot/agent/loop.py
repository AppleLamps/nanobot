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
        max_iterations: int = 20,
        max_tool_calls: int = 40,
        max_no_progress: int = 3,
        tool_retry_attempts: int = 2,
        tool_retry_backoff: float = 0.5,
        enable_planning: bool = True,
        brave_api_key: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        allowed_tools: list[str] | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig
        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.max_no_progress = max_no_progress
        self.tool_retry_attempts = tool_retry_attempts
        self.tool_retry_backoff = tool_retry_backoff
        self.enable_planning = enable_planning
        self.brave_api_key = brave_api_key
        self.exec_config = exec_config or ExecToolConfig()
        self.allowed_tools = allowed_tools
        
        self.context = ContextBuilder(workspace)
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

    async def _build_plan(self, messages: list[dict[str, Any]]) -> str | None:
        """Generate a brief plan before tool execution."""
        if not self.enable_planning:
            return None

        plan_messages = list(messages)
        plan_messages.append({
            "role": "system",
            "content": (
                "Before using any tools, write a brief plan (bullet list). "
                "Do not call tools."
            )
        })

        try:
            response = await self.provider.chat(
                messages=plan_messages,
                tools=[],
                model=self.model
            )
        except Exception as e:
            logger.warning(f"Planning step failed: {e}")
            return None

        if response.content:
            return response.content.strip()
        return None

    async def _execute_tool_with_retries(self, tool_call) -> str:
        """Execute a tool with bounded retries and structured error output."""
        last_error = ""
        for attempt in range(1, self.tool_retry_attempts + 1):
            try:
                return await self.tools.execute(tool_call.name, tool_call.arguments)
            except Exception as e:
                last_error = str(e)
                logger.warning(
                    f"Tool {tool_call.name} failed (attempt {attempt}/{self.tool_retry_attempts}): {e}"
                )
                if attempt < self.tool_retry_attempts:
                    await asyncio.sleep(self.tool_retry_backoff * attempt)

        return (
            "[tool_error]\n"
            f"Tool '{tool_call.name}' failed after {self.tool_retry_attempts} attempts.\n"
            f"Error: {last_error}\n"
            "Hint: check input arguments, permissions, or try an alternative approach."
        )
    
    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")
    
    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
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
        session = self.sessions.get_or_create(msg.session_key)
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

        plan = await self._build_plan(messages)
        if plan:
            messages = self.context.add_assistant_message(messages, f"Plan:\n{plan}")
        
        # Agent loop
        iteration = 0
        final_content = None
        tool_calls_executed = 0
        no_progress_iterations = 0
        last_tool_signature: list[tuple[str, str]] | None = None
        
        while iteration < self.max_iterations:
            iteration += 1
            
            # Call LLM
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            
            # Handle tool calls
            if response.has_tool_calls:
                tool_signature = [
                    (tc.name, json.dumps(tc.arguments, sort_keys=True))
                    for tc in response.tool_calls
                ]
                if tool_signature == last_tool_signature:
                    no_progress_iterations += 1
                else:
                    no_progress_iterations = 0
                last_tool_signature = tool_signature

                if no_progress_iterations >= self.max_no_progress:
                    final_content = (
                        "I seem to be repeating tool calls without making progress. "
                        "Please clarify or rephrase the request."
                    )
                    break

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
                
                # Execute tools
                budget_exhausted = False
                for tool_call in response.tool_calls:
                    if tool_calls_executed >= self.max_tool_calls:
                        final_content = (
                            "Stopped due to tool budget limits. "
                            "Please narrow the request or provide more constraints."
                        )
                        budget_exhausted = True
                        break
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    result = await self._execute_tool_with_retries(tool_call)
                    tool_calls_executed += 1
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                if budget_exhausted:
                    break
            else:
                # No tool calls, we're done
                final_content = response.content
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

        plan = await self._build_plan(messages)
        if plan:
            messages = self.context.add_assistant_message(messages, f"Plan:\n{plan}")
        
        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None
        tool_calls_executed = 0
        no_progress_iterations = 0
        last_tool_signature: list[tuple[str, str]] | None = None
        
        while iteration < self.max_iterations:
            iteration += 1
            
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model
            )
            
            if response.has_tool_calls:
                tool_signature = [
                    (tc.name, json.dumps(tc.arguments, sort_keys=True))
                    for tc in response.tool_calls
                ]
                if tool_signature == last_tool_signature:
                    no_progress_iterations += 1
                else:
                    no_progress_iterations = 0
                last_tool_signature = tool_signature

                if no_progress_iterations >= self.max_no_progress:
                    final_content = (
                        "Background task is repeating tool calls without progress. "
                        "Please clarify or adjust the request."
                    )
                    break

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
                
                budget_exhausted = False
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments)
                    logger.debug(f"Executing tool: {tool_call.name} with arguments: {args_str}")
                    if tool_calls_executed >= self.max_tool_calls:
                        final_content = (
                            "Stopped background task due to tool budget limits. "
                            "Please narrow the request or provide more constraints."
                        )
                        budget_exhausted = True
                        break
                    result = await self._execute_tool_with_retries(tool_call)
                    tool_calls_executed += 1
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                if budget_exhausted:
                    break
            else:
                final_content = response.content
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
        
        response = await self._process_message(msg)
        return response.content if response else ""
