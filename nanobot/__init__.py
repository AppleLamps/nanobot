"""nanobot - A lightweight AI agent framework."""

from __future__ import annotations

from pathlib import Path

__logo__ = "🐈"


def _version_from_pyproject() -> str | None:
    try:
        import tomllib  # py311+
    except Exception:
        return None

    # Repo layout: <root>/pyproject.toml and <root>/nanobot/__init__.py.
    root = Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return None

    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="replace"))
        project = data.get("project") if isinstance(data, dict) else None
        v = project.get("version") if isinstance(project, dict) else None
        return str(v) if v else None
    except Exception:
        return None


def _get_version() -> str:
    # Prefer installed package metadata when available, otherwise fall back to pyproject.toml
    # (useful in editable/source checkouts).
    try:
        from importlib.metadata import PackageNotFoundError, version

        # Distribution name in pyproject.toml is "nanobot-ai".
        try:
            return version("nanobot-ai")
        except PackageNotFoundError:
            return version("nanobot")
    except Exception:
        return _version_from_pyproject() or "0.0.0"


__version__ = _get_version()
