# 五个来源插件：运行与交接

本页记录已实现的接口和明确限制，供跨设备接续开发。实现位于 `src/a_share_claw/data_plugins/`，不依赖模型 SDK 或 Telegram。

## 来源与能力

| 插件 | 已实现能力 | 配置 | 时点/口径边界 |
| --- | --- | --- | --- |
| `nbs` 国家统计局 | `macro.release_index` 官方目录链接；`macro.release` 官方 HTML 发布正文与表格单元文本 | 无密钥 | 保留明确发布时钟/精度和原文；固定正文指标支持月度/YTD 精确选择；完整数值序列/附件未完成；页面修订历史不明，标记 unverified |
| `pbc` 中国人民银行 | 同上，限定人民银行官网 | 无密钥 | 保留发布原文、单位与明确发布时钟；M2/M1/社融存量同比可精确选择；PDF/Excel 与完整序列未完成；不从累计量推算单月 |
| `tickflow` | `market.quote`、`market.daily_bars`、`financial.income`、`financial.balance_sheet`、`financial.cash_flow` | `TICKFLOW_API_KEY` | K 线解析列式响应并显式记录复权；三表保留原生字段，不能用期末日期代替披露日；当前快照为 unverified，历史三表请求在披露/vintage 映射完成前直接拒绝，禁止提升为正式输入 |
| `fred` | `macro.series` 与 `macro.series_metadata`，在 FRED API 设置 ALFRED 实时区间查询指定 vintage | `FRED_API_KEY` | 同时固定 realtime_start/end；检查观测窗口、返回 vintage 与分页截断；缺失值保留 null；元数据保留来源原生单位/频率/季调；日期级时点验证，不代表盘中可用性 |
| `sec` | `company.facts` 公司事实；`company.filing_metadata` 精确 accession 的 recent filing 元数据 | `SEC_USER_AGENT`（应用名称 + 联系邮箱） | 过滤 filed/end 晚于截止日的事实；保留 accn/form/start/end/unit；不把 YTD 当单季，不累加重复披露；标准 taxonomy/entity-wide 数据，不重建完整报表版式或分部自定义标签 |

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

## B 接入进展：FRED / SEC 精确来源选择

`FRED` 1.1.0 新增 `macro.series_metadata`（params 仅 series_id），独立计划并归档同 vintage 的标题、原生单位、频率、季调和 last_updated。观察值与元数据必须来自同一 series/vintage；不把 last_updated 当作每个观测的原始发布日期。

受信任宿主 `DataRun.select(requirement_id, selector, metadata_requirement_id=...)` 只读取本 run 已计划、已抓取的归档，分别核对结果与原始响应哈希，保存 `selection-<hash>.json`：

- FRED selector 恰为 series_id/observation_date/units/frequency/seasonal_adjustment；metadata_requirement_id 必填。精确选一天，不按“最新值”回退，不重标单位、不补 null。
- SEC selector 恰为 cik/concept/unit/period_start/period_end/filed/accession；period_start=null 表示时点项。保留原生 duration 和 accession，拒绝单季代替 YTD、单位缩放猜测与重复披露冲突。可选 metadata_requirement_id 只绑定本 run 独立计划的 company.filing_metadata，不能提供任意元数据对象。

选择产物为 `source-selection-v1`，不是核心 FactPacket。`available_at=null` 并明确 vintage/filed 日期级精度，`core_admission_complete=false`、`official_output_allowed=false`。仍须完成精确发布时点、核心字段/单位映射及同 run 宿主接线；不能把来源选择通过写成已恢复评分。FRED/SEC 的 unverified 来源不通过该入口；NBS/PBC 下述正文候选选择保持 unverified，附件映射与行情身份/日历仍未完成，不暗用网页或其他供应商补数。

本轮只使用离线来源 fixture 验证上述新增实现；本机 FRED_API_KEY、SEC_USER_AGENT、TICKFLOW_API_KEY 尚未配置，真实接口验收未完成。完整业务 case 留在五源接入之后。

## B 接入进展：NBS / PBC 原生正文候选

NBS/PBC 1.1.0 保留明确的发布时钟和精度：官网无时区的时钟按中国当地时间解释，显式时区转换为 Asia/Shanghai；元数据与日期矛盾直接拒绝。只有日期时 available_at=null，不制造午夜。

`DataRun.select` 的 macro.release selector 恰为 metric/year/month/period_kind，period_kind 为 month 或 year_to_date；不能传元数据需求。固定 `official-macro-prose-v1` 从本 run 的哈希绑定正文选择，不接受模型给正则或映射：

- NBS：工业增加值同比、服务业生产指数同比、社零同比（月度或累计）；固定资产投资同比仅累计；全国城镇调查失业率、CPI、核心 CPI、PPI 仅月度。
- PBC：M2/M1 同比、社融存量同比仅月末存量。保留口径/修订备注；不把累计社融增量变成单月，不混旧年度 M1 对照。
- 原生 percent、统计起止、固定报告标题/表头、精确指标表达必须匹配；缺指标、单位不同、重复数值冲突直接返回缺口，不选“最近值”。发布晚于截止或时钟晚于抓取也拒绝。

选择产物的 observation.eligibility **仍为 unverified**，缺口保留，core_admission_complete/official_output_allowed 均为 false。正文提取不证明页面修订 vintage，也不生成核心评分、阈值或风险行动。此范围是来源候选映射，不是完整五源接线或真实接口验收；PDF/Excel、完整序列、更多指标、核心 FactPacket 与来源 vintage 验证尚未完成。本轮仍只用合成发布页验证实现，真实市场 case 未启动。

## B 接入进展：SEC 披露身份元数据

SEC 1.1.0 新增 `company.filing_metadata`，params 恰为 cik/accession，限定同一 SEC provider 的 `/submissions/CIK##########.json`。只从 recent 原生列中选一次确切 accession，保留 filed/report_date/form/primary_document 和原始 acceptanceDateTime；未知、重复、缺列、无时区或未来时钟明确报缺口。filing-agent accession 前缀不要求等于公司 CIK。早期文件不自动下载，缺 accession 不换最新披露。

`DataRun.select` 可同时绑定公司事实与元数据归档，核对 CIK/accession/form/filed/cutoff/原始哈希；report_date 是整份报表期末，不能替换比较期事实的原生 start/end。来源被标记 verified 的范围只是接口身份/日期检查，不包括公开可得时点：acceptance 原始字符串和 source-declared offset 分别保留，**不把 acceptance 当 public dissemination，也不自行校正源时区**。available_at=null、public_dissemination_certified=false，core_admission_complete/official_output_allowed 均为 false。

本轮仅有合成列式响应验证：实际 SEC 配置仍缺失，浏览工具也无法读取该 JSON；未确认账户样本及所有历史形态。公开传播/修订验证、核心字段映射与同 run 宿主接线仍待补齐，不能以元数据能力完成宣告真实接口或五源准入完成。依据 [SEC Submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) 与 [PDS 技术规范](https://www.sec.gov/info/edgar/specifications/pds-dissemination-spec022315.pdf) 保留接收、发布与已抓取的区别；不暗增 PDS 订阅或其他来源。

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

1. E0 最小核心验收已闭环，证据与范围见 E0_INFRA.md；一般语义质量、连续日更和 E1–E5 仍按后续边界推进。
2. B 已有 FRED/SEC 精确来源选择及 NBS/PBC 固定正文候选；继续补核心字段/单位、精确时点/vintage 和同 run 接线。附件/完整序列按实际需求补，不冒称已覆盖。
3. TickFlow 接口保留现有 unverified/历史拒绝门禁，真实样本与披露/PIT 通过后才考虑准入；行情主源遵守当前任务授权，接口存在不代表主源授权。FRED/SEC 同样需完成到核心 FactPacket 的映射和接线。
4. 取数仍只产生证据，发布权属于核心。五源完成后才运行用户指定的 PCE/中国经济/三指数季度展望 case；不足以支持每日评分的材料只生成研究报告与缺口。

## 接口依据

- [TickFlow 文档索引](https://docs.tickflow.org/llms.txt)：K 线与三表 REST 路径、参数和列式行情响应。
- [FRED series metadata](https://fred.stlouisfed.org/docs/api/fred/series.html)：原生单位、频率、季调及 series last_updated。
- [FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)：observation/realtime 日期与分页。
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)：Company Facts、标准 taxonomy 与公平访问要求。
- [国家统计局最新发布](https://www.stats.gov.cn/sj/zxfb/) 与 [人民银行调查统计](https://www.pbc.gov.cn/diaochatongjisi/116219/index.html)：官方发布页面。
