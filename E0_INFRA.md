# E0 基础设施与验收

开发基线：`docs/portable-harness-data-plugins`，2026-09-30。核心负责运行、契约、评分和风控；插件只提供事实。本文不表示 Issue #1 的 E1–E5 或全部投研业务已经完成。

## 已实现

- `harness/contracts.py`：七类 FailureCategory、RunStatus、EvalResult、ToolResult、PromptCacheTrace、RunRequest/RunOutcome 与四字段 Scope。
- `harness/trace.py`：带校验和的事务迁移、runs/steps/tools/models/artifacts/evaluations/proposals；旧会话和任务保留。查询/追加必须匹配作用域，终态禁止追加；proposal 表仅预留。
- 每次 Agent/CLI 请求先生成 run_id。异常、超时和取消写入终态；调度器不会把 blocked/failed 通知为完成。逐次模型调用记录哈希、usage 和相邻输入前缀长度；不保存模型输入正文，缺失的缓存和首字延迟为 null。
- 宏观保留既有 L1、L3 风险/情绪、L2 disabled、4/7 与 3/7 权重及仓位区间；AI 复用增长、动量、情绪、流动性与绝对利率/Brent 门禁。
- 核心验证作用域、日期、单位、覆盖、来源字段和事实哈希。缺数据、未来数据、unverified/fallback、政策变化和预算耗尽阻止动作。
- 一个 SQLite 事务更新正式状态、历史和终态；按 scope + workflow 分别保存，不回退到较早日期。迁移 v2 保留 v1 已有正式状态；日期截止查询不读取未来状态。research/replay 永远 NO_ACTION；部分归档不等于正式状态。
- 公司/行业核心执行器冻结逐字段事实需求，角色共享不可变事实包；技术核验与辩论按计划启用。引用门禁和受信任的独立语义评估均通过后才交付推断，风险结论保持 NO_ACTION。
- 核心生成并验证 JSON 与 Markdown 报告，归档失败不能发布；`harness state` 校验作用域、日期、报告路径及哈希。报告与 computed_output 归档均标为 staged，最终发布状态由成功终态和正式状态事务决定。
- Agent 只调用五源插件和规则/时点工具，规则剔除历史观测/示例。未经业务 evaluator 的模型自由结论不交付为评分或仓位建议。

## 无模型、无插件运行

在仓库根目录：

```bash
uv sync --locked --extra dev
uv run python -m a_share_claw harness plan --workflow macro --date 2026-07-13
uv run python -m a_share_claw harness run facts.json --workflow macro --date 2026-07-13
uv run python -m a_share_claw trace RUN_ID
uv run python -m a_share_claw trace RUN_ID --full
uv run python -m a_share_claw harness replay RUN_ID
uv run python -m a_share_claw harness state --workflow macro --date 2026-07-13
```

run 默认 replay；显式 `--mode official` 才进入正式门禁。操作人必须提供已经审核的事实和来源，核心不能仅凭 source 字符串证明数据真实或 vintage 正确。CLI 身份参数用于本地个人入口；远程宿主必须根据自身认证映射 Scope。

## 插件下一步填充事实包

FactPacket 顶层恰有 `as_of_date/facts`；每项恰有 `capability/scope_key/data/provenance/fallback_status`。scope_key 从规划结果取得。必需能力由核心计划决定，不接受插件提供的评分、权重或动作。

provenance 必須有 `source/source_file/source_timestamp/publication_date/observation_date/data_period/sha256`。sha256 是规范化事实的 canonical JSON 哈希（排序键、紧凑分隔符、UTF-8、禁止 NaN），原始响应哈希另存。source_timestamp 必须带时区；发布时间、观测日及嵌套行情/披露日期不能晚于请求日。

宏观字段/单位见 `engine.MACRO_UNITS`：NFP 为千人、利率为百分数、信用利差为百分点。行情为固定 universe、daily、159682.SZ QFQ/CNY 和 QQQ.US NONE/USD。AI 使用 `schema_version=ai-inputs-v1` 与既有 ai_strategy 原始输入：增长 yoy/融资比例为小数、融资金额为人民币元、期权 put/call 为比例；宏观历史沿用旧规则单位，不接受预计算 action。

归档在 `data/harness_runs/<scope>/<run_id>/`。replay 检查授权作用域、文件根目录、哈希和政策版本，保留 AI 当前仓位参数，不拉取新数据；政策变化则拒绝回放。

## 验收与未完成边界

2026-09-30 按 PR #2 的 `773564e` 和 Issue #1 实际清单复核。该版本 [Actions run 36698444615](https://github.com/le0820/a_share_claw/actions/runs/36698444615) 已通过 Python 3.11/3.12 的离线检查；127 passed / 40 subtests 是覆盖范围内的证据，不能据此宣称全部业务或 Issue #1 完成。本文只更新验收边界，没有重跑真实市场 case。

| Issue #1 E0 项 | 当前实现与接线 | 验收边界 / 下一步 |
| --- | --- | --- |
| FailureCategory / RunStatus / EvalResult / ToolResult | 已实现；核心、Agent 与数据 CLI 使用结构化结果 | 已有契约与失败传播检查；旧人工 ToolRuntime / MCP 全面标准化属于 E1，不能算全部已迁移 |
| 每次 InvestmentAgent.run 生成 run_id | 已实现；run_result 先建记录，run 为兼容渲染入口 | fake、SDK 脚本、异常/取消有证据；真实模型业务完成尚未验收 |
| route、loaded/missing context、state scope | 记录已接线；SDK 记录装配结果，核心记录实际读取的政策快照、缺失项和 scope | 正常加载/缺 IDENTITY/缺必需 compiled 文件已验；版本化协议已接线；缺协议时在模型调用前停止，角色业务执行另列 |
| SQLite migration / 最小 trace repository | 已实现；事务迁移、作用域授权、终态和 scoped official state | 已有旧库保留、回滚、隔离与不回退检查；崩溃恢复/完整 memory 迁移留在后续阶段 |
| CLI 按 run_id 查摘要 | 已实现；trace / --full / --list 与计算回放 | 回放范围为宏观/AI/price_statistics 核心计算，不等于 SDK 会话或全部研究工作流回放 |

**整体状态：E0 契约、上下文、冻结规划、核心公司/行业执行、统一报告与状态读取已有对应实现和合成验收；显式 SDK 路径已有模拟 HTTP 验证；mixed 已有独立切片实现；普通 chat 自动接线、真实研究质量、策略回测及完整需求映射仍未闭环。PR #2 暂不合并，Issue #1 保持 open。**

本轮上下文修复在 Python 3.11/3.12 各执行相关核心回归一次：`tests/test_harness.py` 均为 31 passed。缺失 IDENTITY 或必需 compiled 文件时保留 route/context、记录缺失项及 CONTEXT_TRUNCATION_FAILURE，阻止计算/产物/正式状态。此证据仅适用于本项修复，不是新增真实数据 case 或全业务验收。

### 本轮协议与规划收口

- `src/a_share_claw/RESEARCH_OPERATIONS.md` 取代未提交部署手册作为配置/上下文依赖，核心政策快照固定其版本。Agent 读取完整简短协议；缺协议在模型配置/调用前 blocked，不回退归档 SOP 或旧工具。
- `harness/planning.py` 固定 question hash、scope/date/mode、policy/version、required/optional/disabled 能力、报告章节、停止/完成条件及参数；canonical document 不可变，消费者拿到独立副本。核心在证据前归档 `plan.json`，发布前检查其不变性。
- quant 的 universe/window/adjustment/benchmark/metrics 缺失明确列为 unresolved；不把宏观评分标的静默当作用户回测标的。产业/公司辩论按共享证据后有真正双向不确定性决定，不强制每次辩论。
- 该协议提交关闭协议可复现和核心规划记录项；其后核心执行进展见下节。SDK 计划绑定、真实模型验收和完整业务切片仍未闭环，不能据此进入五源接入或市场 case。

### 公司/行业核心执行、报告与状态闭环（2026-09-30）

核心库 `Harness.run(..., research_spec=..., role_runner=..., semantic_reviewer=...)` 已接线。spec 明确对象、指标、单位、统计期、类型和观测窗口；事实必须逐字段匹配。每个角色输出绑定同一 packet/version、回答必需问题并引用已准入事实；有必要的辩论包含两方初始观点和回应，最后由 Ping Heng 给出监控触发条件。回调只获得不可变请求副本，超时晚到结果不能进入发布事务；此机制不替代宿主的系统沙箱。

结构化引用检查只证明契约匹配，不能证明推断质量。核心另外要求受信任的独立语义评估回调，缺评估、评估不通过或候选哈希不匹配均不交付自由文案。当前验收用脚本化角色和脚本化 fixture reviewer，未调用真实模型、未完成真实研究质量验收。

JSON 报告固定来源、日期、fallback、政策版本和结果；宏观含评分、驱动项、L2 禁用、仓位纪律及可用历史对比。正式发布要求同 run 的报告已归档并通过验证；归档失败、身份错配、计划/政策变化、较早日期覆盖均不能更新正式状态。SQLite 迁移 v2 保存状态历史；`harness state` 可查指定日期前的授权状态，并验证报告路径/哈希。旧全局 JSON 不自动迁入；该版本尚未接线 Markdown 与其他宿主读取，后续进展见报告交付章节。

本地 Python 3.12 全套为 **156 passed / 40 subtests**；新增业务及相关基础回归在 Python 3.11/3.12 各为 **66 passed**，覆盖角色引用/缺问题/回应/超时/取消、事实口径、独立评估、报告归档失败、历史日期与迁移保留。此证据仅验证固定合成事实的核心行为，不是五源真实接入或市场 case。

### 显式 SDK 研究执行入口

`SDKResearchAdapter.bind(session)` 为核心提供角色与独立评估回调，每阶段新建无工具/MCP/handoff/history 的 SDK run，显式绑定端点客户端与模型，限制一个 turn 和剩余时间；不修改 SDK 全局模型/客户端，使用本地 Harness hooks 记录 operation 和逐模型请求，禁用 SDK 远程 exporter。CLI `--model-executor configured` 是连接端点的显式选项，离线 replay 不启动模型。同步受信任宿主入口为 `InvestmentAgent.run_core_result(context, message, as_of_date=..., packet=..., research_spec=..., workflow=..., mode="research")`；异步 Telegram/chat 自动桥接仍未完成。

`--research-spec` 接受 checked_spec 的 JSON：subject、technical_required、debate_required、debate_reason、required_facts、questions。required_facts 每项固定 fact_id/entity/metric/unit/data_period/value_type/observation_start/observation_end；questions 每项固定 question_id/question/role/required_fact_ids。plan 可读此规格而不调用模型；模型执行的事实包必须先通过同一核心门禁。当前规格由受信任宿主提供，模型辅助框架编译及其到取证需求的自动映射尚未完成。

Python 3.12 全量检查为 **164 passed / 40 subtests**，Python 3.11 相关检查为 **44 passed / 6 subtests**。本地模拟 HTTP 验证使用真正安装的 SDK 与兼容客户端：共享 run_id、七个角色阶段加一次独立评估、零取数工具、资源关闭、错误 JSON/引用/评估不发布、缺事实/端点零调用、CLI 和同步宿主一致。响应为固定合成 fixture，仍不代表真实端点可用或模型质量验收。依据 [官方模型适配](https://developers.openai.com/api/docs/guides/agents/models) 与 [观测说明](https://developers.openai.com/api/docs/guides/agents/integrations-observability)，模型和 trace 配置属于适配层，核心仍拥有验证和发布权。

### 独立价格统计与宏观展望

`--quant-spec` / `Harness.run(..., quant_spec=...)` 接受 quant-spec-v1：operation=price_statistics、frequency=daily、window_start/end、带时区 cutoff_timestamp、assets、benchmark、metrics 和 annualization_factor。每个 asset 固定 symbol/name/unit/currency/adjustment/market_timezone/calendar_source，anchor 和 sessions 明列 trade_date/close_at。price_history 的 price-series-v1 保留每个 series 的同一身份、frequency/source/source_file/source_timestamp/publication_date；rows 明列 trade_date/close/available_at。行情必须逐日匹配声明日历，含前期收盘锚点；缺行、重复、代理、口径不符、无效收盘或盘前值均阻断。捕获、披露和可得时间不得超出截止，执行截止不得晚于宿主时钟。覆盖标签是 complete_against_declared_calendar，日历与来源真实性仍依赖受信任宿主审核及后续插件验收。

支持 period_return（末值/锚点-1）、非负 max_drawdown、简单收益样本标准差 × sqrt(声明年化因子)、excess_return（相对 benchmark 的百分点差）和共同日期收盘变动相关性。相关性零方差以 null/zero_variance 披露；没有分红、费用或 FX 转换，不是策略回测。metrics 单位随输出保存，跨市场非同步收盘和日期差异保留在 series_audit/limitations。旧日评分的 market_history 固定 universe 不受此入口替代。

`--outlook-spec` 包含 quant_spec、research_spec、forecast_start/end。研究规格复用原事实契约，至少固定 Hong Guan 的 base_scenario/market_comparison 和 Ping Heng 的 risk_monitoring 问题，market_comparison 必须引用所有声明价格指标。可用 derived_requirements(quant_spec) 在取证前编译派生指标身份。macro_release_facts 使用 macro-release-facts-v1，逐事实字段与 research-facts-v1 一致并增加带时区 available_at；当日截止检查精确到时间。核心从已准入价格计算指标，保存 derivation artifact/input hash，再加入同一 packet；供应商不能提交 price.* 派生事实冒充核心结果。Hong Guan → 可选 Jia Zhi → Ping Heng 后独立语义评估，报告包含预测窗口、基准情景、来源与监控条件，始终保持 NO_ACTION；不虚构 L1/L3/composite 或概率。模型无新取数权限。

固定合成窗口覆盖收益/回撤/波动的解析值、代理/缺行/占位值/日期拒绝、undefined 相关性、CLI 规格回放、宏观派生事实及真正 SDK 的本地模拟 HTTP 路径。修复 NASDAQ Composite 名称被误判为 Composite 日评分关键词。该证据不是完整 Q3 数据、真实插件或四季度市场 case。该版本尚未完成 mixed 切片，后续进展见下节；自动框架编译/普通 chat 绑定、真实研究质量仍未完成；Markdown 后续进展见报告交付章节。

### Mixed 切片和原请求覆盖门禁

`--mixed-spec` / `Harness.run(..., mixed_spec=...)` 固定 2–8 个必需切片。规格顶层仅 slices，每项仅 slice_id/workflow/question/parameters；允许 macro、ai、company、industry、quant、outlook，禁止递归 mixed、覆盖 scope/date/mode 或任意额外参数。参数分别为 macro={}、ai={current_ai_pct}、company/industry={research_spec}、quant={quant_spec}、outlook={outlook_spec}。父计划的必需/可选能力从切片政策合并，不能沿用固定宏观默认列表。

mixed 输入是带日期的切片包络：`{"as_of_date":"YYYY-MM-DD","slices":[{"slice_id":"...","packet":FactPacket}]}`。每个 packet 保持前述 FactPacket 契约；未提供的切片只记录缺数据。每个子运行独立校验并消费同一 scope/date 和父运行剩余预算，记录 parent_run/slice_result。正式父请求仍要求宏观子切片等待 A 股收盘；子运行使用 research/replay，始终 NO_ACTION、不得发布任何 workflow 的正式状态。等待或坏宏观输入不压住有效研究；取消立即终止父运行，超时晚到回调不能发布。

父运行保存 slice_status、已评估子报告的 partial_reports 和 mixed_progress。必需切片未完成则父运行 blocked/failed，并明确 combined gaps；不交付综合报告或正式状态。所有切片成功后，还必须独立评估原始请求整体覆盖，再生成、验证并归档父报告，最后仅由父运行原子发布 mixed 状态。综合评估、父报告或 computed_output 归档失败均不能留下子切片正式状态。局部报告描述符是 staged，不表示整个请求完成。

角色与评估请求现在包含原始 user_request，评估标准包括 original_request_satisfied；子切片保留全请求及自己的范围，父评估检查整体覆盖。普通 trace 仍只存文本哈希；嵌套 slice question/research subject/questions 同样哈希化。语义评估仍依赖受信任 reviewer 的判断，固定 fixture 不能证明真实研究质量。mixed 没有离线会话重建器，CLI replay 明确返回 replay_unavailable，不丢弃规格后启动新模型。

本地 Python 3.11/3.12 全套各为 **194 passed / 41 subtests**。该路径完成后使用合成事实核对等待/缺输入/坏哈希/跨 scope、父评估与归档失败、取消/超时、嵌套文本 trace 隐私，以及真正 SDK 的本地模拟 HTTP、CLI 零模型规划与拒绝回放；没有接入真实五源或执行市场 case。普通 chat 自动规划与业务接线及真实质量仍需闭环；报告交付进展见下节。

### 人类可读报告与授权交付

核心从同一已评估 JSON 生成确定性 Markdown，渲染版本进入报告和政策快照。报告包含运行/日期/fallback、来源发布时间/观测日/统计期/捕获时间/哈希，工作流的评分或统计，研究事实/核心派生值、角色推断及引用、未知项和监控条件；保留 L2 禁用、NO_ACTION 与条件预测边界。单位随指标显示，相关性 undefined 保留 null；模型与来源文本按纯文本转义，不能添加标题、图片或任意链接。归档重读采用相同 canonical 顺序，角色按初始观点/回应/最终风险的阶段排列。

report.json 与 report.md 均为 staged，分别验证并归档；Markdown 失败与 JSON 失败一样阻止发布。正式状态持有两个同 run/scope 的归档描述符，SQLite 事务要求完整的 frozen_plan/policy_snapshot/required_evidence/scope_and_date/report_contract/report_markdown_contract 硬评估，不能用单个通过的权限检查代替业务门禁。

`harness report RUN_ID --format markdown|json [--date YYYY-MM-DD]` 先授权，再读取成功终态、必需 evaluator、计划/报告/计算结果和两个报告的归档哈希、目录及身份绑定。按 run_id 查询精确历史发布事务，保留同日较早的正式报告；不因为文件存在、mode=official 或中间 computed_output 就标为已发布。失败运行即使留下 staged 文件也拒绝交付，越作用域、越日期和篡改均拒绝。`InvestmentAgent.read_core_report` 是相同的同步宿主读取入口；不发送任何通知。

新版 `harness state` 通过同一读取 gate 校验双报告；旧数据库的 JSON-only 历史仍保留原有 state 读取兼容，但不会伪造补建 Markdown。旧运行缺少 Markdown evaluator 时 report 命令返回 report_not_found，需要在适用版本重新执行才能拥有双报告。计算 replay 检查 Markdown 哈希后跳过文本解析；仍不拉新数据或启动模型。

本地 Python 3.11/3.12 全量各为 **211 passed / 41 subtests**，新增实际 CLI 进程检查随后双版本各通过 1 项。本项使用各工作流固定合成事实验证归档/读取往返、纯文本转义、阶段顺序、同日精确历史、跨 scope/日期拒绝、文件篡改、未完成 staged 拒绝交付、渲染与 Markdown 归档失败保护，以及不得以单权限 evaluator 发布。它关闭报告/同步交付实现项，不证明实际源真实性、真实研究质量或 Telegram 自动业务接线。

### A. 先闭环核心，再进入插件接入

按以下顺序补齐实现、接线和验收证据。可使用固定事实包验证核心行为，不提前执行用户的真实市场 case。

1. **运行协议与上下文（本轮已接线）。** 必要协议进入版本控制并被核心/SDK 加载；缺协议先阻断。记录实际 loaded/missing、政策版本与 state scope。完整 token 压缩/长期记忆不是本项完成条件。
2. **框架与业务执行。** 请求先冻结问题、日期、mode、scope、输出模板、必需/可选/禁用证据；宏观保持既有评分与风控，产业研究消费同一版本事实包并按 IDENTITY 执行技术/价值链/必要辩论/最终风险 gate。company 复用研究契约；mixed 按独立切片记录等待和完成；quant 先冻结 universe/window/adjustment/benchmark/metrics，不凭现有市场事实包宣称回测完成。
3. **报告与发布。** 事实、推断、缺口和来源分别可追溯；报告和归档成功后才能发布。正式状态读取方接到带 scope 的 SQLite 状态，旧全局 JSON 不自动注入。报告失败、缺证据、unverified、越界日期均只交付 NO_ACTION，不能提升半成品。
4. **验收闭环。** 每项保存对应版本、输入约束、run_id、trace/evaluator/产物证据和结论；“已实现”“已接线”“已验收”分开记录。正确拒绝可验收为 gate 成功，不能记为研究任务完成。核心缺口未关闭前，不进入 B。

当前 company/industry/outlook 可通过核心库回调或显式 CLI/宿主 SDK 入口执行；quant 的 price_statistics 可离线计算/回放，其他策略回测明确返回 quant_operation_not_implemented。mixed 已具备独立切片与父运行统一发布；普通 chat 取证入口仍停在 core_evaluation_pending。宏观展望使用独立事实和指标模板，不借日评分标的或缺失评分生成动作。

### B. 然后接入初步规划的五个事实接口

仅在 A 完成后推进 NBS、PBC、TickFlow、FRED、SEC 到核心 FactPacket 的映射。逐接口登记能力、真实字段、单位、统计期/披露日、修订 vintage、覆盖和不可得项。插件不能新增评分权重或把预计算 action 当事实。

NBS/PBC 目前保留发布正文；数值和附件解析未完成。TickFlow 真实账户样本、三表披露/单位和历史 PIT 未验收；FRED/SEC 已有时点过滤，但尚未自动接入评分事实包。缺凭据或不支持的证券/指标继续返回缺口，不另接网页、旧 pipeline 或其他供应商补数。当前任务 AGENTS.md 的行情主源限制必须先落实；现有 TickFlow 接口的存在不构成主源授权。

`data` 插件运行的 `official_output_allowed=false` 保持不变；它表示取数本身没有发布权。核心单独通过 evaluator、报告与原子发布门禁后才可允许 official。手工 FactPacket 的可运行性不等于插件到核心链路已验收。

### C. 最后执行用户指定的业务 case

五源映射和入口接线完成后，再通过已授权插件取得美国 8 月 PCE、中国 8 月国民经济运行和 NASDAQ Composite / 创业板 / 科创50 三季度数据，生成四季度展望及基准情景，并沿用同一核心 trace、证据和风控门禁。

冻结发布日期/截止时点、季度末完整性、指数身份、指标单位与来源。季度未收盘只可标明部分窗口；NASDAQ Composite 不能以 QQQ/NDX 替代。该材料不足以填满既有每日评分全部必需指标时，展望保持研究模式 / NO_ACTION，不虚构 L1/L3/composite 或交易动作。此前提前下载的原始材料尚未准入，只留在 data 中；不作为完成证据。

### 明确留给后续阶段的范围

完整 ContextManifest/上下文压缩与长期记忆、通用宿主桥接、可靠 Cron/崩溃恢复、E3 的 30–50 固定任务与 ≥20 故障注入、四臂缓存性能对照及 E4/E5 proposal 审批/自提升继续按 Issue #1 推进。这些边界必须保留，不能为本次 case 扩张范围或标记完成。连续正式宏观日更和 AI 正式输入验收依赖 B；一次 case 通过也不替代连续服务观察。
