#!/usr/bin/env python3
"""Validate and package a skill directory into a .skill (zip) file.

Usage:
    python package_skill.py <path/to/skill-folder> [output-directory]

Examples:
    python package_skill.py skills/my-skill
    python package_skill.py skills/my-skill ./dist
"""

import re
import sys
import zipfile
from pathlib import Path


def validate_skill(skill_dir: Path) -> list[str]:
    """Validate a skill directory. Returns list of error messages (empty = valid)."""
    errors: list[str] = []

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        errors.append("Missing SKILL.md file.")
        return errors

    content = skill_md.read_text(encoding="utf-8", errors="replace")

    # Check frontmatter
    if not content.startswith("---"):
        errors.append("SKILL.md must start with YAML frontmatter (---).")
        return errors

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        errors.append("SKILL.md has malformed YAML frontmatter (missing closing ---).")
        return errors

    frontmatter = match.group(1)
    meta: dict[str, str] = {}
    for line in frontmatter.split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip("\"'")

    # Required fields
    if "name" not in meta and "description" not in meta:
        errors.append("Frontmatter must contain at least 'name' or 'description'.")

    if "description" in meta:
        desc = meta["description"]
        if not desc or desc.startswith("TODO"):
            errors.append("Description is empty or still a TODO placeholder.")
        elif len(desc) < 20:
            errors.append(f"Description is too short ({len(desc)} chars). Aim for 20+ characters.")

    # Check skill name matches directory name
    if "name" in meta:
        name = meta["name"]
        if name != skill_dir.name:
            errors.append(
                f"Skill name in frontmatter ('{name}') doesn't match directory name ('{skill_dir.name}')."
            )

    # Check body has content beyond frontmatter
    body = content[match.end():].strip()
    if len(body) < 50:
        errors.append("SKILL.md body is too short. Add meaningful instructions.")

    # Warn about extraneous files
    extraneous = {"README.md", "CHANGELOG.md", "INSTALLATION_GUIDE.md", "QUICK_REFERENCE.md"}
    for f in skill_dir.iterdir():
        if f.name in extraneous and f.name != "SKILL.md":
            errors.append(f"Extraneous file '{f.name}' — skills should only contain SKILL.md + resources.")

    return errors


def package_skill(skill_dir: Path, output_dir: Path) -> Path:
    """Package a skill directory into a .skill zip file."""
    skill_name = skill_dir.name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{skill_name}.skill"

    with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(skill_dir.rglob("*")):
            if file_path.is_file():
                # Skip __pycache__ and hidden files
                if "__pycache__" in file_path.parts or file_path.name.startswith("."):
                    continue
                arcname = f"{skill_name}/{file_path.relative_to(skill_dir)}"
                zf.write(file_path, arcname)

    return output_file


def main():
    if len(sys.argv) < 2:
        print("Usage: package_skill.py <path/to/skill-folder> [output-directory]", file=sys.stderr)
        sys.exit(1)

    skill_dir = Path(sys.argv[1]).expanduser().resolve()
    output_dir = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) > 2 else skill_dir.parent

    if not skill_dir.is_dir():
        print(f"Error: Not a directory: {skill_dir}", file=sys.stderr)
        sys.exit(1)

    # Validate
    print(f"Validating skill: {skill_dir.name}")
    errors = validate_skill(skill_dir)

    if errors:
        print("\n❌ Validation failed:")
        for err in errors:
            print(f"  • {err}")
        sys.exit(1)

    print("✓ Validation passed")

    # Package
    output_file = package_skill(skill_dir, output_dir)
    print(f"✓ Packaged skill: {output_file}")
    print(f"  Size: {output_file.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
