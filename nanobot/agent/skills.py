"""Skills loader for agent capabilities."""

import json
import os
import re
import shutil
from pathlib import Path

# Default builtin skills directory (relative to this file)
BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"


class SkillsLoader:
    """
    Loader for agent skills.
    
    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """
    
    def __init__(self, workspace: Path, builtin_skills_dir: Path | None = None):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self._metadata_cache: dict[str, tuple[float, dict]] = {}
        self._content_cache: dict[str, tuple[float, str]] = {}

    def resolve_skill_path(self, name: str) -> Path | None:
        """Resolve a skill name to its SKILL.md path."""
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill
        return None
    
    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.
        
        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.
        
        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []
        
        # Workspace skills (highest priority)
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "workspace"})
        
        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and not any(s["name"] == skill_dir.name for s in skills):
                        skills.append({"name": skill_dir.name, "path": str(skill_file), "source": "builtin"})
        
        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills
    
    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.
        
        Args:
            name: Skill name (directory name).
        
        Returns:
            Skill content or None if not found.
        """
        path = self.resolve_skill_path(name)
        if not path:
            return None

        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0

        cached = self._content_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

        content = path.read_text(encoding="utf-8", errors="replace")
        self._content_cache[key] = (mtime, content)
        return content
    
    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.
        
        Args:
            skill_names: List of skill names to load.
        
        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")
        
        return "\n\n---\n\n".join(parts) if parts else ""
    
    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).
        
        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.
        
        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""
        
        def escape_xml(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        lines = ["<skills>"]
        for s in all_skills:
            name = escape_xml(s["name"])
            path = s["path"]
            desc = escape_xml(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)
            
            lines.append(f"  <skill available=\"{str(available).lower()}\">")
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")
            
            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{escape_xml(missing)}</requires>")
            
            lines.append(f"  </skill>")
        lines.append("</skills>")
        
        return "\n".join(lines)
    
    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def skills_availability_signature(self, names: list[str]) -> tuple:
        """
        Return a stable signature capturing current availability for skills.

        This includes the resolved CLI paths (via shutil.which) and env var presence
        for each skill's declared requirements. It's intended for cache invalidation.
        """

        def _one(name: str) -> tuple:
            meta = self._get_skill_meta(name)
            requires = meta.get("requires", {}) if isinstance(meta, dict) else {}

            bins = requires.get("bins", []) if isinstance(requires, dict) else []
            envs = requires.get("env", []) if isinstance(requires, dict) else []

            # Normalize and de-dupe for stability.
            bins_norm = sorted({str(b).strip() for b in bins if str(b).strip()})
            envs_norm = sorted({str(e).strip() for e in envs if str(e).strip()})

            bins_state = tuple((b, shutil.which(b) or "") for b in bins_norm)
            env_state = tuple((e, "1" if os.environ.get(e) else "0") for e in envs_norm)
            return (name, bins_state, env_state)

        return tuple(_one(n) for n in (names or []))
    
    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name
    
    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end():].strip()
        return content
    
    def _parse_nanobot_metadata(self, raw: str) -> dict:
        """Parse nanobot metadata JSON from frontmatter."""
        try:
            data = json.loads(raw)
            return data.get("nanobot", {}) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    
    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True
    
    def _get_skill_meta(self, name: str) -> dict:
        """Get nanobot metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_nanobot_metadata(meta.get("metadata", ""))
    
    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_nanobot_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result
    
    def get_skill_metadata(self, name: str) -> dict | None:
        """
        Get metadata from a skill's frontmatter.
        
        Args:
            name: Skill name.
        
        Returns:
            Metadata dict or None.
        """
        path = self.resolve_skill_path(name)
        if not path:
            return None

        key = str(path)
        try:
            mtime = path.stat().st_mtime
        except Exception:
            mtime = 0.0

        cached = self._metadata_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]

        content = path.read_text(encoding="utf-8", errors="replace")
        metadata: dict[str, str] = {}
        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                # Lightweight YAML-ish parsing for simple "key: value" and block scalars.
                # We intentionally avoid a full YAML dependency here; skill frontmatter is expected
                # to be small and mostly flat. This parser is resilient to colons inside values
                # and supports:
                #   metadata: |
                #     {...}
                raw = match.group(1)
                lines = raw.split("\n")
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if not line.strip():
                        i += 1
                        continue
                    if ":" not in line:
                        i += 1
                        continue

                    key_part, rest = line.split(":", 1)
                    key = key_part.strip()
                    rest = rest.lstrip()

                    if rest in ("|", ">"):
                        i += 1
                        buf: list[str] = []
                        while i < len(lines):
                            nxt = lines[i]
                            if nxt.startswith((" ", "\t")):
                                buf.append(nxt.lstrip(" \t"))
                                i += 1
                                continue
                            break
                        metadata[key] = "\n".join(buf).rstrip()
                        continue

                    value = rest.strip()
                    if (
                        (value.startswith('"') and value.endswith('"'))
                        or (value.startswith("'") and value.endswith("'"))
                    ) and len(value) >= 2:
                        value = value[1:-1]
                    metadata[key] = value
                    i += 1

        self._metadata_cache[key] = (mtime, metadata)
        return metadata or None
