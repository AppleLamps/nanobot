"""Configuration loading utilities."""

import json
import re
from pathlib import Path
from typing import Any

from nanobot.config.schema import Config


def get_config_path() -> Path:
    """Get the default configuration file path."""
    return get_data_dir() / "config.json"


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path
    return get_data_path()


def load_config(config_path: Path | None = None) -> Config:
    """
    Load configuration from file or create default.
    
    Args:
        config_path: Optional path to config file. Uses default if not provided.
    
    Returns:
        Loaded configuration object.
    """
    path = config_path or get_config_path()
    
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            return Config.model_validate(convert_keys(data))
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")
    
    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.
    
    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = config_path or get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    
    # Convert to camelCase format
    data = config.model_dump()
    data = convert_to_camel(data)
    
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def convert_keys(data: Any) -> Any:
    """Convert camelCase keys to snake_case for Pydantic."""
    if isinstance(data, dict):
        return {camel_to_snake(k): convert_keys(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_keys(item) for item in data]
    return data


def convert_to_camel(data: Any) -> Any:
    """Convert snake_case keys to camelCase."""
    if isinstance(data, dict):
        return {snake_to_camel(k): convert_to_camel(v) for k, v in data.items()}
    if isinstance(data, list):
        return [convert_to_camel(item) for item in data]
    return data


def camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case."""
    # Handle common camelCase/PascalCase and consecutive uppercase runs (acronyms).
    #
    # Examples:
    # - bridgeUrl -> bridge_url
    # - apiKey -> api_key
    # - allowIPv6 -> allow_ipv6
    if not name:
        return ""

    # Fast path for already-snake-ish values.
    if "_" in name and name.lower() == name:
        return name

    out: list[str] = []
    n = len(name)

    def _lower_run_len(start: int) -> int:
        j = start
        while j < n and name[j].islower():
            j += 1
        return j - start

    for i, ch in enumerate(name):
        if not ch.isupper():
            out.append(ch)
            continue

        if i > 0:
            prev = name[i - 1]
            nxt = name[i + 1] if i + 1 < n else ""

            if prev.islower() or prev.isdigit():
                out.append("_")
            elif prev.isupper() and nxt and nxt.islower():
                # Only split acronym -> word transitions when the following lowercase
                # run is "word-like" (length > 1). This keeps cases like "IPv6"
                # together, while still splitting "APIKey" -> "api_key".
                if _lower_run_len(i + 1) > 1:
                    out.append("_")

        out.append(ch.lower())

    # Normalize a few separators.
    return re.sub(r"[-\s]+", "_", "".join(out))


def snake_to_camel(name: str) -> str:
    """Convert snake_case to camelCase."""
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])
