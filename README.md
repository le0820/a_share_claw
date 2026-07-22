# a_share_claw

一个按 NanoClaw 最小化思想重写的个人 A 股投研 Agent。目标不是复刻 OpenClaw/NanoClaw 的通用框架，而是围绕个人投资研究保留最短链路：

`Telegram -> a_share_claw host -> OpenAI Agents SDK -> 投研上下文路由 -> 本地领域工具（可选 MCP） -> SQLite 记忆与任务 -> Telegram`

参考项目：

- NanoClaw: https://github.com/nanocoai/nanoclaw
- OpenAI Agents SDK: https://openai.github.io/openai-agents-python/

## 功能映射

| 章节 | 当前实现 |
| --- | --- |
| 第 1 章 Telegram Bot | `src/a_share_claw/telegram.py` 使用 Telegram Bot API long polling 接收和回复消息 |
| 第 2 章 AI 能力 | `src/a_share_claw/agent.py` 使用 OpenAI Agents SDK 的 `Agent` / `Runner`；通过 OpenAI 兼容端点接入国产模型（`ASCLAW_MODEL_*`），`ASCLAW_OPENAI_MODEL` 保留为示例假值 |
| 第 3 章投研上下文与领域工具 | 显式加载治理文档和结构化状态，按宏观评分/产业研究路由上下文与本地 function tools；MCP 仅作为可选外部适配层 |
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
# ASCLAW_OPENAI_MODEL is the tutorial placeholder; the real model is the domestic one below.
OPENAI_API_KEY=sk-...
ASCLAW_OPENAI_MODEL=gpt-5.5
# Domestic model via OpenAI-compatible endpoint (no OpenAI network/cost needed).
ASCLAW_MODEL_PROVIDER=tencent
ASCLAW_MODEL_BASE_URL=https://tokenhub.tencentmaas.com/v1
ASCLAW_MODEL_API_KEY=sk-...
ASCLAW_MODEL_NAME=hy3
# 用户本地时间/定时任务，不用它判断 A 股交易日
ASCLAW_TIMEZONE=America/Los_Angeles
# A 股行情、pipeline 和 as_of_date 的日期边界
ASCLAW_MARKET_TIMEZONE=Asia/Shanghai
```

初始化数据库：

```bash
python -m a_share_claw init-db
```

先用本地假 AI 检查链路：

```bash
ASCLAW_FAKE_AI=1 python -m a_share_claw chat "测试一下"
```

真实模型走国产 OpenAI 兼容端点（`ASCLAW_MODEL_*`）：OpenAI Agents SDK 的 `Agent`/`Runner` 接口保持不变，只是把底层 client 指向 `ASCLAW_MODEL_BASE_URL`。不配置 `ASCLAW_MODEL_API_KEY` 时回退到 `ASCLAW_OPENAI_MODEL` 这个示例假值。可选供应商见 `.env.example` 中的 `tencent` / `volcengine` 预设。

启动 Telegram bot 和调度器：

```bash
python -m a_share_claw run
```

> **macOS SSL 问题**：如遇到 `SSL: CERTIFICATE_VERIFY_FAILED` 错误，需指定证书路径：
> ```bash
> SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())") python -m a_share_claw run
> ```

## 第 2 章 配置模型与启动 Telegram Bot

第 1 章已配好 Telegram Bot；第 2 章把 AI 能力接上国产模型并启动服务。OpenAI Agents SDK
的接口保持不变，仅把底层 client 指向 `ASCLAW_MODEL_*` 配置的国产 OpenAI 兼容端点。

1. 初始化数据库（仅首次）：

   ```bash
   python -m a_share_claw init-db
   ```

2. 先用假 AI 验证整条链路（不消耗模型额度）：

   ```bash
   ASCLAW_FAKE_AI=1 python -m a_share_claw chat "测试一下"
   ```

3. 启动 Telegram 轮询 Bot 与定时调度器：

   ```bash
   python -m a_share_claw run
   ```

   > **macOS SSL 问题**：如遇到 `SSL: CERTIFICATE_VERIFY_FAILED`，需指定证书路径：
   > ```bash
   > SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())") python -m a_share_claw run
   > ```

4. 想确认当前生效配置（不会打印密钥）：

   ```bash
   python -m a_share_claw show-config
   ```

说明：`.env` 中已默认配置 `tencent/hy3` 国产模型，因此无需 `OPENAI_API_KEY` 即可真实
调用。要换供应商（如 volcengine/doubao），改 `.env` 里的 `ASCLAW_MODEL_*` 即可；不填
`ASCLAW_MODEL_API_KEY` 时会回退到 `ASCLAW_OPENAI_MODEL` 这个示例假值。

模型客户端默认**不走系统代理**（`trust_env=False`），因为 `tokenhub` 等国产端点应直连；
若你的环境通过 SOCKS 代理访问外网且需要走代理，可设 `ASCLAW_MODEL_TRUST_ENV=1`
（此时需先安装 `socksio`，否则会报 `Using SOCKS proxy...` 错误）。

Telegram 的 `api.telegram.org` 在国内被墙，**必须走代理**。`.env` 中通过
`HTTPS_PROXY` / `HTTP_PROXY` 指向本地 http 代理（例如 `http://127.0.0.1:<proxy-port>`），
`http_client.py` 用的 `urllib` 会自动读取这两个变量；模型与国产端点仍走直连，互不影响。

## 内置投研工具

Agent 可调用这些工具：

- `get_a_share_quote`: 通过东方财富公开接口获取非正式实时快照；正式评分行情仍以 `easy-tdx` 为准。
- `get_macro_series`: 通过 FRED CSV 获取宏观时间序列，例如 `CPIAUCSL`、`DGS10`、`FEDFUNDS`。
- `search_industry_research`: 搜索行业基本面资料链接。
- `web_search` / `fetch_url`: 搜索并拉取网页文本。
- `remember` / `recall_memories`: 保存和读取长期记忆。
- `schedule_task` / `list_tasks` / `cancel_task`: 创建和管理定时任务。
- `read_text_file` / `write_text_file`: 在项目工作区内读写文本文件。
- `run_bash`: 仅一般维护路由可见；在工作区执行非破坏性命令，默认关闭，需要 `ASCLAW_ENABLE_BASH=1`。投研路由即使开启该环境变量也不暴露此工具。

## 第 3 章 投研上下文与领域工具编排

第三章不把本项目的规则、治理文档和流水线整体重构成 MCP server。它们属于不同层：

| 层 | 文件 | 运行方式 |
| --- | --- | --- |
| 治理与路由 | `AGENTS.md`、`IDENTITY.md`、`DATA_CONTRACT.md` | 由 host 按固定顺序注入 Agent instructions |
| 恢复索引 | `GUIDELINE.md` | 只在上下文恢复或排障时读取，不进入每次启动上下文 |
| 结构化规则 | `src/compiled/*.json` | 由本地领域工具按层读取，不把全部规则无差别塞入 prompt |
| 确定性执行 | `src/pipeline/` | 由受约束的本地 function tools 调用 |
| 运行输出 | `data/raw/`、`data/analysis/`、`data/scores/`、`data/reports/`、`data/state/` | 与源码分离，按 `as_of_date` 保存 |
| 外部系统 | 第三方 MCP server | 通过 `.mcp.json` 按需启用 |

### 启动上下文

Host 为每次 Agent run 构造确定性上下文：

1. `AGENTS.md`
2. `README.md`
3. `IDENTITY.md`
4. `DATA_CONTRACT.md`
5. `data/state/system_state.json`

随后根据请求路由增量加载：

- 混合请求：当同一条消息同时要求正式宏观评分/市场复盘与个股、财报或产业链取证时，路由为 `mixed`；按“宏观切片 + deepresearch 切片”分别执行，同时加载两套 Operations，但仍不开放 Bash/Codex。
- 宏观/行情/仓位：`src/pipeline/OPERATIONS.md`，并开放评分、规则、仓位状态和数据审计工具。ETF 加减仓也属于此路由。
- 量化分析：`src/pipeline/OPERATIONS.md`，并开放结构化状态、数据审计、确定性 pipeline 和结果文件工具。
- 个股调研：`data/deepresearch/OPERATIONS.md` 索引，并开放行情、一手资料搜索/拉取、仓位状态和研究输出工具。
- 行业/产业链研究：`data/deepresearch/OPERATIONS.md` 索引，并开放取证、技术基线、价值链与报告工具。
- 一般任务：只保留基础上下文和通用低风险工具。

不在启动时扫描 `.openclaw`、历史 memory、journal、analysis 或完整 SOP。长期记忆仍按当前用户加载，不能混入其他用户或会话内容。

### 路由优先级与工具边界

Host 在调用模型前完成确定性路由，不让 Telegram 上的小模型自己决定该开放哪类工具。优先级为：

1. 同时命中每日评分/数据审计与财报、业绩预告、个股或产业链取证 -> `mixed`。
2. L1/L2/L3、综合评分、数据审计 -> `macro`。
3. 回测、因子、相关性、波动率、夏普比率、RSI 等显式量化方法 -> `quant`。
4. 个股/公司对象或 A 股公司代码，且带研究或交易意图 -> `company`。
5. 产业链、供应链、价值链、上下游、竞争格局 -> `industry`。
6. ETF 加减仓、仓位、A 股盘面或宏观请求 -> `macro`。
7. 其余 -> `general`。

`mixed` / `macro` / `quant` / `company` / `industry` 均不开放 `run_bash` 或宽泛 Codex 工具；这不等于禁止联网取证。它们仍可使用对应 allowlist 中的 `web_search`、`fetch_url` 和专用 MCP。例如“分析 A 股早盘并评估 159682、159516 是否加仓”必须路由为 `macro`，不得落入 `general`；同时要求“每日评分 + 最新业绩预告”则必须路由为 `mixed`。

### 每日管线市场时段门禁

Host 以 `ASCLAW_MARKET_TIMEZONE` 判定同日正式管线是否可运行，不能让模型仅凭 `inspect_data_audit` 的缺失文件自行判断：

| 阶段 | 同日正式管线 | 审计缺失时的确定性动作 |
|:---|:---:|:---|
| 盘前（09:30 前） | 禁止 | 返回 `WAIT_FOR_CLOSE`；继续执行不依赖收盘数据的 deepresearch 切片 |
| 盘中（09:30–15:00） | 禁止 | 返回 `WAIT_FOR_CLOSE`；不得把“产物尚未生成”写成“管线不可执行” |
| 盘后（15:00 起） | 允许 | 缺失正式产物时执行 `run_macro_pipeline(stage="full")` |
| 历史日期 | 允许 | 按显式 `as_of_date` 执行，并继续禁止未来数据和静默 fallback |

`inspect_data_audit` 是诊断工具，不是执行结果。其返回必须包含市场阶段、`official_run_allowed` 和下一步动作。`run_macro_pipeline` 在盘前/盘中由工具层拒绝同日正式运行；如未来需要盘中评分，应单独实现带 `provisional` 标签的模式。

### 双时区日期契约

- `ASCLAW_TIMEZONE` 保留用户当前时区，用于 Telegram 和定时任务。
- `ASCLAW_MARKET_TIMEZONE` 默认 `Asia/Shanghai`，只用于 A 股交易日、`as_of_date` 和 pipeline 的未来日期校验。
- 美股/美国宏观数据仍保留来源自身的 `observation_date` / `release_date`，不因 A 股市场时区而改写。

### 本地领域工具

第三章新增以下受约束工具：

- `get_system_state`: 读取当前结构化评分、仓位和 `data_audit`。
- `get_compiled_rule`: 按 `L1` / `L2` / `L3` / `weights` / `sources` 读取编译规则。
- `get_market_session_status`: 返回指定 `as_of_date` 的盘前/盘中/盘后/历史日期正式运行门禁。
- `run_macro_pipeline`: 对显式 `as_of_date` 执行宏观流水线；默认禁止 stale/static fallback。
- `generate_daily_report`: 从同日结构化结果生成报告。
- `inspect_data_audit`: 检查指定日期的来源、发布日期和 fallback 状态。
- `run_ai_strategy`: 执行独立的 AI 成长状态、动量/情绪/流动性叠加层和仓位状态机；历史当前版本回放必须显式授权且不会写回正式状态。

正式宏观评分不依赖模型自由拼接 Bash 命令。`as_of_date`、禁止未来数据、禁止静默 fallback、正式报告禁止 static fallback 等规则由工具实现和 pipeline 双重校验。

AI 策略规则与数据能力分别位于 `src/compiled/ai_strategy_rules.json` 和 `src/compiled/ai_data_source_map.json`；运行产物按日期写入 `data/raw/ai/`、`data/factors/ai/`、`data/signals/ai/` 与 `data/backtests/ai/`。它是独立仓位叠加层，不替代当前禁用的 L2。

### 目录契约

```text
src/
  a_share_claw/
    research_context.py  # 启动上下文、工作流识别和工具路由
    research_tools.py    # 投研领域工具门面
  compiled/              # L1/L2/L3/权重/数据源的结构化规则
  pipeline/              # 确定性数据获取、评分和报告脚本
data/
  raw/                   # 按日期保存的原始数据
  analysis/              # P1 动量/风险等中间结果
  scores/                # L1/L2/L3/composite 结构化分数
  reports/               # 结构化日报
  state/system_state.json
  research/output/       # 产业链研究报告
```

### 第三章验收

- 启动文件顺序固定，归档目录不会进入 prompt。
- 宏观/仓位、量化、个股、产业链和一般任务加载不同 Operations 和工具集合。
- 使用真实中文请求同时测试路由结果和工具 allowlist；测试用 fake runner，不调用模型。
- 用户位于美国时区时，中国已进入下一交易日的 A 股请求不得被误判为“未来日期”。
- 所有正式输出包含 `as_of_date`、source/source timestamp 和 fallback 状态。
- 缺失精确日期数据时默认停止；未来数据永不作为 fallback。
- pipeline 与 Agent 工具使用同一组 `data/` 路径。
- 不同 `platform + user_id + chat_id + agent_key` 的上下文、记忆和任务保持隔离。
- MCP 未配置时不影响本地领域工具；配置后只暴露 allowlist 内的外部工具。

## Deep Research MCP 接入

`.mcp.json` 已注册两个 stdio server，可复制的无密钥版本位于 `.mcp.example.json`：

| Server | 版本 | 允许工具 | 启用路由 |
|:---|:---|:---|:---|
| Tavily | `tavily-mcp@0.2.21` | `tavily_search` / `tavily_extract` / `tavily_crawl` / `tavily_map` / `tavily_research` | `mixed`, `company`, `industry` |
| QVeris | `@qverisai/mcp@0.9.0` | MCP 直接暴露 `discover` / `inspect` / `usage_history` / `credits_ledger`；`call` 由 host gate 包装 | `mixed`, `company`, `industry` |

在 `.env` 填入：

```bash
TAVILY_API_KEY=
TAVILY_HUMAN_ID=
QVERIS_API_KEY=
QVERIS_REGION=
QVERIS_MAX_RETRIES=3
```

`.mcp.json` 只保存 `${TAVILY_API_KEY}` / `${QVERIS_API_KEY}` 引用，真实密钥只存在已忽略的 `.env`。空 Tavily key 启动 keyless 模式；空 QVeris key 可列出工具，调用时返回可操作错误。

Host 根据 `workflows` 字段过滤 MCP server，因此纯宏观评分、ETF 仓位和量化路由不会被注入这组 deep-search 元工具；只有含公司/行业取证切片的 `mixed` 请求会同时获得宏观领域工具与 Tavily/QVeris。`data/deepresearch/OPERATIONS.md` 定义 `PLAN -> TOOL_CALL -> EVIDENCE_GATE -> ACTION -> TEAM_SYNTHESIS` 协议，其中逻辑 `web_fetch` 对应 `tavily_extract`，未配置 MCP 时才显式回退到 `fetch_url`。

内置 `web_search` 返回结构化状态：`ok`、`backend`、`result_count`、`results` 和 `reason`。空数组只表示该后端本次 `no_results`，不能据此宣称“未配置搜索后端”；在 `mixed/company/industry` 中应继续使用已注入的 Tavily/QVeris 链路。

QVeris 的原始 MCP `call` 已从模型可见 allowlist 移除。模型必须先完成 Tavily 取证和 QVeris `discover → inspect`，然后调用 `assess_deepresearch_evidence` 返回 `SUFFICIENT` 或 `NEED_QVERIS_CALL`。只有后者会对已审查的 `search_id + tool_id` 开放一次 `qveris_readonly_call`；结果返回后必须重新评估，不能连续调用。

MCP 用于第三方联网取证，不承载本项目内部的启动契约、compiled rules 或 pipeline。首次启动需要 Node.js / `npx` 下载已锁定的 npm 包。

### Pipeline 数据边界

- 行情客户端固定为 `easy-tdx==1.20.4`，使用 `with MacClient.from_best_host()` / `with MacExClient.from_best_host()` 管理连接。可执行标的只从 `src/pipeline/pipeline_universe.json` 读取，行情历史保存到 `data/raw/market/`。
- 首次运行先执行 `uv sync --project src/pipeline`；Telegram host 会优先使用该命令生成的 `src/pipeline/.venv/bin/python`。
- 当日宏观可以联网获取；历史宏观只能复用同日归档，禁止用当前修订后的 FRED/AKShare 返回值回填过去。RSI 和 P1 全部过滤 `trade_date <= as_of_date`。
- FF5 与中国风格因子回归已退出正式管线；L2 明确为 `disabled`/`null`，不得用动量、搜索结果、指数代理或硬编码成份静默替代。
- Composite 保留原 L1:L3 的 0.40:0.30 比例并重归一为 `L1 × 4/7 + L3 × 3/7`；L2 权重为 0。
- P1 仅保留 easy-tdx 可独立计算的动量、VaR/CVaR、回撤和相关性。ETF 成份和财务证据仍可由 Agent host 经 Tavily/QVeris 取证后交给 `fetch_fundamental.py` 校验聚合，但只作为可选研究证据。
- 历史评分会生成同日 score/report，但不会覆盖日期更晚的 `data/state/system_state.json`。

## 设计取舍

- 单进程：不引入容器、前端 dashboard、通用多渠道注册系统。
- SQLite：统一保存用户、会话、消息、长期记忆和定时任务。
- Telegram only：当前只实现 Telegram 入口，后续要加 Slack/Discord 可按 `telegram.py` 新增 adapter。
- Agent 工具内聚：股市行情、宏观、搜索、记忆、文件、调度和投研流水线都通过本地 function tools 暴露给 Agent。
- 上下文最小化：只加载第三章规定的短入口文件和当前工作流所需 Operations，不默认扫描旧 `.openclaw` / `.codex` 资产。
- MCP 可选：只有跨进程、跨客户端或第三方工具需要协议边界时才启用。
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
  research_context.py  投研上下文加载与工作流路由
  research_tools.py    投研领域工具门面
src/compiled/       编译后的评分规则
src/pipeline/       宏观评分与报告流水线
data/               用户隔离状态和按日期生成的投研输出
```

## 注意

这只是研究助手，不是投资顾问。行情和宏观数据接口可能受上游变动影响；关键投资决策前需要交叉验证原始数据源、公告、财报和交易所披露。

## 工作区卫生

- `src/a_share_claw/` 是唯一实质运行实现；根目录 `a_share_claw/` 只保留 checkout 启动兼容 shim。
- Python 环境只保留根 `.venv`（Agent host）和 `src/pipeline/.venv`（行情/评分）；不维护版本后缀副本。
- `data/` 是可审计运行证据，默认不纳入源码提交；缓存、字节码与 `.DS_Store` 不进入工作区清单。
- 架构、数据契约和执行说明分别由 `README.md`、`DATA_CONTRACT.md` 与两份 `OPERATIONS.md` 维护，不再建立平行架构文档或独立重复测试框架。
