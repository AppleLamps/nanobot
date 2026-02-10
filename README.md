<div align="center">
  <img src="nanobot_logo.png" alt="nanobot" width="500">
  <h1>nanobot: Ultra-Lightweight Personal AI Assistant</h1>
  <p>
    <a href="https://pypi.org/project/nanobot-ai/"><img src="https://img.shields.io/pypi/v/nanobot-ai" alt="PyPI"></a>
    <a href="https://pepy.tech/project/nanobot-ai"><img src="https://static.pepy.tech/badge/nanobot-ai" alt="Downloads"></a>
    <img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
    <a href="./COMMUNICATION.md"><img src="https://img.shields.io/badge/Feishu-Group-E9DBFC?style=flat&logo=feishu&logoColor=white" alt="Feishu"></a>
    <a href="./COMMUNICATION.md"><img src="https://img.shields.io/badge/WeChat-Group-C5EAB4?style=flat&logo=wechat&logoColor=white" alt="WeChat"></a>
    <a href="https://discord.gg/MnCvHqpUGB"><img src="https://img.shields.io/badge/Discord-Community-5865F2?style=flat&logo=discord&logoColor=white" alt="Discord"></a>
  </p>
</div>

🐈 **nanobot** is an **ultra-lightweight** personal AI assistant inspired by [Clawdbot](https://github.com/openclaw/openclaw)

⚡️ Delivers core agent functionality in about **~10,000** lines of Python (excluding tests) — **~98% smaller** than Clawdbot's 430k+ lines.

## 📢 News

- **2026-02-01** 🎉 nanobot launched! Welcome to try 🐈 nanobot!

## Key Features of nanobot

🪶 **Ultra-Lightweight**: About ~10,000 lines of Python (excluding tests) — ~98% smaller than Clawdbot - core functionality.

🔬 **Research-Ready**: Clean, readable code that's easy to understand, modify, and extend for research.

⚡️ **Lightning Fast**: Minimal footprint means faster startup, lower resource usage, and quicker iterations.

💎 **Easy-to-Use**: One command to onboard and you're ready to go.

🔀 **Subagent Delegation**: Spawn background subagents for long-running tasks while the main agent stays responsive.

## 🏗️ Architecture

<p align="center">
  <img src="nanobot_arch.png" alt="nanobot architecture" width="800">
</p>

## ✨ Features

<table align="center">
  <tr align="center">
    <th><p align="center">📈 24/7 Real-Time Market Analysis</p></th>
    <th><p align="center">🚀 Full-Stack Software Engineer</p></th>
    <th><p align="center">📅 Smart Daily Routine Manager</p></th>
    <th><p align="center">📚 Personal Knowledge Assistant</p></th>
  </tr>
  <tr>
    <td align="center"><p align="center"><img src="case/search.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/code.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/scedule.gif" width="180" height="400"></p></td>
    <td align="center"><p align="center"><img src="case/memory.gif" width="180" height="400"></p></td>
  </tr>
  <tr>
    <td align="center">Discovery • Insights • Trends</td>
    <td align="center">Develop • Deploy • Scale</td>
    <td align="center">Schedule • Automate • Organize</td>
    <td align="center">Learn • Memory • Reasoning</td>
  </tr>
</table>

## ✅ Requirements

- Python 3.11+
- Node.js 18+ (only for the WhatsApp channel)
- Optional: Docker, uv

## 📦 Install

**Install from source** (latest features, recommended for development)

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
pip install -e .
```

For contributors:

```bash
pip install -e ".[dev]"
```

**Install with [uv](https://github.com/astral-sh/uv)** (stable, fast)

```bash
uv tool install nanobot-ai
```

**Install from PyPI** (stable)

```bash
pip install nanobot-ai
```

## 🚀 Quick Start

> [!TIP]
> Run `nanobot onboard` to create `~/.nanobot/config.json` (default profile). In an interactive terminal, it will also prompt you for API keys (you can skip and edit the JSON later). Use `nanobot onboard --no-prompt` for non-interactive runs.
> Get API keys: [OpenRouter](https://openrouter.ai/keys) (LLM) · [Brave Search](https://brave.com/search/api/) (optional, for web search) · [Firecrawl](https://firecrawl.dev/) (optional, for web scraping)
> You can also change the model to any provider/model you have access to.
>
> Profiles: use `nanobot --profile jason ...` (or `NANOBOT_PROFILE=jason`) to use `~/.nanobot_jason/` instead of `~/.nanobot/`.

**1. Initialize**

```bash
nanobot onboard
```

**2. Configure** (default: `~/.nanobot/config.json`)

```json
{
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    }
  },
  "agents": {
    "defaults": {
      "model": "openai/gpt-oss-120b:exacto",
      "memoryScope": "session",
      "maxConcurrentMessages": 4
    }
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": "BSA-xxx"
      },
      "firecrawl": {
        "apiKey": "fc-xxx"
      }
    }
  }
}
```

**3. Chat**

```bash
nanobot agent -m "What is 2+2?"
```

That's it! You have a working AI assistant in 2 minutes.

## 🖥️ Local Models (vLLM)

Run nanobot with your own local models using vLLM or any OpenAI-compatible server.

**1. Start your vLLM server**

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct --port 8000
```

**2. Configure** (`~/.nanobot/config.json`)

```json
{
  "providers": {
    "vllm": {
      "apiKey": "dummy",
      "apiBase": "http://localhost:8000/v1"
    }
  },
  "agents": {
    "defaults": {
      "model": "meta-llama/Llama-3.1-8B-Instruct"
    }
  }
}
```

**3. Chat**

```bash
nanobot agent -m "Hello from my local LLM!"
```

> [!TIP]
> The `apiKey` can be any non-empty string for local servers that don't require authentication.

## 🖥️ Web UI (Optional)

Prefer a local browser chat UI? Enable the built-in `webui` channel.

**1. Configure** (`~/.nanobot/config.json`)

```json
{
  "channels": {
    "webui": {
      "enabled": true,
      "host": "127.0.0.1",
      "port": 18791
    }
  }
}
```

**2. Run**

```bash
nanobot gateway --webui
# or enable channels.webui.enabled in config.json and run:
nanobot gateway
```

**3. Open**

- `http://127.0.0.1:18791/`

Notes:

- By default it binds to loopback (`127.0.0.1`) for safety.
- If you bind to a non-loopback host (e.g. `0.0.0.0`), set `channels.webui.authToken` and open with `?token=...`.

## 💬 Chat Apps

Talk to your nanobot through Telegram, WhatsApp, or Feishu — anytime, anywhere.

| Channel | Setup |
|---------|-------|
| **Telegram** | Easy (just a token) |
| **WhatsApp** | Medium (scan QR) |
| **Feishu** | Medium (app credentials) |

<details>
<summary><b>Telegram</b> (Recommended)</summary>

**1. Create a bot**

- Open Telegram, search `@BotFather`
- Send `/newbot`, follow prompts
- Copy the token

**2. Configure**

```json
{
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

> Get your user ID from `@userinfobot` on Telegram.

**3. Run**

```bash
nanobot gateway
```

</details>

<details>
<summary><b>WhatsApp</b></summary>

Requires **Node.js ≥18**.

**1. Link device**

```bash
nanobot channels login
# Scan QR with WhatsApp → Settings → Linked Devices
```

**2. Configure**

```json
{
  "channels": {
    "whatsapp": {
      "enabled": true,
      "allowFrom": ["+1234567890"]
    }
  }
}
```

**3. Run** (two terminals)

```bash
# Terminal 1
nanobot channels login

# Terminal 2
nanobot gateway
```

</details>

<details>
<summary><b>Feishu (飞书)</b></summary>

Uses **WebSocket** long connection — no public IP required.

```bash
pip install "nanobot-ai[feishu]"
```

**1. Create a Feishu bot**

- Visit [Feishu Open Platform](https://open.feishu.cn/app)
- Create a new app → Enable **Bot** capability
- **Permissions**: Add `im:message` (send messages)
- **Events**: Add `im.message.receive_v1` (receive messages)
  - Select **Long Connection** mode (requires running nanobot first to establish connection)
- Get **App ID** and **App Secret** from "Credentials & Basic Info"
- Publish the app

**2. Configure**

```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": []
    }
  }
}
```

> `encryptKey` and `verificationToken` are optional for Long Connection mode.
> `allowFrom`: Leave empty to allow all users, or add `["ou_xxx"]` to restrict access.

**3. Run**

```bash
nanobot gateway
```

> [!TIP]
> Feishu uses WebSocket to receive messages — no webhook or public IP needed!

</details>

## ⚙️ Configuration

Config file (default): `~/.nanobot/config.json` (use `--profile` / `NANOBOT_PROFILE` or `--data-dir` / `NANOBOT_DATA_DIR` to change this)

### Providers

> [!NOTE]
> Groq provides free voice transcription via Whisper. If configured, Telegram voice messages will be automatically transcribed.

| Provider | Purpose | Get API Key |
|----------|---------|-------------|
| `openrouter` | LLM (recommended, access to all models) | [openrouter.ai](https://openrouter.ai) |
| `anthropic` | LLM (Claude direct) | [console.anthropic.com](https://console.anthropic.com) |
| `openai` | LLM (GPT direct) | [platform.openai.com](https://platform.openai.com) |
| `groq` | LLM + **Voice transcription** (Whisper) | [console.groq.com](https://console.groq.com) |
| `gemini` | LLM (Gemini direct) | [aistudio.google.com](https://aistudio.google.com) |
| `zhipu` | LLM (Zhipu/GLM) | [open.bigmodel.cn](https://open.bigmodel.cn) |
| `bedrock` | AWS Bedrock models (use `bedrock/` prefix in model name) | [AWS Console](https://aws.amazon.com/bedrock/) |
| `vllm` | Local / OpenAI-compatible endpoint | — (set `apiBase` instead) |

### Agents

`agents.defaults` controls runtime behavior of the core agent.

| Field | Purpose | Default |
|------|---------|---------|
| `workspace` | Workspace path | `~/.nanobot/workspace` (or `~/.nanobot_<profile>/workspace`) |
| `provider` | Explicit provider override | `""` |
| `model` | LLM model id | `openai/gpt-oss-120b:exacto` |
| `fallbackModels` | Fallback models if primary fails | `[]` |
| `maxTokens` | Max tokens per response | `8192` |
| `temperature` | Sampling temperature | `0.7` |
| `maxToolIterations` | Max tool loop iterations per message | `20` |
| `memoryScope` | Memory isolation boundary: `session` = per chat (channel:chat_id), `user` = per user (channel:sender_id), `global` = workspace-wide | `session` |
| `maxConcurrentMessages` | Max number of different chats processed in parallel (messages within the same chat are still sequential) | `4` |
| `memoryMaxChars` | Prompt budget for memory injection (chars) | `6000` |
| `skillsMaxChars` | Prompt budget for skills injection (chars) | `12000` |
| `bootstrapMaxChars` | Prompt budget for bootstrap files (chars) | `4000` |
| `historyMaxChars` | Prompt budget for conversation history (chars) | `80000` |
| `toolErrorBackoff` | Tool retry backoff (attempts) | `3` |
| `autoTuneMaxTokens` | Auto-tune response length | `false` |
| `initialMaxTokens` | Initial tokens when auto-tune is on | `null` |
| `autoTuneStep` | Auto-tune step size | `512` |
| `autoTuneThreshold` | Auto-tune trigger threshold | `0.85` |
| `autoTuneStreak` | Consecutive triggers to adjust | `3` |
| `subagentBootstrapChars` | Prompt budget for subagent bootstrap | `3000` |
| `subagentContextChars` | Prompt budget for subagent context | `3000` |

### Memory

Memory files live under the workspace `memory/` directory and are scoped based on `memoryScope`:

- `session`: `memory/sessions/<safe-session-key>/MEMORY.md`
- `user`: `memory/users/<safe-user-key>/MEMORY.md`

`safe-*` keys are derived from `channel:chat_id` or `channel:sender_id` with filesystem-unsafe characters replaced (e.g., `:` -> `_`).

nanobot indexes memory into `memory/memory.sqlite3` (SQLite with FTS when available) and retrieves only the most relevant chunks per request, instead of injecting a growing `MEMORY.md` into every prompt.

### Sessions

nanobot persists chat history as JSONL under `~/.nanobot/sessions/` (or `~/.nanobot_<profile>/sessions/`). Session writes are locked and atomic, so concurrent chats (or multiple processes) don't corrupt the history.

### Tools

`tools` controls tool behavior and access.

| Field | Purpose | Default |
|-------|---------|---------|
| `tools.web.search.apiKey` | Brave Search API key | `""` |
| `tools.web.search.maxResults` | Max search results | `5` |
| `tools.web.firecrawl.apiKey` | Firecrawl API key (enables `firecrawl_scrape` tool) | `""` |
| `tools.exec.timeout` | Shell command timeout (seconds) | `60` |
| `tools.exec.restrictToWorkspace` | Block commands accessing paths outside workspace | `true` |
| `tools.allowedTools` | Optional allowlist of tool names (e.g. `["read_file", "web_search"]`) | `null` (all tools) |

<details>
<summary><b>Full config example</b></summary>

```json
{
  "agents": {
    "defaults": {
      "model": "openai/gpt-oss-120b:exacto",
      "fallbackModels": ["anthropic/claude-sonnet-4-20250514"],
      "memoryScope": "session",
      "maxConcurrentMessages": 4
    }
  },
  "providers": {
    "openrouter": {
      "apiKey": "sk-or-v1-xxx"
    },
    "groq": {
      "apiKey": "gsk_xxx"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "123456:ABC...",
      "allowFrom": ["123456789"]
    },
    "whatsapp": {
      "enabled": false
    },
    "feishu": {
      "enabled": false,
      "appId": "cli_xxx",
      "appSecret": "xxx",
      "encryptKey": "",
      "verificationToken": "",
      "allowFrom": []
    }
  },
  "tools": {
    "web": {
      "search": {
        "apiKey": "BSA..."
      },
      "firecrawl": {
        "apiKey": "fc-..."
      }
    },
    "exec": {
      "timeout": 60,
      "restrictToWorkspace": true
    }
  }
}
```

</details>

## 🤖 Multiple Agents (Profiles)

Profiles let you run **multiple independent agents**, each with its own config, workspace, memory, and sessions. This is useful for separating personal vs work assistants, running different models, or giving each agent a different personality.

**How it works:** `--profile <name>` (or `NANOBOT_PROFILE=<name>`) switches the data directory from `~/.nanobot/` to `~/.nanobot_<name>/`.

### Quick Start

```bash
# Create a "work" agent with its own config
nanobot --profile work onboard

# Create a "personal" agent
nanobot --profile personal onboard
```

### Configure Each Agent Independently

Each profile gets its own `config.json`, so you can use different models, providers, or settings:

```bash
# Edit the work agent's config
# ~/.nanobot_work/config.json
```

```json
{
  "providers": {
    "anthropic": { "apiKey": "sk-ant-xxx" }
  },
  "agents": {
    "defaults": {
      "model": "anthropic/claude-sonnet-4-20250514",
      "memoryScope": "session"
    }
  }
}
```

```bash
# Edit the personal agent's config
# ~/.nanobot_personal/config.json
```

```json
{
  "providers": {
    "openrouter": { "apiKey": "sk-or-v1-xxx" }
  },
  "agents": {
    "defaults": {
      "model": "openai/gpt-4o",
      "memoryScope": "user"
    }
  }
}
```

### Use Each Agent

```bash
# Chat with the work agent
nanobot --profile work agent -m "Summarize yesterday's PRs"

# Chat with the personal agent
nanobot --profile personal agent -m "What's on my calendar today?"

# Run the work agent as a gateway (Telegram, WhatsApp, etc.)
nanobot --profile work gateway

# Check status of any profile
nanobot --profile personal status
```

### Using Environment Variables

```bash
# Set profile via environment variable (useful in scripts, cron, Docker)
export NANOBOT_PROFILE=work
nanobot agent -m "Hello from the work agent!"

# Or override the entire data directory
export NANOBOT_DATA_DIR=/path/to/custom/data
nanobot agent -m "Using a custom data directory"
```

### What Each Profile Gets

| Resource | Path |
|----------|------|
| Config | `~/.nanobot_<profile>/config.json` |
| Workspace | `~/.nanobot_<profile>/workspace/` |
| Sessions | `~/.nanobot_<profile>/sessions/` |
| Memory | `~/.nanobot_<profile>/workspace/memory/` |
| Skills | `~/.nanobot_<profile>/workspace/skills/` |

Each profile is fully isolated — different models, different memories, different personalities. The default profile (no `--profile` flag) uses `~/.nanobot/`.

## CLI Reference

| Command | Description |
|---------|-------------|
| `nanobot onboard` | Initialize config & workspace |
| `nanobot onboard --no-prompt` | Initialize without interactive prompts |
| `nanobot agent -m "..."` | Chat with the agent |
| `nanobot agent` | Interactive chat mode |
| `nanobot agent -m "..." --media img.png` | Chat with image/PDF attachments |
| `nanobot agent --session my-project` | Chat in a named session |
| `nanobot gateway` | Start the gateway (all enabled channels + cron + heartbeat) |
| `nanobot gateway --webui` | Start gateway with Web UI enabled |
| `nanobot gateway --port 8080` | Start gateway on a custom port |
| `nanobot status` | Show status (API keys, workspace, providers) |
| `nanobot channels login` | Link WhatsApp (scan QR) |
| `nanobot channels status` | Show all channel configurations and status |
| `nanobot skills list` | List all available skills with descriptions |
| `nanobot skills init <name>` | Create a new skill scaffold with SKILL.md template |
| `nanobot skills install <file>` | Install a .skill package (zip archive) |
| `nanobot skills install <file> --force` | Install and overwrite existing skill |
| `nanobot cron list` | List all scheduled jobs with status |
| `nanobot cron list --all` | List all jobs including disabled ones |
| `nanobot cron add ...` | Add a scheduled job (see Scheduled Tasks section) |
| `nanobot cron remove <id>` | Remove a scheduled job |
| `nanobot cron enable <id>` | Enable a job |
| `nanobot cron enable <id> --disable` | Disable a job without deleting |
| `nanobot cron run <id>` | Manually trigger a job |
| `nanobot cron run <id> --force` | Force-run a disabled job |
| `nanobot --profile <name> ...` | Use a named profile (separate config & data) |
| `nanobot --version` | Show version |

### Global Flags

| Flag | Env Variable | Description |
|------|-------------|-------------|
| `--profile <name>` | `NANOBOT_PROFILE` | Use `~/.nanobot_<name>/` for all data |
| `--data-dir <path>` | `NANOBOT_DATA_DIR` | Override the data directory entirely |
| `--version` / `-v` | — | Print version and exit |

<details>
<summary><b>Scheduled Tasks (Cron)</b></summary>

nanobot supports scheduled tasks with flexible timing (cron expressions, intervals, or one-time execution) and two job types:

**Job Types:**
- **task** (default) - Message is processed by the agent with full tool access; agent's response is delivered
- **reminder** - Message is delivered verbatim without agent processing (simple notification)

**Scheduling Options:**
- `--cron` - Standard cron expression (e.g., "0 9 * * *" for 9 AM daily)
- `--every` - Repeat every N seconds
- `--at` - One-time execution at ISO timestamp (e.g., "2026-03-01T14:00:00")

```bash
# Add a task job (processed by agent; default)
nanobot cron add --name "daily" --message "Good morning! What's on my schedule?" --cron "0 9 * * *"

# Add a job (interval in seconds)
nanobot cron add --name "hourly" --message "Check system status" --every 3600

# Add a one-time job
nanobot cron add --name "reminder" --message "Call dentist" --at "2026-03-01T14:00:00"

# Deliver the agent's response to a specific channel (with --deliver)
nanobot cron add --name "report" --message "Daily summary" --cron "0 18 * * *" \
  --deliver --to "123456789" --channel "telegram"

# Deliver a reminder verbatim (bypass agent loop with --type reminder)
nanobot cron add --name "water" --type reminder --message "💧 Drink water!" --every 3600 \
  --deliver --to "123456789" --channel "telegram"

# List jobs
nanobot cron list
nanobot cron list --all  # include disabled jobs

# Enable/disable a job
nanobot cron enable <job_id>
nanobot cron enable <job_id> --disable

# Manually run a job
nanobot cron run <job_id>
nanobot cron run <job_id> --force  # run even if disabled

# Remove a job
nanobot cron remove <job_id>
```

</details>

## 🧩 Skills

Skills are modular packages that extend nanobot's capabilities with specialized knowledge, workflows, and bundled scripts. They let the agent handle domain-specific tasks it couldn't do with generic knowledge alone.

### Built-in Skills

| Skill | Description |
|-------|-------------|
| `github` | Interact with GitHub using the `gh` CLI |
| `weather` | Get weather info using wttr.in and Open-Meteo |
| `summarize` | Summarize URLs, files, and YouTube videos |
| `tmux` | Remote-control tmux sessions |
| `skill-creator` | Create and package new skills |
| `website-maintainer` | Manage and maintain a personal website/blog |

### Using Skills

Skills are loaded automatically. The agent sees a summary of all available skills in its system prompt and reads the full `SKILL.md` on demand when a task matches a skill's description.

Custom skills go in `~/.nanobot/workspace/skills/<skill-name>/SKILL.md`.

### Creating Skills

Ask the agent to create a skill — it has a built-in `skill-creator` skill with full instructions. Or scaffold one manually:

```bash
# Quick scaffold
nanobot skills init my-skill --description "Help with X"

# The agent can also use the full init script for more options:
# python nanobot/skills/skill-creator/scripts/init_skill.py my-skill --path ~/.nanobot/workspace/skills --resources scripts,references
```

### Installing Skills

Install a `.skill` file (a zip archive containing a skill directory):

```bash
nanobot skills install path/to/my-skill.skill

# Overwrite an existing skill
nanobot skills install path/to/my-skill.skill --force
```

### Skill Structure

```
my-skill/
├── SKILL.md          # Required: instructions + YAML frontmatter
├── scripts/          # Optional: executable code
├── references/       # Optional: docs loaded into context on demand
└── assets/           # Optional: templates, images, etc.
```

## 🔀 Subagents

nanobot can **spawn background subagents** to handle long-running tasks while the main agent stays responsive. This is the primary execution strategy — the main agent delegates work via `spawn`, responds immediately, and the subagent reports back when done.

- **`spawn(task, label?)`** — Delegate a task to a background subagent with full tool access
- **`subagent_control(action, label?)`** — List or cancel running subagents

Subagents run asynchronously with the same tools as the main agent (file ops, shell, web, etc.) and announce their results back to the conversation when complete.

## 💓 Heartbeat Service

nanobot includes a **proactive wake-up service** that periodically checks for tasks to execute automatically. When the gateway is running, the heartbeat service wakes up the agent every 30 minutes (configurable) to check for pending work.

**How it works:**

1. Create a `HEARTBEAT.md` file in your workspace (`~/.nanobot/workspace/`)
2. Add tasks as markdown checkboxes:
   ```markdown
   - [ ] Check if any important emails need responses
   - [ ] Review calendar for upcoming events
   - [ ] Monitor system resources
   ```
3. The agent processes unchecked tasks during each heartbeat
4. Returns `HEARTBEAT_OK` if no actionable tasks are found

This enables your nanobot to be truly proactive — monitoring, checking, and acting without waiting for your messages.

## 🐳 Docker

> [!TIP]
> The `-v ~/.nanobot:/root/.nanobot` flag mounts your local config directory into the container, so your config and workspace persist across container restarts. If you're using a profile, mount `~/.nanobot_<profile>` instead.

Build and run nanobot in a container:

```bash
# Build the image
docker build -t nanobot .

# Initialize config (first time only)
docker run -v ~/.nanobot:/root/.nanobot --rm nanobot onboard

# Edit config on host to add API keys
vim ~/.nanobot/config.json

# Run gateway (connects to Telegram/WhatsApp)
docker run -v ~/.nanobot:/root/.nanobot -p 18790:18790 -p 18791:18791 nanobot gateway --webui

# Or run a single command
docker run -v ~/.nanobot:/root/.nanobot --rm nanobot agent -m "Hello!"
docker run -v ~/.nanobot:/root/.nanobot --rm nanobot status
```

## 📁 Project Structure

```
nanobot/
├── agent/          # 🧠 Core agent logic
│   ├── loop.py     #    Agent loop (LLM ↔ tool execution)
│   ├── context.py  #    Prompt builder
│   ├── memory.py   #    Persistent memory
│   ├── memory_db.py#    Memory index + retrieval (SQLite/FTS)
│   ├── skills.py   #    Skills loader
│   ├── subagent.py #    Background task execution
│   └── tools/      #    Built-in tools (incl. spawn)
├── skills/         # 🎯 Bundled skills (github, weather, tmux...)
├── channels/       # 📱 Channel integrations (Telegram, WhatsApp, Feishu, WebUI)
├── webui/          # 🌐 Built-in browser chat UI (HTML/CSS/JS)
├── bus/            # 🚌 Message routing
├── cron/           # ⏰ Scheduled tasks
├── heartbeat/      # 💓 Proactive wake-up
├── providers/      # 🤖 LLM providers (OpenRouter, etc.)
├── session/        # 💬 Conversation sessions
├── config/         # ⚙️ Configuration
├── utils/          # 🔧 Shared helpers
└── cli/            # 🖥️ Commands
```

## 🤝 Contribute & Roadmap

PRs welcome! The codebase is intentionally small and readable. 🤗

**Roadmap** — Pick an item and [open a PR](https://github.com/HKUDS/nanobot/pulls)!

- [x] **Voice Transcription** — Support for Groq Whisper (Issue #13)
- [ ] **Multi-modal** — See and hear (images, voice, video)
- [ ] **Long-term memory** — Never forget important context
- [ ] **Better reasoning** — Multi-step planning and reflection
- [ ] **More integrations** — Discord, Slack, email, calendar
- [ ] **Self-improvement** — Learn from feedback and mistakes

### Contributors

<a href="https://github.com/HKUDS/nanobot/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=HKUDS/nanobot&max=100&columns=12" />
</a>

## ⭐ Star History

<div align="center">
  <a href="https://star-history.com/#HKUDS/nanobot&Date">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=HKUDS/nanobot&type=Date&theme=dark" />
      <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=HKUDS/nanobot&type=Date" />
      <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=HKUDS/nanobot&type=Date" style="border-radius: 15px; box-shadow: 0 0 30px rgba(0, 217, 255, 0.3);" />
    </picture>
  </a>
</div>

<p align="center">
  <em> Thanks for visiting ✨ nanobot!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.nanobot&style=for-the-badge&color=00d4ff" alt="Views">
</p>

<p align="center">
  <sub>nanobot is for educational, research, and technical exchange purposes only</sub>
</p>
