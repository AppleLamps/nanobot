# Available Tools

This document describes the tools available to nanobot.

## File Operations

### read_file

Read the contents of a file.

```python
read_file(path: str) -> str
```

### write_file

Write content to a file (creates parent directories if needed).

```python
write_file(path: str, content: str) -> str
```

### edit_file

Edit a file by replacing specific text.

```python
edit_file(path: str, old_text: str, new_text: str) -> str
```

### list_dir

List contents of a directory.

```python
list_dir(path: str) -> str
```

## Shell Execution

### exec

Execute a shell command and return output.

```python
exec(command: str, working_dir: str = None) -> str
```

**Safety Notes:**

- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- Optional `restrictToWorkspace` config to limit paths

## Web Access

### web_search

Search the web using Brave Search API.

```python
web_search(query: str, count: int = 5) -> str
```

Returns search results with titles, URLs, and snippets. Requires `tools.web.search.apiKey` in config.

### web_fetch

Fetch and extract main content from a URL.

```python
web_fetch(url: str, extractMode: str = "markdown", maxChars: int = 50000) -> str
```

**Notes:**

- Content is extracted using readability
- Supports markdown or plain text extraction
- Output is truncated at 50,000 characters by default

## Communication

### message

Send a message to the user (used internally).

```python
message(content: str, channel: str = None, chat_id: str = None) -> str
```

## Background Tasks (Subagents)

### spawn

Delegate a task to a background subagent. **This is your primary execution strategy.**

```python
spawn(task: str, label: str = None) -> str
```

The subagent runs asynchronously with full tool access (files, exec, web) and reports back when done. While it works, you remain free to chat with the user.

**Use `spawn` for any task requiring 2+ tool calls.** This includes: web searches, file modifications, running commands, research, code generation, multi-step analysis.

Only skip spawning for: direct knowledge answers, single quick file reads, conversational replies.

Write clear, detailed task descriptions so the subagent knows exactly what to do.

### subagent_control

List or cancel running subagents.

```python
subagent_control(action: "list" | "cancel", task_id: str = None) -> str
```

## Scheduled Reminders (Cron)

Use the `exec` tool to create scheduled reminders with `nanobot cron add`:

### Set a recurring reminder

```bash
# Every day at 9am
nanobot cron add --name "morning" --message "Good morning! ☀️" --cron "0 9 * * *"

# Every 2 hours
nanobot cron add --name "water" --message "Drink water! 💧" --every 7200
```

### Set a one-time reminder

```bash
# At a specific time (ISO format)
nanobot cron add --name "meeting" --message "Meeting starts now!" --at "2025-01-31T15:00:00"
```

### Manage reminders

```bash
nanobot cron list              # List all jobs
nanobot cron remove <job_id>   # Remove a job
```

## Heartbeat Task Management

The `HEARTBEAT.md` file in the workspace is checked every 30 minutes.
Use file operations to manage periodic tasks:

### Add a heartbeat task

```python
# Append a new task
edit_file(
    path="HEARTBEAT.md",
    old_text="## Example Tasks",
    new_text="- [ ] New periodic task here\n\n## Example Tasks"
)
```

### Remove a heartbeat task

```python
# Remove a specific task
edit_file(
    path="HEARTBEAT.md",
    old_text="- [ ] Task to remove\n",
    new_text=""
)
```

### Rewrite all tasks

```python
# Replace the entire file
write_file(
    path="HEARTBEAT.md",
    content="# Heartbeat Tasks\n\n- [ ] Task 1\n- [ ] Task 2\n"
)
```

---

## Adding Custom Tools

To add custom tools:

1. Create a class that extends `Tool` in `nanobot/agent/tools/`
2. Implement `name`, `description`, `parameters`, and `execute`
3. Register it in `AgentLoop._register_default_tools()`

## Skills Management (CLI)

Use `exec` to manage skills:

- `nanobot skills init <name> -d "description"`: scaffold a new skill in workspace
- `nanobot skills list`: list all available skills
- `nanobot skills install <file.skill>`: install a .skill package
- `nanobot skills install <file.skill> --force`: overwrite an existing skill

### Skill Creation Scripts (from skill-creator skill)

- `python <skill-creator-dir>/scripts/init_skill.py <name> --path <dir>`: full scaffold with optional resources
- `python <skill-creator-dir>/scripts/package_skill.py <skill-dir>`: validate and package into .skill file
