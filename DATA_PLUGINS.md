# 五个主来源插件：运行与交接

当前阶段与退出条件见 [E0_INFRA.md](E0_INFRA.md#当前验收结论与阶段入口)。下方分阶段记录保留当时证据，不表示旧缺口仍然全部存在；五源真实验收未全部完成，整合业务 case 尚未启动。

本页记录已实现的接口和明确限制，供跨设备接续开发。实现位于 `src/a_share_claw/data_plugins/`，不依赖模型 SDK 或 Telegram。

## 来源与能力

| 插件 | 已实现能力 | 配置 | 时点/口径边界 |
| --- | --- | --- | --- |
| `nbs` 国家统计局 | `macro.release_index` 目录；`macro.release` 历史候选；`macro.release_snapshot` 当前官网版本 | 无密钥 | 保留明确发布时钟/精度和原文；固定正文指标支持月度/YTD 精确选择；完整数值序列/附件未完成；页面修订历史不明，标记 unverified |
| `pbc` 中国人民银行 | 同上，限定人民银行官网 | 无密钥 | 保留发布原文、单位与明确发布时钟；M2/M1/社融存量同比可精确选择；PDF/Excel 与完整序列未完成；不从累计量推算单月 |
| `easytdx` | `market.index_catalog` 国际指数目录；`market.index_daily_snapshot` 原生指数日线当前版本 | 可选 market extra 锁定 easy-tdx 1.20.4 | 固定指数身份、无复权、原生点位；SDK 在隔离子进程取数，只连声明主机；核心复核冻结日历/窗口；不是历史 PIT 认证 |
| `tickflow`（辅助、默认禁用） | `market.quote`、`market.daily_bars`、`financial.income`、`financial.balance_sheet`、`financial.cash_flow` | `TICKFLOW_API_KEY` | K 线解析列式响应并显式记录复权；三表保留原生字段，不能用期末日期代替披露日；当前快照为 unverified，历史三表请求在披露/vintage 映射完成前直接拒绝，禁止提升为正式输入 |
| `fred` | `macro.series` / `macro.series_metadata` 历史查询；`macro.series_snapshot` / `macro.series_metadata_snapshot` 当前捕获 | `FRED_API_KEY` | 同时固定 realtime_start/end；检查观测窗口、返回 vintage 与分页截断；缺失值保留 null；元数据保留来源原生单位/频率/季调；日期级时点验证，不代表盘中可用性 |
| `sec` | `company.facts` / `company.filing_metadata` 历史选择；`company.facts_snapshot` / `company.filing_metadata_snapshot` 当前配对快照 | `SEC_USER_AGENT`（应用名称 + 联系邮箱） | 过滤 filed/end 晚于截止日的事实；保留 accn/form/start/end/unit；不把 YTD 当单季，不累加重复披露；标准 taxonomy/entity-wide 数据，不重建完整报表版式或分部自定义标签 |

`status=ok` 仅代表该能力的取数/时点检查通过，不代表整体投研评估通过。`unverified`、`gap` 都留在缺口报告中。所有插件取证运行的 `official_output_allowed=false`：它们不持有发布权。核心已有独立评分/报告/事务门禁；NBS/PBC 当前快照、FRED PCE 原生指数和 easy-tdx 指数日线已有明确宿主研究交接；SEC 当前原生公司事实亦已显式交接；显式可信宿主的缺失能力取证已接线；普通 chat 默认绑定及日评分字段尚未接线，不能借取数成功恢复官方评分或仓位行动。

## 配置与命令

在本机未提交的 `.env` 中设置需要的来源，不必一次配置全部：

```dotenv
ASCLAW_DATA_PROVIDERS=nbs,pbc,easytdx,fred,sec
TICKFLOW_API_KEY=
FRED_API_KEY=
SEC_USER_AGENT=
```

空 `ASCLAW_DATA_PROVIDERS` 表示零来源，仍可规划。密钥和 SEC 联系信息不会出现在清单、请求计划或 provenance。HTTP 插件传输默认直连，不继承企业设备的代理；只接受声明的 HTTPS 主机，禁止跳转，30 秒连接超时、最多三次尝试、20 MB 响应上限。easy-tdx 另走明确固定的 TCP 行情协议，见下述隔离边界；不把 TCP 取数伪装为 HTTPS 原文。SEC 单进程最多约五次请求/秒；多进程部署需在宿主额外共享限流。

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

历史查询选择产物为 `source-selection-v1`，不是核心 FactPacket。`available_at=null` 并明确 vintage/filed 日期级精度，`core_admission_complete=false`、`official_output_allowed=false`。仍须完成精确发布时点、核心字段/单位映射及同 run 宿主接线；不能把来源选择通过写成已恢复评分。FRED/SEC 的 unverified 来源不通过该入口；NBS/PBC 下述正文候选选择保持 unverified，附件映射与行情身份/日历仍未完成，不暗用网页或其他供应商补数。

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

## B 接入进展：当前官网快照到核心展望

NBS/PBC 1.2.0 新增 `macro.release_snapshot`（params 仍为 url），只接受抓取当天的中国日期。它证明本次 HTTPS 响应看到的版本，available_at 使用归档 retrieved_at；原始 publication_date 和明确 publisher_available_at 单独保留。历史页面修订仍不明，historical_vintage_certified=false。旧 macro.release 继续 unverified，不从旧运行或旧候选提升资格；跨日捕获/历史 cutoff 拒绝。

显式可信宿主用四字段 Scope 构造 DataRun，取证前调用 `plan_core_outlook(plan, outlook_spec, bindings)` 冻结来源与核心需求。NBS/PBC bindings 恰为 fact_id/requirement_id/selector；FRED 快照另需 metadata_requirement_id；CN、原生指标、percent、月度/YTD 起止须匹配全部非价格必需事实。核心价格派生项不由插件填充。核心规格哈希、source/core contract 和 raw/result/selection 各自绑定，不给模型改 source/单位/日期的工具。

抓取后 `core_macro_evidence()` 产生 macro_release_facts 能力的 envelope，可由宿主与独立 price_history 一并交给核心。macro-release-facts-v2 / research-facts-v2 保留 availability 元数据、来源口径备注及两个时钟；核心再次核对 scope、冻结研究规格、capture/cutoff、原始发布日期和版本边界。来源归档仍不持发布权，核心评估/双报告/事务决定交付。当前快照不证明较早日期的页面版本，也不能写入抓取前的 frozen cutoff。计划可以声明未来 cutoff，但执行必须等该时点结束，不能以未来规格通过计算。

JSON/Markdown 和角色 packet 均保留当前版本口径；旧 v1 发布时钟契约保持不变。新增合成端到端检查从来源计划/原始页走到核心统计、角色、评估和双报告，保持 NO_ACTION；两版回归各 324 passed / 41 subtests。真实来源样本验收以本机 data/harness_acceptance 的原始响应/哈希/归档为准；不能用上述合成成功宣告所有官网形态、五源自动取证或真实季度市场 case 已完成。

显式宿主接线及 SEC 映射已实现；普通 chat 默认绑定、实际行情覆盖/真实业务链路及五源真实接口验收继续待补。该范围支持当前捕获的研究事实，不恢复历史正式日评分，也未执行用户市场 case。

## B 接入进展：FRED PCE 原生指数到核心计算

FRED 1.2.0 新增两个显式当前捕获能力，保留旧历史查询的日期级边界。`macro.series_snapshot` 的 params 为 series_id/start_date/vintage_date，可选 end_date/limit；`macro.series_metadata_snapshot` 为 series_id/vintage_date。as_of_date 是当天中国日期，vintage_date 则由宿主明确指定当天 Chicago 日期。Chicago 是本适配器的来源日期政策；不是 per-observation 发布时钟，不暗自换 vintage。两个捕获实际跨来源午夜/中国日期时直接报缺口。

元数据独立计划并归档，保留原生 title/units/frequency/seasonal_adjustment/last_updated/notes。精确选择使用同 run、同 series/vintage 的两个归档；available_at 取两个实际 retrieved_at 的较晚者。当前捕获资格不证明较早盘中的观测可得性，也不证明原始发布日期。`publication_date=null`、发布精度 unknown，series last_updated 不替代观测的 release date；历史 macro.series 仍 available_at=null，不进入这条当前交接。

可信宿主的 `plan_core_outlook` 现在可绑定 PCEPI/PCEPILFE 原生月度指数，FRED binding 必须提供 planned metadata_requirement_id。固定核心映射要求 US、Index 2017=100、Monthly、Seasonally Adjusted、精确原生标题和每月第一天的 observation_date；标题/基期/季调变动返回缺口，不能把名义消费、季度序列或 percent 重标为价格指数。原生数据没有服务端变换。

核心 `harness/macro_derivation.py` 持有版本化 PCE 计算：pce_mom/pce_yoy/core_pce_mom/core_pce_yoy 使用 `(current / comparison - 1) * 100`，不年化、不先四舍五入。research_spec 在抓取前必须同时声明目标月和上月/去年同月的精确 native_requirement；native fact_id 固定为 `fred.<series_id>.<YYYY-MM-01>`。比较月缺失、null、非正数、不同 vintage/run、错误单位/标题均阻断，不做最新值替代。插件不能绑定/填写核心计算项；角色只能消费核心已重算的目录。

macro_metrics 独立归档保留公式、冻结输入契约、原始事实与依赖哈希，派生事实引用该归档；角色与 JSON/Markdown 保留原生值、计算引用、未知发布日期、当前版本说明。汇总 envelope 的 publication_date 明确标记 aggregate_snapshot_capture_date_not_original_release，不冒充个别观测发布日期。计算结果是同版本指数计算，可能与发布机构按精度处理后的 headline percent 不同。

该范围是显式宿主研究交接；普通 chat 自动取证、历史 intraday/PIT、其他 FRED 指标、日评分字段和真实账户接口仍未完成。当前只以合成 JSON/时钟/价格/角色验证新增实现，未执行 8 月 PCE/三指数真实季度 case。五源与行情来源政策未闭环前继续等待完整业务 case。

## B 接入进展：主行情指数到核心价格统计

按用户最新 AGENTS.md，默认主源五项现在为 NBS/PBC/easytdx/FRED/SEC。TickFlow 保留在注册表中，只有显式 ASCLAW_DATA_PROVIDERS=tickflow 才加载，不能进入 primary price handoff；没有暗用六个源完成同一研究问题。Agent 仍无取数/安装/改配置工具。

`easytdx` 1.0.0 只提供本次捕获的指数事实：market.index_catalog（空 params）用于国际指数原生目录，保持 unverified/discovery；market.index_daily_snapshot params 恰为 symbol/provider_code/start_date/end_date/count，count 1..600。as_of_date 必须为捕获当天中国日期，历史请求直接拒绝。固定语义身份为 399006.SZ / 创业板指、000688.SH / 科创50、COMP.NASDAQ / NASDAQ Composite；unit=index_points、adjustment=none，币种/市场时区明确。provider_code 必须显式指定，国际代码先查目录再形成需求；QQQ/NDX、含糊名称、其他指数和重复/非法 OHLC 不准入。

独立接口验收已从当前原生目录发现 market=12、A_IXIC / 纳斯达克综合，另有 A_NDX / 纳斯达克100；不将后者当综合指数。原先短窗口身份/字段验收保留；后续 c1b4ab9 实现基线的独立实际验收已取得完整 Q3 与锚点，逐日匹配审核日历并准备核心价格交接，范围见 E0_INFRA.md；尚未执行整合季度展望。

SDK 延迟在隔离子进程导入，固定 easy-tdx==1.20.4，父进程不导入/运行供应商评分或交易策略。子进程不继承 API 凭据、代理或 SDK 主机覆盖，EASY_TDX_CONFIG_DIR 使用临时目录，不改用户全局配置；只连接固定 MAC/MAC_EX 主机，禁 SDK 自动重连/任意选源，60 秒总预算、20 MB 解码输出上限。raw 格式明确为 decoded_sdk_response，不冒称已保存原始 TCP wire bytes。宿主须在运行插件的解释器安装该固定 SDK；缺少依赖返回 not_configured，不换版本/源。可选 market extra 固定官方 PyPI CDN wheel 与 SHA256，uv.lock 同时固定兼容 pandas 2.3.3；普通核心安装不导入 SDK。包索引/仓库直连 404 和初次构建失败保留在验收记录，不能用浏览缓存宣告安装成功。本机干净 Python 3.11/3.12 安装及隔离导入已通过；CI 亦安装该 extra 并验证导入，其他宿主仍须实际验收。

受信任宿主在抓取前调用 plan_core_quant(plan, quant_spec, bindings)，bindings 恰为 symbol/requirement_id；或在 plan_core_outlook 中附 price_bindings，使宏观与行情同 run 冻结。源需求必须覆盖 anchor、窗口和全部声明会话，identity/unit/currency/adjustment/timezone 必须匹配。core_price_evidence 复核 frozen contract、raw/result 哈希，拒绝缺交易日、重复日期或市场尚未收盘。供应商不返回统计分数，收益率/波动/回撤仍由核心计算。

price-series-v2 保留 current_snapshot 与原生身份，每行 available_at 使用实际归档捕获时间，publication_date 明确是快照日期；不制造历史收盘的原始可得时点或复权 vintage。核心重新核对 scope、quant_spec_hash、capture/cutoff、完整冻结日历和类型，JSON/Markdown 记录当前版本限制。price-series-v1 的旧契约不变；当前研究保持 NO_ACTION、没有来源发布权。

已实现后再以合成 SDK JSON/时钟/价格/角色检查三指数到核心统计和双报告，以及同 run NBS/PBC+价格到展望。实际短窗口只证明当前接口/身份/字段，不证明历史 PIT、官方日历、跨源数值一致性或完整季度 case。完整业务仍等 FRED/SEC 实际配置/接口验收及自动取证闭环。

## 宿主日历与行情依赖准备

安装固定行情适配依赖，随后保持同一已同步环境：

```bash
uv sync --locked --extra dev --extra market
uv run --no-sync python -m a_share_claw data plugins
```

market 为可选依赖；固定官方 CDN 原件与发布哈希，不能改用未核对的镜像或当前最新 SDK。SDK 自带的评分/策略仍不进入核心。

可信宿主可调用 `harness.calendar.session_calendar(rules_file, window_start, window_end, reference=aware_datetime)` 准备 quant-spec-v1 的 calendar_source/market_timezone/anchor/sessions。exchange-calendar-rules-v1 恰含 schema_version、exchange、market_timezone、coverage_start/end、regular_close（HH:MM）、closed_dates、special_closes（日期→提前收盘 HH:MM）、source_documents、review_status=host_reviewed、reviewed_at。每份 source_documents 恰含 url、同目录相对 source_file、sha256、nullable publication_date、retrieved_at；原始文件哈希、审核/捕获时钟、覆盖和相对路径均核对。

工具只按已审核的周一至周五/明确休市/提前收盘规则构造会话，用市场 ZoneInfo 保留 DST 和最后一个实际前期收盘锚点；不足覆盖、零会话或档案变更直接拒绝。将返回字段合并入宿主 asset，再交核心冻结；不增加第六个行情插件、不让模型选择假日或访问文件。calendar_source 保留规则哈希、档案路径与来源 URL/raw 哈希，原规格及报告沿用该字段。

哈希只证明档案完整性，host_reviewed 不认证来源真实性、历史 PIT 或意外停市。宿主仍须审核完整年度/区间公告和后续临时通知，不能只列工作日或从返回行情反推应有日历。本次归档的 2026 Q3 官方计划会话仅是宿主约束准备，未取季度价格、未执行业务 case；实际 bar 覆盖仍须在取数交接时逐日验收。

## B 接入进展：SEC 当前快照到核心原生研究事实

SEC 1.2.0 增加 `company.facts_snapshot` / `company.filing_metadata_snapshot`，参数分别复用 facts/metadata。只接受抓取当天中国日期；两份原生响应须同 run、同 CIK/accession/form/filed，capture 取较晚 retrieved_at。filed 保留来源日期，acceptance 原字符串/声明时区保持，publisher_available_at=null、public_dissemination_certified=false；不将接收时钟当公开传播时间，不证明抓取前修订/PIT。旧 company.facts/filing_metadata 选择仍 available_at=null，不提升历史归档。

可信宿主用 `plan_core_research(plan, research_spec, bindings, cutoff_timestamp=..., workflow="company")`（或 industry）在取数前冻结四字段 Scope 和全部事实；`core_research_evidence()` 输出 research-facts-v2 / primary_documents。bindings 恰为 fact_id/requirement_id/metadata_requirement_id/selector，metadata 必须独立计划。目标 entity 恰为 CIK+十位编号，metric 为原生 taxonomy:concept，unit 保留原生单位，data_period 是明确 start/end（时点项只用 end）；不猜 ticker、缩放、YTD 减法或同比。`harness.sec_facts.native_requirement(selector, fact_id)` 构造相同核心契约。

plan_core_outlook 亦支持该 SEC binding，与 NBS/PBC/FRED/easy-tdx 可共用一个冻结 source run。核心重新核对研究规格、scope、捕获/归档时钟、原生披露字段与单位/统计期；文件/结果/契约哈希在 handoff 重核。双报告及角色 packet 保留 sec_filing，Markdown 明确接收/传播区别。来源 select 的 core_admission_complete=false 保持；实际业务 evaluator 和报告门禁负责准入，不因 handoff 取得发布权。

本项实施后用合成原生 JSON/时钟/角色验证公司交付、比较期、时点项、SEC+宏观展望，以及五源同 run→核心 PCE/价格计算→研究双报告。定向检查暴露的捕获时钟不一致缺口已修复，原失败保留。未调用真实 SEC/FRED 接口或模型，也不是季度业务 case。真实配置、跨设备 SDK 安装、宿主自动取证和完整日历/季度覆盖仍待闭环。

## B 接入进展：可信宿主冻结需求自动取证

`data_plugins.adapter.PluginEvidenceAdapter(artifact_root, configuration)` 由可信宿主显式注入 `Harness.run/run_async(..., evidence_adapter=...)`，或 InvestmentAgent 的 run_core_result/run_core_result_async。普通 run_result/chat 不接受该参数，也不开放 source/MCP/Bash/file 工具。configuration 恰含 source_plan、bindings、price_bindings、cutoff_timestamp；使用上述精确原生 bindings，source_plan 不得夹带未绑定需求，已有能力的 source requirements 会跳过。

核心先加载政策、编译/独立审核框架、冻结 plan，再只读准入已有 packet；已有能力还须通过同一事实身份/完整覆盖检查。不完整或错误的已有能力直接阻断，不能静默替换；完全已具备时零来源调用。缺失能力才生成一个 scope/plan_id/core_run_id 绑定的 source batch，registry/config 快照只实例化所需且显式启用的来源。未启用与缺凭据分别保留 provider_unavailable/not_configured，不转其他网站。当前仅支持 research 的 company/industry/quant/outlook；replay/official/mixed/日评分的自动来源映射未开放。

每次源请求使用核心预算与统一 ToolResult，工具 trace 只保存归档来源和事实哈希，不复制原生数值/正文。源侧保存原始/result/contract/host-core-link；核心仍拥有同一个编译→取证→计算→角色→评估→双报告 run_id，源 run_id 通过显式 link 关联。整个 batch 超出剩余调用数时取数前停止。源 gap/unverified/truncated 阻断 handoff；即使各 fetch 成功，缺日历会话/单位/时点也不能交付研究报告。

没有显式 host clock 时，核心在取证后更新实际 evaluation_clock；固定 host clock 永不放宽。quant/outlook 的截止时钟仍须在取数前冻结：可设一个剩余运行预算内的近未来边界，抓取完成后在该边界前只等待，不再扩大取证范围；超过预算返回 WAIT_FOR_CUTOFF、零源调用，抓取晚于边界则失败。不会用运行起点错误拒绝随后实际发生的捕获，也不回填历史可得时点。

取消/超时终止核心，晚到回调不能继续后续需求、进入角色或发布。正在执行的供应商 I/O 可能在原 scope/source run 留下迟到取证文件；这不是核心可交付 artifact 或正式状态，库级取消不等于强杀外部 SDK/网络进程。

显式 CLI 入口（不需要人工拼接已取到的 packet）：

```bash
uv run --locked python -m a_share_claw harness run --workflow outlook --date YYYY-MM-DD \
  --mode research --outlook-spec HOST_SPEC.json --source-contract HOST_SOURCE_CONTRACT.json \
  --model-executor configured
```

可同时给已有 packet_file，只补缺失能力。HOST_SOURCE_CONTRACT 必须由可信宿主按原生身份准备；用户文字或模型输出不能直接变成来源 URL/计划。没有模型 executor 的 quant 路径仍可独立计算。完整自动 URL/披露发现、普通 chat 默认业务绑定、所有宿主部署尚未验收。

本项实现后再验证合成五源/模型回调、实际 CLI 的零插件缺口、来源加载范围、预算/取消、核心报告和真实时钟经过取证的顺序。未联网、未调用实际模型、不是完整季度 case；FRED/SEC 实际接口、跨设备 SDK 安装、官方会话日历和季度覆盖继续待闭环。

## Agent 数据边界与兼容变化

普通 chat 的七种路由已接核心框架编译/冻结/缺口门禁，模型调用没有插件、文件、MCP 或执行工具。旧六工具选源/取证循环已移除；五源由独立 data CLI/显式可信宿主保留；上述绑定式自动取证已接线，普通 chat 默认请求仍只编译框架和报告缺口。

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
3. easy-tdx 主源已按最新授权统一；指数接口、明确日历、实际完整 Q3 覆盖与显式核心价格交接已验收，端到端实际业务运行仍未验收。TickFlow 仅为默认禁用的辅助接口，保持 unverified/历史拒绝门禁。FRED PCE 当前研究映射已接线，实际接口仍待凭据验收；SEC 当前原生映射已接线，真实接口仍待验收。
4. 取数仍只产生证据，发布权属于核心。五源完成后才运行用户指定的 PCE/中国经济/三指数季度展望 case；不足以支持每日评分的材料只生成研究报告与缺口。

## 接口依据

- [easy-tdx 固定 SDK](https://pypi.org/project/easy-tdx/1.20.4/) 与 [源码](https://github.com/handsomejustin/easy_tdx)：MAC/MAC_EX 原生身份/日线；本机安装与实际接口单独验收。
- [Nasdaq COMP 身份](https://indexes.nasdaq.com/Index/Overview/COMP)、[深交所创业板指代码](https://investor.szse.cn/video/t20100707_538280.html)、[上交所科创50代码](https://star.sse.com.cn/aboutus/research/report/c/10056573/files/ac9c082e4dbb44ca8bf2e1045c6ff704.pdf)：固定指数含义；不当作行情备用源。
- [TickFlow 文档索引](https://docs.tickflow.org/llms.txt)：K 线与三表 REST 路径、参数和列式行情响应。
- [FRED series metadata](https://fred.stlouisfed.org/docs/api/fred/series.html)：原生单位、频率、季调及 series last_updated。
- [FRED PCEPI](https://fred.stlouisfed.org/series/PCEPI) / [PCEPILFE](https://fred.stlouisfed.org/series/PCEPILFE)：固定原生月度价格指数身份；实际 API 样本仍待凭据。
- [FRED observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)：observation/realtime 日期与分页。
- [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)：Company Facts、标准 taxonomy 与公平访问要求。
- [国家统计局最新发布](https://www.stats.gov.cn/sj/zxfb/) 与 [人民银行调查统计](https://www.pbc.gov.cn/diaochatongjisi/116219/index.html)：官方发布页面。
