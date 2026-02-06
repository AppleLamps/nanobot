#!/usr/bin/env python3
"""Initialize a new skill directory with template files.

Usage:
    python init_skill.py <skill-name> --path <output-directory> [--resources scripts,references,assets] [--examples]

Examples:
    python init_skill.py my-skill --path ./skills
    python init_skill.py my-skill --path ./skills --resources scripts,references
    python init_skill.py my-skill --path ./skills --resources scripts --examples
"""

import argparse
import re
import sys
from pathlib import Path


def validate_name(name: str) -> str:
    """Validate skill name: lowercase, digits, hyphens only, under 64 chars."""
    if not re.match(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$", name) and not re.match(r"^[a-z0-9]$", name):
        raise ValueError(
            f"Invalid skill name '{name}'. Use lowercase letters, digits, and hyphens only (max 64 chars)."
        )
    return name


def create_skill(name: str, output_dir: Path, resources: list[str], examples: bool) -> Path:
    """Create a new skill directory with template files."""
    skill_dir = output_dir / name
    if skill_dir.exists():
        print(f"Error: Skill directory already exists: {skill_dir}", file=sys.stderr)
        sys.exit(1)

    skill_dir.mkdir(parents=True)

    # Generate SKILL.md
    skill_md = f"""---
name: {name}
description: "TODO: Describe what this skill does and when to use it. Be specific about triggers."
---

# {name}

TODO: Write instructions for using this skill.

## Overview

Describe the skill's purpose and capabilities.

## Usage

Step-by-step guidance for the agent.

## Notes

- Add constraints, caveats, or examples.
"""
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # Create requested resource directories
    for res in resources:
        res_dir = skill_dir / res
        res_dir.mkdir(exist_ok=True)

        if examples:
            if res == "scripts":
                (res_dir / "example.py").write_text(
                    '#!/usr/bin/env python3\n"""Example script — replace or delete this file."""\n\nprint("Hello from skill script!")\n',
                    encoding="utf-8",
                )
            elif res == "references":
                (res_dir / "example.md").write_text(
                    "# Example Reference\n\nReplace this file with actual reference material.\n",
                    encoding="utf-8",
                )
            elif res == "assets":
                (res_dir / "README.md").write_text(
                    "# Assets\n\nPlace template files, images, or other output resources here.\n",
                    encoding="utf-8",
                )

    return skill_dir


def main():
    parser = argparse.ArgumentParser(description="Initialize a new nanobot skill.")
    parser.add_argument("name", help="Skill name (lowercase, hyphens, digits)")
    parser.add_argument("--path", required=True, help="Output directory for the skill")
    parser.add_argument(
        "--resources",
        default="",
        help="Comma-separated resource dirs to create: scripts,references,assets",
    )
    parser.add_argument(
        "--examples", action="store_true", help="Add example files in resource directories"
    )

    args = parser.parse_args()

    try:
        name = validate_name(args.name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.path).expanduser().resolve()
    resources = [r.strip() for r in args.resources.split(",") if r.strip()] if args.resources else []

    valid_resources = {"scripts", "references", "assets"}
    invalid = set(resources) - valid_resources
    if invalid:
        print(f"Error: Invalid resource types: {invalid}. Use: {valid_resources}", file=sys.stderr)
        sys.exit(1)

    skill_dir = create_skill(name, output_dir, resources, args.examples)
    print(f"✓ Created skill at {skill_dir}")
    print(f"  Edit {skill_dir / 'SKILL.md'} to add your instructions.")


if __name__ == "__main__":
    main()
