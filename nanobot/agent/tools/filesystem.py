"""File system tools: read, write, edit."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


def _resolve_path(
    path: str,
    workspace_root: Path | None,
    restrict_to_workspace: bool,
) -> tuple[Path | None, str | None]:
    file_path = Path(path).expanduser()
    if restrict_to_workspace and workspace_root is not None:
        root = workspace_root.expanduser().resolve()
        if not file_path.is_absolute():
            file_path = root / file_path
        resolved = file_path.resolve()
        if root not in resolved.parents and resolved != root:
            return None, f"Error: Path outside workspace: {path}"
    return file_path, None


class ReadFileTool(Tool):
    """Tool to read file contents."""

    parallel_safe = True
    cacheable = True

    def __init__(self, workspace_root: Path | None = None, restrict_to_workspace: bool = False):
        self.workspace_root = workspace_root
        self.restrict_to_workspace = restrict_to_workspace
    
    @property
    def name(self) -> str:
        return "read_file"
    
    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read"
                }
            },
            "required": ["path"]
        }
    
    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            file_path, error = _resolve_path(
                path, self.workspace_root, self.restrict_to_workspace
            )
            if error:
                return error
            if file_path is None:
                return f"Error: Invalid path: {path}"
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"
            
            content = file_path.read_text(encoding="utf-8")
            return content
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error reading file: {str(e)}"

    def cache_key(self, params: dict[str, Any]) -> str | None:
        # Include stat info so edits invalidate cache without relying on TTL.
        raw = params.get("path")
        if not isinstance(raw, str) or not raw:
            return None
        file_path, error = _resolve_path(
            raw, self.workspace_root, self.restrict_to_workspace
        )
        if error or file_path is None:
            return None
        try:
            if not file_path.exists() or not file_path.is_file():
                return None
            st = file_path.stat()
            return f"{str(file_path.resolve())}|{st.st_mtime_ns}|{st.st_size}"
        except Exception:
            return None


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(self, workspace_root: Path | None = None, restrict_to_workspace: bool = False):
        self.workspace_root = workspace_root
        self.restrict_to_workspace = restrict_to_workspace
    
    @property
    def name(self) -> str:
        return "write_file"
    
    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                }
            },
            "required": ["path", "content"]
        }
    
    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            file_path, error = _resolve_path(
                path, self.workspace_root, self.restrict_to_workspace
            )
            if error:
                return error
            if file_path is None:
                return f"Error: Invalid path: {path}"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(self, workspace_root: Path | None = None, restrict_to_workspace: bool = False):
        self.workspace_root = workspace_root
        self.restrict_to_workspace = restrict_to_workspace
    
    @property
    def name(self) -> str:
        return "edit_file"
    
    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to edit"
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace"
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace with"
                }
            },
            "required": ["path", "old_text", "new_text"]
        }
    
    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        try:
            file_path, error = _resolve_path(
                path, self.workspace_root, self.restrict_to_workspace
            )
            if error:
                return error
            if file_path is None:
                return f"Error: Invalid path: {path}"
            if not file_path.exists():
                return f"Error: File not found: {path}"
            
            content = file_path.read_text(encoding="utf-8")
            
            if old_text not in content:
                return f"Error: old_text not found in file. Make sure it matches exactly."
            
            # Count occurrences
            count = content.count(old_text)
            if count > 1:
                return (
                    f"Error: old_text appears {count} times. "
                    "Please provide more context to make it unique."
                )
            
            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")
            
            return f"Successfully edited {path}"
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error editing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(self, workspace_root: Path | None = None, restrict_to_workspace: bool = False):
        self.workspace_root = workspace_root
        self.restrict_to_workspace = restrict_to_workspace
    
    @property
    def name(self) -> str:
        return "list_dir"
    
    @property
    def description(self) -> str:
        return "List the contents of a directory."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list"
                }
            },
            "required": ["path"]
        }
    
    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            dir_path, error = _resolve_path(
                path, self.workspace_root, self.restrict_to_workspace
            )
            if error:
                return error
            if dir_path is None:
                return f"Error: Invalid path: {path}"
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"
            
            items = []
            for item in sorted(dir_path.iterdir()):
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")
            
            if not items:
                return f"Directory {path} is empty"
            
            return "\n".join(items)
        except PermissionError:
            return f"Error: Permission denied: {path}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
