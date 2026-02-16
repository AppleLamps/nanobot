# Nanobot Logic Review (Requests, Subagents, Users, Tasks)

## Overview
Nanobot routes inbound channel messages through `MessageBus` → `AgentLoop` → `LLMProvider`/tools, then emits `OutboundMessage` back to the originating channel. Per-session ordering is enforced in the agent loop while allowing limited cross-session concurrency. Subagents run a parallel tool loop via `SubagentManager` and report back using a system message that the main agent summarizes for the user. Scheduled tasks (cron/heartbeat) use direct agent execution with optional delivery to channels.

## Request Handling Flow
- **Ingress**: `BaseChannel._handle_message()` validates allowlist + rate limit, constructs `InboundMessage`, and publishes to `MessageBus`.
- **Scheduling**: `AgentLoop.run()` consumes inbound messages and enforces per-session ordering with a tail-chain, while `asyncio.Semaphore` caps concurrent sessions.
- **Context**: `ContextBuilder.build_messages()` constructs a system prompt (identity + bootstrap + memory + skills) + trimmed history + current message (with optional media attachments).
- **Tool loop**: `AgentLoop._run_tool_loop()` calls the LLM, executes tool calls with `ToolRegistry`, emits status updates, applies consecutive tool-error backoff, and returns final content.
- **Persistence**: `SessionManager` stores user/assistant turns in JSONL with atomic writes; media paths are normalized to workspace-relative when possible.

## Subagent Lifecycle
- **Spawn**: `SpawnTool` invokes `SubagentManager.spawn()` with origin channel/chat ID.
- **Execution**: `SubagentManager._run_subagent()` builds a focused prompt (optionally enriched by `ContextBuilder`), runs a bounded tool loop (max 15 iterations), and enforces an overall timeout.
- **Progress**: periodic status updates (`metadata.type = status`) are emitted to the originating chat.
- **Result delivery**: subagent posts a `system` `InboundMessage` back to the main agent; `AgentLoop._process_system_message()` performs a lightweight summary.
- **Control**: `SubagentControlTool` lists, inspects, or cancels tasks.

## User Handling
- **Allowlist**: `BaseChannel.is_allowed()` enforces per-channel allowlists, otherwise defaults to open access.
- **Rate limiting**: per-sender throttling via `rate_limit_s` in `BaseChannel` (sender-id scoped).
- **Session keys**: `InboundMessage.session_key` defaults to `channel:chat_id` but can be overridden by channel metadata (e.g., WebUI).
- **WebUI**: supports session switching and per-session settings (model, verbosity, restrict_workspace), with auth token enforcement when not loopback.

## Task Handling
- **Subagent tasks**: delegated via `spawn`, with results summarized back to the user.
- **Cron jobs**: `CronService` runs scheduled jobs; tasks use `agent.process_direct()` and may deliver responses to channels; reminders bypass the agent and are delivered verbatim.
- **Heartbeat**: periodically checks `HEARTBEAT.md` and calls the agent if actionable content exists.

## Potential Flaws / Risks
1. **Unbounded inbound backlog**: `AgentLoop.run()` creates a task per inbound message; if a session’s tail is slow (tool-heavy), tasks can accumulate while waiting on `prev`, increasing memory usage under bursty traffic.
2. **System-message payload size**: subagent results are sent wholesale in the system message; there is no truncation before the summary LLM call, risking oversized payloads or model errors for large outputs.
3. **Subagent scale limits**: `SubagentManager` has no global concurrency cap; a user or tool could spawn many subagents and saturate resources.
4. **Session override trust boundary**: `AgentLoop._process_message()` trusts `metadata.session_key` for session routing. For WebUI this is intended, but on any exposed channel it could enable cross-session history access if metadata is user-controlled.
5. **`restrict_workspace` toggle**: WebUI can flip `restrict_workspace` per session; if WebUI is exposed and auth is weak, this could allow filesystem/exec access outside the workspace.
6. **Subagent fallback behavior**: subagents call the provider with `use_fallbacks=False`; transient provider failures that would otherwise be recovered by fallbacks will cause subagent failure.
7. **Cron scheduling gaps**: for `cron` schedules, missing `croniter` results in `next_run` becoming `None` and silent non-execution aside from a log warning.
8. **Rate limiting scope**: throttling is per `sender_id` only; group chats or shared sender identifiers can bypass per-chat rate control.
9. **Tool error backoff clarity**: after repeated tool errors, the user only sees a short hint. Full error context is not surfaced, which can slow debugging of environment or path issues.

## Recommendations
1. **Bound inbound backlog**: consider a per-session queue or drop policy, or avoid creating a task per inbound message until it can acquire the session tail.
2. **Truncate system-message payloads**: cap subagent result size before sending to the summarizer (e.g., max N chars with a note).
3. **Add subagent concurrency limit**: introduce a semaphore or max-running cap with clear feedback when the limit is reached.
4. **Harden session overrides**: only honor `metadata.session_key` for trusted channels (e.g., WebUI) or add a validation/allowlist.
5. **Restrict `restrict_workspace`**: make `restrict_workspace=false` opt-in via config or admin-only control, and audit logging for toggles.
6. **Enable fallback or retry**: allow subagents to use provider fallbacks or a simple retry strategy on transient errors.
7. **Surface cron dependency issues**: expose a user-visible warning when croniter is unavailable and a cron schedule is configured.
8. **Improve error reporting**: optionally include a fuller tool error in logs or attach a “details available” hint to the user.

## Checklist
- [x] Per-session ordering is enforced in `AgentLoop.run()`.
- [x] Cross-session concurrency is capped via a semaphore.
- [x] Subagent execution is isolated from direct user messaging.
- [ ] Subagent results are size-capped before summarization.
- [ ] Subagent concurrency is capped to prevent resource exhaustion.
- [ ] Session-key override is limited to trusted channels.
- [ ] `restrict_workspace` disablement is gated or audited.
- [ ] Cron warns users when `croniter` is missing and cron schedules won’t run.
- [ ] Clearer user-facing tool error diagnostics are available.
