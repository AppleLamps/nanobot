"""Shell execution tool."""

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class ExecTool(Tool):
    """
    Tool to execute shell commands.

    Security note: this is not a sandbox. The guardrails are best-effort string/path checks
    and should not be treated as a security boundary. For untrusted deployments, disable
    this tool or run nanobot inside an OS-level sandbox (e.g. container/VM/jail).
    """
    
    def __init__(
        self,
        timeout: int = 60,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
    ):
        self.timeout = timeout
        self.working_dir = working_dir
        self.deny_patterns = deny_patterns or [
            r"\brm\s+-[rf]{1,2}\b",          # rm -r, rm -rf, rm -fr
            r"\bdel\s+/[fq]\b",              # del /f, del /q
            r"\brmdir\s+/s\b",               # rmdir /s
            r"\b(format|mkfs|diskpart)\b",   # disk operations
            r"\bdd\s+if=",                   # dd
            r">\s*/dev/sd",                  # write to disk
            r"\b(shutdown|reboot|poweroff)\b",  # system power
            r":\(\)\s*\{.*\};\s*:",          # fork bomb
        ]
        # Add extra Windows/PowerShell patterns when running on Windows.
        if sys.platform.startswith("win"):
            self.deny_patterns.extend(
                [
                    r"\bremove-item\b.*\b(-recurse|-r)\b",            # Remove-Item -Recurse
                    r"\bremove-item\b.*\b(-force|-f)\b",              # Remove-Item -Force
                    r"\bri\b.*\b(-recurse|-r)\b",                     # ri -Recurse (alias)
                    r"\bri\b.*\b(-force|-f)\b",                       # ri -Force (alias)
                    r"\bdel\b\s+/.+\b",                               # del /... (broad)
                    r"\brd\b\s+/s\b",                                 # rd /s
                    r"\breg\s+delete\b",                              # reg delete
                    r"\bformat-volume\b",                             # Format-Volume
                    r"\bclear-disk\b",                                # Clear-Disk
                    r"\bremove-partition\b",                          # Remove-Partition
                    r"\bstop-computer\b|\brestart-computer\b",        # Stop/Restart-Computer
                ]
            )
        self.allow_patterns = allow_patterns or []
        self.restrict_to_workspace = restrict_to_workspace
    
    @property
    def name(self) -> str:
        return "exec"
    
    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Not a sandbox; best-effort safety checks only."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute"
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory for the command"
                }
            },
            "required": ["command"]
        }
    
    async def execute(self, command: str, working_dir: str | None = None, **kwargs: Any) -> str:
        base_root = self.working_dir or os.getcwd()
        cwd = working_dir or self.working_dir or os.getcwd()
        guard_error = self._guard_command(command, cwd)
        if guard_error:
            return guard_error

        # If the tool is restricted, the caller must not be able to escape by
        # overriding working_dir to somewhere outside the configured root.
        if self.restrict_to_workspace:
            try:
                root = Path(base_root).resolve()
                resolved_cwd = Path(cwd).resolve()
                if root not in resolved_cwd.parents and resolved_cwd != root:
                    return "Error: Command blocked by safety guard (working_dir outside workspace)"
            except Exception:
                return "Error: Command blocked by safety guard (invalid working_dir)"

        # Never pass through obvious secret env vars (API keys/tokens). This prevents trivial
        # leaks via `env`/`printenv` and reduces the blast radius of subprocess execution.
        env = self._build_subprocess_env()
        
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Command timed out after {self.timeout} seconds"
            
            output_parts = []
            
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            
            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")
            
            result = "\n".join(output_parts) if output_parts else "(no output)"
            
            # Truncate very long output
            max_len = 10000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"
            
            return result
            
        except Exception as e:
            return f"Error executing command: {str(e)}"

    def _build_subprocess_env(self) -> dict[str, str]:
        """
        Build a subprocess environment with common secret keys removed.

        This is not a perfect defense, but it prevents the most common accidental leaks
        (e.g., printing inherited environment variables).
        """
        env = dict(os.environ)
        explicit_block = {
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "ANTHROPIC_API_KEY",
            "GROQ_API_KEY",
            "BRAVE_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_AD_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
        }

        # Remove explicit keys and any "likely secret" keys by name.
        likely_secret = re.compile(
            r"(_API_KEY|_ACCESS_KEY|_SECRET(_KEY)?|_TOKEN|PASSWORD)$", re.IGNORECASE
        )
        for k in list(env.keys()):
            if k in explicit_block or likely_secret.search(k):
                env.pop(k, None)
        return env

    def _guard_command(self, command: str, cwd: str) -> str | None:
        """
        Best-effort safety guard for potentially destructive commands.

        Important: this cannot reliably prevent malicious behavior (nested execution,
        alternate shells, symlinks, env expansion, interpreter tricks, etc.). Treat it
        as a foot-gun reduction mechanism, not a security boundary.
        """
        cmd = command.strip()
        lower = cmd.lower()

        for pattern in self.deny_patterns:
            if re.search(pattern, lower):
                return "Error: Command blocked by safety guard (dangerous pattern detected)"

        if self.allow_patterns:
            if not any(re.search(p, lower) for p in self.allow_patterns):
                return "Error: Command blocked by safety guard (not in allowlist)"

        if self.restrict_to_workspace:
            # Block obvious directory-escape patterns that don't contain a slash.
            # This is intentionally conservative: users can still use subdirs.
            if re.search(r"\b(cd|chdir|pushd|set-location|sl)\s+\.\.(\s|$)", lower):
                return "Error: Command blocked by safety guard (directory escape detected)"
            if re.search(r"\b(cd|chdir|pushd|set-location|sl)\s+([a-z]:\\|\\\\|/)", lower):
                return "Error: Command blocked by safety guard (absolute directory change detected)"

            if "..\\" in cmd or "../" in cmd:
                return "Error: Command blocked by safety guard (path traversal detected)"

            cwd_path = Path(cwd).resolve()

            win_paths = re.findall(r"[A-Za-z]:\\[^\\\"']+", cmd)
            posix_paths = re.findall(r"/[^\s\"']+", cmd)

            for raw in win_paths + posix_paths:
                try:
                    p = Path(raw).resolve()
                except Exception:
                    continue
                if cwd_path not in p.parents and p != cwd_path:
                    return "Error: Command blocked by safety guard (path outside working dir)"

        return None
