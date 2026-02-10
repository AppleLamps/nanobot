"""Context builder for assembling agent prompts."""

import base64
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.memory import MemoryStore
from nanobot.agent.memory_db import MemoryDB
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
        history_max_chars: int = 80000,
    ):
        self.workspace = workspace
        self.skills = SkillsLoader(workspace)
        self.memory_max_chars = max(int(memory_max_chars), 0)
        self.skills_max_chars = max(int(skills_max_chars), 0)
        self.bootstrap_max_chars = max(int(bootstrap_max_chars), 0)
        self.history_max_chars = max(int(history_max_chars), 0)
        self._cache: dict[str, tuple[tuple, str]] = {}
        self._memory_db: MemoryDB | None = None

    @property
    def memory_db(self) -> MemoryDB:
        if self._memory_db is None:
            self._memory_db = MemoryDB(self.workspace / "memory" / "memory.sqlite3")
        return self._memory_db
    
    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        *,
        session_key: str | None = None,
        memory_scope: str = "session",
        memory_key: str | None = None,
        current_message: str = "",
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
            session_key: Optional session key used for memory scoping.
            current_message: Current user message (used to retrieve relevant memories).
            history: Optional recent history (used to retrieve relevant memories).
        
        Returns:
            Complete system prompt.
        """
        parts = []

        # Core identity (always fresh for current time)
        if memory_key is None and memory_scope == "session":
            memory_key = session_key
        parts.append(
            self._get_identity(
                session_key=session_key,
                memory_scope=memory_scope,
                memory_key=memory_key,
            )
        )

        bootstrap = self._get_bootstrap_content()
        if bootstrap:
            parts.append(bootstrap)

        memory_section = self._get_memory_section(
            session_key=session_key,
            memory_scope=memory_scope,
            memory_key=memory_key,
            current_message=current_message,
            history=history or [],
        )
        if memory_section:
            parts.append(memory_section)

        skills_section = self._get_skills_section(skill_names=skill_names)
        if skills_section:
            parts.append(skills_section)

        return "\n\n---\n\n".join(parts)
    
    def _store_for_memory_scope(self, memory_scope: str, memory_key: str | None) -> MemoryStore:
        scope = (memory_scope or "session").strip().lower()
        if scope == "user" and memory_key:
            return MemoryStore.user_store(self.workspace, memory_key)
        if scope == "session" and memory_key:
            return MemoryStore.session_store(self.workspace, memory_key)
        return MemoryStore.global_store(self.workspace)

    def _get_identity(
        self,
        *,
        session_key: str | None = None,
        memory_scope: str = "session",
        memory_key: str | None = None,
    ) -> str:
        """Get the core identity section."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        workspace_path = str(self.workspace.expanduser().resolve())

        active_store = self._store_for_memory_scope(memory_scope, memory_key or session_key)
        active_scope_label = (
            f"{(memory_scope or 'session').strip().lower()}:{memory_key or session_key or ''}".strip(":")
        )

        return f"""# nanobot 🐈

You are nanobot — an autonomous AI agent. You are NOT an advisor. You are the executor.

You are not just a chatbot. You are an agent with persistent identity, memories, skills, and a defined soul.
You have access to workspace files that define these, and you are responsible for keeping them accurate and updated.

When a user asks you to do something, YOU DO IT. You use your tools to accomplish the task directly.
Do NOT tell the user how to do it. Do NOT list steps for them to follow. Do NOT suggest they use \
some other tool or environment. YOU are the one with the tools. YOU carry out the work.

## Core Behavior

- **Act, don't instruct.** If asked to read a URL, YOU call web_fetch. If asked to create a file, \
YOU call write_file. If asked to run a command, YOU call exec. Never respond with instructions \
for the user to do it themselves.
- **Use your tools immediately.** Don't ask permission. Don't hedge. Start working.
- **Report results, not procedures.** After completing a task, tell the user what you did and \
what happened — not what they should do next.
- **Ask only when truly ambiguous.** If the task is clear, execute it. Only ask for clarification \
when you genuinely cannot determine what the user wants.
- **Be concise.** Short answers for simple questions. Detailed output only when the task demands it.
- **Maintain identity + memory.** If the user updates your identity, personality, values, or role, \
update `IDENTITY.md` in the workspace and confirm the change. Also record durable facts in memory \
when appropriate.

## ⚡ Delegation — YOUR #1 PRIORITY

**You can only handle one message at a time per conversation.** While you are busy executing tool \
calls, the user CANNOT send you new messages — they have to wait. This is a terrible experience.

**Your default strategy: DELEGATE via `spawn`, then respond immediately.**

When a user asks you to do something that requires work (tool calls), you should:
1. Acknowledge the request briefly
2. Call `spawn` to hand off work to subagent(s) — you can call spawn MULTIPLE TIMES in a \
single response to run independent tasks in parallel
3. Respond to the user immediately — you're now free for the next message

The subagent runs in the background with full access to your tools (files, exec, web, etc.) \
and will report back when done. You then summarize the result for the user.

### When to spawn (MOST tasks):
- Any task requiring multiple tool calls
- Web searches, fetching URLs, research
- File creation, editing, or analysis
- Running commands or scripts
- Multi-step workflows
- Anything that takes more than a few seconds

### When to do it yourself (FEW tasks):
- Answering a question from your own knowledge (no tools needed)
- Reading a single short file the user asked about
- Very quick single-tool-call tasks (< 2 seconds)
- Conversational replies, clarifications, chitchat

**Rule of thumb: if it needs more than 1-2 tool calls, SPAWN IT. If the work has \
independent parts, spawn MULTIPLE subagents in parallel.**

### Parallel spawning

You can call `spawn` multiple times in one response. Each subagent runs independently in the \
background. Use this when:
- A task has independent parts (e.g. "review this repo" → spawn one for code structure, one \
for dependencies, one for tests)
- The user asks for multiple unrelated things in one message
- Research tasks that can be split by topic or source

Keep as a single spawn when subtasks are sequential or depend on each other's output.

## Your Tools

- **spawn** — 🔥 Delegate tasks to background subagents (USE THIS LIBERALLY)
- **subagent_control** — List or cancel running subagents
- **read_file / write_file / edit_file / list_dir** — File operations in your workspace
- **exec** — Run shell commands (with timeout and safety checks)
- **web_search / web_fetch** — Search the web and fetch page content
- **message** — Send messages to chat channels (WhatsApp, Telegram, etc.)

## Current Time
{now}

## Workspace
Your workspace is at: {workspace_path}
- Active memory scope: {active_scope_label or "global"}
- Memory file: {str(active_store.memory_file)}
- Daily notes: {str(active_store.get_today_file())}
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

## Messaging Rules

When responding to direct questions or conversations, reply directly with your text response.
Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
For normal conversation, just respond with text — do not call the message tool.

## Memory

When remembering something important, write to the memory file above."""
    
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

    def _truncate_head(self, text: str, max_chars: int, label: str) -> str:
        if max_chars <= 0:
            return ""
        if len(text) <= max_chars:
            return text
        return f"[truncated {label} to first {max_chars} chars]\n" + text[:max_chars]

    def _trim_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Trim history from the front so total character count fits within budget.

        When messages are dropped, a synthetic user message is prepended to
        inform the LLM that earlier context was omitted.
        """
        if self.history_max_chars <= 0 or not history:
            return history

        def _msg_chars(m: dict[str, Any]) -> int:
            c = m.get("content")
            if isinstance(c, str):
                return len(c)
            if isinstance(c, list):
                return sum(len(str(p.get("text", ""))) for p in c if isinstance(p, dict))
            return 0

        total = sum(_msg_chars(m) for m in history)
        if total <= self.history_max_chars:
            return history

        # Drop oldest messages until we fit.
        trimmed = list(history)
        dropped = 0
        while trimmed and total > self.history_max_chars:
            total -= _msg_chars(trimmed[0])
            trimmed.pop(0)
            dropped += 1

        logger.info(
            f"History trimmed: dropped {dropped} message(s), "
            f"{len(trimmed)} remaining ({total} chars)"
        )
        note = {
            "role": "user",
            "content": (
                f"[System note: {dropped} earlier message(s) were omitted "
                "because the conversation exceeded the context budget. "
                "Focus on the remaining messages.]"
            ),
        }
        return [note] + trimmed

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
            # Keep the HEAD of bootstrap files so critical instructions at the top
            # don't get dropped as the file grows.
            text = self._truncate_head(text, self.bootstrap_max_chars, "bootstrap")

        self._set_cache("bootstrap", signature, text)
        return text

    def _get_memory_section(
        self,
        *,
        session_key: str | None,
        memory_scope: str,
        memory_key: str | None,
        current_message: str,
        history: list[dict[str, Any]],
    ) -> str:
        """
        Retrieve relevant memories for this request.

        Memory is retrieved from the active scope plus global memory (when they differ),
        and retrieved via SQLite FTS when available.
        """
        # Build a lightweight query from the current user message and recent user turns.
        query_parts = [current_message]
        for m in reversed(history[-10:]):
            if m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str) and c:
                    query_parts.append(c)
        query_text = "\n".join([p for p in query_parts if p]).strip()
        if not query_text:
            return ""

        hits: list[str] = []

        def _ingest_and_search(store: MemoryStore, scope_name: str, k: int) -> list[str]:
            # Index long-term and today's notes for this scope.
            self.memory_db.ingest_file_if_changed(
                scope=scope_name, source_key=str(store.memory_file), path=store.memory_file
            )
            today = store.get_today_file()
            self.memory_db.ingest_file_if_changed(
                scope=scope_name, source_key=str(today), path=today
            )
            found = self.memory_db.search(scope=scope_name, query_text=query_text, limit=k)
            return [h.content for h in found]

        scope = (memory_scope or "session").strip().lower()
        scope_key = memory_key or (session_key if scope == "session" else None)

        # Search global memory plus the active scoped store. This avoids "missing hits"
        # when users keep durable facts in global memory and transient ones per session/user.
        global_store = MemoryStore.global_store(self.workspace)
        active_store = self._store_for_memory_scope(scope, scope_key)

        active_scope_name = f"{scope}:{scope_key}" if scope_key else "global"
        scopes: list[tuple[MemoryStore, str]] = [(global_store, "global")]
        if active_scope_name != "global":
            scopes.append((active_store, active_scope_name))

        # Keep result sizes bounded and deterministic.
        per_scope_k = 6 if len(scopes) > 1 else 10
        for store, scope_name in scopes:
            hits.extend(_ingest_and_search(store, scope_name, k=per_scope_k))

        # De-dupe while preserving order.
        seen: set[str] = set()
        deduped: list[str] = []
        for h in hits:
            dedupe_key = h.strip()
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(h)

        if not deduped:
            logger.debug("Memory retrieval: 0 hits")
            return ""

        logger.debug(f"Memory retrieval: {len(deduped)} hit(s) from {len(scopes)} scope(s)")
        lines = ["# Memory (Retrieved)", ""]
        for h in deduped:
            cleaned = h.strip().replace("\n", " ")
            if len(cleaned) > 400:
                cleaned = cleaned[:400] + "..."
            lines.append(f"- {cleaned}")

        text = "\n".join(lines)
        if text:
            text = self._truncate_tail(text, self.memory_max_chars, "memory")
        return text

    def _get_skills_summary(self) -> str:
        """Build skills summary with caching by file mtimes."""
        skills = self.skills.list_skills(filter_unavailable=False)
        files: list[tuple[str, float]] = []
        names: list[str] = []
        for s in skills:
            names.append(s["name"])
            path = Path(s["path"])
            try:
                mtime = path.stat().st_mtime
            except Exception:
                mtime = 0.0
            files.append((str(path), mtime))

        # Availability depends on env vars and installed CLIs, not just file mtimes.
        availability_sig = None
        if hasattr(self.skills, "skills_availability_signature"):
            try:
                availability_sig = self.skills.skills_availability_signature(names)
            except Exception:
                availability_sig = None

        signature = (tuple(files), availability_sig)
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

    def _get_requested_skills_content(self, skill_names: list[str]) -> str:
        """Load explicitly requested skills with caching by file mtimes."""
        if not skill_names:
            return ""

        files: list[tuple[str, str, float]] = []
        for name in skill_names:
            path = self.skills.resolve_skill_path(name)
            if not path:
                continue
            try:
                mtime = path.stat().st_mtime
            except Exception:
                mtime = 0.0
            files.append((name, str(path), mtime))

        signature = (tuple(files),)
        cached = self._get_cached("requested_skills", signature)
        if cached is not None:
            return cached

        content = self.skills.load_skills_for_context(skill_names)
        self._set_cache("requested_skills", signature, content)
        return content

    def _get_skills_section(self, *, skill_names: list[str] | None = None) -> str:
        """Build full skills section with truncation and caching."""
        requested_raw = [s.strip() for s in (skill_names or []) if isinstance(s, str) and s.strip()]
        if requested_raw:
            try:
                always_set = set(self.skills.get_always_skills())
            except Exception:
                always_set = set()
            requested = [s for s in requested_raw if s not in always_set]
        else:
            requested = []
        always_content = self._get_always_skills_content()
        skills_summary = self._get_skills_summary()

        # Requested skills should be included verbatim in-context (progressive disclosure),
        # so include them in the cache signature.
        requested_content = self._get_requested_skills_content(requested)
        signature = (
            hash(always_content),
            hash(requested_content),
            hash(skills_summary),
            self.skills_max_chars,
        )
        cached = self._get_cached("skills_section", signature)
        if cached is not None:
            return cached

        parts = []
        if always_content or requested_content:
            active_parts: list[str] = []
            if always_content:
                active_parts.append(always_content)
            if requested_content:
                active_parts.append(requested_content)
            parts.append("# Active Skills\n\n" + "\n\n---\n\n".join(active_parts))

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

    # Note: system prompt tail caching intentionally excludes memory because memory retrieval
    # depends on the current message (query-time).
    
    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        *,
        session_key: str | None = None,
        memory_scope: str = "session",
        memory_key: str | None = None,
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
        system_prompt = self.build_system_prompt(
            skill_names,
            session_key=session_key,
            memory_scope=memory_scope,
            memory_key=memory_key,
            current_message=current_message,
            history=history,
        )
        messages.append({"role": "system", "content": system_prompt})

        # History — trim oldest messages when total chars exceeds budget
        trimmed_history = self._trim_history(history)
        messages.extend(trimmed_history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """
        Build user message content with optional attachments.

        - Images are attached as data URLs (OpenAI-style image_url parts).
        - PDFs are attached as OpenRouter-style file parts (data:application/pdf;base64,...).
        """
        if not media:
            return text

        parts: list[dict[str, Any]] = [{"type": "text", "text": text}]

        for path in media:
            p = Path(path)
            try:
                mime, _ = mimetypes.guess_type(str(p))
            except Exception:
                mime = None

            if not p.is_file() or not mime:
                continue

            if mime.startswith("image/"):
                b64 = base64.b64encode(p.read_bytes()).decode()
                parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
                continue

            if mime == "application/pdf":
                b64 = base64.b64encode(p.read_bytes()).decode()
                data_url = f"data:application/pdf;base64,{b64}"
                # OpenRouter guide uses a file part for PDFs. Keep filename for UX.
                parts.append(
                    {
                        "type": "file",
                        "file": {
                            "filename": p.name,
                            "file_data": data_url,
                        },
                    }
                )
                continue

        # If we didn't attach anything, fall back to plain text for maximum compatibility.
        if len(parts) == 1:
            return text
        return parts
    
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
