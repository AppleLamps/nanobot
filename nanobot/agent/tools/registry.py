"""Tool registry for dynamic tool management."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import time
from typing import Any

from nanobot.agent.tools.base import Tool


@dataclass(frozen=True)
class _CacheEntry:
    value: str
    expires_at: float | None


class ToolRegistry:
    """
    Registry for agent tools.
    
    Allows dynamic registration and execution of tools.
    """
    
    def __init__(self, *, cache_max_entries: int = 256, max_parallel: int = 8):
        self._tools: dict[str, Tool] = {}
        self._allowed_tools: set[str] | None = None
        self._cache_max_entries = max(int(cache_max_entries), 0)
        self._max_parallel = max(int(max_parallel), 1)
        self._cache: "OrderedDict[str, _CacheEntry]" = OrderedDict()
        self._in_flight: dict[str, asyncio.Future[str]] = {}
    
    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def iter_tools(self) -> list[Tool]:
        """Return registered tool instances (in insertion order)."""
        return list(self._tools.values())
    
    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)
    
    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)
    
    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools
    
    def get_definitions(self) -> list[dict[str, Any]]:
        """Get all tool definitions in OpenAI format."""
        tools = self._tools.values()
        if self._allowed_tools is not None:
            tools = [tool for tool in tools if tool.name in self._allowed_tools]
        return [tool.to_schema() for tool in tools]

    def _tool_parallel_safe(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(getattr(tool, "parallel_safe", False)) if tool else False

    def _cache_get(self, key: str) -> str | None:
        if self._cache_max_entries <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry.expires_at is not None and entry.expires_at <= time.monotonic():
            self._cache.pop(key, None)
            return None
        # LRU bump
        self._cache.move_to_end(key)
        return entry.value

    def _cache_set(self, key: str, value: str, ttl_s: float | None) -> None:
        if self._cache_max_entries <= 0:
            return
        expires_at = None
        if ttl_s is not None:
            try:
                ttl = float(ttl_s)
            except Exception:
                ttl = 0.0
            if ttl > 0:
                expires_at = time.monotonic() + ttl
        self._cache[key] = _CacheEntry(value=value, expires_at=expires_at)
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_entries:
            self._cache.popitem(last=False)

    def _cache_key_for(self, tool: Tool, params: dict[str, Any]) -> str | None:
        if not getattr(tool, "cacheable", False):
            return None
        k = tool.cache_key(params)
        if not k:
            return None
        return f"{tool.name}:{k}"

    def _is_retryable_error(self, result: str) -> bool:
        s = result.strip().lower()
        if not (s.startswith("error:") or s.startswith("warning:")):
            return False
        no_retry = (
            "invalid parameters",
            "not permitted",
            "not found",
            "blocked by safety guard",
            "missing required",
            "should be",
            "url validation failed",
        )
        return not any(marker in s for marker in no_retry)
    
    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """
        Execute a tool by name with given parameters.
        
        Args:
            name: Tool name.
            params: Tool parameters.
        
        Returns:
            Tool execution result as string.
        
        Raises:
            KeyError: If tool not found.
        """
        if self._allowed_tools is not None and name not in self._allowed_tools:
            return f"Error: Tool '{name}' is not permitted"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found"

        try:
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors)
            cache_key = self._cache_key_for(tool, params)
            if cache_key:
                cached = self._cache_get(cache_key)
                if cached is not None:
                    return cached
                in_flight = self._in_flight.get(cache_key)
                if in_flight is not None:
                    return await in_flight

                loop = asyncio.get_running_loop()
                fut: asyncio.Future[str] = loop.create_future()
                self._in_flight[cache_key] = fut
            else:
                fut = None

            try:
                result = await tool.execute(**params)
            except Exception as e:
                result = f"Error executing {name}: {str(e)}"

            retries = int(getattr(tool, "max_retries", 0) or 0)
            while retries > 0 and self._is_retryable_error(result):
                retries -= 1
                try:
                    result = await tool.execute(**params)
                except Exception as e:
                    result = f"Error executing {name}: {str(e)}"

            if cache_key:
                try:
                    if tool.should_cache(result):
                        self._cache_set(cache_key, result, getattr(tool, "cache_ttl_s", None))
                finally:
                    # Unblock any waiters even if caching logic fails.
                    if fut is not None and not fut.done():
                        fut.set_result(result)
                    self._in_flight.pop(cache_key, None)

            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}"

    async def execute_calls(self, tool_calls: list[Any], *, allow_parallel: bool = True) -> list[str]:
        """
        Execute a sequence of tool calls, optionally parallelizing consecutive parallel-safe calls.

        Returns results in a stable order matching tool_calls.
        """
        if not tool_calls:
            return []

        results: list[str] = [""] * len(tool_calls)
        i = 0
        sem = asyncio.Semaphore(self._max_parallel) if allow_parallel else None

        async def _run_one(call: Any) -> str:
            if sem is None:
                return await self.execute(call.name, call.arguments)
            await sem.acquire()
            try:
                return await self.execute(call.name, call.arguments)
            finally:
                sem.release()

        while i < len(tool_calls):
            call = tool_calls[i]
            if allow_parallel and self._tool_parallel_safe(call.name):
                j = i + 1
                while j < len(tool_calls) and self._tool_parallel_safe(tool_calls[j].name):
                    j += 1
                chunk = tool_calls[i:j]
                tasks = [asyncio.create_task(_run_one(c)) for c in chunk]
                chunk_results = await asyncio.gather(*tasks)
                for k, r in enumerate(chunk_results):
                    results[i + k] = r
                i = j
            else:
                results[i] = await self.execute(call.name, call.arguments)
                i += 1

        return results
    
    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def set_allowed_tools(self, allowed: list[str] | None) -> None:
        """Restrict available tools to an allowlist (None = no restriction)."""
        if allowed is None:
            self._allowed_tools = None
            return
        self._allowed_tools = set(allowed)
    
    def __len__(self) -> int:
        return len(self._tools)
    
    def __contains__(self, name: str) -> bool:
        return name in self._tools
