# a_share_claw

一个围绕个人 A 股投研构建的 Agent 系统。它不是 OpenClaw/NanoClaw 的通用复刻；当前优先交付两条可审计的投研工作流：

1. 宏观日度评分与仓位纪律。
2. 行业链/公司研究与证据化辩论。

目标链路为：

`Telegram -> a_share_claw host -> OpenAI Agents SDK -> 投研路由 -> 受约束的领域工具/MCP -> 可审计数据产物`

> **项目状态（2026-07-31）**：第 1、2 章已有可运行基础；当前仍在收口第 3 章。第 4–8 章及 loop-engineer 不是“已完成能力”，即使仓库中已有少量辅助模块或单元测试。

## 当前边界

### 已有基础

- Telegram long polling 入口与白名单配置。
- OpenAI Agents SDK runner，以及国产 OpenAI 兼容端点配置。
- 宏观、量化、公司、行业和混合请求的确定性路由与工具 allowlist。
- 宏观日度评分管线、产业研究取证 gate、可选 Tavily/QVeris MCP 适配。
- AI 行业仓位叠加层的增长状态 + 战术信号原型（独立于 L2）。

### 不能因此宣称完成

- 当前 `data/state/system_state.json` 的传统宏观正式状态仍停在 `20260713`；没有连续运行至当前市场日的正式宏观日更，不能把代码能力当作已稳定运行的服务。
- AI 叠加层的 `20260730` / `20260731` 信号为 `unverified` research replay，不更新正式状态，不能作为正式交易指令。
- L2 因子层按数据契约明确禁用；它不是待用动量或搜索结果替代的空位。
- 盘中/盘前正式收盘评分尚未实现；同日正式请求必须等待 A 股收盘门禁。

## 真实路线图与验收

完成状态只以“端到端可运行、符合数据契约、通过对应验收”判断；存在文件、类或单元测试不等于章节完成。

| 阶段 | 状态 | 目标 | 完成定义 |
| --- | --- | --- | --- |
| 1. Telegram 入口 | 基础已具备 | 接收授权用户消息并回复 | `init-db`、本地 fake AI、真实 Telegram 一次收发均验证；异常不会使轮询退出 |
| 2. 模型接入 | 基础已具备 | 通过受控的 OpenAI 兼容端点运行 Agent | 国内模型直连、Telegram 代理、超时/错误信息和生效配置均在真实环境验证 |
| 3. 投研执行平面 | **进行中** | 将策略、数据契约、研究路由和受约束工具闭环 | 宏观与产业研究均按日期、来源、fallback 输出；正式宏观日更恢复；AI P0/P1 只有在正式数据可用时写入状态 |
| 4. 工具与证据工作台 | 未完成 | 将搜索、网页、文件和必要执行能力变为可审计、最小权限的工作流工具 | 明确每个工具的授权、输入/输出契约、审计记录、失败边界和端到端研究验收；不把宽泛 Bash 当作正式投研执行路径 |
| 5. 短期上下文管理 | 未完成 | 管理每个会话的上下文生命周期和 token 预算 | 有保留窗口、摘要/压缩、恢复策略、上下文预算和回归测试；不会因历史无限增长而失控 |
| 6. 长期记忆与隔离 | 未完成 | 只在允许的主体边界内检索、写入和注入记忆 | 记忆作用域、保留/删除、检索排序和 prompt 注入规则明确；跨 `platform + user + chat + agent_key` 的隔离有端到端测试 |
| 7. 任务与 Cron 调度 | 未完成 | 可靠地创建、执行、重试、观测和取消一次性/周期性投研任务 | 支持明确时区与 cron/固定周期语义，具备幂等、失败重试、并发/错过执行策略、状态查询与通知验收 |
| 8. 运行隔离与可观测性 | 未完成 | 将会话、任务、工具调用、数据产物和权限作为独立运行边界 | 身份授权、会话/任务/记忆/产物隔离一致；日志、指标、审计和故障恢复可验证 |
| 9. loop-engineer / 自提升 | 未开始 | 用受控评估驱动策略和流程改善，而不是让 Agent 自行改写生产规则 | 固定评测集、变更提案、回放、人工批准、版本化回滚和上线后监控齐备；无自动越权交易或修改数据契约 |

### 第 3 章当前工作

第 3 章包含此前所有策略相关开发：L1/L3 评分、L2 禁用与权重重归一、数据日期/来源契约、宏观/研究路由、MCP evidence gate，以及 AI 行业仓位叠加层。

当前增量在完善 AI 战术加仓门禁：除了流动性分位数，还要求 2Y、10Y、30Y 与 10Y TIPS 在同一五日窗口内均未上行，并且 Brent 不触发通胀冲击。其目的是避免“分位数看似宽松、绝对利率或油价实际恶化”时错误触发 `ADD`。

第 3 章收口前的优先验收：

1. 恢复连续、可审计的正式宏观日更；每份产物包含 `as_of_date`、数据源、发布日期/观测日期和 fallback 状态。
2. 验证 AI 当前日期的正式输入链路；历史 current-vintage 回放始终保持 `unverified`，不得写入正式状态。
3. 对宏观、量化、公司、行业与 mixed 路由完成真实请求的工具边界回归；不只验证 fake runner。
4. 将当前流动性门禁变更提交为可回滚的版本，并保存相应回放证据。

## 投研规则和数据边界

`DATA_CONTRACT.md` 是所有正式产物的最高数据规则。每份正式评分或研究必须声明：

- `as_of_date`、`generated_at`、`data_period`。
- 精确 source / file path / URL 与可得的 `source_timestamp` 或发布日期。
- `fallback_status`：`none`、`stale_fallback`、`static_fallback` 或 `unverified`。

硬性约束：

- 历史问题不得使用请求日期之后的数据；没有精确数据默认停止，不能静默回退。
- A 股交易日用 `ASCLAW_MARKET_TIMEZONE=Asia/Shanghai` 判断，用户时区只用于 Telegram 和任务调度。
- 同日正式收盘评分仅在 A 股收盘后可运行；盘前和盘中返回 `WAIT_FOR_CLOSE`。
- L2 保持 `disabled/null`，Composite 使用 `L1 × 4/7 + L3 × 3/7`。
- AI 策略中增长决定结构仓位；数据覆盖不足只能 `NO_ACTION`，不得转换成中性分数。

详见 [DATA_CONTRACT.md](DATA_CONTRACT.md)、[宏观运行手册](src/pipeline/OPERATIONS.md) 和 [产业研究运行手册](data/deepresearch/OPERATIONS.md)。

## 系统结构

```text
src/
  a_share_claw/             # Telegram host、Agent、路由、领域工具、SQLite 基础设施
  compiled/                 # 版本化 L1/L2/L3/权重与 AI 策略规则
  pipeline/                 # 确定性数据获取、评分、报告与 AI 叠加层脚本
data/
  raw/                      # 按日期归档的原始输入
  analysis/                 # P1 风险/动量等中间结果
  scores/                   # L1/L2/L3/composite 结构化评分
  reports/                  # 日报
  factors/ai/ signals/ai/   # AI 叠加层的日期化因子与信号
  research/output/          # 产业研究输出
  state/system_state.json   # 最新正式状态；不是历史回放的写入目标
tests/                      # 现有单元与路由回归测试
```

`src/a_share_claw/` 是唯一运行实现；根目录 `a_share_claw/` 仅是兼容启动 shim。运行数据是审计证据，默认不作为代码提交物。

## 快速开始（开发验证）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m a_share_claw init-db
ASCLAW_FAKE_AI=1 python -m a_share_claw chat "测试一下"
```

配置 `.env`：

```bash
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_ALLOWED_USER_IDS=123456789

# 国内 OpenAI 兼容端点；不配置时才回落到教程占位模型。
ASCLAW_MODEL_PROVIDER=tencent
ASCLAW_MODEL_BASE_URL=https://tokenhub.tencentmaas.com/v1
ASCLAW_MODEL_API_KEY=sk-...
ASCLAW_MODEL_NAME=hy3
ASCLAW_OPENAI_MODEL=gpt-5.5

# Telegram/任务的用户时区，与 A 股市场日期边界分离。
ASCLAW_TIMEZONE=America/Los_Angeles
ASCLAW_MARKET_TIMEZONE=Asia/Shanghai
```

查看不含密钥的生效配置：

```bash
python -m a_share_claw show-config
```

启动 Telegram 和当前的轮询调度原型：

```bash
python -m a_share_claw run
```

> Telegram 在需要代理的网络环境中使用 `HTTPS_PROXY` / `HTTP_PROXY`；模型客户端默认 `trust_env=False` 并直连国产端点。若确实需要让模型走系统代理，设置 `ASCLAW_MODEL_TRUST_ENV=1`，并确认已安装 `socksio`。

## 第 3 章运行入口

正式宏观评分必须显式传入日期，并在收盘门禁通过后运行：

```bash
AS_OF_DATE=YYYYMMDD
cd src/pipeline
uv sync
uv run python fetch_etf_data.py --days 500
uv run python fetch_macro.py --date "$AS_OF_DATE"
uv run python fetch_us_macro.py --date "$AS_OF_DATE"
uv run python p1_upgrade.py --date "$AS_OF_DATE"
uv run python run_scoring.py --date "$AS_OF_DATE"
uv run python generate_daily_report.py --date "$AS_OF_DATE"
```

AI 叠加层使用独立的日期化流水线，不会替代 L2：

```bash
AS_OF_DATE=YYYYMMDD
cd src/pipeline
uv run python fetch_ai_market.py --date "$AS_OF_DATE" --days 800
uv run python fetch_ai_growth.py --date "$AS_OF_DATE"
uv run python fetch_ai_macro.py --date "$AS_OF_DATE"
uv run python fetch_cn_sentiment.py --date "$AS_OF_DATE"
uv run python compute_ai_factors.py --date "$AS_OF_DATE"
uv run python run_ai_position.py --date "$AS_OF_DATE" --current-ai-pct 57.5
```

历史 AI 回放需要显式使用每个脚本的 research-only/unverified 开关；这类产物不能更新 `system_state.json`。完整参数和失败处理以 [OPERATIONS.md](src/pipeline/OPERATIONS.md) 为准。

## MCP 与工具边界

Tavily 和 QVeris 是第 3 章的可选外部取证层，不承载内部评分规则或运行状态。配置示例在 `.mcp.example.json`，真实密钥只放在已忽略的 `.env`：

```bash
TAVILY_API_KEY=
TAVILY_HUMAN_ID=
QVERIS_API_KEY=
QVERIS_REGION=
QVERIS_MAX_RETRIES=3
```

- 纯宏观、ETF 仓位和量化请求不会加载 deep-research MCP。
- `mixed`、公司和产业链请求可使用 Tavily/QVeris；QVeris 读取须经 `discover -> inspect -> evidence gate -> 一次 readonly call -> 再评估`。
- `run_bash` 默认关闭；正式投研评分只通过固定领域工具和 pipeline，不允许由模型自由拼接命令。

## 当前已知技术债务

- 现有 `SQLiteSession` 只保存会话历史，并不解决短期上下文生命周期管理。
- 现有长期记忆是按 `user_id` 的文件追加和 SQLite 查询；它尚未满足按 `platform + user + chat + agent_key` 的严格隔离要求。
- 现有 `Scheduler` 是 host 进程内的 SQLite polling loop，只支持一次性或秒级周期；它不是完成态的 cron 服务。
- `loop-engineer`、离线评测、变更审批和策略自提升控制面尚未开始建设。

## 参考

- [NanoClaw](https://github.com/nanocoai/nanoclaw)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)

## 免责声明

本项目是研究助手，不是投资顾问。行情和宏观数据接口可能变动；任何投资决策前均应交叉核验交易所披露、公告、财报和原始数据。
