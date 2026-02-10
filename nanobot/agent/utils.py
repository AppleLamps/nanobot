"""Shared utilities for agent and subagent loops."""

from __future__ import annotations


def is_tool_error(result: str) -> bool:
    """Check whether a tool result string indicates an error or warning."""
    s = result.strip().lower()
    return s.startswith("error:") or s.startswith("warning:")
