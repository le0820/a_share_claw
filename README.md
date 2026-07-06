# a_share_claw

一个按 NanoClaw 最小化思想重写的个人 A 股投研 Agent。目标不是复刻 OpenClaw/NanoClaw 的通用框架，而是围绕个人投资研究保留最短链路：

`Telegram -> a_share_claw host -> OpenAI Agents SDK -> 本地工具/MCP -> SQLite 记忆与任务 -> Telegram`

参考项目：

- NanoClaw: https://github.com/nanocoai/nanoclaw
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/

## 功能映射

| 章节 | 当前实现 |
| --- | --- |
| 第 1 章 Telegram Bot | `src/a_share_claw/telegram.py` 使用 Telegram Bot API long polling 接收和回复消息 |
| 第 2 章 AI 能力 | `src/a_share_claw/agent.py` 使用 OpenAI Agents SDK 的 `Agent` / `Runner` |
| 第 3 章 MCP 工具 | `src/a_share_claw/mcp.py` 读取 `.mcp.json` 并挂载 stdio MCP server |
| 第 4 章 Bash/搜索/资料整理 | 内置 `run_bash`、`web_search`、`fetch_url`、文件读写、行情和宏观工具 |
| 第 5 章短期记忆 | OpenAI Agents SDK `SQLiteSession` 保存每个会话上下文 |
| 第 6 章长期记忆 | `data/users/<user>/memory.md` + SQLite `memories` 表 |
| 第 7 章定时调度 | SQLite `tasks` 表 + `Scheduler` 轮询，到期执行并通知 Telegram |
| 第 8 章上下文隔离 | 按 `platform + user_id + chat_id + agent_key` 隔离 conversation/session/memory |

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

编辑 `.env`：

```bash
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_ALLOWED_USER_IDS=123456789
OPENAI_API_KEY=sk-...
ASCLAW_OPENAI_MODEL=gpt-5.5
```

初始化数据库：

```bash
python -m a_share_claw init-db
```

先用本地假 AI 检查链路：

```bash
ASCLAW_FAKE_AI=1 python -m a_share_claw chat "测试一下"
```

启动 Telegram bot 和调度器：

```bash
python -m a_share_claw run
```

> **macOS SSL 问题**：如遇到 `SSL: CERTIFICATE_VERIFY_FAILED` 错误，需指定证书路径：
> ```bash
> SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())") python -m a_share_claw run
> ```

## 内置投研工具

Agent 可调用这些工具：

- `get_a_share_quote`: 通过东方财富公开接口获取 A 股实时行情快照。
- `get_macro_series`: 通过 FRED CSV 获取宏观时间序列，例如 `CPIAUCSL`、`DGS10`、`FEDFUNDS`。
- `search_industry_research`: 搜索行业基本面资料链接。
- `web_search` / `fetch_url`: 搜索并拉取网页文本。
- `remember` / `recall_memories`: 保存和读取长期记忆。
- `schedule_task` / `list_tasks` / `cancel_task`: 创建和管理定时任务。
- `read_text_file` / `write_text_file`: 在项目工作区内读写文本文件。
- `run_bash`: 在工作区执行非破坏性命令，默认关闭，需要 `ASCLAW_ENABLE_BASH=1`。

## MCP 接入

复制示例：

```bash
cp .mcp.example.json .mcp.json
```

`.mcp.json` 格式：

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
      "allowedTools": ["read_file", "write_file", "list_directory"]
    }
  }
}
```

启动后，Agent 会把这些 MCP server 作为 OpenAI Agents SDK 的 `mcp_servers` 传入。需要 `npx`、网络或其他外部进程的 MCP，要由运行环境自行准备。

## 设计取舍

- 单进程：不引入容器、前端 dashboard、通用多渠道注册系统。
- SQLite：统一保存用户、会话、消息、长期记忆和定时任务。
- Telegram only：当前只实现 Telegram 入口，后续要加 Slack/Discord 可按 `telegram.py` 新增 adapter。
- Agent 工具内聚：股市行情、宏观、搜索、记忆、文件、调度都作为工具暴露给 Agent。
- 迁移友好：默认会扫描 `.openclaw` / `.codex` 的 skill、plugin 文本作为提示词素材，但不依赖 OpenClaw 运行时代码。
- 保守安全：Bash 默认关闭，文件读写限制在 `ASCLAW_WORKSPACE_DIR` 内。

## 目录

```text
src/a_share_claw/
  __main__.py       CLI 入口
  telegram.py       Telegram long polling adapter
  agent.py          OpenAI Agents SDK runner
  tools_runtime.py  Agent 本地工具实现
  mcp.py            MCP server 装载
  db.py             SQLite schema 和操作
  memory.py         长期记忆文件
  scheduler.py      定时任务轮询
  market_data.py    A 股与宏观数据工具
  web_search.py     搜索与网页提取
  prompts.py        投研 Agent 身份提示词
```

## 注意

这只是研究助手，不是投资顾问。行情和宏观数据接口可能受上游变动影响；关键投资决策前需要交叉验证原始数据源、公告、财报和交易所披露。
