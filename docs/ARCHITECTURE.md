# a_share_claw Architecture

This project keeps NanoClaw's small-host idea but removes the generic container
runtime, channel registry, service manager, and broad configuration surface.
The first target is one person's A-share research workflow over Telegram.

## Runtime Chain

```text
Telegram user
  -> Telegram long polling adapter
  -> conversation resolver
  -> SQLite messages table
  -> OpenAI Agents SDK runner
  -> built-in Python tools and optional MCP tools
  -> SQLite messages, memories, tasks
  -> Telegram reply
```

## Scheduled Task Chain

```text
user asks the agent to schedule work
  -> schedule_task tool writes SQLite task
  -> Scheduler polls due tasks
  -> InvestmentAgent executes the task prompt
  -> result is sent to Telegram
  -> one-shot task completes, recurring task gets a new run_at
```

## Main Modules

- `src/a_share_claw/__main__.py`: CLI entry point.
- `src/a_share_claw/telegram.py`: Telegram Bot API long polling and message delivery.
- `src/a_share_claw/agent.py`: OpenAI Agents SDK integration.
- `src/a_share_claw/tools_runtime.py`: local tools exposed to the agent.
- `src/a_share_claw/mcp.py`: optional stdio MCP server loading from `.mcp.json`.
- `src/a_share_claw/db.py`: SQLite schema and persistence operations.
- `src/a_share_claw/memory.py`: long-term memory file writing and lookup.
- `src/a_share_claw/scheduler.py`: due-task polling and Telegram notification.
- `src/a_share_claw/market_data.py`: A-share quote and FRED macro helpers.
- `src/a_share_claw/web_search.py`: lightweight search and URL text extraction.
- `src/a_share_claw/prompts.py`: investment research system prompt.

## Context Isolation

Conversations are keyed by:

```text
platform + platform_user_id + chat_id + agent_key
```

Each conversation gets a separate OpenAI Agents SDK `SQLiteSession`, so short
term context does not cross chats or users. Long-term memories are keyed by the
internal `user_id` and mirrored to:

```text
data/users/<user_id>/memory.md
```

## Storage

One SQLite database stores:

- `users`
- `conversations`
- `messages`
- `memories`
- `tasks`
- `settings`

If an older incompatible schema is found, `Storage.init()` archives old tables
as `legacy_<table>_<timestamp>` before creating the current schema.

## Safety Defaults

- Bash is disabled unless `ASCLAW_ENABLE_BASH=1`.
- File writes stay inside `ASCLAW_WORKSPACE_DIR`.
- MCP servers are opt-in through `.mcp.json`.
- Runtime data under `data/` is ignored by git.
