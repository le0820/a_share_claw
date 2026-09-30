# 五个来源插件：运行与交接

本页记录已实现的接口和明确限制，供跨设备接续开发。实现位于 `src/a_share_claw/data_plugins/`，不依赖模型 SDK 或 Telegram。

## 来源与能力

| 插件 | 已实现能力 | 配置 | 时点/口径边界 |
| --- | --- | --- | --- |
| `nbs` 国家统计局 | `macro.release_index` 官方目录链接；`macro.release` 官方 HTML 发布正文与表格单元文本 | 无密钥 | 保留发布日期和原文；未建设新版国家数据网站的完整数值序列接口；页面修订历史不明，标记 unverified |
| `pbc` 中国人民银行 | 同上，限定人民银行官网 | 无密钥 | 保留发布原文、单位和日期；PDF/Excel 附件和完整标准化宏观序列尚未解析；不推算缺失数值 |
| `tickflow` | `market.quote`、`market.daily_bars`、`financial.income`、`financial.balance_sheet`、`financial.cash_flow` | `TICKFLOW_API_KEY` | K 线解析列式响应并显式记录复权；三表保留原生字段，不能用期末日期代替披露日；当前快照为 unverified，历史三表请求在披露/vintage 映射完成前直接拒绝，禁止提升为正式输入 |
| `fred` | `macro.series`，在 FRED API 设置 ALFRED 实时区间查询指定 vintage | `FRED_API_KEY` | 同时固定 realtime_start/end；检查观测窗口、返回 vintage 与分页截断；缺失值保留 null；日期级时点验证，不代表盘中可用性 |
| `sec` | `company.facts`，按 CIK 和 taxonomy:concept 请求公司事实 | `SEC_USER_AGENT`（应用名称 + 联系邮箱） | 过滤 filed/end 晚于截止日的事实；保留 accn/form/start/end/unit；不把 YTD 当单季，不累加重复披露；标准 taxonomy/entity-wide 数据，不重建完整报表版式或分部自定义标签 |

`status=ok` 仅代表该能力的取数/时点检查通过，不代表整体投研评估通过。`unverified`、`gap` 都留在缺口报告中。所有插件取证运行的 `official_output_allowed=false`：它们不持有发布权。核心已有独立评分/报告/事务门禁；原生插件结果尚未规范化接入，不能借取数成功自动恢复官方评分或仓位行动。

## 配置与命令

在本机未提交的 `.env` 中设置需要的来源，不必一次配置全部：

```dotenv
ASCLAW_DATA_PROVIDERS=nbs,pbc,tickflow,fred,sec
TICKFLOW_API_KEY=
FRED_API_KEY=
SEC_USER_AGENT=
```

空 `ASCLAW_DATA_PROVIDERS` 表示零来源，仍可规划。密钥和 SEC 联系信息不会出现在清单、请求计划或 provenance。插件传输默认直连，不继承企业设备的代理；只接受声明的 HTTPS 主机，禁止跳转，30 秒连接超时、最多三次尝试、20 MB 响应上限。SEC 单进程最多约五次请求/秒；多进程部署需在宿主额外共享限流。

```bash
uv sync --locked --extra dev
uv run --locked python -m a_share_claw data plugins
uv run --locked python -m a_share_claw data plan examples/data-plan.json
uv run --locked python -m a_share_claw data fetch examples/data-plan.json
```

以上三个命令不调用模型，也不需要 Telegram。`plan` 不联网；`fetch` 只执行计划里的需求。JSON 输出含 run_id、结果、缺口和归档目录。fetch 存在必需缺口或 unverified 时退出码为 2；plan 成功为 0。可选缺口仍展示，不使 fetch 的退出码变为 2。

`examples/data-plan.json` 用于说明参数，日期固定，避免偷偷改变历史研究问题。按实际问题修订计划后再执行；例子里的历史 TickFlow 三表需求会明确返回 historical_unavailable（先配置凭据后才能进入该检查）。无凭据时返回 `not_configured`，绝不转到备用网站。

外部宿主可调用上述 CLI，或使用以下库接口：

```python
from a_share_claw.data_plugins import DataRun, default_registry

registry = default_registry()
run = DataRun(registry.snapshot(), artifact_root, scope="workspace/principal/session/agent")
run.plan(plan_dict)
result = await run.fetch("requirement-id")
summary = run.summary()
```

CLI 是本机个人模式；服务端集成必须由可信宿主构造 scope，不能接受模型自报身份。Codex、Claude Code、Meta Muse、WorkBuddy 的专用桥接仍需分别验收。

## 计划、热插拔与证据

计划必须包含非空 `framework` 和 `requirements` 数组。每项必填 requirement_id/provider/capability/as_of_date，params 为具体能力参数，required 默认为 true。计划先于网络调用；取数接口只接收已登记的 requirement_id。最多 50 项需求，计划可以追加但不能改变/移除旧需求；每次追加保存新版本。发现新发布地址后，扩展计划再抓取正文。

注册表由可信应用代码管理，Agent 没有安装、注册、改配置的工具。`register`/`unregister` 不需要重启核心，新 run 才看见改动；已有 run 固定实现实例与凭据配置，不静默切换来源。所有 HTTP 连接按 fetch 延迟建立，调用结束即关闭。同一次运行重复请求读取内存中已归档结果；跨运行自动缓存/离线重放尚未实现。

归档位置：`data/plugin_runs/<scope-sha256>/<run_id>/`。包括版本化计划、每项结果 JSON、完整源响应 `.raw` 和 summary。源响应 SHA-256、provider/version/schema_version、source_url、retrieved_at 和请求 cutoff 与结果一起保存。模型输出使用合法 JSON 预览，截断时 `truncated=true`，归档保留完整内容。原始归档可能包含截止日之后的源记录，但模型可见的 FRED/SEC 结果经过筛选；归档本身不是直接注入工具。

归档不上传 GitHub；代码、离线合成 fixture、测试与交接记录上传。运行数据、密钥、企业文件均不进入提交。

## Agent 数据边界与兼容变化

普通 chat 的七种路由已接核心框架编译/冻结/缺口门禁，模型调用没有插件、文件、MCP 或执行工具。旧六工具选源/取证循环已移除；五源由独立 data CLI/受信任宿主保留，B 完成核心需求到规范化事实映射后才接入自动取证。

- 不再向 Agent 提供通用网页搜索、任意 URL、MCP、QVeris、Bash、Codex 子工具、任意文件读写、旧取数流水线。
- 即使旧配置启用了 Bash/Codex/MCP，也不能重开这些入口。
- 不注入旧 system_state、长期记忆、任意 skill/plugin 文本和旧 SDK 会话中的工具证据。数据库历史保留；核心规划上下文以当前请求、可信政策和宿主约束为准；研究角色只消费已准入事实。
- 旧 pipeline、ToolRuntime 和 MCP 模块保留供显式人工维护及离线回归，Agent 不持有其执行工具。旧策略的计算和风险权重未改动。
- 当前自动回复是核心结构化规划和缺口；缺事实时不会交付未经评估的模型研究文案。核心事实/角色/报告 evaluator 已实现，但合成检查不证明真实语义质量。用户文字和模型知识不能成为可提升的插件证据。

限制作用于本 Harness 的 Agent 工具入口。外部宿主如果另外给自己的 Agent 开放浏览器/网络/Bash，需要在宿主侧同步限制；本库不能沙箱化宿主全部进程。

## 后续最小修改范围

执行顺序以 [E0_INFRA.md](E0_INFRA.md#验收与未完成边界) 为准：核心验收闭环 → 五源映射/接线 → 真实业务 case。

1. 先补运行协议、实际上下文记录、业务执行器/证据 evaluator、报告发布与状态读取；明确已实现/接线/验收和后续阶段边界。
2. 核心验收通过后，按 compiled 规则完成 NBS/PBC 数值与附件的版本化映射；逐来源保留单位、统计期、发布时间和修订信息。
3. TickFlow 接口保留现有 unverified/历史拒绝门禁，真实样本与披露/PIT 通过后才考虑准入；行情主源遵守当前任务授权，接口存在不代表主源授权。FRED/SEC 同样需完成到核心 FactPacket 的映射和接线。
4. 取数仍只产生证据，发布权属于核心。五源完成后才运行用户指定的 PCE/中国经济/三指数季度展望 case；不足以支持每日评分的材料只生成研究报告与缺口。

## 接口依据

- [TickFlow 文档索引](https://docs.tickflow.org/llms.txt)：K 线与三表 REST 路径、参数和列式行情响应。
- [FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)：observation/realtime 日期与分页。
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)：Company Facts、标准 taxonomy 与公平访问要求。
- [国家统计局最新发布](https://www.stats.gov.cn/sj/zxfb/) 与 [人民银行调查统计](https://www.pbc.gov.cn/diaochatongjisi/116219/index.html)：官方发布页面。
