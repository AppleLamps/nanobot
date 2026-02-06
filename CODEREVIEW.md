# Nanobot Code Review

**Date:** 2026-02-06
**Scope:** Full codebase analysis — Python core, TypeScript bridge, tests, Dockerfile, configuration
**Total Issues Found:** 94 (5 Critical, 14 High, 38 Medium, 37 Low)

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Critical Issues](#critical-issues)
3. [High Severity Issues](#high-severity-issues)
4. [Medium Severity Issues](#medium-severity-issues)
5. [Low Severity Issues](#low-severity-issues)
6. [Issue Index by File](#issue-index-by-file)

---

## Executive Summary

| Severity | Count | Key Themes |
|----------|------:|------------|
| **Critical** | 5 | Provider selection logic dead code, zip slip vulnerability, orphaned futures, unhandled promise rejections, zombie processes |
| **High** | 14 | SSRF, SQLite resource leaks, unbounded subagents, XSS injection, race conditions, no WebSocket auth, container runs as root |
| **Medium** | 38 | Memory leaks (caches, queues), concurrency races, swallowed exceptions, missing validation, non-atomic writes, security misconfigurations |
| **Low** | 37 | Dead code, code quality, minor logic issues, test quality, incomplete exports |

The most impactful finding is **C-01**: the explicit provider selection code in `config/schema.py` is trapped inside a docstring and never executes, causing the `agents.defaults.provider` config to be silently ignored.

---

## Critical Issues

### C-01 — Provider Selection Code Trapped in Docstring
- **File:** `nanobot/config/schema.py`, lines 173-185
- **Category:** Bugs / Logic Errors
- **Impact:** The `agents.defaults.provider` config field is silently ignored. Provider selection always falls through to the priority-based waterfall, so users with multiple API keys get the wrong provider.

The `_select_provider` method's docstring contains what is clearly intended to be executable code. The code that reads the `provider` field, looks it up via `getattr`, and returns the matching `ProviderConfig` is inside the triple-quoted docstring:

```python
def _select_provider(self) -> tuple[str | None, ProviderConfig | None]:
    """
    explicit = (self.agents.defaults.provider or "").strip().lower()
    if explicit:
        cfg = getattr(self.providers, explicit, None)
        if cfg is not None:
            return explicit, cfg
        return None, None
    Select the configured provider in priority order.
    ...
    """
    if self.providers.openrouter.api_key:
        return "openrouter", self.providers.openrouter
```

---

### C-02 — Zip Slip Path Traversal in Skill Installation
- **File:** `nanobot/cli/commands.py`, lines 883-905
- **Category:** Security
- **Impact:** A maliciously crafted `.skill` archive can overwrite arbitrary files on the system via entries like `../../.bashrc`.

`zf.extractall(skills_dir)` extracts a user-supplied zip archive without validating that member paths do not escape the target directory. Python's `zipfile.extractall()` does not guard against this on versions before 3.12.

```python
with zipfile.ZipFile(skill_path, "r") as zf:
    names = zf.namelist()
    top_dirs = {n.split("/")[0] for n in names if "/" in n}
    # ...
    zf.extractall(skills_dir)  # No path validation on members
```

---

### C-03 — Orphaned Future Causes Hung Waiters in Tool Cache Dedup
- **File:** `nanobot/agent/tools/registry.py`, lines 148-183
- **Category:** Concurrency / Resource Management
- **Impact:** If an exception occurs between creating the `Future` and reaching the `finally` cleanup block, the Future in `_in_flight` is never resolved, causing any concurrent caller awaiting it to hang forever.

```python
loop = asyncio.get_running_loop()
fut: asyncio.Future[str] = loop.create_future()
self._in_flight[cache_key] = fut
# ... if exception occurs here before finally block ...
# Lines 163-169: e.g., int(getattr(tool, "max_retries", 0) or 0) could raise ValueError
# The outer except (line 182-183) catches but does NOT clean up the future
except Exception as e:
    return f"Error executing {name}: {str(e)}"
```

---

### C-04 — Unhandled Promise Rejection in WhatsApp Reconnection
- **File:** `bridge/src/whatsapp.ts`, lines 93-96
- **Category:** Runtime Errors
- **Impact:** `this.connect()` inside `setTimeout` has no `.catch()`. If `connect()` throws, the unhandled promise rejection may crash the Node.js process.

```typescript
setTimeout(() => {
    this.reconnecting = false;
    this.connect();  // No .catch() — unhandled promise rejection
}, 5000);
```

---

### C-05 — Zombie Process After Timeout Kill
- **File:** `nanobot/agent/tools/shell.py`, lines 129-131
- **Category:** Resource Management
- **Impact:** After a timeout, `process.kill()` is called but `await process.wait()` is never called. Zombie processes accumulate in the process table and can exhaust PID space.

```python
except asyncio.TimeoutError:
    process.kill()
    # Missing: await process.wait()
    return f"Error: Command timed out after {self.timeout} seconds"
```

---

## High Severity Issues

### H-01 — SSRF via Web Fetch Tool (No Private IP Filtering)
- **File:** `nanobot/agent/tools/web.py`, lines 33-43, 128-134
- **Category:** Security
- **Impact:** `_validate_url` does not block requests to private/internal IP ranges (127.0.0.1, 10.x, 172.16-31.x, 192.168.x, 169.254.169.254). An LLM could be prompted to fetch internal service endpoints or cloud metadata.

```python
def _validate_url(url: str) -> tuple[bool, str]:
    p = urlparse(url)
    if p.scheme not in ('http', 'https'):
        return False, f"Only http/https allowed"
    if not p.netloc:
        return False, "Missing domain"
    return True, ""  # No check for private/internal IPs
```

---

### H-02 — SQLite Connections Never Explicitly Closed
- **File:** `nanobot/agent/memory_db.py`, lines 66-70
- **Category:** Resource Management / Memory Leak
- **Impact:** `_connect()` creates a new `sqlite3.Connection` each call. The `with self._connect() as con:` pattern only manages transactions (commit/rollback), NOT connection closure. Every call leaks a connection.

```python
def _connect(self) -> sqlite3.Connection:
    con = sqlite3.connect(self.db_path, timeout=3.0)
    con.execute("PRAGMA journal_mode=WAL;")
    return con  # "with" only handles transactions, not closure
```

---

### H-03 — No Limit on Concurrent Subagents
- **File:** `nanobot/agent/subagent.py`, lines 50, 80-83
- **Category:** Resource Management / Memory Leak
- **Impact:** No cap on concurrently running subagents. A misbehaving LLM could call `spawn` in a loop, exhausting memory and compute.

```python
self._running_tasks: dict[str, asyncio.Task[None]] = {}  # No size limit

async def spawn(self, task: str, ...) -> str:
    task_id = str(uuid.uuid4())[:8]
    bg_task = asyncio.create_task(self._run_subagent(...))
    self._running_tasks[task_id] = bg_task  # Unbounded growth
```

---

### H-04 — XSS via URL Injection in Telegram Markdown-to-HTML
- **File:** `nanobot/channels/telegram.py`, line 50
- **Category:** Security
- **Impact:** A URL containing double-quotes can break out of the `href` attribute. URLs are not validated for `javascript:` scheme. HTML escaping happens before link extraction, corrupting URLs with `&`.

```python
# HTML escaping (line 47) runs BEFORE link regex (line 50):
text = text.replace("&", "&amp;")  # corrupts URLs with &
text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
# URL portion is not escaped — " can break out of href attribute
```

---

### H-05 — Race Condition in WhatsApp `send()` vs WebSocket Reconnection
- **File:** `nanobot/channels/whatsapp.py`, lines 105-122, 60-75
- **Category:** Concurrency
- **Impact:** `send()` checks `self._ws` then uses it. Between check and use, the connection can drop and `self._ws` becomes `None`, causing `AttributeError`.

```python
async def send(self, msg: OutboundMessage) -> None:
    if not self._ws or not self._connected:  # check
        return
    # ... self._ws could become None here ...
    await self._ws.send(json.dumps(payload))  # use — potential AttributeError
```

---

### H-06 — Auth Token Passed in URL Query String (WebUI)
- **File:** `nanobot/channels/webui.py`, lines 184-185, 455
- **Category:** Security
- **Impact:** Query parameters are logged in browser history, Referer headers, proxy logs, and web server access logs, exposing the auth token.

---

### H-07 — Feishu Message Dedup Race Condition
- **File:** `nanobot/channels/feishu.py`, lines 239-246
- **Category:** Concurrency
- **Impact:** The check-then-set pattern for `_processed_message_ids` is not atomic across `await` boundaries. Two coroutines for the same `message_id` can both pass the check before either writes, causing duplicate processing.

```python
if message_id in self._processed_message_ids:
    return
self._processed_message_ids[message_id] = None
await self._add_reaction(message_id, "THUMBSUP")  # yields control — duplicate can slip through
```

---

### H-08 — `choices[0]` Access Without Empty-List Guard
- **File:** `nanobot/providers/litellm_provider.py`, line 177
- **Category:** Runtime Errors
- **Impact:** If the API returns an empty `choices` list (content-filter refusal, error conditions), this raises an unhandled `IndexError`.

```python
choice = response.choices[0]  # IndexError if choices is empty
```

---

### H-09 — Non-Atomic Cron Job Store Writes
- **File:** `nanobot/cron/service.py`, line 161
- **Category:** Resource Management
- **Impact:** Direct write to the target file. If interrupted mid-write (crash, SIGKILL, disk full), all scheduled jobs are lost.

```python
self.store_path.write_text(json.dumps(data, indent=2))  # Not atomic
```

---

### H-10 — `cron run` CLI Never Executes Job Logic
- **File:** `nanobot/cli/commands.py`, lines 1179-1197
- **Category:** Bugs / Logic Errors
- **Impact:** Creates `CronService` with `on_job=None`. The job callback is skipped, but the user sees "Job executed" — silently misleading.

```python
service = CronService(store_path)  # on_job=None
# ... _execute_job checks "if self.on_job:" which is False
console.print(f"[green]✓[/green] Job executed")  # No work was done
```

---

### H-11 — Auth Token Printed to Console
- **File:** `nanobot/cli/commands.py`, lines 653-656
- **Category:** Security
- **Impact:** WebUI auth token is embedded in URL, printed via `console.print`, and passed to `webbrowser.open`. Exposed in terminal scrollback and shell history.

```python
token_param = f"?token={config.channels.webui.auth_token}"
webui_url = f"http://{host}:{wport}/{token_param}"
console.print(f"[green]✓[/green] WebUI: {webui_url}")  # Token in plaintext
```

---

### H-12 — No Input Validation on Bridge WebSocket Commands
- **File:** `bridge/src/server.ts`, lines 46, 70-73
- **Category:** Security / Error Handling
- **Impact:** Incoming data is cast to `SendCommand` with no validation. Missing `to`/`text` fields pass `undefined` to WhatsApp API. Unknown command types still receive success acknowledgment.

```typescript
const cmd = JSON.parse(data.toString()) as SendCommand;
await this.handleCommand(cmd);
ws.send(JSON.stringify({ type: 'sent', to: cmd.to }));  // Always "sent" regardless
```

---

### H-13 — No WebSocket Authentication on Bridge
- **File:** `bridge/src/server.ts`, lines 40-64
- **Category:** Security
- **Impact:** Any client reaching the port can connect, send WhatsApp messages, and receive all incoming messages. No token, secret, or origin check.

---

### H-14 — Docker Container Runs as Root
- **File:** `Dockerfile`
- **Category:** Security
- **Impact:** No `USER` directive. The entire application, including the shell exec tool, runs as root inside the container.

---

## Medium Severity Issues

### M-01 — Unbounded `_last_seen` Dictionary in BaseChannel
- **File:** `nanobot/channels/base.py`, lines 34, 143
- **Category:** Memory Leak
- **Impact:** Every unique `sender_id` is stored permanently with no eviction, TTL, or size cap.

### M-02 — `start_all` Silently Swallows Channel Startup Exceptions
- **File:** `nanobot/channels/manager.py`, line 100
- **Category:** Error Handling
- **Impact:** `asyncio.gather(*tasks, return_exceptions=True)` captures exceptions as return values that are never inspected.

### M-03 — Channel Start Tasks Not Stored for Cleanup
- **File:** `nanobot/channels/manager.py`, lines 94-100
- **Category:** Resource Management
- **Impact:** Tasks are local variables. `stop_all()` cannot cancel them. Channels stuck in blocking operations leak tasks.

### M-04 — `_init_channels` Only Catches ImportError
- **File:** `nanobot/channels/manager.py`, lines 36-82
- **Category:** Error Handling
- **Impact:** Non-`ImportError` exceptions from a channel constructor crash the entire `ChannelManager` construction.

### M-05 — HTML Entities Double-Escaped in Telegram Link URLs
- **File:** `nanobot/channels/telegram.py`, lines 47, 50
- **Category:** Bugs / Logic Errors
- **Impact:** URLs containing `&` get escaped to `&amp;` before link regex runs, corrupting links.

### M-06 — WhatsApp `_connected` State Inconsistent with Actual Connection
- **File:** `nanobot/channels/whatsapp.py`, lines 61, 74, 164-167
- **Category:** Bugs / Logic Errors
- **Impact:** `_connected` can be set True by socket open and False by bridge status (or vice versa), causing `send()` to refuse/allow incorrectly.

### M-07 — Feishu WebSocket Thread Has No Cleanup
- **File:** `nanobot/channels/feishu.py`, lines 117-126
- **Category:** Resource Management
- **Impact:** No `thread.join()` in `stop()`. Thread may still be running when other resources are torn down.

### M-08 — Missing None-Checks on Feishu Event Fields
- **File:** `nanobot/channels/feishu.py`, lines 234-254
- **Category:** Runtime Errors
- **Impact:** Malformed events from Feishu API cause `AttributeError`. Generic `except Exception` silently drops the message.

### M-09 — Feishu App Credentials Potentially Logged
- **File:** `nanobot/channels/feishu.py`, lines 79-83, 109-113
- **Category:** Security
- **Impact:** Lark client set to `LogLevel.INFO` may log `app_id` and `app_secret` in SDK output.

### M-10 — CSP Allows `unsafe-inline` for Scripts (WebUI)
- **File:** `nanobot/channels/webui.py`, lines 263-270
- **Category:** Security
- **Impact:** `script-src 'self' 'unsafe-inline'` negates XSS protection that CSP should provide.

### M-11 — Incomplete Path Traversal Mitigation (WebUI)
- **File:** `nanobot/channels/webui.py`, line 254
- **Category:** Security
- **Impact:** `".." not in relpath` is a simple string check. Does not resolve the path and verify it is within expected directory.

### M-12 — File Handle Leaked on Upload Error (WebUI)
- **File:** `nanobot/channels/webui.py`, lines 732-747
- **Category:** Resource Management
- **Impact:** If exception occurs between `open(dest, "wb")` and storing in `self._uploads`, the file handle leaks.

### M-13 — Upload State Never Cleaned Up After Completion (WebUI)
- **File:** `nanobot/channels/webui.py`, lines 796-822
- **Category:** Bugs / Logic Errors
- **Impact:** Completed/failed upload entries persist in `self._uploads` until disconnect. Subsequent chunk for same ID writes to closed handle.

### M-14 — `connect-src` Allows WebSocket to Any Host (WebUI)
- **File:** `nanobot/channels/webui.py`, line 268
- **Category:** Security
- **Impact:** `connect-src 'self' ws: wss:` allows exfiltration via WebSocket to external servers if XSS is achieved.

### M-15 — Log Buffer Shared Across Instances (WebUI)
- **File:** `nanobot/channels/webui.py`, line 69
- **Category:** Memory Leak
- **Impact:** Class-level `_log_buffer` deque and log sink are never removed on `stop()`, continuing to capture logs indefinitely.

### M-16 — Non-LiteLLM Exceptions Not Wrapped in LLMError
- **File:** `nanobot/providers/litellm_provider.py`, lines 156-173
- **Category:** Error Handling
- **Impact:** `ConnectionError`, `OSError`, `httpx.TimeoutException` propagate as raw exceptions. Callers catching `LLMError` miss them.

### M-17 — Transcription Swallows All Exceptions
- **File:** `nanobot/providers/transcription.py`, lines 63-65
- **Category:** Error Handling
- **Impact:** Bare `except Exception` returns empty string. Caller cannot distinguish "no speech" from "auth failed" or "server unreachable."

### M-18 — Unbounded Session Cache
- **File:** `nanobot/session/manager.py`, line 87
- **Category:** Memory Leak
- **Impact:** `self._cache` grows without bound. No eviction, TTL, or size limit.

### M-19 — `get_history` Assumes `role`/`content` Keys Always Exist
- **File:** `nanobot/session/manager.py`, line 68
- **Category:** Runtime Errors
- **Impact:** Missing keys in stored messages cause unhandled `KeyError`.

### M-20 — Session `delete` Has TOCTOU Race and Skips File Lock
- **File:** `nanobot/session/manager.py`, lines 221-225
- **Category:** Concurrency
- **Impact:** Check-then-delete without lock. Concurrent access can cause corruption.

### M-21 — Async Cache Update Race on Concurrent Saves
- **File:** `nanobot/session/manager.py`, lines 172-179
- **Category:** Concurrency
- **Impact:** Two concurrent `save_async` calls can result in stale session overwriting newer one in cache.

### M-22 — Unbounded Message Queues
- **File:** `nanobot/bus/queue.py`, lines 17-18
- **Category:** Memory Leak
- **Impact:** Both queues have no `maxsize`. If processing stalls, messages accumulate without limit.

### M-23 — No Graceful Queue Shutdown Mechanism
- **File:** `nanobot/bus/queue.py`, lines 24-34
- **Category:** Resource Management
- **Impact:** Consumers block forever on `queue.get()`. No sentinel, cancellation token, or drain method.

### M-24 — Gateway Binds to `0.0.0.0` by Default
- **File:** `nanobot/config/schema.py`, line 110
- **Category:** Security
- **Impact:** Exposes gateway to all network interfaces. Should default to `127.0.0.1` like WebUI.

### M-25 — Secrets Stored as Plain Strings in Config
- **File:** `nanobot/config/schema.py`, lines 21, 31-33, 44, 93
- **Category:** Security
- **Impact:** `model_dump()` serializes all secrets to cleartext JSON. Pydantic `SecretStr` would prevent accidental exposure.

### M-26 — Config File Opened Without Explicit Encoding
- **File:** `nanobot/config/loader.py`, lines 36, 61
- **Category:** Bugs / Logic Errors
- **Impact:** Platform default encoding (not guaranteed UTF-8) can cause `UnicodeDecodeError` on non-ASCII config content.

### M-27 — Corrupt Config Silently Replaced with Defaults
- **File:** `nanobot/config/loader.py`, lines 39-43
- **Category:** Error Handling
- **Impact:** A typo in user's config silently reverts to all defaults. Bot may connect with wrong credentials.

### M-28 — `max_iterations` Not Guarded Against Zero/Negative
- **File:** `nanobot/agent/loop.py`, lines 64-66, 438
- **Category:** Bugs / Logic Errors
- **Impact:** Config value of 0 or negative means the tool loop never enters, and every message gets the "no response" fallback.

### M-29 — No File Size Limit on Media Attachments (Memory Exhaustion)
- **File:** `nanobot/agent/context.py`, lines 437-465
- **Category:** Resource Management
- **Impact:** Arbitrarily large images/PDFs are fully loaded + base64-encoded into memory without any size check.

### M-30 — Missing Exception Handling in Media Processing Loop
- **File:** `nanobot/agent/context.py`, lines 437-466
- **Category:** Error Handling
- **Impact:** A single failed `read_bytes()` prevents processing all subsequent media files.

### M-31 — No Wall-Clock Timeout for Subagents
- **File:** `nanobot/agent/subagent.py`, lines 136-180
- **Category:** Resource Management
- **Impact:** Iteration limit exists but no wall-clock timeout. A hung LLM call blocks the subagent task indefinitely.

### M-32 — Recursive Failure in Subagent Error Handler
- **File:** `nanobot/agent/subagent.py`, lines 188-191
- **Category:** Error Handling
- **Impact:** If `_announce_result` itself raises an exception in the error handler, the exception propagates unhandled.

### M-33 — Non-Atomic Memory File Read-Modify-Write
- **File:** `nanobot/agent/memory.py`, lines 64-76
- **Category:** Concurrency
- **Impact:** Concurrent `append_today` calls can overwrite each other's changes.

### M-34 — No Rate Limiting on Message Tool
- **File:** `nanobot/agent/tools/message.py`, lines 60-86
- **Category:** Security
- **Impact:** A runaway LLM loop can send unlimited messages to external chat platforms.

### M-35 — `truncate_string` Bug with Small `max_len`
- **File:** `nanobot/utils/helpers.py`, lines 62-66
- **Category:** Bugs / Logic Errors
- **Impact:** When `max_len < len(suffix)`, negative slicing produces a result longer than `max_len`. E.g., `truncate_string("hello world", 2)` returns `"hello worl..."` (13 chars).

```python
return s[: max_len - len(suffix)] + suffix  # max_len=2, suffix="..." → s[:-1] + "..."
```

### M-36 — `safe_filename` Does Not Prevent Directory Traversal Fully
- **File:** `nanobot/utils/helpers.py`, lines 69-75
- **Category:** Security
- **Impact:** Does not handle `..`, null bytes, or control characters. Empty-whitespace input returns empty string.

### M-37 — Corrupted Cron Store Silently Replaced with Empty Store
- **File:** `nanobot/cron/service.py`, lines 111-113
- **Category:** Error Handling
- **Impact:** Next `_save_store` call overwrites the (possibly recoverable) file, permanently destroying all jobs.

### M-38 — No Locking on Concurrent Cron Store Access
- **File:** `nanobot/cron/service.py`, lines 72-161
- **Category:** Concurrency
- **Impact:** Two CronService instances (gateway + CLI) can overwrite each other's changes.

---

## Low Severity Issues

### L-01 — `_last_seen` Not Thread-Safe
- **File:** `nanobot/channels/base.py`, lines 138-143
- **Category:** Concurrency
- **Impact:** Fragile if threading model changes; currently mitigated by GIL.

### L-02 — `is_allowed` Pipe-Delimited Parsing Undocumented
- **File:** `nanobot/channels/base.py`, lines 106-113
- **Category:** Code Quality
- **Impact:** Administrators may not understand the pipe convention in `allow_from` config.

### L-03 — Misleading Docstring in `start_all`
- **File:** `nanobot/channels/manager.py`, line 84
- **Category:** Code Quality
- **Impact:** Says "Start WhatsApp channel" but starts all channels.

### L-04 — Telegram `GroqTranscriptionProvider` Instantiated Per Message
- **File:** `nanobot/channels/telegram.py`, lines 277-278
- **Category:** Resource Management
- **Impact:** Wasteful if provider has heavy initialization. Should be cached on instance.

### L-05 — Duplicate `from pathlib import Path` Import
- **File:** `nanobot/channels/telegram.py`, lines 7, 263
- **Category:** Dead Code

### L-06 — Telegram `stop()` Does Not Guard Against Unstarted Updater
- **File:** `nanobot/channels/telegram.py`, lines 157-160
- **Category:** Runtime Errors
- **Impact:** If `start()` fails partway through, `stop()` calls methods on unstarted components.

### L-07 — Unused `from typing import Any` in WhatsApp Channel
- **File:** `nanobot/channels/whatsapp.py`, line 4
- **Category:** Dead Code

### L-08 — No Validation of WhatsApp Bridge Message Structure
- **File:** `nanobot/channels/whatsapp.py`, lines 124-157
- **Category:** Security
- **Impact:** `sender.split("@")` raises `AttributeError` if bridge sends non-string sender.

### L-09 — Feishu Dedup Cache Comment Contradicts Code
- **File:** `nanobot/channels/feishu.py`, lines 245-246
- **Category:** Code Quality
- **Impact:** Comment says "keep most recent 500" but code keeps 1000.

### L-10 — `new_session` Handler Missing `sender_id` in Response (WebUI)
- **File:** `nanobot/channels/webui.py`, line 564
- **Category:** Bugs / Logic Errors
- **Impact:** Client may rely on `sender_id` being present in session message type.

### L-11 — Upload Path Disclosure (WebUI)
- **File:** `nanobot/channels/webui.py`, lines 749-750
- **Category:** Security
- **Impact:** Internal directory structure exposed to client.

### L-12 — `_handle_ws_message` Is 330+ Lines Long (WebUI)
- **File:** `nanobot/channels/webui.py`, lines 535-871
- **Category:** Code Quality
- **Impact:** Handles 12+ message types in one method. Difficult to read, test, and maintain.

### L-13 — API Key Stored as Plain Instance Attribute
- **File:** `nanobot/providers/base.py`, line 49
- **Category:** Security
- **Impact:** Credential exposed if object is serialized, logged, or inspected.

### L-14 — `status_code` Falsy-Value Confusion with `or`
- **File:** `nanobot/providers/litellm_provider.py`, line 101
- **Category:** Bugs / Logic Errors
- **Impact:** Status code `0` (falsy but not None) would fall through to alternate attribute.

### L-15 — OpenRouter Detection Heuristic Is Fragile
- **File:** `nanobot/providers/litellm_provider.py`, lines 58-62
- **Category:** Bugs / Logic Errors
- **Impact:** Relies on `sk-or-` prefix. Breaks if OpenRouter changes key format.

### L-16 — Transcription Env Var Fallback Inconsistent with Other Providers
- **File:** `nanobot/providers/transcription.py`, line 19
- **Category:** Security
- **Impact:** Other providers deliberately avoid env var reads; transcription does not.

### L-17 — Synchronous File Open in Async Transcription Method
- **File:** `nanobot/providers/transcription.py`, line 43
- **Category:** Resource Management

### L-18 — Session `_load` Fails Entire Session on Single Corrupt Line
- **File:** `nanobot/session/manager.py`, line 145
- **Category:** Error Handling
- **Impact:** One corrupt JSONL line loses all valid messages in the session.

### L-19 — `datetime.now()` Uses Local Time (Multiple Files)
- **Files:** `nanobot/session/manager.py`, `nanobot/bus/events.py`
- **Category:** Bugs / Logic Errors
- **Impact:** Timestamps may be inconsistent across timezone boundaries.

### L-20 — Deprecated Pydantic v1-Style Inner `Config` Class
- **File:** `nanobot/config/schema.py`, lines 227-229
- **Category:** Code Quality

### L-21 — Config Save Is Not Atomic
- **File:** `nanobot/config/loader.py`, lines 61-62
- **Category:** Resource Management

### L-22 — `snake_to_camel` Loses Leading Underscores
- **File:** `nanobot/config/loader.py`, lines 131-134
- **Category:** Bugs / Logic Errors

### L-23 — Error Reporting Uses `print()` Instead of Logger
- **File:** `nanobot/config/loader.py`, lines 40-41
- **Category:** Code Quality

### L-24 — Silent Exception Swallowing in `_emit_status`
- **File:** `nanobot/agent/loop.py`, lines 293-306
- **Category:** Error Handling

### L-25 — Potential TypeError in `_record_usage`
- **File:** `nanobot/agent/loop.py`, lines 264-268
- **Category:** Runtime Errors

### L-26 — Binary Files Cause Cryptic Error in ReadFileTool
- **File:** `nanobot/agent/tools/filesystem.py`, line 70
- **Category:** Error Handling

### L-27 — Relative Paths Resolve Against CWD, Not Workspace
- **File:** `nanobot/agent/tools/filesystem.py`, lines 14-22
- **Category:** Bugs / Logic Errors

### L-28 — Multiple Unused Methods in MemoryStore
- **File:** `nanobot/agent/memory.py`, lines 57-141
- **Category:** Dead Code

### L-29 — Skill Caches Grow Without Eviction
- **File:** `nanobot/agent/skills.py`, lines 25-26
- **Category:** Memory Leak

### L-30 — Non-Deterministic Skill Ordering
- **File:** `nanobot/agent/skills.py`, lines 53, 61
- **Category:** Code Quality

### L-31 — UUID Collision Risk with 8-Character Truncation (Multiple Files)
- **Files:** `nanobot/agent/subagent.py` line 71, `nanobot/cron/service.py` line 287
- **Category:** Bugs / Logic Errors
- **Impact:** 32 bits of entropy. ~50% collision probability after ~65,000 IDs.

### L-32 — `parse_session_key` Accepts Empty Channel/Chat ID
- **File:** `nanobot/utils/helpers.py`, lines 78-91
- **Category:** Bugs / Logic Errors

### L-33 — Heartbeat `response.upper()` on Potential None
- **File:** `nanobot/heartbeat/service.py`, line 118
- **Category:** Runtime Errors
- **Impact:** If `on_heartbeat` returns `None`, `response.upper()` raises `AttributeError`.

### L-34 — Skill Packaging Validates Name AND Description Together
- **File:** `nanobot/skills/skill-creator/scripts/package_skill.py`, line 47
- **Category:** Bugs / Logic Errors
- **Impact:** `if "name" not in meta and "description" not in meta` should use `or` — both fields are individually required.

### L-35 — `rglob("*")` Follows Symlinks in Skill Packaging
- **File:** `nanobot/skills/skill-creator/scripts/package_skill.py`, lines 86-92
- **Category:** Security
- **Impact:** Symlinks pointing outside skill directory are included in the package.

### L-36 — Timezone-Naive `--at` Parsing in Cron CLI
- **File:** `nanobot/cli/commands.py`, lines 1119-1122
- **Category:** Bugs / Logic Errors

### L-37 — Various Test Quality Issues
- **Files:** Multiple test files
- **Category:** Test Quality
- **Details:**
  - `test_multi_chat_concurrency.py`: Pointless `@pytest.mark.parametrize` with single value; dead `else` branch
  - `test_tool_parallel_cache.py`: `asyncio.sleep(0)` synchronization may be flaky
  - `test_cron_service_execution.py`: Only tests forced execution; scheduled path untested
  - `test_multimodal_attachments.py`: Uses fake file content that breaks if validation is added
  - No test coverage for TypeScript bridge code
  - No error-path tests for several components (memory_db, config_loader, cron_service)

---

## Issue Index by File

| File | Issues |
|------|--------|
| `nanobot/config/schema.py` | C-01, M-24, M-25, L-20 |
| `nanobot/cli/commands.py` | C-02, H-10, H-11, L-36 |
| `nanobot/agent/tools/registry.py` | C-03 |
| `bridge/src/whatsapp.ts` | C-04 |
| `nanobot/agent/tools/shell.py` | C-05 |
| `nanobot/agent/tools/web.py` | H-01 |
| `nanobot/agent/memory_db.py` | H-02 |
| `nanobot/agent/subagent.py` | H-03, M-31, M-32, L-31 |
| `nanobot/channels/telegram.py` | H-04, M-05, L-04, L-05, L-06 |
| `nanobot/channels/whatsapp.py` | H-05, M-06, L-07, L-08 |
| `nanobot/channels/webui.py` | H-06, M-10, M-11, M-12, M-13, M-14, M-15, L-10, L-11, L-12 |
| `nanobot/channels/feishu.py` | H-07, M-07, M-08, M-09, L-09 |
| `nanobot/providers/litellm_provider.py` | H-08, M-16, L-14, L-15 |
| `bridge/src/server.ts` | H-12, H-13 |
| `Dockerfile` | H-14 |
| `nanobot/channels/base.py` | M-01, L-01, L-02 |
| `nanobot/channels/manager.py` | M-02, M-03, M-04, L-03 |
| `nanobot/providers/transcription.py` | M-17, L-16, L-17 |
| `nanobot/session/manager.py` | M-18, M-19, M-20, M-21, L-18, L-19 |
| `nanobot/bus/queue.py` | M-22, M-23 |
| `nanobot/config/loader.py` | M-26, M-27, L-21, L-22, L-23 |
| `nanobot/agent/loop.py` | M-28, L-24, L-25 |
| `nanobot/agent/context.py` | M-29, M-30 |
| `nanobot/agent/memory.py` | M-33, L-28 |
| `nanobot/agent/tools/message.py` | M-34 |
| `nanobot/utils/helpers.py` | M-35, M-36, L-32 |
| `nanobot/cron/service.py` | H-09, M-37, M-38, L-31 |
| `nanobot/agent/tools/filesystem.py` | L-26, L-27 |
| `nanobot/agent/skills.py` | L-29, L-30 |
| `nanobot/heartbeat/service.py` | L-33 |
| `nanobot/skills/skill-creator/scripts/package_skill.py` | L-34, L-35 |
| `nanobot/bus/events.py` | L-19 |
| `nanobot/providers/base.py` | L-13 |
| `tests/*` | L-37 |
