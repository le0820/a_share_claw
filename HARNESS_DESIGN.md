# a_share_claw Harness 顶层设计

修订日期：2026-09-30。本文保留目标架构；已落地的五源插件、计划/缺口、运行快照、文件归档和 Agent 来源限制见 [DATA_PLUGINS.md](DATA_PLUGINS.md)。下文未标为已实现的统一请求、evaluator、SQLite trace 与宿主专用集成仍是目标。与 [README.md](README.md)、[研究角色与模板](IDENTITY.md)、[数据契约](DATA_CONTRACT.md) 配合使用；评估建设继续由 [Issue #1](https://github.com/le0820/a_share_claw/issues/1) 跟踪。

## 1. 产品主体与分层

a_share_claw 是可移植的投研 Harness。研究框架由问题、分析模板、指标、证据要求和风险边界构成；数据接口在框架形成并识别缺口之后接入。

| 层 | 职责 | 扩展边界 |
| --- | --- | --- |
| Harness 核心 | 投研契约、模板、路由、数据需求/缺口、上下文、确定性计算、风控、评估、trace、受控改进提案 | 不导入 Telegram、供应商 SDK 或特定模型 SDK；依赖抽象接口 |
| 宿主/模型适配 | 统一请求与身份映射、模型调用、工具执行、响应交付 | CLI、外部 Agent、模型端点、可选 Telegram；宿主权限映射后才可使用 |
| 数据插件 | 按能力提供量化、宏观、披露和研究证据 | 本地归档、Python adapter、HTTP、MCP 或宿主工具桥接；均接受核心校验 |
| 持久化设施 | 运行记录、版本化 artifact、隔离状态和回放输入 | SQLite 可作为首个实现；正式状态由核心门禁决定是否提升 |

依赖方向是适配器/插件实现核心接口，核心按协议调用。数据获取与评分计算分离；插件返回数据，核心控制规则。外部 Agent 的 prompt、角色演绎或成功回复均不能代替可执行的校验与门禁。

已有基础：`DATA_CONTRACT.md`、研究角色/输出模板、compiled 规则、确定性路由、评分管线、部分市场时点/状态/MCP 门禁及 L0 回归测试。已实现：显式 JSON 研究计划/缺口、五源插件注册/热插拔与运行快照、文件证据归档。未完成：模板自动编译、统一宿主接口、全链路 run trace、跨运行固定回放和统一 evaluator。评估与风控是主体职责，但完整评估框架仍需建设。

## 2. 运行模式

| 模式 | 模型/执行来自哪里 | Harness 的职责 |
| --- | --- | --- |
| 外部宿主 | Codex、Claude Code、Meta Muse、WorkBuddy 等提供模型和受授权工具 | 提供同一计划/证据/评估契约；验证输入与产物，保存 trace，守住状态与风险门禁 |
| 独立模型端点 | 配置模型 URL、模型名、协议和必要凭据，runner 提供执行循环 | 无消息平台即可规划与研究；数据插件按缺口另行启用 |
| 离线/确定性回放 | 固定计划、归档数据与 fake/scripted model；计算可不调用模型 | 契约检查、评分、门禁、回放和评估不依赖实时网络 |

外部宿主优先经结构化 CLI/库接口接入；MCP 可作为后续协议适配，不能假定每个宿主都支持同一协议。专用宿主桥接须逐个验收。现有 `chat` 支持本地模型入口，`run` 仍是 Telegram 兼容入口；Telegram、通知与调度均为可选适配。

模型 URL 接入解决模型可用性，端点仍须匹配协议、模型和认证。单有模型即可形成框架与缺口报告；正式评分需要真实且合规的数据输入。宿主提供模型时不要求用户重复配置独立模型密钥。

## 3. 统一请求与权限作用域

拟定义 `RunRequest`：请求/输入摘要、`as_of_date`、输出模式（规划/研究/正式/回放）、核心作用域、预算、宿主信息和模型能力引用。run_id 在核心入口生成，不依赖消息平台的 message_id，也不复用 conversation/session ID。

核心作用域为 `workspace + principal + session + agent_key`。宿主适配器将 platform/user/chat 映射到这些字段并保留来源标识；`principal` 必须区分不同宿主的身份，除非有显式账户绑定。CLI 个人模式也必须显式建立授权身份。迁移保留历史数据，不能仅更名字段就扩大共享范围。

可见工具不等于获准调用；可见文件不等于可注入用户状态。核心根据作用域、任务、工具 allowlist 和数据用途判定权限。宿主取得的证据也须通过同一输入契约和权限校验，未验证产物不能标为 official。

## 4. 先框架、再缺口、再数据

```text
RunRequest + run_id
  -> 路由 / scope / 输出模式
  -> ResearchPlan：研究框架、模板、假设、指标与完成标准
  -> DataRequirement[]：所需能力、口径、时点、覆盖要求
  -> 验证授权的已有 artifact
  -> GapReport：已满足 / 缺失 / 不兼容 / 可选省略 / 策略禁用
  -> 对真实缺口选择并延迟加载数据插件
  -> 返回 envelope + 原始 artifact，核心校验
  -> 计算 / 证据综合 / 风控
  -> evaluator / 失败归因
  -> 报告与受门禁保护的状态提升
```

`ResearchPlan` 固定 workflow、模板/规则版本、研究切片、指标、数据需求、允许工具、预算、停止条件和评估标准。可先输出空字段模板与待验证假设，不能在取证前预定交易结论。数据获取若揭示新问题，先形成新计划版本并记录原因，再请求额外证据；插件不能自行扩展研究目标。

`DataRequirement` 至少包含 requirement_id、capability/metric、symbol/universe、日期窗口、频率、单位/币种、复权口径、publication/availability cutoff、vintage、最小覆盖和 required/optional。缺口报告绑定具体 requirement_id，不泛化成“数据不可用”。

先检查本地归档和宿主提供的合规证据，满足需求就不启动外部连接。无插件时仍可交付计划、报告框架和精确缺口；不能补零、补中性分数或用模型知识冒充实时数据。必需项未满足时停止对应正式切片；mixed 请求中的独立切片可继续。可选项按 compiled 覆盖/重归一规则省略，L2 禁用属于策略状态，不能自动寻找代理数据激活。

例如量化回测先固定标的、样本窗口、复权、基准、成本和指标，再计算缺失的 K 线/基准需求；宏观评分先从固定规则推导所需宏观序列、发布日期和市场时点，再补缺口。不会因某个数据 API 容易调用而改变研究方法。

## 5. 数据插件契约与热插拔

插件清单 `PluginManifest` 拟包含：plugin_id、实现/配置/schema 版本、capabilities、支持的市场/频率/历史范围/vintage、权限、credential_ref、成本/速率/timeout 限制，以及允许的 fallback。清单读取不建立网络连接。

最小协议为能力描述、按 requirement 获取数据、释放资源。registry 只管理已安装且受授权的实现；按缺口匹配能力与数据契约，通过校验后才连接。无需预先购买或配置所有数据源，也不允许 Agent 为补缺口自动安装任意代码、授予权限或扩大付费额度。

| 能力例子 | 可迁移的现有适配 | 核心约束 |
| --- | --- | --- |
| `market.daily_bars` / `market.quote` | easy-tdx、本地归档、合规宿主工具 | 标的/复权/窗口/交易日与来源明确；历史不能引入未来价格 |
| `macro.series` | FRED/ALFRED、CN macro scripts、归档文件 | 发布可用时间和 vintage 满足请求；当前修订不能冒充历史信息集 |
| `company.filing` / `fundamental.holdings` | SEC、发行人披露、证据化输入 | 披露时点、单位、财务期间和持仓覆盖满足契约 |
| `research.search` / `research.document` | Tavily、QVeris、网页或宿主检索桥接 | 区分无结果、后端错误和证据不足；保留 provenance 与 evidence gate |

以上能力名是设计示例。easy-tdx、FRED、AkShare、Tavily、QVeris 均是可替换实现；语义不一致的数据不是替换源。供应商变更须通过契约测试并记录 source selection，不能随意将广义美元指数重标为 DXY，或用搜索文本替代量化时间序列。

热插拔语义：

- 运行进程可注册、启用、禁用或替换插件配置，不重启 Harness 核心；新配置作用于下一次 run。
- 每次 run 固定 registry/config 快照和所选插件版本，按需求延迟建立连接；跨平台宿主可采用同一快照通过短生命周期调用执行。
- 移除插件先停止新 run 选择它，已有引用完成或显式取消后释放资源。强制断连记为结构化失败；不会静默换源并继续提升正式状态。
- provider fallback 只能使用预先允许且满足同一数据契约的来源，记录原因、来源变化和 fallback 状态；取回新证据后重新评估。
- 原始 artifact、哈希和版本化映射留存。历史回放按该 run 的快照读取；缺少历史版本/输入就报告不可回放，不调用新版数据补写历史。

插件返回 Issue #1 定义的统一 `ToolResult` envelope，包含 `ok/status/error_code/retryable/data/provenance/fallback_status/truncated/usage`。模型可看到裁剪数据，trace 保存完整脱敏 envelope 与原始 artifact 引用。`ok=true` 不是核心校验通过；缺来源、时点、覆盖或关键字段时仍拒绝使用。

## 6. 评估、风险与状态提升

`RunStatus` 描述执行状态，`EvalResult` 描述评估结果，两者分开；规划阶段成功生成缺口报告不等于正式投研完成。硬门禁独立于语义质量、延迟和成本，不合并为单一 reward。

数据时点、跨作用域隔离、unverified/official 边界、缺必需数据的动作结论、MCP evidence gate、半成品状态提升由核心确定性校验。外部宿主和独立模型运行都遵守同一规则；数据插件不提供覆盖核心规则的开关。

正式产物只有在必要输入与硬门禁通过后才可提升；保存计算/研究产物与更新 official state 是不同动作。模型或插件均不得自动修改生产规则、DATA_CONTRACT、evaluator 或执行交易；改进形成版本化 proposal，固定集/held-out 回放与人工批准继续按 Issue #1 实施。

E0 trace 从入口到结束统一记录请求与 scope、host/model adapter、路由、loaded/missing context、状态和预算；预留计划/需求版本和缺口摘要，在下一步规划模块实现后填入。E1 再补插件选择/权限、工具完整 envelope、耗时与 artifact；E2–E5 补上下文、固定回放、归因和 proposal。凭据、敏感 URL query 和私有文件内容不得未经脱敏写入 trace；配置仅保存凭据引用。

## 7. 实施顺序与验收

1. **顶层设计（本次）**：同步 README、启动规则、研究角色和数据契约，明确现有/待实现边界。
2. **核心请求与 E0**：定义统一请求/scope、FailureCategory/RunStatus/EvalResult/ToolResult，建设版本化 SQLite trace 与 CLI 摘要；保留 `InvestmentAgent.run() -> str` 兼容入口，Telegram 适配同一核心请求。
3. **框架与缺口闭环**：从模板/compiled 规则形成版本化计划、需求和缺口；通过零插件的规划、归档证据验证和必需缺口阻断验收。
4. **最小插件与 E1**：先实现本地 artifact 插件，再包裹现有行情/宏观获取，按缺口调用；分离 fetch/compute/report，增加 envelope、生命周期和运行快照。
5. **宿主/模型适配与后续评估**：拆出 SDK/Telegram 可选依赖，以 CLI/库协议接入外部宿主，再逐项验证，完成 E2–E5。框架与插件迁移不表示 Issue #1 已完成。

首批迁移验收：无 Telegram/外部数据凭据可生成框架与缺口；相同归档输入在 CLI 与宿主桥接下通过相同硬门禁；新增 provider 不改核心；缺口为空时无外部连接；插件替换后旧 run 仍用原快照；错误 schema、未来数据、缺 provenance 和跨用户状态均被拦截；缺必需项不产生 official action/state。

当前已提供 `data plugins/plan/fetch` CLI 和 `data_plugins` 库；专用宿主桥接尚未验收。后续模块按 core、host/model adapters、data plugins、storage 四个边界迁移；既有 fetch 脚本与 Telegram 路径保留到兼容回归通过，再逐步替换。

## 本轮实施覆盖

本轮优先落地五个来源及数据入口约束，不宣称完成上述全部阶段。Agent 已移除旧 fetch/MCP/自由执行工具，并暂时停用旧 SDK 会话/记忆/状态的数据注入。旧流水线保留人工入口，但不能作为 Agent 的绕过路径；正式评分和自动状态提升需等待插件输入迁移。实现、限制、可复现命令与下一步均以 [DATA_PLUGINS.md](DATA_PLUGINS.md) 为准。
