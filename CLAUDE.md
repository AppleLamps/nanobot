# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

```bash
# Install for development
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test file
pytest tests/test_provider.py

# Run a single test function
pytest tests/test_provider.py::test_chat_returns_llm_response

# Lint
ruff check nanobot/

# Format check
ruff format --check nanobot/
```

## Architecture

Nanobot is an ultra-lightweight AI agent framework (~9,600 lines of Python). The core flow is:

**Channel → MessageBus → AgentLoop → LLMProvider → Tools → MessageBus → Channel**

### Key Components

- **AgentLoop** (`agent/loop.py`): Main agent loop. Receives messages from the bus, builds context (bootstrap files + memory + skills + history), calls the LLM, executes tools in a loop (max 20 iterations), and publishes responses.

- **LLMProvider** (`providers/base.py`, `providers/openrouter_provider.py`): Abstract base + concrete implementation. Uses direct httpx calls to OpenRouter's OpenAI-compatible API (`POST /api/v1/chat/completions`). No streaming — request/response only. Provider is injected into AgentLoop and SubagentManager via constructor.

- **MessageBus** (`bus/queue.py`): Async inbound/outbound queues that decouple channels from the agent. Channels publish `InboundMessage`, agent consumes and produces `OutboundMessage`.

- **ToolRegistry** (`agent/tools/registry.py`): Manages tool registration, caching (LRU with TTL), and parallel execution. Tools are request-scoped. Individual tools are in `agent/tools/` (filesystem, shell, web, spawn, message, subagent_control).

- **Memory** (`agent/memory.py`, `agent/memory_db.py`): Three scopes — global (workspace), session (channel:chat_id), user (channel:sender_id). Stored as markdown files + SQLite FTS index. Context builder injects relevant chunks via semantic search.

- **Channels** (`channels/`): BaseChannel abstract class with implementations for Telegram, WhatsApp (Node.js bridge), Feishu (WebSocket), and WebUI (HTTP + WebSocket). Managed by ChannelManager.

- **SubagentManager** (`agent/subagent.py`): Spawns background agents for long-running tasks. Same tools as main agent, isolated context. Main agent stays responsive.

- **Config** (`config/schema.py`, `config/loader.py`): Pydantic models with camelCase (JSON) ↔ snake_case (Python) bidirectional conversion. Loaded from `~/.nanobot/config.json`. Provider selection priority: explicit > openrouter > anthropic > openai > gemini > zhipu > groq > vllm.

- **Context Builder** (`agent/context.py`): Assembles the system prompt from bootstrap files (AGENTS.md, SOUL.md, USER.md, TOOLS.md), memory chunks, skill summaries, and conversation history with configurable char budgets.

- **Skills** (`agent/skills.py`): Markdown-based skill packages loaded from `workspace/skills/`. YAML frontmatter + instructions in SKILL.md. Summaries injected into prompt; full content loaded on demand.

### Concurrency Model

- Per-chat sequential: messages from the same chat are processed in order
- Cross-chat parallel: up to `maxConcurrentMessages` (default 4) different chats in parallel
- SessionManager uses FileLock for atomic JSONL session writes
- ToolRegistry limits parallel tool execution (default 8)

## Testing Patterns

- Tests use `pytest` + `pytest-asyncio` (asyncio_mode = "auto")
- LLM calls are mocked by monkeypatching `httpx.AsyncClient.post` with a `FakeResponse` class
- Use `tmp_path` fixture for filesystem operations
- Two known pre-existing test failures: `test_multi_chat_concurrency` and `test_webui_channel` (unrelated to provider work)

## Code Style

- Ruff with line-length 100, target Python 3.11
- Lint rules: E, F, I, N, W (ignoring E501)
- Config JSON uses camelCase; Python code uses snake_case

## Data Directory Resolution

1. `NANOBOT_DATA_DIR` env var (absolute override)
2. `NANOBOT_PROFILE=name` / `--profile name` → `~/.nanobot_name/`
3. Default: `~/.nanobot/`

## Entry Point

CLI entry point: `nanobot/cli/commands.py` (typer app, registered as `nanobot` console script). Provider is instantiated here and injected into AgentLoop/SubagentManager.
