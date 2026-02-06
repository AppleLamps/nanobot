"""CLI commands for nanobot."""

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from nanobot import __version__, __logo__

app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=False,
    invoke_without_command=True,
)

console = Console()


def _prompt_optional_secret(label: str) -> str:
    """
    Prompt for a secret value (API key), allowing blank to skip.
    Uses hidden input so the value isn't echoed to the terminal.
    """
    value = typer.prompt(
        f"{label} (leave blank to skip)",
        default="",
        show_default=False,
        hide_input=True,
    )
    return (value or "").strip()


def _prompt_optional_text(label: str) -> str:
    """Prompt for a text value, allowing blank to skip."""
    value = typer.prompt(f"{label} (leave blank to skip)", default="", show_default=False)
    return (value or "").strip()


def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    if ctx.invoked_subcommand is not None:
        return

    from InquirerPy import inquirer

    commands = [
        {"name": "onboard  — Initialize nanobot configuration and workspace", "value": "onboard"},
        {"name": "gateway  — Start the nanobot gateway", "value": "gateway"},
        {"name": "agent    — Interact with the agent directly", "value": "agent"},
        {"name": "status   — Show nanobot status", "value": "status"},
        {"name": "channels — Manage channels", "value": "channels"},
        {"name": "skills   — Manage skills", "value": "skills"},
        {"name": "cron     — Manage scheduled tasks", "value": "cron"},
    ]

    console.print(f"\n  {__logo__} [bold]nanobot[/bold] — Personal AI Assistant\n")

    selected = inquirer.select(
        message="Select a command:",
        choices=commands,
        default="gateway",
        pointer="❯",
    ).execute()

    if selected is None:
        raise typer.Exit()

    # Re-invoke the CLI with the selected command
    import subprocess
    raise SystemExit(
        subprocess.run([sys.executable, "-m", "nanobot", selected]).returncode
    )


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command()
def onboard(
    prompt: bool | None = typer.Option(
        None,
        "--prompt/--no-prompt",
        help="Prompt for missing keys/settings (defaults to on in a TTY, off in non-interactive runs).",
    ),
    openrouter_key: str | None = typer.Option(
        None, "--openrouter-key", envvar="OPENROUTER_API_KEY", help="OpenRouter API key."
    ),
    anthropic_key: str | None = typer.Option(
        None, "--anthropic-key", envvar="ANTHROPIC_API_KEY", help="Anthropic API key."
    ),
    openai_key: str | None = typer.Option(
        None, "--openai-key", envvar="OPENAI_API_KEY", help="OpenAI API key."
    ),
    gemini_key: str | None = typer.Option(
        None, "--gemini-key", envvar="GEMINI_API_KEY", help="Gemini API key."
    ),
    groq_key: str | None = typer.Option(
        None, "--groq-key", envvar="GROQ_API_KEY", help="Groq API key."
    ),
    zhipu_key: str | None = typer.Option(
        None, "--zhipu-key", envvar="ZHIPU_API_KEY", help="Zhipu API key."
    ),
    vllm_base: str | None = typer.Option(
        None, "--vllm-base", envvar="VLLM_API_BASE", help="vLLM / local OpenAI-compatible base URL."
    ),
    brave_key: str | None = typer.Option(
        None, "--brave-key", envvar="BRAVE_API_KEY", help="Brave Search API key (enables web.search tool)."
    ),
    model: str | None = typer.Option(
        None, "--model", help="Default model (e.g. openai/gpt-oss-120b:exacto)."
    ),
):
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, load_config, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path
    
    config_path = get_config_path()
    config_existed = config_path.exists()
    do_prompt = prompt if prompt is not None else sys.stdin.isatty()
    
    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        if do_prompt:
            if typer.confirm("Update API keys and settings in the existing config?", default=True):
                config = load_config(config_path)
            elif typer.confirm("Overwrite the existing config?", default=False):
                config = Config()
            else:
                raise typer.Exit()
        else:
            # Non-interactive runs default to updating in place (no destructive overwrite).
            config = load_config(config_path)
    else:
        config = Config()
    
    # Provider API keys (optional, but needed for the agent to run).
    if do_prompt:
        console.print("\n[bold]LLM Provider Setup[/bold]")
        console.print("[dim]You can skip any prompt and edit ~/.nanobot/config.json later.[/dim]\n")

    # Apply any explicitly provided values first (flags/env).
    if openrouter_key:
        config.providers.openrouter.api_key = openrouter_key.strip()
    if anthropic_key:
        config.providers.anthropic.api_key = anthropic_key.strip()
    if openai_key:
        config.providers.openai.api_key = openai_key.strip()
    if gemini_key:
        config.providers.gemini.api_key = gemini_key.strip()
    if groq_key:
        config.providers.groq.api_key = groq_key.strip()
    if zhipu_key:
        config.providers.zhipu.api_key = zhipu_key.strip()
    if vllm_base:
        config.providers.vllm.api_base = vllm_base.strip()
    if brave_key:
        config.tools.web.search.api_key = brave_key.strip()
    if model:
        config.agents.defaults.model = model.strip() or config.agents.defaults.model

    if do_prompt:
        from InquirerPy import inquirer

        # Map provider names to config attrs, key URLs, and suggested default models.
        _providers = {
            "openrouter": {
                "label": "OpenRouter (recommended — access many models with one key)",
                "config_attr": "openrouter",
                "key_url": "https://openrouter.ai/keys",
                "default_model": "qwen/qwen3-coder-next",
            },
            "anthropic": {
                "label": "Anthropic",
                "config_attr": "anthropic",
                "key_url": "https://console.anthropic.com/settings/keys",
                "default_model": "anthropic/claude-sonnet-4-20250514",
            },
            "openai": {
                "label": "OpenAI",
                "config_attr": "openai",
                "key_url": "https://platform.openai.com/api-keys",
                "default_model": "openai/gpt-4o",
            },
            "gemini": {
                "label": "Google Gemini",
                "config_attr": "gemini",
                "key_url": "https://aistudio.google.com/apikey",
                "default_model": "gemini/gemini-2.5-flash",
            },
            "groq": {
                "label": "Groq",
                "config_attr": "groq",
                "key_url": "https://console.groq.com/keys",
                "default_model": "groq/llama-3.3-70b-versatile",
            },
            "zhipu": {
                "label": "Zhipu",
                "config_attr": "zhipu",
                "key_url": None,
                "default_model": "zhipu/glm-4-plus",
            },
            "vllm": {
                "label": "vLLM / Local (OpenAI-compatible endpoint)",
                "config_attr": "vllm",
                "key_url": None,
                "default_model": None,
            },
        }

        provider_choices = [
            {"name": info["label"], "value": name}
            for name, info in _providers.items()
        ]

        chosen = inquirer.select(
            message="Choose your LLM provider:",
            choices=provider_choices,
            default="openrouter",
            pointer="❯",
        ).execute()

        info = _providers[chosen]
        prov_cfg = getattr(config.providers, info["config_attr"])

        if chosen == "vllm":
            # vLLM needs a base URL, not an API key.
            if not prov_cfg.api_base:
                base = _prompt_optional_text(
                    "Base URL (e.g. http://localhost:8000/v1)"
                )
                if base:
                    prov_cfg.api_base = base
        else:
            if not prov_cfg.api_key:
                url_hint = f"  Get one at: {info['key_url']}" if info["key_url"] else ""
                if url_hint:
                    console.print(f"[dim]{url_hint}[/dim]")
                key = _prompt_optional_secret(f"{info['label']} API key")
                if key:
                    prov_cfg.api_key = key

        # Set a sensible default model for the chosen provider.
        if info["default_model"] and not model:
            config.agents.defaults.model = info["default_model"]

        # Optional: web search API key (Brave Search).
        if not config.tools.web.search.api_key:
            brave = _prompt_optional_secret("Brave Search API key (enables web.search tool)")
            if brave:
                config.tools.web.search.api_key = brave

        # Optional: override default model
        if not model:
            selected_model = typer.prompt(
                "Default model", default=config.agents.defaults.model, show_default=True
            )
            config.agents.defaults.model = (
                (selected_model or "").strip() or config.agents.defaults.model
            )

    save_config(config)
    verb = "Saved" if config_existed else "Created"
    console.print(f"[green]✓[/green] {verb} config at {config_path}")
    
    # Create workspace
    workspace = get_workspace_path()
    console.print(f"[green]✓[/green] Created workspace at {workspace}")
    
    # Create default bootstrap files
    _create_workspace_templates(workspace)
    
    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    if not config.get_api_key():
        console.print("  1. Add your API key to [cyan]~/.nanobot/config.json[/cyan]")
        console.print("     Get one at: https://openrouter.ai/keys")
        console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    else:
        console.print("  1. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
        console.print("  2. Check: [cyan]nanobot status[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")




def _create_workspace_templates(workspace: Path):
    """Create default workspace template files."""
    templates = {
        "AGENTS.md": """# Agent Instructions (Read First)

You are nanobot, the user's personal AI assistant. Your job is to be useful and reliable.

## Guidelines

- Explain what you're doing before using tools
- Ask clarifying questions when the request is ambiguous
- Prefer small, verifiable steps over big guesses
- Summarize what you changed (and where) after edits

## Tools Available

You have access to:
- File operations (read, write, edit, list)
- Shell commands (exec)
- Web access (search, fetch)
- Messaging (message)
- Background tasks (spawn)

## Bootstrap Files

Bootstrap files live in the workspace root:
- `AGENTS.md`: operating rules (this file)
- `SOUL.md`: personality and values
- `USER.md`: user preferences
- `TOOLS.md`: tool reference
- `IDENTITY.md`: role definition

## Memory

- Memory lives under `memory/` in the workspace.
- IMPORTANT: The active memory file path is shown in your system prompt as:
  `Memory file: ...`
  Always write durable facts and user preferences to that file.
- Use daily notes (`YYYY-MM-DD.md`) for temporary/log-style notes.

## Scheduled Reminders

When the user asks for a reminder at a specific time, use `exec` to run:
```
nanobot cron add --name "reminder" --message "Your message" --at "YYYY-MM-DDTHH:MM:SS" --deliver --to "USER_ID" --channel "CHANNEL"
```
Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).

Do NOT just write reminders to a memory file; that won't trigger actual notifications.

## Heartbeat Tasks

`HEARTBEAT.md` is checked every 30 minutes (when running the gateway). Manage periodic tasks by editing this file:
- Add tasks with `edit_file`
- Remove tasks with `edit_file`
- Rewrite the whole list with `write_file`

Task format examples:
```
- [ ] Check weather forecast for today
- [ ] Review today's calendar at 9am
```

## Skills

Skills are modular packages that extend your capabilities with specialized knowledge, workflows, and tools.

### Using Skills

- Your system prompt lists available skills under `<skills>`.
- To use a skill, read its SKILL.md with `read_file` to get the full instructions.
- Skills may contain bundled scripts, references, and assets in subdirectories.

### Creating Skills

When a user asks you to create a skill, use the `skill-creator` skill for guidance.
To read the full skill-creator instructions:
```
read_file("<skill-creator-dir>/SKILL.md")
```
The skill-creator skill contains `scripts/init_skill.py` and `scripts/package_skill.py` for scaffolding and packaging.

Quick scaffold (without loading skill-creator): use `exec` to run:
```
nanobot skills init <skill-name> --description "what the skill does"
```

### Installing Skills

When a user provides a `.skill` file, install it with `exec`:
```
nanobot skills install <path-to-file.skill>
```

Skills are installed to the workspace `skills/` directory and are available immediately.
""",
        "SOUL.md": """# Soul

I am nanobot, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
""",
        "IDENTITY.md": """# Identity

You are nanobot: a practical, tool-using personal assistant.

## Role

- Help the user get real work done: research, writing, coding, planning, automation.
- Use tools to reduce uncertainty or effort. If you can answer directly, answer directly.
- Preserve consistency by following `AGENTS.md`, `SOUL.md`, `USER.md`, and the active memory file shown in the system prompt.
""",
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
        "TOOLS.md": """# Tools (nanobot)

## File Operations

- `read_file(path)`: read a file
- `write_file(path, content)`: write a file (creates parent directories)
- `edit_file(path, old_text, new_text)`: replace an exact snippet (must be unique)
- `list_dir(path)`: list a directory

## Shell Execution

- `exec(command, working_dir?)`: run a shell command

Notes:
- Commands can time out
- Dangerous commands are blocked best-effort
- Output is truncated when very long

## Web Access

- `web_search(query, count?)`: Brave Search (requires `BRAVE_API_KEY` / config)
- `web_fetch(url, extractMode?, maxChars?)`: fetch and extract page content

## Communication

- `message(content, channel?, chat_id?)`: send a message to a specific chat

## Background Tasks

- `spawn(task, label?)`: spawn a subagent for longer work

## Skills Management (CLI)

Use `exec` to manage skills:

- `nanobot skills init <name> -d "description"`: scaffold a new skill in workspace
- `nanobot skills list`: list all available skills
- `nanobot skills install <file.skill>`: install a .skill package
- `nanobot skills install <file.skill> --force`: overwrite an existing skill

### Skill Creation Scripts (from skill-creator skill)

- `python <skill-creator-dir>/scripts/init_skill.py <name> --path <dir>`: full scaffold with optional resources
- `python <skill-creator-dir>/scripts/package_skill.py <skill-dir>`: validate and package into .skill file
""",
    }
    
    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)
            console.print(f"  [dim]Created {filename}[/dim]")
    
    # Create memory directory and MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")
        console.print("  [dim]Created memory/MEMORY.md[/dim]")

    # Create scope directories so the default config (memoryScope=session) feels real immediately.
    # CLI default session id is "cli:default" -> on disk "cli_default".
    sessions_dir = memory_dir / "sessions"
    users_dir = memory_dir / "users"
    sessions_dir.mkdir(exist_ok=True)
    users_dir.mkdir(exist_ok=True)

    cli_session_dir = sessions_dir / "cli_default"
    cli_session_dir.mkdir(parents=True, exist_ok=True)
    cli_session_memory = cli_session_dir / "MEMORY.md"
    if not cli_session_memory.exists():
        cli_session_memory.write_text(
            """# Session Memory (cli:default)

This file is used when `memoryScope` is set to `session` and you're chatting from the CLI default session.

## Durable Facts

- 
""",
            encoding="utf-8",
        )
        console.print("  [dim]Created memory/sessions/cli_default/MEMORY.md[/dim]")


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(18790, "--port", "-p", help="Gateway port"),
    webui: bool = typer.Option(False, "--webui", help="Enable the local Web UI channel"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot gateway."""
    from nanobot.config.loader import load_config, get_data_dir
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.agent.loop import AgentLoop
    from nanobot.channels.manager import ChannelManager
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    
    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)
    
    console.print(f"{__logo__} Starting nanobot gateway on port {port}...")
    
    config = load_config()

    # Convenience: allow enabling WebUI without editing config.json.
    if webui and getattr(config.channels, "webui", None):
        config.channels.webui.enabled = True
    
    # Create components
    bus = MessageBus()
    
    # Create provider (supports OpenRouter, Anthropic, OpenAI, Bedrock)
    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model = config.agents.defaults.model
    is_bedrock = model.startswith("bedrock/")

    if not api_key and not is_bedrock:
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in ~/.nanobot/config.json under providers.openrouter.apiKey")
        raise typer.Exit(1)
    
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=config.agents.defaults.model
    )
    
    # Create agent
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        max_iterations=config.agents.defaults.max_tool_iterations,
        agent_config=config.agents.defaults,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        allowed_tools=config.tools.allowed_tools,
    )
    
    # Create cron service
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        response = await agent.process_direct(
            job.payload.message,
            session_key=f"cron:{job.id}"
        )
        # Optionally deliver to channel
        if job.payload.deliver and job.payload.to:
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "whatsapp",
                chat_id=job.payload.to,
                content=response or ""
            ))
        return response
    
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path, on_job=on_cron_job)
    
    # Create heartbeat service
    async def on_heartbeat(prompt: str) -> str:
        """Execute heartbeat through the agent."""
        return await agent.process_direct(prompt, session_key="heartbeat")
    
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        on_heartbeat=on_heartbeat,
        interval_s=30 * 60,  # 30 minutes
        enabled=True
    )
    
    # Create channel manager
    channels = ChannelManager(config, bus)
    
    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")
        console.print("[dim]Tip: run `nanobot gateway --webui` or enable a channel in ~/.nanobot/config.json[/dim]")

    # Web UI hint (served by the webui channel itself).
    webui_url: str | None = None
    try:
        if getattr(config.channels, "webui", None) and config.channels.webui.enabled:
            host = (config.channels.webui.host or "127.0.0.1").strip()
            wport = int(config.channels.webui.port or 18791)
            token_param = ""
            if (config.channels.webui.auth_token or "").strip():
                token_param = f"?token={config.channels.webui.auth_token}"
            webui_url = f"http://{host}:{wport}/{token_param}"
            console.print(f"[green]✓[/green] WebUI: {webui_url}")
    except Exception:
        # Never fail gateway startup due to a hint.
        pass
    
    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")
    
    console.print(f"[green]✓[/green] Heartbeat: every 30m")

    # Auto-open WebUI in the default browser.
    if webui_url:
        import webbrowser
        webbrowser.open(webui_url)

    async def run():
        agent_task: asyncio.Task | None = None
        channels_task: asyncio.Task | None = None
        try:
            await cron.start()
            await heartbeat.start()

            # asyncio.run() handles SIGINT by cancelling the main task; we still want
            # our cleanup to run reliably (finally block below).
            agent_task = asyncio.create_task(agent.run(), name="agent.run")
            channels_task = asyncio.create_task(channels.start_all(), name="channels.start_all")
            await asyncio.gather(agent_task, channels_task)
        except (asyncio.CancelledError, KeyboardInterrupt):
            # Cancellation is expected on SIGINT; proceed to cleanup.
            pass
        finally:
            console.print("\nShutting down...")
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()

            for t in (agent_task, channels_task):
                if t is not None and not t.done():
                    t.cancel()
            # Ensure background tasks settle before closing the loop.
            await asyncio.gather(*(t for t in (agent_task, channels_task) if t is not None), return_exceptions=True)
    
    asyncio.run(run())




# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:default", "--session", "-s", help="Session ID"),
    media: list[str] = typer.Option(
        [],
        "--media",
        help="Attach local image/PDF paths (repeatable). Example: --media ./diagram.png --media ./doc.pdf",
    ),
):
    """Interact with the agent directly."""
    from nanobot.config.loader import load_config
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.agent.loop import AgentLoop
    
    config = load_config()
    
    api_key = config.get_api_key()
    api_base = config.get_api_base()
    model = config.agents.defaults.model
    is_bedrock = model.startswith("bedrock/")

    if not api_key and not is_bedrock:
        console.print("[red]Error: No API key configured.[/red]")
        raise typer.Exit(1)

    bus = MessageBus()
    provider = LiteLLMProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=config.agents.defaults.model
    )
    
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        brave_api_key=config.tools.web.search.api_key or None,
        exec_config=config.tools.exec,
        allowed_tools=config.tools.allowed_tools,
        agent_config=config.agents.defaults,
    )
    
    if message:
        # Single message mode
        async def run_once():
            response = await agent_loop.process_direct(message, session_id, media=media or None)
            console.print(f"\n{__logo__} {response}")
        
        asyncio.run(run_once())
    else:
        # Interactive mode
        console.print(f"{__logo__} Interactive mode (Ctrl+C to exit)\n")
        
        async def run_interactive():
            while True:
                try:
                    user_input = console.input("[bold blue]You:[/bold blue] ")
                    if not user_input.strip():
                        continue
                    
                    response = await agent_loop.process_direct(user_input, session_id)
                    console.print(f"\n{__logo__} {response}\n")
                except KeyboardInterrupt:
                    console.print("\nGoodbye!")
                    break
        
        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")

skills_app = typer.Typer(help="Manage skills")
app.add_typer(skills_app, name="skills")


@skills_app.command("init")
def skills_init(
    name: str = typer.Argument(..., help="Skill name (directory name)"),
    description: str = typer.Option("", "--description", "-d", help="Short description"),
):
    """Create a new skill scaffold in the workspace."""
    from nanobot.utils.helpers import get_skills_path

    skills_dir = get_skills_path()
    skill_dir = skills_dir / name
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        console.print(f"[red]Skill already exists at {skill_file}[/red]")
        raise typer.Exit(1)

    skill_dir.mkdir(parents=True, exist_ok=True)
    desc = description or name
    content = f"""---
description: "{desc}"
---
# {name}

## Goal
Describe what this skill helps the agent do.

## When to use
- Example: Use when the user asks for X.

## Steps
1. Step-by-step guidance for the agent.

## Notes
- Add any constraints, caveats, or examples.
"""
    skill_file.write_text(content, encoding="utf-8")
    console.print(f"[green]✓[/green] Created skill at {skill_file}")


@skills_app.command("list")
def skills_list():
    """List all available skills."""
    from nanobot.agent.skills import SkillsLoader
    from nanobot.utils.helpers import get_workspace_path

    workspace = get_workspace_path()
    loader = SkillsLoader(workspace)
    all_skills = loader.list_skills(filter_unavailable=False)

    if not all_skills:
        console.print("No skills found.")
        return

    table = Table(title="Available Skills")
    table.add_column("Name", style="cyan")
    table.add_column("Source", style="yellow")
    table.add_column("Description")

    for s in all_skills:
        meta = loader.get_skill_metadata(s["name"])
        desc = (meta.get("description", "") if meta else "") or "[dim]—[/dim]"
        if len(desc) > 80:
            desc = desc[:77] + "..."
        table.add_row(s["name"], s["source"], desc)

    console.print(table)


@skills_app.command("install")
def skills_install_file(
    path: str = typer.Argument(..., help="Path to a .skill file (zip archive)"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing skill"),
):
    """Install a skill from a .skill package file."""
    import zipfile

    from nanobot.utils.helpers import get_skills_path

    skill_path = Path(path).expanduser().resolve()
    if not skill_path.exists():
        console.print(f"[red]File not found: {skill_path}[/red]")
        raise typer.Exit(1)

    if not zipfile.is_zipfile(skill_path):
        console.print(f"[red]Not a valid .skill (zip) file: {skill_path}[/red]")
        raise typer.Exit(1)

    skills_dir = get_skills_path()

    with zipfile.ZipFile(skill_path, "r") as zf:
        # Detect skill name from the top-level directory in the archive
        names = zf.namelist()
        top_dirs = {n.split("/")[0] for n in names if "/" in n}
        if len(top_dirs) != 1:
            console.print("[red]Invalid .skill archive: expected exactly one top-level directory.[/red]")
            raise typer.Exit(1)

        skill_name = top_dirs.pop()
        target_dir = skills_dir / skill_name

        if target_dir.exists() and not force:
            console.print(
                f"[red]Skill '{skill_name}' already exists at {target_dir}[/red]\n"
                f"Use --force to overwrite."
            )
            raise typer.Exit(1)

        if target_dir.exists():
            import shutil
            shutil.rmtree(target_dir)

        zf.extractall(skills_dir)

    # Verify SKILL.md was extracted
    if not (target_dir / "SKILL.md").exists():
        console.print(f"[red]Warning: No SKILL.md found in extracted skill '{skill_name}'[/red]")
    else:
        console.print(f"[green]✓[/green] Installed skill '{skill_name}' to {target_dir}")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = (
        f"appId: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    )
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # WebUI
    if getattr(config.channels, "webui", None):
        wu = config.channels.webui
        wu_config = f"{wu.host}:{wu.port}"
        table.add_row(
            "WebUI",
            "✓" if wu.enabled else "✗",
            wu_config,
        )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess
    
    # User's bridge location
    user_bridge = Path.home() / ".nanobot" / "bridge"
    
    # Check if already built
    if (user_bridge / "dist" / "index.js").exists():
        return user_bridge
    
    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)
    
    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)
    
    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge
    
    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)
    
    console.print(f"{__logo__} Setting up bridge...")
    
    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))
    
    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)
        
        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)
        
        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)
    
    return user_bridge


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess
    
    bridge_dir = _get_bridge_dir()
    
    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")
    
    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


# ============================================================================
# Cron Commands
# ============================================================================

cron_app = typer.Typer(help="Manage scheduled tasks")
app.add_typer(cron_app, name="cron")


@cron_app.command("list")
def cron_list(
    all: bool = typer.Option(False, "--all", "-a", help="Include disabled jobs"),
):
    """List scheduled jobs."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    jobs = service.list_jobs(include_disabled=all)
    
    if not jobs:
        console.print("No scheduled jobs.")
        return
    
    table = Table(title="Scheduled Jobs")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Schedule")
    table.add_column("Status")
    table.add_column("Next Run")
    
    import time
    for job in jobs:
        # Format schedule
        if job.schedule.kind == "every":
            sched = f"every {(job.schedule.every_ms or 0) // 1000}s"
        elif job.schedule.kind == "cron":
            sched = job.schedule.expr or ""
        else:
            sched = "one-time"
        
        # Format next run
        next_run = ""
        if job.state.next_run_at_ms:
            next_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(job.state.next_run_at_ms / 1000))
            next_run = next_time
        
        status = "[green]enabled[/green]" if job.enabled else "[dim]disabled[/dim]"
        
        table.add_row(job.id, job.name, sched, status, next_run)
    
    console.print(table)


@cron_app.command("add")
def cron_add(
    name: str = typer.Option(..., "--name", "-n", help="Job name"),
    message: str = typer.Option(..., "--message", "-m", help="Message for agent"),
    every: int = typer.Option(None, "--every", "-e", help="Run every N seconds"),
    cron_expr: str = typer.Option(None, "--cron", "-c", help="Cron expression (e.g. '0 9 * * *')"),
    at: str = typer.Option(None, "--at", help="Run once at time (ISO format)"),
    deliver: bool = typer.Option(False, "--deliver", "-d", help="Deliver response to channel"),
    to: str = typer.Option(None, "--to", help="Recipient for delivery"),
    channel: str = typer.Option(None, "--channel", help="Channel for delivery (e.g. 'telegram', 'whatsapp')"),
):
    """Add a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronSchedule
    
    # Determine schedule type
    if every:
        schedule = CronSchedule(kind="every", every_ms=every * 1000)
    elif cron_expr:
        schedule = CronSchedule(kind="cron", expr=cron_expr)
    elif at:
        import datetime
        dt = datetime.datetime.fromisoformat(at)
        schedule = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
    else:
        console.print("[red]Error: Must specify --every, --cron, or --at[/red]")
        raise typer.Exit(1)
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    job = service.add_job(
        name=name,
        schedule=schedule,
        message=message,
        deliver=deliver,
        to=to,
        channel=channel,
    )
    
    console.print(f"[green]✓[/green] Added job '{job.name}' ({job.id})")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    if service.remove_job(job_id):
        console.print(f"[green]✓[/green] Removed job {job_id}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID"),
    disable: bool = typer.Option(False, "--disable", help="Disable instead of enable"),
):
    """Enable or disable a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    job = service.enable_job(job_id, enabled=not disable)
    if job:
        status = "disabled" if disable else "enabled"
        console.print(f"[green]✓[/green] Job '{job.name}' {status}")
    else:
        console.print(f"[red]Job {job_id} not found[/red]")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
    force: bool = typer.Option(False, "--force", "-f", help="Run even if disabled"),
):
    """Manually run a job."""
    from nanobot.config.loader import get_data_dir
    from nanobot.cron.service import CronService
    
    store_path = get_data_dir() / "cron" / "jobs.json"
    service = CronService(store_path)
    
    async def run():
        return await service.run_job(job_id, force=force)
    
    if asyncio.run(run()):
        console.print(f"[green]✓[/green] Job executed")
    else:
        console.print(f"[red]Failed to run job {job_id}[/red]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import load_config, get_config_path

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path

    console.print(f"{__logo__} nanobot Status\n")

    console.print(f"Config: {config_path} {'[green]✓[/green]' if config_path.exists() else '[red]✗[/red]'}")
    console.print(f"Workspace: {workspace} {'[green]✓[/green]' if workspace.exists() else '[red]✗[/red]'}")

    if config_path.exists():
        console.print(f"Model: {config.agents.defaults.model}")
        
        # Check API keys
        has_openrouter = bool(config.providers.openrouter.api_key)
        has_anthropic = bool(config.providers.anthropic.api_key)
        has_openai = bool(config.providers.openai.api_key)
        has_gemini = bool(config.providers.gemini.api_key)
        has_vllm = bool(config.providers.vllm.api_base)
        
        console.print(f"OpenRouter API: {'[green]✓[/green]' if has_openrouter else '[dim]not set[/dim]'}")
        console.print(f"Anthropic API: {'[green]✓[/green]' if has_anthropic else '[dim]not set[/dim]'}")
        console.print(f"OpenAI API: {'[green]✓[/green]' if has_openai else '[dim]not set[/dim]'}")
        console.print(f"Gemini API: {'[green]✓[/green]' if has_gemini else '[dim]not set[/dim]'}")
        vllm_status = f"[green]✓ {config.providers.vllm.api_base}[/green]" if has_vllm else "[dim]not set[/dim]"
        console.print(f"vLLM/Local: {vllm_status}")


if __name__ == "__main__":
    app()
