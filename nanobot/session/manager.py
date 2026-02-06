"""Session management for conversation history."""

import asyncio
import json
import os
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger
from filelock import FileLock

from nanobot.utils.helpers import ensure_dir, safe_filename, get_sessions_path


@dataclass
class Session:
    """
    A conversation session.
    
    Stores messages in JSONL format for easy reading and persistence.
    """

    _HISTORY_RETENTION_MULTIPLIER = 2  # Keep up to 2x max_messages; trim when exceeded.
    
    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
    
    def get_history(self, max_messages: int = 50) -> list[dict[str, Any]]:
        """
        Get message history for LLM context.
        
        Args:
            max_messages: Maximum messages to return (non-positive values return an empty list).
        
        Returns:
            List of messages in LLM format.
        """
        if max_messages <= 0:
            return []

        # max_messages is positive here due to the guard above.
        max_retained = max_messages * self._HISTORY_RETENTION_MULTIPLIER
        if len(self.messages) > max_retained:
            # Permanently trim older messages to prevent unbounded session growth.
            self.messages = self.messages[-max_retained:]
            self.updated_at = datetime.now()

        # Get recent messages
        recent = self.messages[-max_messages:] if len(self.messages) > max_messages else self.messages
        
        # Convert to LLM format (just role and content)
        return [{"role": m["role"], "content": m["content"]} for m in recent]
    
    def clear(self) -> None:
        """Clear all messages in the session."""
        self.messages = []
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.
    
    Sessions are stored as JSONL files in the sessions directory.
    """
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        # Use helper so tests can monkeypatch Path.home().
        self.sessions_dir = ensure_dir(get_sessions_path())
        self._cache: dict[str, Session] = {}

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_lock_path(self, path: Path) -> Path:
        return Path(str(path) + ".lock")

    def _lock_for(self, path: Path) -> FileLock:
        # FileLock uses a separate lock file, so it works on Windows and across processes.
        return FileLock(str(self._get_lock_path(path)))

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.
        
        Args:
            key: Session key (usually channel:chat_id).
        
        Returns:
            The session.
        """
        # Check cache
        if key in self._cache:
            return self._cache[key]
        
        # Try to load from disk
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        
        self._cache[key] = session
        return session
    
    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        
        if not path.exists():
            return None
        
        try:
            messages: list[dict[str, Any]] = []
            metadata: dict[str, Any] = {}
            created_at = None

            with self._lock_for(path):
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        data = json.loads(line)

                        if data.get("_type") == "metadata":
                            metadata = data.get("metadata", {})
                            created_at = (
                                datetime.fromisoformat(data["created_at"])
                                if data.get("created_at")
                                else None
                            )
                        else:
                            messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
            )
        except Exception as e:
            logger.warning(f"Failed to load session {key}: {e}")
            return None
    
    def save(self, session: Session) -> None:
        """Save a session to disk."""
        self._save_to_disk(session)
        self._cache[session.key] = session

    async def save_async(self, session: Session) -> None:
        """
        Save a session without blocking the asyncio event loop.

        This offloads disk I/O + fsync to a thread, then updates the in-memory cache.
        """
        await asyncio.to_thread(self._save_to_disk, session)
        self._cache[session.key] = session

    def _save_to_disk(self, session: Session) -> None:
        """Synchronous save implementation (atomic write + fsync)."""
        path = self._get_session_path(session.key)

        tmp_path = Path(str(path) + ".tmp")
        with self._lock_for(path):
            with open(tmp_path, "w", encoding="utf-8") as f:
                # Write metadata first
                metadata_line = {
                    "_type": "metadata",
                    "key": session.key,
                    "created_at": session.created_at.isoformat(),
                    "updated_at": session.updated_at.isoformat(),
                    "metadata": session.metadata,
                }
                f.write(json.dumps(metadata_line) + "\n")

                # Write messages
                for msg in session.messages:
                    f.write(json.dumps(msg) + "\n")
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace to avoid torn writes.
            os.replace(tmp_path, path)
    
    def delete(self, key: str) -> bool:
        """
        Delete a session.
        
        Args:
            key: Session key.
        
        Returns:
            True if deleted, False if not found.
        """
        # Remove from cache
        self._cache.pop(key, None)
        
        # Remove file
        path = self._get_session_path(key)
        if path.exists():
            path.unlink()
            return True
        return False
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.
        
        Returns:
            List of session info dicts.
        """
        sessions = []
        
        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            # Metadata written by this codebase always includes the real key.
                            # If it's missing (e.g., corrupted/legacy files), do not attempt to
                            # reconstruct from the filename because it's lossy (underscores are valid
                            # in keys and cannot be distinguished from ":"->"_" replacement).
                            key = data.get("key") or path.stem
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue
        
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
