# 跨设备开发交接：五源插件

## E0 核心续开发（2026-09-30）

继续使用 `docs/portable-harness-data-plugins`。先补核心运行/策略/门禁，再接事实插件。统一契约、事务迁移、作用域 trace、逐模型请求观测、宏观/AI 计算、CLI 摘要/回放与失败通知已落地，详见 [E0_INFRA.md](E0_INFRA.md)。原 main 的本地未提交文件未改动。

本地完整套件按锁文件分别验证 Python 3.11/3.12：均为 **127 passed, 40 subtests passed**。包括宏观与旧流水线结果比对、AI 输入与覆盖门禁、迁移失败回滚、归档失败撤销发布、作用域及正式状态隔离。下文的 90 项及旧 CI 结果属于先前提交，不能代表本次提交。

截至该次提交，尚未完成公司/行业/mixed/quant 业务执行器、供应商事实映射与连续正式日更；后续公司/行业核心进展见文末验收记录。暂不合并 main、不关闭 Issue #1。后续按 README 先验收核心业务门禁，再接插件。

日期：2026-09-30。相关 Issue：[#1](https://github.com/le0820/a_share_claw/issues/1)。代码分支：`docs/portable-harness-data-plugins`，PR：[#2](https://github.com/le0820/a_share_claw/pull/2)。

### 验收顺序与上下文缺口收口

`754db41` 统一核心验收 → 五源接线 → 市场 case 的顺序。随后修复核心 trace 的固定 context 清单：记录实际政策读取项，缺 IDENTITY/必需 compiled 文件时保留路由与缺失项并明确归因；Python 3.11/3.12 的相关核心回归各为 31 passed。运行协议、业务执行、报告/发布与状态读取仍待闭环，尚未进入真实插件接入或市场 case。

## 本轮提交

- 顶层设计：`f0d94d3b49eb5eee02563dc23be4c8f300a28316`。
- 五源与 Agent 来源约束：[`bb62572f9bfc58e1231883ea06082c7ed9d1edcb`](https://github.com/le0820/a_share_claw/commit/bb62572f9bfc58e1231883ea06082c7ed9d1edcb)。
- 随后提交锁文件、GitHub Actions 与本交接记录，支持换设备直接复现。

实现与能力边界以 [DATA_PLUGINS.md](DATA_PLUGINS.md) 为准，计划例子在 [examples/data-plan.json](examples/data-plan.json)。来源配置见 [.env.example](.env.example)。不要把本轮文件归档/run_id 当作 Issue #1 的完整 E0 SQLite trace。

## 已执行验证

本地 Python 3.12：`pytest -q` 为 **90 passed, 40 subtests passed**，无跳过。覆盖五源离线合成响应、凭据缺失零网络调用、FRED vintage/window/分页、SEC filed/end 过滤、官网文本日期、TickFlow 三表路径/列式 K 线、未来数据拒绝、域名/跳转限制、脱敏错误、热移除快照、作用域归档、计划追加及六种 Agent 路由。

原有策略、存储、调度、Telegram、pipeline 日期/状态回归也通过。dev extra 补齐数值计算依赖；离线评分测试可在当前解释器执行，无需建立独立 pipeline 环境。

`data plugins`、`data plan examples/data-plan.json` 和帮助入口已实测，不调用模型或 Telegram。`git diff --check`、Python 编译检查通过。

公开官网只读实测：NBS 最新发布目录/正文、PBC 调查统计目录/正文均成功读取。NBS 使用 `PubDate=2026/09/30 09:30`，PBC 发布页含 `2026-09-14`，对应日期解析已纳入测试。官网结果保留 unverified，未升级为可正式评分的数值序列。

未使用真实 TickFlow/FRED 密钥或 SEC 联系身份，也未调用真实模型。三者目前通过文档契约与离线合成 fixture 验证，账户权限、实际样本和生产连通性仍待配置后核对。

## GitHub 中继续开发

```bash
git clone https://github.com/le0820/a_share_claw.git
cd a_share_claw
git switch docs/portable-harness-data-plugins
uv sync --locked --extra dev
uv run --locked pytest -q
uv run --locked python -m a_share_claw data plugins
uv run --locked python -m a_share_claw data plan examples/data-plan.json
```

GitHub Actions 工作流是 `Harness offline tests`，在 push/PR 上执行 Python 3.11、3.12 测试与 CLI smoke，上传 JUnit 结果。远端是否通过应以 PR Checks/Actions 最新运行记录为准；本地通过不代替远端结论。

代码和测试在 GitHub；运行数据、密钥、企业工作区文件没有上传。临时本地检出位于系统临时目录，不是长期开发依赖。

## 核心协议与冻结规划（2026-09-30）

版本化 `RESEARCH_OPERATIONS.md` 已接到配置、Agent 和核心政策快照；部署手册不再作为运行依赖。SDK 缺关键协议时在模型调用前 blocked；同日宏观等待与独立研究切片在协议上分离。核心在证据前归档不可变规划，固定日期/scope/mode、证据能力、报告与停止条件；计划被改动时不能发布。

相关 Python 3.11/3.12 离线回归各为 67 passed / 31 subtests；Python 3.12 全量离线回归为 137 passed / 40 subtests，覆盖真实加载、缺协议零模型调用、计划归档/副本隔离、改计划不晋级，以及原计算/CLI 回放。角色执行、逐字段需求编译、报告发布和状态读取仍待闭环；没有接入真实插件或执行市场 case。

## 公司/行业核心执行与发布门禁（2026-09-30）

公司/行业核心库已接冻结事实需求、角色执行、引用检查和独立语义评估；角色共享 packet/version，必要辩论包含相互回应，风险结论保持 NO_ACTION。core JSON 报告成功归档/校验后才可更新正式状态；所有磁盘中间产物保留 staged。迁移 v2 保留旧正式状态并建立按 scope/workflow/date 查询的历史，CLI state 校验报告路径与哈希。

Python 3.12 全套 156 passed / 40 subtests；Python 3.11/3.12 的新增业务及相关基础回归各 66 passed。角色/评估回调均为合成 fixture，未调用真实模型或数据源。截至该提交尚未闭环 SDK 接线、研究质量验收、mixed/quant 和宏观展望模板；五源真实映射与最终 case 均未开始，PR #2 暂不合并。

## 显式 SDK 执行接线（2026-09-30）

核心 company/industry 执行可绑定配置端点的 SDKResearchAdapter。CLI 显式提供审核 facts/spec 与 --model-executor configured；同步 InvestmentAgent.run_core_result 使用同一核心。每阶段新建无工具/历史/MCP 的 SDK 调用，显式客户端、单 turn、剩余时间与本地 operation trace；不回落全局默认模型。普通 chat 仍仅取证并 blocked，离线 replay 不启动模型。

Python 3.12 全量检查为 164 passed / 40 subtests；Python 3.11 的 SDK/核心研究/宿主/Agent 相关检查为 44 passed / 6 subtests。新增验证通过本地 MockTransport 使用实际 SDK/client，响应仍是合成 fixture；不是实际模型或市场 case。截至该 SDK 提交，普通 chat 规划绑定、真实端点质量、mixed/quant、宏观展望和插件需求映射仍未关闭，继续先收口 A 再进入五源接入。

## 独立价格统计与宏观展望（2026-09-30）

quant-spec-v1 冻结独立标的、频率、锚点、声明交易日/收盘时刻、截止、benchmark 与指标。只支持价格统计，不把它写成策略回测；行情身份/覆盖/可得时点门禁先于计算。新增 outlook 模板消费精确可得时点的宏观事实和核心派生指标，Hong Guan/可选 Jia Zhi/Ping Heng 共用 packet，独立评估与报告后交付 NO_ACTION，不生成缺输入的日评分。CLI 和显式 SDK 接线已覆盖固定合成窗口；NASDAQ Composite 路由误识别也已修复。

Python 3.11/3.12 的相关统计、路由与 Agent 检查各为 28 passed / 32 subtests；固定短窗口与脚本化评估不等于真实 case。普通 chat 自动编译/绑定、mixed 切片、真实质量、Markdown 和五源事实映射仍未完成。实际季度日历、真实指数/数据字段尚未验收；当前合成短窗口不是用户市场 case。先完成 A，再推进 B/C。

## Mixed 独立切片与原始请求评估（2026-09-30）

mixed-spec 冻结 2–8 个必需切片与独立规格，父计划按实际切片合并能力，输入按 slice_id 分配 FactPacket。宏观等待/缺数据/坏事实不压住有效研究；子运行共享 scope/date 和父剩余预算，始终不发布正式状态。父运行保留已评估局部报告的 staged 描述符，全部切片成功及独立整体评估、报告与归档通过后才原子发布 mixed 状态。取消立即停止；父评估或归档失败不会留下子 workflow 的正式状态。

角色和独立评估收到原始 user_request，新增原请求覆盖标准；嵌套规格文本在 trace 中哈希化。CLI --mixed-spec 和显式 SDK/同步宿主已接线；mixed 离线会话重建不支持，replay 明确拒绝，不重新调用模型。

Python 3.11/3.12 全量离线回归各 **194 passed / 41 subtests**，含真正 SDK/client 的本地 MockTransport、CLI 零模型规划/拒绝回放、独立等待、坏宏观不压住研究、父评估/报告/输出失败、取消和超时晚到结果。合成事实和 fixture reviewer 只验证 infra；普通 chat 自动框架/接线、Markdown/其他宿主交付、真实研究质量和五源事实映射仍待闭环。没有执行真实市场 case，PR #2 暂不合并。

## JSON/Markdown 报告和授权交付（2026-09-30）

核心从同一已评估结果生成并验证 JSON 与确定性 Markdown。来源/日期/fallback、事实与核心派生值、角色推断/引用/未知项、统计口径和风险分别展示；模型与来源文本以纯文本转义，角色按研究阶段排列。两份报告都归档成功后才允许发布，正式事务要求六个必需业务硬 evaluator 和两个同 run/scope 的报告描述符。

CLI harness report 与同步宿主 read_core_report 使用相同 scope/日期/成功终态/评估/哈希/归档绑定门禁；按确切 run_id 的正式历史区分 published/research，staged 文件不证明发布。新版 state 也校验双报告；旧 JSON-only 历史不丢弃，不伪造新 Markdown。计算 replay 验证 Markdown 哈希但不把它解析成事实或调用模型。

本地 Python 3.11/3.12 全量检查各 211 passed / 41 subtests；其后新增的实际 CLI 进程读取/跨作用域拒绝/含 Markdown 回放检查双版本各通过 1 项。固定合成事实覆盖七个工作流的双报告往返、阶段顺序、同日精确历史、源/模型格式转义、篡改/越日期/跨主体拒绝、staged 失败拒绝交付、Markdown 渲染/归档失败保护和单权限 evaluator 发布绕过。没有连接真实模型或来源，没有执行市场 case。普通 chat 自动框架/核心绑定、真实研究质量与五源事实映射仍需闭环，PR #2 暂不合并。

## 受约束的模型辅助框架编译（2026-09-30）

核心在取证前编译 framework/parameters/unresolved_constraints，独立评估原请求覆盖后冻结计划。模型不能修改 scope/date/workflow/政策/能力，也不能创造实际仓位、量化窗口、日历或预测范围；缺少可执行规格只保留显式缺口，执行在证据前停止。outlook 派生事实逐字段匹配核心规格，框架评估与研究评估各自归档。普通 trace 对框架和缺口原因仅记哈希。

CLI 显式 plan --model-executor configured、同步 plan_core_result 和 run_core_result 的 compile_framework 选项已接同一核心。实际 SDK 的本地 MockTransport 核对两次规划请求零插件，以及编译→独立框架评估→角色→研究评估五次请求共用一个 run。Python 3.11/3.12 全量离线回归各 **229 passed / 41 subtests**，另含缺约束、拒绝越权/未来/错误评估及超时晚到结果保护。响应和事实仍为合成 fixture；没有调用真实模型或来源。普通 chat 的异步取消/预算/取证桥接、真实规划和研究质量仍未关闭，核心验收未整体完成；随后才是五源映射和真实 case，PR #2 暂不合并。

## 下一步顺序（2026-09-30 校正）

以 [E0_INFRA.md 的验收与未完成边界](E0_INFRA.md#验收与未完成边界) 为准。旧记录中“先配置 TickFlow、最后补 E0”的顺序已失效。

1. 先闭环核心：版本化运行协议与实际上下文记录 → 框架/业务执行 → 报告/发布及状态读取 → 逐项验收证据。已有 CI 结果不代替业务验收。
2. 核心验收通过后，接入 NBS/PBC/TickFlow/FRED/SEC 事实映射；按当前数据/主源授权、单位、披露日、vintage 和覆盖限制准入，不允许原生响应直接提升正式状态。
3. 五源接线后，再执行 8 月 PCE、8 月中国国民经济和三指数三季度 → 四季度展望 case。此前直接下载的原始材料未验收，不计作接入或 case 完成。
4. 连续正式日更、完整上下文/记忆、调度恢复与 E3–E5 留在对应后续工作包；PR #2 和 Issue #1 的关闭条件分别记录，不因一个 case 通过而整体宣告完成。

当前行为变化：Agent 仅有六个插件/规则工具；旧 MCP、网页、自由执行、文件、旧 state/记忆/SDK 工具历史不再进入取证上下文。旧人工 pipeline 仍可维护，但不能作为 Agent 绕过插件的入口。外部宿主若另有浏览器/网络工具，需在宿主侧同步约束。
