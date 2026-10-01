# a_share_claw

一个可由不同宿主调用、也可独立接入模型端点运行的 A 股投研 Harness。主体是投研契约、研究模板、工作流、评估与风控；入口、模型和数据源通过适配层接入。当前优先交付两条可审计的投研工作流：

1. 宏观日度评分与仓位纪律。
2. 行业链/公司研究与证据化辩论。

目标链路为：

`宿主/模型适配 -> 投研路由与框架 -> 数据需求与缺口 -> 按需数据插件 -> 契约校验 -> 分析/风控/评估 -> 可审计产物`

> **开发顺序（2026-09-30）**：先按 [E0 验收与未完成边界](E0_INFRA.md#验收与未完成边界) 闭环核心基础设施与业务门禁，再接入初步规划的五个事实接口，最后执行真实市场展望 case。统一运行契约、版本化 SQLite trace、CLI 摘要/回放和宏观/AI 确定性计算已实现，见 [E0_INFRA.md](E0_INFRA.md)。公司/行业核心执行器、统一 JSON/Markdown 报告与日期限制的状态读取已有合成事实验收；显式配置端点的角色/评估 SDK 路径已有本地模拟 HTTP 验证；独立价格统计与宏观展望模板已有合成执行；mixed 已实现独立切片与父运行发布门禁；模型辅助框架编译已接线；普通 chat 已接核心异步规划与缺口门禁；实际模型的固定合成公司/行业与展望已有有限人工验收，范围及质量限制见 E0_INFRA.md；五源事实映射、策略回测及连续正式日更仍待验收。五源接口与入口限制见 [DATA_PLUGINS.md](DATA_PLUGINS.md)；NBS/PBC 当前快照、FRED PCE 原生月度指数和 easy-tdx 指数日线已有显式研究交接；环比/同比与价格统计由核心计算。主行情已按最新 AGENTS 统一为 easy-tdx，TickFlow 默认禁用；FRED/SEC 实际接口、SEC 映射、完整日历/季度覆盖及自动取证仍待验收。发布文本和原生响应尚不等于评分事实。

## 顶层设计

- **Harness 核心**：维护数据与权限契约、研究模板、路由、评分、风险门禁，以及运行 trace 和硬门禁 evaluator。改进提案控制面仍待建设。核心不依赖 Telegram 用户 ID、某个模型 SDK 或具体数据供应商。
- **宿主与模型适配层**：Codex、Claude Code、Meta Muse、WorkBuddy 等环境是目标宿主；宿主可提供模型和工具执行能力，Harness 仍负责契约与正式产物门禁。独立模式通过模型 URL、模型名和端点需要的凭据运行。Telegram 是可选消息适配器，调度和通知不决定核心投研生命周期。
- **数据插件层**：先形成投研框架、输出模板和必需数据清单，再检查已有证据，最后仅接入补齐缺口所需的数据能力。量化行情、宏观序列、公司披露和搜索分别声明能力；无插件时仍能生成框架和缺口报告，必需证据不足时阻止正式评分或交易动作结论。

宿主适配和数据插件是两个独立扩展点；接入新宿主不需要重写投研框架，更换数据源不改变指标定义、评分权重或风控阈值。目标宿主清单表示设计兼容目标，逐个集成与端到端验收仍待完成。

完整边界、运行契约、热插拔语义和实施顺序见 [HARNESS_DESIGN.md](HARNESS_DESIGN.md)。数据时点、来源和替换限制以 [DATA_CONTRACT.md](DATA_CONTRACT.md) 为准。

## 当前边界

### 已有基础

- Telegram long polling 入口与白名单配置。
- OpenAI Agents SDK runner，以及国产 OpenAI 兼容端点配置。
- 宏观、量化、公司、行业和混合请求的确定性路由与工具 allowlist。
- 宏观日度评分管线、产业研究取证 gate 和旧 MCP 模块保留用于人工维护；均不再作为 Agent 取数入口。
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
| 1. 宿主适配 | 本地 CLI/Telegram 基础已具备；通用适配未完成 | 所有入口提交统一请求，使用同一核心门禁 | 无 Telegram 配置也能运行；外部宿主与 CLI 对同一输入产生一致的契约检查和 trace；身份/权限显式映射 |
| 2. 模型接入 | 独立 OpenAI 兼容端点基础已具备；宿主模型适配未完成 | 支持宿主提供模型或独立模型 URL 两种模式 | 核心不绑定模型 SDK；模型名、凭据引用、超时与预算明确；模型不可替代硬门禁 |
| 3. 投研执行平面 | **进行中** | 将策略、数据契约、研究路由和受约束工具闭环 | 宏观与产业研究均按日期、来源、fallback 输出；正式宏观日更恢复；AI P0/P1 只有在正式数据可用时写入状态 |
| 4. 数据插件与证据工作台 | 五源接口与入口限制已实现；归一化事实到核心评分的自动接入未完成 | 从投研模板推导缺口，按需接入可替换的数据能力 | 零插件可规划；仅加载必要插件；插件增删不改核心；结果有统一 envelope、来源/时点校验与回放快照 |
| 5. 短期上下文管理 | 未完成 | 管理每个会话的上下文生命周期和 token 预算 | 有保留窗口、摘要/压缩、恢复策略、上下文预算和回归测试；不会因历史无限增长而失控 |
| 6. 长期记忆与隔离 | 未完成 | 只在允许的主体边界内检索、写入和注入记忆 | 采用 `workspace + principal + session + agent_key` 核心作用域，入口映射原 platform/user/chat；隔离、保留/删除和注入均有端到端测试 |
| 7. 任务与 Cron 调度 | 未完成 | 可靠地创建、执行、重试、观测和取消一次性/周期性投研任务 | 支持明确时区与 cron/固定周期语义，具备幂等、失败重试、并发/错过执行策略、状态查询与通知验收 |
| 8. 运行隔离与可观测性 | E0 trace/作用域门禁已实现；完整隔离与恢复未完成 | 将会话、任务、工具调用、数据产物和权限作为独立运行边界 | 身份授权、会话/任务/记忆/产物隔离一致；日志、指标、审计和故障恢复可验证 |
| 9. loop-engineer / 自提升 | 未开始 | 用受控评估驱动策略和流程改善，而不是让 Agent 自行改写生产规则 | 固定评测集、变更提案、回放、人工批准、版本化回滚和上线后监控齐备；无自动越权交易或修改数据契约 |

### 第 3 章当前工作

第 3 章包含此前所有策略相关开发：L1/L3 评分、L2 禁用与权重重归一、数据日期/来源契约、宏观/研究路由、MCP evidence gate，以及 AI 行业仓位叠加层。新的开发顺序先收口可复用投研框架与数据需求，再补数据插件；已有固定管线仅保留人工兼容入口，不能绕过 Agent 的插件限制。

最近已提交的 AI 战术加仓门禁除了流动性分位数，还要求 2Y、10Y、30Y 与 10Y TIPS 在同一五日窗口内均未上行，并且 Brent 不触发通胀冲击。其目的是避免“分位数看似宽松、绝对利率或油价实际恶化”时错误触发 `ADD`。新架构继续保留这些风险约束。

按新架构先收口框架/需求/E0，再接入数据插件。正式数据链路仍需完成以下业务验收：

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
- A 股交易日用 `ASCLAW_MARKET_TIMEZONE=Asia/Shanghai` 判断，用户时区用于宿主展示和任务调度。
- 同日正式收盘评分仅在 A 股收盘后可运行；盘前和盘中返回 `WAIT_FOR_CLOSE`。
- L2 保持 `disabled/null`，Composite 使用 `L1 × 4/7 + L3 × 3/7`。
- AI 策略中增长决定结构仓位；数据覆盖不足只能 `NO_ACTION`，不得转换成中性分数。

详见 [DATA_CONTRACT.md](DATA_CONTRACT.md) 和 [宏观运行手册](src/pipeline/OPERATIONS.md)。核心运行协议已版本化为 [RESEARCH_OPERATIONS.md](src/a_share_claw/RESEARCH_OPERATIONS.md)，由配置、Agent 上下文和核心政策快照共同引用。`data/deepresearch/OPERATIONS.md` 为部署档案，不再作为运行依赖；缺关键协议时在模型调用前停止。协议可加载不代表业务执行器已验收。

## 系统结构

```text
src/
  a_share_claw/             # 当前 Agent runner、路由、领域工具、SQLite 与可选入口
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

以上是当前目录，不代表核心/宿主/数据插件已经物理拆分。目标模块划分和兼容迁移见 [HARNESS_DESIGN.md](HARNESS_DESIGN.md)。

## 快速开始（开发验证）

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
python -m a_share_claw init-db
ASCLAW_FAKE_AI=1 python -m a_share_claw chat "测试一下"
```

独立模型模式配置 `.env`，本地 `chat` 不需要 Telegram token：

```bash
# 国内 OpenAI 兼容端点；不配置时才回落到教程占位模型。
ASCLAW_MODEL_PROVIDER=tencent
ASCLAW_MODEL_BASE_URL=https://tokenhub.tencentmaas.com/v1
ASCLAW_MODEL_API_KEY=sk-...
ASCLAW_MODEL_NAME=hy3
ASCLAW_OPENAI_MODEL=gpt-5.5

# 宿主展示/任务的用户时区，与 A 股市场日期边界分离。
ASCLAW_TIMEZONE=America/Los_Angeles
ASCLAW_MARKET_TIMEZONE=Asia/Shanghai
```

```bash
python -m a_share_claw chat "先梳理宏观日度评分框架和所需数据"
```

模型 URL 须符合当前 OpenAI 兼容协议，并配置端点接受的模型名与必要凭据。未来由外部宿主提供模型时无需另配模型 URL；统一宿主协议尚未实现。模型接入也不会自动补齐投研所需的真实数据。

查看不含密钥的生效配置：

```bash
python -m a_share_claw show-config
```

可选 Telegram 入口：先在 `.env` 中配置以下两项，再启动当前的轮询调度原型。

```dotenv
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_ALLOWED_USER_IDS=123456789
```

```bash
python -m a_share_claw run
```

显式 `run` 启动可选 Telegram 适配；不带子命令只显示帮助。`data plugins/plan/fetch` 是模型无关宿主入口，注册表支持库级热插拔。

> Telegram 在需要代理的网络环境中使用 `HTTPS_PROXY` / `HTTP_PROXY`；模型客户端默认 `trust_env=False` 并直连国产端点。若确实需要让模型走系统代理，设置 `ASCLAW_MODEL_TRUST_ENV=1`，并确认已安装 `socksio`。

## 第 3 章运行入口

先使用核心入口验证运行逻辑与评分策略，事实包、回放和验收范围见 [E0_INFRA.md](E0_INFRA.md)：

```bash
uv sync --locked --extra dev
uv run python -m a_share_claw harness plan --workflow macro --date 2026-07-13
uv run python -m a_share_claw harness run facts.json --workflow macro --date 2026-07-13
uv run python -m a_share_claw trace RUN_ID
uv run python -m a_share_claw harness report RUN_ID --format markdown
```

默认回放只产生 NO_ACTION。正式模式只接收经过操作人审核的规范化事实，按作用域与 workflow 原子更新 SQLite 状态；公司/行业核心库入口可注入受信任的角色执行和独立语义评估回调；CLI 显式 SDK 执行可用；独立 price_statistics 与 outlook 路径已接线；mixed 已接线，普通 chat 已接核心异步入口；五源事实映射、真实业务质量和策略回测待验收，固定合成模型验收范围见 E0_INFRA.md。插件不得修改核心规则。

公司/行业研究需由受信任宿主提供审核事实与冻结研究规格。显式使用已配置端点执行角色和独立评估：

```bash
uv run python -m a_share_claw harness plan --workflow industry --date 2026-07-13 --research-spec spec.json
uv run python -m a_share_claw harness run facts.json --workflow industry --date 2026-07-13 --research-spec spec.json --mode research --model-executor configured
```

模型辅助框架编译先校验宿主约束并独立评估，不加载插件或获取事实：

```bash
uv run python -m a_share_claw harness plan --workflow industry --date 2026-07-13 --question "梳理产业链价值捕获、证据需求和风险边界" --model-executor configured
uv run python -m a_share_claw harness plan --workflow outlook --date 2026-07-14 --question "形成条件市场展望框架" --planning-constraints constraints.json --model-executor configured
```

constraints.json 固定实际仓位或量化/预测窗口等宿主条件，详见 [框架编译](E0_INFRA.md#模型辅助框架编译)。成功 plan 仅表示 framework_only，缺口与必需能力仍须填充。同步宿主 plan_core_result 可规划；run_core_result(..., compile_framework=True, planning_constraints=...) 可在同一 run 内编译后消费宿主审核的事实包。普通 chat 已接核心异步规划/缺口入口；自动取证的规范化事实映射仍属于后续五源接入。

`spec.json` 字段见 [E0_INFRA.md](E0_INFRA.md#显式-sdk-研究执行入口)。未指定 executor 的 plan 不调用模型，也不会自动连接端点。配置端点必须显式给出 provider/base URL/model name/API key，不回落到 SDK 全局默认客户端。模型无取数/文件/MCP 工具，输出仍经过核心校验与发布门禁。普通 chat 的自由文案不能构造审核事实包；插件自动映射仍待接线。

异步宿主 `run_core_result_async(..., packet=..., workflow=..., compile_framework=True)` 使用同一核心 run；取消会传播至当前模型和 mixed 子运行，核心记录终态后才结束等待。数值规格在 planning_constraints 中固定。普通 run_result 不接受事实包或 official 模式；当前自动回复是核心冻结计划及明确缺口，五源映射完成前不调用来源。旧模型选源/六工具循环与 SDK 全局默认客户端路径已移除，取源 CLI 保留独立研究边界。详见 [异步入口验收](E0_INFRA.md#异步宿主与普通-chat-核心入口)。

价格统计使用独立规格与 price_history，不使用日评分固定标的：

```bash
uv run python -m a_share_claw harness plan --workflow quant --date 2026-07-14 --quant-spec quant.json
uv run python -m a_share_claw harness run facts.json --workflow quant --date 2026-07-14 --quant-spec quant.json --mode research
uv run python -m a_share_claw harness run outlook-facts.json --workflow outlook --date 2026-07-14 --outlook-spec outlook.json --mode research --model-executor configured
uv run python -m a_share_claw harness plan --workflow mixed --date 2026-07-13 --mixed-spec mixed-spec.json
uv run python -m a_share_claw harness run mixed-facts.json --workflow mixed --date 2026-07-13 --mixed-spec mixed-spec.json --mode research --model-executor configured
```

成功运行会归档 report.json 与 report.md；任一渲染/验证/归档失败都不能发布。`harness report RUN_ID --format markdown --date YYYY-MM-DD` 在读取前检查授权作用域、成功终态、必需 evaluator、JSON/Markdown 哈希和归档绑定，并从正式历史事务决定 published/research；文件中的 staged 标记不自行变成正式发布。`--format json` 返回同一读取结果的结构化内容。同步宿主可用 `InvestmentAgent.read_core_report(context, run_id, as_of_date=...)`；异步宿主可用 run_core_result_async；普通 chat 已接核心规划与缺口门禁，五源事实映射尚未接线。

以上文件由受信任宿主提供，字段见 [价格统计与宏观展望](E0_INFRA.md#独立价格统计与宏观展望)。统计与展望输出保持 NO_ACTION，不生成缺少输入的日度分数。季度日历和数据身份仍需来源验收；已声明的短窗口不能冒充完整季度。

以下旧命令仅供人工维护，仍包含五源以外的旧供应商。Agent 不得调用；迁移为插件输入前，不属于五源 Harness 的正式输出路径。历史计算回归保留，日期与风控约束不变：

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

## 插件与工具边界

参阅 [DATA_PLUGINS.md](DATA_PLUGINS.md) 配置五源和查看 JSON 计划例子：

```bash
uv sync --locked --extra dev
uv run --locked python -m a_share_claw data plugins
uv run --locked python -m a_share_claw data plan examples/data-plan.json
uv run --locked python -m a_share_claw data fetch examples/data-plan.json
```

Agent 只暴露计划、清单、插件取数、缺口报告和本地规则/时钟工具。通用网页、MCP、Bash、Codex 扩展、文件读写与旧 pipeline 均不暴露；旧开关不能绕过。`.mcp.example.json` 只作为旧接口参考。无来源/凭据时交付缺口，不能用模型知识补数。外部宿主还需限制自己的其他取数工具；CLI 不会替宿主建立系统级沙箱。

## 当前已知技术债务

- 旧 `SQLiteSession` 历史保留，但不再注入新 Agent 运行，避免携入未验证网页/工具证据；可验证跨轮上下文尚待实现。
- 现有长期记忆是按 `user_id` 的文件追加和 SQLite 查询；它尚未满足按 `platform + user + chat + agent_key` 的严格隔离要求。
- 新的环境无关作用域需要从现有 platform/user/chat 映射，并保留历史会话数据；当前隔离机制仍需迁移与验证。
- 现有 `Scheduler` 是 host 进程内的 SQLite polling loop，只支持一次性或秒级周期；它不是完成态的 cron 服务。
- `loop-engineer`、离线评测、变更审批和策略自提升控制面尚未开始建设。
- 插件取数入口已实现；旧 fetch/compute/report 的输入迁移、跨运行缓存/回放和正式状态提升仍未完成。

## 参考

- [NanoClaw](https://github.com/nanocoai/nanoclaw)
- [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)

## 免责声明

本项目是研究助手，不是投资顾问。行情和宏观数据接口可能变动；任何投资决策前均应交叉核验交易所披露、公告、财报和原始数据。
