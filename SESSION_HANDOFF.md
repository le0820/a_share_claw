# 开发交接：E0、五源与月度历史 case

记录日期：2026-10-01。此文件是下一会话的定向交接入口；当前实现以实际 Git HEAD、README 与代码为准，历史验收按以下锚点读取。不要把本纪要当作新业务输入事实。

## 分支与继续工作

- `main`：完成开发、来源验收和业务 case 后的合并线。PR #2 已于 2026-10-01 合并，提交 `e06ffb2c15d3ac35c8907b993ea22c0b94a2bc90`。
- `docs/portable-harness-data-plugins`：用户指定的原 PR #2 开发分支；`develop` 为同内容的同步镜像。只在原开发分支提交，再同步 develop，避免两条开发线各自产生不同修改。
- 本机主 checkout：`/Users/yifanshen/a_share_claw`（main）。开发 worktree：`/Users/yifanshen/.codex/worktrees/e0-infrastructure/a_share_claw`（原 PR #2 分支）。路径可能随机器变化，先运行 `git worktree list`。
- 合并后两条开发引用快进至 main；新开发前先核查 `git status --short`、`git branch -vv`、`git log -1`。不要强推、全局 clean 或覆盖下一会话的未提交工作。
- 此次明确撤销 main 旧的 README/IDENTITY 修改及未提交的 CODEX_BASELINE_TEAM/DEVELOPMENT_PLAN/EVALUATION_HARNESS 文档。`.env`、本机数据和研究报告保留；不能把未提交旧文档作为实现依据。

## 已接受的设计决定

1. infra 先负责生命周期、冻结契约、计算、风控、评估、归档与正式发布。开发顺序：验收与未完成边界 → 按需求接入五源 → 整合业务 case。
2. 默认事实源是 NBS/PBC/easy-tdx/BEA/SEC；FRED 显式可选、TickFlow 默认禁用。BEA 公开发布与历史表替代本次 PCE 的 FRED 路径；SEC 公开读取所需联系标识已在本机环境配置，不需要把联系值或密钥写入仓库。
3. 模型没有工具、MCP 或 handoff。显式可信宿主使用 PluginEvidenceAdapter 按冻结规格取证；普通 chat 当前只完成框架/缺口，自动来源绑定待完成。外部宿主还须约束自身工具权限。
4. 月度新发布与历史事实一起分析。`research_spec.monthly_history` 每组 2–13 个连续月份，绑定指标、实体、单位和 basis；声明历史规格时须覆盖其要求的月度事实，并在基准情景/监控中引用。
5. 同比差值是百分点变化，不是环比；累计同比不是单月增速。不同发布版本仅比较公布值，不能认证修订统一的历史趋势。两个相邻月份不能认证持续趋势；旧单期规格只能交付单期观察。
6. 来源成功不等于核心证据准入，独立模型审查通过不等于一般文案准确。research/replay 保持 NO_ACTION/official_output_allowed=false；正式模式只有核心全部门禁与状态事务可授权。
7. L2 disabled/null，Composite = L1 × 4/7 + L3 × 3/7；AI 为独立叠加层，不替代 L2。缺必需事实不能生成中性分数。传统个人正式状态仍停于 20260713，未恢复连续正式日更。

## 已交付与验收范围

| 范围 | 实现与验收 | 未完成边界 |
| --- | --- | --- |
| E0 最小五项 | 核心 FailureCategory/RunStatus/EvalResult/ToolResult；Agent run_id；route/context/state scope；事务 migration/授权 trace repository；CLI trace 摘要/full/list | 完整 Issue #1 保持 open；旧 MCP/ToolRuntime 全量迁移、完整 ContextManifest/记忆、自提升控制面未完成 |
| 核心业务 | 冻结计划、确定性宏观/AI、研究角色、quant/outlook/mixed、取消与预算、双格式报告、授权读取、正式事务门禁 | 合成/限定真实验收不是全部业务认证；无回测或交易执行 |
| 五源 | 本次能力的官方响应、精确字段/期间/单位、哈希与核心交接；SEC 指定概念配对，三指数 Q3 全覆盖 | 不等于所有概念/标的覆盖、历史 PIT 或五源直接填满正式日评分 |
| 月度历史 | 30 条原生宏观事实、15 组 7/8 月对照，核心计算和报告引用 | 自动月更、跨运行缓存重验、修订一致长历史尚未完成 |
| 最终 Q4 case | 原始 8 次来源调用；最终同 Scope 事实复用，0 次新来源请求、1 次 Tencent hy3 独立 SDK 审查；宿主逐条修订复核后定稿 | SDK 曾误放行因果越界和自造阈值；无人复核文案未验收。无目标点位、概率、日评分或交易建议 |

代码锚点：`20403b57b53a8736d80809779bbd3d9ef819e5ba`；PR #2 最终提交：`ba661ddaf2fbf8b5ce0f924f47719789e10563d9`。最终 [CI 36831251887](https://github.com/le0820/a_share_claw/actions/runs/36831251887) Python 3.11/3.12 各 479 passed / 41 subtests，locked 安装、CLI、JUnit 均通过。CI 是无模型/来源凭据的离线检查；真实取证与人工文案验收另存本机。后续提交的 CI 应单独核实，不继承这些计数作为新验收。

## 本机证据索引（默认忽略，不在 GitHub 代码仓库）

按实际问题只读所需 acceptance 或审查记录；不要启动时递归加载原始响应或完整 trace。

- `data/harness_acceptance/core_acceptance_3b7cb95/acceptance.json`：E0 最小锚点。
- `data/harness_acceptance/quarter_sources_c1b4ab9/acceptance.json`：NBS/PBC 与三指数 Q3 来源/覆盖。
- `data/harness_acceptance/bea_sec_frozen_handoff_20261001_verified/acceptance.json`：BEA/SEC 冻结核心交接。
- `data/harness_acceptance/monthly_history_sources_20261001_v2/acceptance.json`：7/8 月宏观来源与历史对照。
- `data/harness_acceptance/quarter_business_case_with_history_20261001/final-case-boundary.json`：最终 case 与 PR #2 的验收锚点；其中 local_main_checkout_preserved 是当时操作记录，已被此次显式撤销旧修改的指令取代。
- 同目录 `review_revision4/acceptance.json`、`manual-review.json`、`statistics-independent-check.json`：最终验收与独立数值复核。此前失败/被拒版本保留用于问题追踪，不能交付为最终报告。
- 可读报告：`data/research/output/q4_market_outlook_2026_asof_20261001.md`；SHA256 `7d82711e114bc99d6c1ce896054518eb12fa40e99c8a394edcc7ffccf91efe17`。
- 最终 run_id：`208a8eaacdf74077ad34be2e2e901016`；实际 Scope 与规格在该目录 frozen-host-contract.json 中。不要改用默认 chat Scope 读取，或把另一 Scope 的文件直接拼进 packet。

本次 as_of_date 为 2026-10-01 捕获边界，不是 9 月 30 日历史 PIT。美国 PCE 采用同一 8 月发布版本的历史表：7→8 月 headline/core YoY 均持平，MoM 0.1→0.3% / 0.1→0.2%；不能混入年度更新前旧 7 月发布值。完整 Q3 要求 6 月 30 日锚点 + 纳指 64 个交易日、创业板/科创 50 各 65 个交易日。收益为本地价格口径，未含 FX、分红或费用。

## 下一阶段最小范围

README 待办表是推进顺序。下一项建议收口普通 chat 的可信宿主来源配置：先确定协议/未完成边界，再复用既有显式宿主适配器，补足必要源能力，最后验收真实整合请求与缺源拒绝。不要重新开放旧六工具/Bash，或先写一批镜像实现的测试。

月度复用后续需明确 Scope 授权、版本/修订、发布日期、缺月、重复执行、恢复及历史重验策略；现有同 Scope 单次复用不等于跨会话缓存已实现。一般语义质量、固定故障评测、完整长期记忆、可靠 Cron、正式日更、回测和 E4/E5 各有独立退出条件。

## 新会话可复制提示

> 继续开发 le0820/a_share_claw。先读取 README.md、IDENTITY.md、DATA_CONTRACT.md 和已授权状态，再定向读取 SESSION_HANDOFF.md；核查 git status/worktree/当前 HEAD。PR #2 已完成 E0 最小五项、选定五源能力和经宿主人工复核的月度历史 Q4 case，完整 Issue #1 仍未完成。开发分支为 docs/portable-harness-data-plugins，develop 仅同步镜像，开发验收后经 PR 合并 main。请先确认下一阶段最小范围与未完成边界，再实施接口闭环，最后验收整合 case。模型无直接工具权限；数据插件只填事实，研究保持 NO_ACTION，不绕过 Scope/日期/版本/发布门禁。下一项以 README 待办为准，若本机证据不存在则明确说明，不能继承未核验的完成声明。

验证入口（不含真实源或模型凭据）：

```bash
uv sync --locked --extra dev --extra market
uv run --no-sync python -m a_share_claw data plugins
uv run --no-sync python -m a_share_claw harness plan --workflow macro --date 2026-07-13
ASCLAW_DATA_PROVIDERS='' uv run --no-sync pytest -q
```

真实网络/模型验收须显式使用已授权环境配置并保留来源快照；不要把密钥、联系值、私人状态或原始会话提交到仓库。

## 2026-10-02 继续开发记录（本轮范围）

在原开发 worktree 实施六阶段执行边界与 HTML 归档读取门禁；核心 quant 输出增加准入收盘序列供 SVG 图表使用，月度历史报告也有公布率图。增加 HTML 转义、篡改拒绝、输出失败保留正式状态与七工作流阶段边界验收。旧双格式报告仍能读取；旧报告不自动补 HTML。离线 replay 校验 HTML 哈希后跳过渲染文件。

完整用户 goal 保持未完成：交接建议的普通 chat 可信来源配置仍需实现；各来源/模型/角色/评价步骤的细粒度 ReAct span、公开决策与观测界面，纯规划/缺口 HTML、报告浏览入口，资金流向与申万一级行业轮动，S&P500/Nasdaq100/沪深300/创业板真实行情接线与数值/日历验收仍需继续。不能用六个阶段或合成三指数图宣告整体完成。下一轮从本 worktree 的未完成边界推进，不重新开放旧工具或绕过来源政策。

本机预览 `data/harness_acceptance/html_react_20261002/preview/report.html` 仅为合成数据验收，明确 Synthetic 标签；不是实际行情或投资输出。当前机器缺少 uv/3.11/3.12 验证环境，使用主 checkout 的 Python 3.13 venv，核心 SDK/pandas 已对齐 uv.lock 对应版本；不继承旧 CI 的双 Python 版本通过声明。

本轮离线回归：Python 3.13，489 passed / 1 skipped / 41 subtests passed；`data/harness_acceptance/html_react_20261002/junit.xml` 与 acceptance.json 留存。未调用真实数据或模型；未跑 3.11/3.12 CI。

## 2026-10-02 本轮增量与下一步

同一开发 worktree：新增细粒度 react-action-v1、取消安全的原子 span 闭合、离线同 Scope 工作台（列表/冻结规格/门禁/公开时间轴/报告链接）。`harness ui` 可导出规划/缺口诊断 HTML，尚未自动在每个终态导出。模型候选与私有思维链不进入界面。

新增核心四指数 watch 规格及外部精确 easytdx 来源合同；真实 Q3 当前捕获验收 run_id=`de3365db042640b0b75469ef99ffb034`，Scope/来源/HTML/工作台见 `data/harness_acceptance/market_visualization_20261002/acceptance.json`。4 次 source、0 次 model；收益/回撤独立复算通过；NO_ACTION。首次 NDX 阻断及诊断保留：异常在2025-05-08，落在冻结窗口外；仅调整筛选顺序，窗口内 OHLC 仍严格拒绝。不要把当前捕获改记为历史可用数据。

普通 chat 增加显式 TrustedChatProfile / --host-contract，固定四字段 Scope、日期、workflow、规格和既有来源合同，校验先于模型/来源。合成 SDK+四来源真实代码链路、缺源、改规格、跨 Scope 拒绝通过；默认 chat 无绑定。真实模型整合验收尚未执行。

完整 goal 继续 active：资金流向（待用户口径回复时按全市场成交方向先设计）、申万一级分类和轮动、每个终态自动 HTML、真实模型 chat 整合与业务质量仍需完成。全市场资金流必须完整声明股票集合/成交覆盖/单位，不能用几只样本或 SDK 静默回退冒充；申万分类须版本及31行业完整覆盖，交易行情继续 easytdx。下一轮从这些未完成边界推进，不重新开放旧工具或换用非授权行情商。

## 2026-10-02 终态 HTML 与资金/行业发现进展

`RunSession` 在 Harness/普通 chat 的 artifact_root 下自动生成 run.html/run_view.json；SQLite 终态/正式状态与诊断描述符在同一事务。工作台渲染失败回滚发布，降级最小失败诊断；持续磁盘故障仍结束为 failed，明确 terminal_html_unavailable。迟到回调不再改写 trace。`harness view RUN_ID --date` 校验同 Scope、截止日、哈希、终态/输出绑定；业务 report.html 继续原独立门禁。普通 RunSession 无 artifact_root 的内部工具测试不是业务报告入口。

已冻结资金/行业要求，新增 easytdx 原生发现能力（未核心准入）：三市全量原生报价按逐页header总数校验、代码排序且不排除类别。真实发现 SH2320/SZ2906/BJ351=5577只，所有 server_update_date=20260930，amount/main_net_amount/date/time 无缺字段；原始字段包含3/5日主力净额和原生行业代码。`data/harness_acceptance/market_sector_20261002/framework.json`、flow-discovery-result.json/flow_discovery 保存明确 Scope 与原始捕获。不跑 SDK get_fund_flow/get_history_fund_flow/get_board_summary（有静默回退/缺值转0/聚合风险）。资金图下一步需核心 provider-independent 冻结规格、原生语义/单位/日期/全量 universe 准入，核心计算聚合与signed金额图，先验证真实数值再接普通chat。

申万真实绑定仍缺口：通达信原生一级目录128项不是申万31行业；扩展70目录374项无申万匹配；扩展62目录截断拒绝；market1精确801010调用失败；申万官方分类页网络/网页工具均失败。来源计划/原始响应均已保留，不反复重启相同已结束任务。下一步可检查申万官方元数据可达性、明确原生分类层级到SW2021的审核映射或SDK其他精确identity；不能换未经授权行情商或用通达信板块代替。无合法来源仍须缺口页，不造轮动图。

完整 goal 仍 active：资金图、申万轮动真实准入与图表、真实模型普通chat整合验收尚未完成。终态HTML合成故障/取消与开发实际plan/gap已验证，示例在 `data/harness_acceptance/terminal_html_20261002/acceptance.json`。当前工作树为本增量，提交后记录commit；不要将发现成功当成整个研究/图表验收完成。

## 2026-10-02 原生资金快照核心与图表增量

新增 provider-independent flow-spec-v1 / flow-output-v1、精确来源 handoff、核心 SH/SZ/BJ/ALL 聚合、signed SVG及原始金额表，沿用ReAct/HTML/NO_ACTION门禁。缺字段、集合/名称不匹配、日期/单位错误、收盘前非零值、模型改规格均拒绝；研究快照不能正式发布，混合和展望暂不接此操作。普通chat合成SDK整合与修改freshness策略的拒绝已通过，真实模型尚未验收。

真实冻结5577只后重新采集：严格首运行 `84db4ca219f74afcbef4de843407fd23` 因6条00:00:01原生零值阻断。随后明确冻结 retain_unfinalized_native_zero，仅保留原生0并逐条披露，不认证停牌/未上市/全天无交易或最终收盘完整性。原始采集成功 `5400ce513c924c6bbfc65d2f5b51373d`，1source/0model；同Scope哈希复验后复用该捕获，最终审计报告 `c3aa09dc22b443b1b3ab1a802ca4d22e`，0新增source/0model。实际汇总Decimal独立复算通过，HTML布局与异常披露浏览器检查通过。证据与失败/旧报告均保留在 `data/harness_acceptance/fund_flow_20261002/`，最新acceptance.json指向最终报告。

尚缺：北交所351条原生主力净额全部为0且有成交额，SDK/源字段支持需要认证，不能据此认定资金平衡；ALL同受限制。BSE日历只保存官方主站索引摘录及明确403失败，未冒充原始全文。申万2021一级31行业仍没有合法完整分类/行情，不能以128项通达信行业替代。下一步可检查已安装SDK ex/mac_client.goods_count(market)及goods_list分页以解决market62目录截断，再验证官方申万元数据和精确身份；当前资金图是有限快照验收，不是整个goal完成。继续真实模型普通chat整合、申万轮动、资金语义/最终收盘覆盖，并维护完整Issue #1未完成边界。

本增量最终离线回归：Python3.13，541 passed / 41 subtests；未重跑3.11/3.12 CI。真实资金源调用2次（首严格运行阻断、显式快照运行成功），后续2次同Scope原始哈希复验复用、0新增来源；没有模型调用。临时localhost验收服务与浏览器页已关闭。main未修改，开发提交后develop快进镜像。

## 2026-10-02 用户范围修正与完整目录检查

用户明确“不需要北交所，我不关注北交所”。当前资金图只采沪深，不再将北交所字段支持作为剩余目标。新增flow-spec-v2/markets=[SH,SZ]；worker、来源合同、核心集合/ALL和HTML都使用同一冻结范围；旧v1三市仅归档兼容。真实新采5226只（SH2320/SZ2906），run=`6ac64f6a0aa242bd8d9d89dbdb58a14a`，1source/0model，Decimal独立复算通过，3条SZ00:00:01原生零值明确披露；来源与报告位于 `data/harness_acceptance/fund_flow_shsz_20261002/acceptance.json`。旧三市记录保留，不改写或删除。

申万目录方向取得新证据：初试全global目录106798条超限，保留失败；改用SDK market排序契约，原生count+二分相邻边界探针+完整目标连续页，严格header/offset/count。market62实际2320条、3页、28探针、6个重复代码，重复身份原样保留并标记，整体仍unverified。raw及 `complete-catalog-acceptance.json` 位于market_sector验收目录；没有801xxx/申万31，唯一名称含申万的是931595中证申万通胀防御，不替代申万一级。原官方申万发布页仍超时；下一步须合法SW元数据及行情绑定，或者继续核心轮动规格/计算/图表实现并保留真实来源缺口，不能把TDX/中证分类改名成SW。

本增量最终离线回归Python3.13：549 passed / 41 subtests；沪深真实HTML浏览器布局及无BJ条目检查通过，临时服务/页已关闭。普通四指数、ReAct、所有终态HTML仍保留既有验收；整体goal未完成，继续SW轮动核心与真实绑定、真实模型聊天整合，北交所已从当前研究范围和剩余验收要求排除。
