"""Context builder for assembling agent prompts."""

import base64
import mimetypes
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """
    
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    
    def __init__(
        self,
        workspace: Path,
        memory_max_chars: int = 6000,
        skills_max_chars: int = 12000,
        bootstrap_max_chars: int = 4000,
    ):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
        self.memory_max_chars = max(int(memory_max_chars), 0)
        self.skills_max_chars = max(int(skills_max_chars), 0)
        self.bootstrap_max_chars = max(int(bootstrap_max_chars), 0)
        self._cache: dict[str, tuple[tuple, str]] = {}
    
    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
        
        Returns:
            Complete system prompt.
        """
        parts = []

        # Core identity (always fresh for current time)
        parts.append(self._get_identity())

        # Cached prompt tail (bootstrap, memory, skills)
        tail = self._get_prompt_tail()
        if tail:
            parts.append(tail)

        return "\n\n---\n\n".join(parts)
    
    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())
        
        return f"""# nanobot 🐈

You are nanobot, a helpful AI assistant. You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Current Time
{now}

## Workspace
Your workspace is at: {workspace_path}
- Memory files: {workspace_path}/memory/MEMORY.md
- Daily notes: {workspace_path}/memory/YYYY-MM-DD.md
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
For normal conversation, just respond with text - do not call the message tool.

Always be helpful, accurate, and concise. When using tools, explain what you're doing.
When remembering something, write to {workspace_path}/memory/MEMORY.md"""
    
    def _get_cached(self, key: str, signature: tuple) -> str | None:
        cached = self._cache.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        return None

    def _set_cache(self, key: str, signature: tuple, value: str) -> None:
        self._cache[key] = (signature, value)

    def _truncate_tail(self, text: str, max_chars: int, label: str) -> str:
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        return f"[truncated {label} to last {max_chars} chars]\n" + text[-max_chars:]

    def _get_bootstrap_content(self) -> str:
        """Load bootstrap files from workspace with caching and truncation."""
        files: list[tuple[str, float]] = []
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                try:
                    mtime = file_path.stat().st_mtime
                except Exception:
                    mtime = 0.0
                files.append((str(file_path), mtime))

        signature = (tuple(files), self.bootstrap_max_chars)
        cached = self._get_cached("bootstrap", signature)
        if cached is not None:
            return cached

        parts = []
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8", errors="replace")
                parts.append(f"## {filename}\n\n{content}")

        text = "\n\n".join(parts) if parts else ""
        if text:
            text = self._truncate_tail(text, self.bootstrap_max_chars, "bootstrap")

        self._set_cache("bootstrap", signature, text)
        return text

    def _get_memory_context(self) -> str:
        """Load memory context with caching and truncation."""
        long_term = self.memory.memory_file
        today = self.memory.get_today_file()

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime if path.exists() else 0.0
            except Exception:
                return 0.0

        signature = ((str(long_term), _mtime(long_term)), (str(today), _mtime(today)),
                     self.memory_max_chars)
        cached = self._get_cached("memory", signature)
        if cached is not None:
            return cached

        memory = self.memory.get_memory_context()
        if memory:
            memory = self._truncate_tail(memory, self.memory_max_chars, "memory")

        self._set_cache("memory", signature, memory)
        return memory

    def _get_skills_summary(self) -> str:
        """Build skills summary with caching by file mtimes."""
        skills = self.skills.list_skills(filter_unavailable=False)
        files: list[tuple[str, float]] = []
        for s in skills:
            path = Path(s["path"])
            try:
                mtime = path.stat().st_mtime
            except Exception:
                mtime = 0.0
            files.append((str(path), mtime))

        signature = (tuple(files),)
        cached = self._get_cached("skills_summary", signature)
        if cached is not None:
            return cached

        summary = self.skills.build_skills_summary()
        self._set_cache("skills_summary", signature, summary)
        return summary

    def _get_always_skills_content(self) -> str:
        """Load always-on skills with caching by file mtimes."""
        always_skills = self.skills.get_always_skills()
        files: list[tuple[str, float]] = []
        for name in always_skills:
            path = self.skills.resolve_skill_path(name)
            if not path:
                continue
            try:
                mtime = path.stat().st_mtime
            except Exception:
                mtime = 0.0
            files.append((name, str(path), mtime))

        signature = (tuple(files),)
        cached = self._get_cached("always_skills", signature)
        if cached is not None:
            return cached

        content = self.skills.load_skills_for_context(always_skills) if always_skills else ""
        self._set_cache("always_skills", signature, content)
        return content

    def _get_skills_section(self) -> str:
        """Build full skills section with truncation and caching."""
        always_content = self._get_always_skills_content()
        skills_summary = self._get_skills_summary()

        signature = (hash(always_content), hash(skills_summary), self.skills_max_chars)
        cached = self._get_cached("skills_section", signature)
        if cached is not None:
            return cached

        parts = []
        if always_content:
            parts.append(f"# Active Skills\n\n{always_content}")

        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        text = "\n\n---\n\n".join(parts) if parts else ""
        if text:
            text = self._truncate_tail(text, self.skills_max_chars, "skills")

        self._set_cache("skills_section", signature, text)
        return text

    def _get_prompt_tail(self) -> str:
        """Build the system prompt tail (everything except identity)."""
        bootstrap = self._get_bootstrap_content()
        memory = self._get_memory_context()
        skills_section = self._get_skills_section()

        signature = (hash(bootstrap), hash(memory), hash(skills_section))
        cached = self._get_cached("prompt_tail", signature)
        if cached is not None:
            return cached

        parts = []
        if bootstrap:
            parts.append(bootstrap)
        if memory:
            parts.append(f"# Memory\n\n{memory}")
        if skills_section:
            parts.append(skills_section)

        tail = "\n\n---\n\n".join(parts)
        self._set_cache("prompt_tail", signature, tail)
        return tail
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Optional skills to include.
            media: Optional list of local file paths for images/media.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(skill_names)
        messages.append({"role": "system", "content": system_prompt})

        # History
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text
        
        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        
        if not images:
            return text
        return images + [{"type": "text", "text": text}]
    
    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.
        
        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.
        
        Returns:
            Updated message list.
        """
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.
        
        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
        
        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
        
        if tool_calls:
            msg["tool_calls"] = tool_calls
        
        messages.append(msg)
        return messages
