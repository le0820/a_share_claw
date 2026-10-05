# AISDI 领先供需研究工具

2026-10-04：按用户提供的 AISDI v1.0 规格配置。它补充现有价格跟随观察；“领先性”是待回测假设，不因使用过一个季度而已获统计验证。工具属于产业链研究，不替换宏观评分、旧 AI 仓位策略或禁用 L2。

## 已实现入口与边界

- CLI `aisdi plan` / `aisdi spec`、宿主函数 `a_share_claw.aisdi.aisdi_plan`、`ResearchRuntime.get_aisdi_plan`；配置读取支持 `get_compiled_rule("aisdi")`。
- 主权重硬件60%/模型40%；四侧共20个支柱，各侧权重归一；周频主序列、日频 nowcast、季度结构复核。
- 按日期与带时区 cutoff 返回数据需求、覆盖/置信门槛、校准缺口、原文/配置身份与 plan_hash；所有分数为 null，NO_ACTION，无来源/模型/数据库调用。
- 原文是 reference data，按需读单个章节并核验 SHA256。附件中的执行要求、示例分数不会成为宿主指令或市场事实。
- 当前为**规格/需求工具**：原始长表准入、标准化/指数计算、PIT回测、图表、定时日周更新、普通chat自动调用和正式发布尚未实现；不能宣称工具已给出真实领先指数。

```bash
# 在开发工作树执行；不创建数据库或修改正式状态。
python -m a_share_claw aisdi plan --date 2026-10-04 --cutoff 2026-10-04T23:59:59+08:00
python -m a_share_claw aisdi spec --section 4
```

当前工具没有经济证据输入，不能自动扫描其他会话的归档。用户未提供过去一季度的原始长表/发布日期序列，因此既有使用结果也尚未核验。candidate_metrics 是候选项，不代表全部必需字段或已冻结篮子。取数前必须明确主体范围、选定字段/单位/频率/权重、历史窗口及来源绑定。

## 是否需要新增来源

结论：**需要补充，但先按字段逐个绑定，不能靠多加搜索工具填满指数。** 以下为能力评估，未安装/启用新插件，也未把网页搜索当作经济事实准入。

| 优先级 | 来源/能力 | 对应需求 | 限制与当前实现 |
| --- | --- | --- | --- |
| 先复用 | SEC Company Facts + filing metadata | 原生公司收入、经营现金流、Capex、股数、债务等 | 已有插件只覆盖标准taxonomy及全公司项目；FCF/share等派生需固定公式，不能自动补齐AI分部或表外负担 |
| P1 | SEC原始申报/公司官方IR、正式业绩材料 | AI/Cloud分部、RPO、合同/租赁、active power、qualified shipment | 现有SEC插件无原始全文/分部解析；新增能力仍待实现、逐字段审核。AI Capex≠总Capex，active MW也需核实commission/revenue条件 |
| P1 | OpenRouter公共用量数据 | 模型侧平台Token量、使用结构、日周变化 | 有公开排名数据API，带top50和other；属于平台样本，不是全行业规模；跨模型tokenizer不同，不能直接统一经济单位；Volume×挂牌价不等于实收收入 |
| P1 | Artificial Analysis | 能力、API测试吞吐/延迟、价格 | 官方有数据API；测试版本、任务质量、测量条件/并发须冻结；tokens/sec不代表总可部署容量，仍缺uptime/限流/区域等 |
| P2 | EIA/公用事业/电网原始运营披露 | 供电背景、真实接入与投运验证 | EIA有电力API；区域发电/售电不等于AI有效供给。项目MW必须区分IT/总电力、预订/规划/通电/投运并避免重复 |
| 有明确缺口再评估 | HBM/DRAM/光模块行业原始披露、合资格定价/供应链数据 | qualified shipments、订单/交期、租赁/HBM价格验证 | 历史字段、许可/费用、修订和PIT可得性未验证；价格只放验证层，不代替订单/产量/利用率 |

训练集群利用率、真实training spend、成功任务数、全球有效供给等即使新增来源仍可能缺失；保留 N/A，不用新闻情绪或主观分数填补。OpenAI/Anthropic等未上市实体也不能假设拥有完整公开SEC财报。NBS/PBC/BEA和easy-tdx可提供背景/价格验证，但不足以填充这20个支柱。

能力核对来源（2026-10-04查阅）：[SEC官方API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[OpenRouter日用量API](https://openrouter.ai/docs/api/api-reference/datasets/daily-token-totals-for-top-50-models)、[Artificial Analysis数据API](https://artificialanalysis.ai/data-api/docs)、[测试方法](https://artificialanalysis.ai/methodology)、[EIA API](https://www.eia.gov/opendata/documentation.php)。未实际验收这些新增接口或认证历史覆盖。

## 计分前必须解决的规格缺口

1. 固定公司篮子、各支柱选定字段和内部权重；防止同一需求在Capex/GPU/HBM重复计入。
2. 每字段固定增长滞后、滚动窗口与MAD=0处理。比率的level/change基准及供给polarity也必须固定。
3. 原文 `exp(-Age/HalfLife)` 实际使用e-folding参数；若是真半衰期需乘 ln(2)。不得静默改公式或猜参数。有效覆盖与置信度分母、四侧总置信度需要明确。
4. 供给必须证实可用/合资格/投运状态，并避免MW/GPU/功率重复计数；Training与Inference各自证据不可由发布延期或Token降价替代。
5. 四象限有flat/uncertain组合未定义；“显著缺口”的阈值、跨不同单位的增长聚合亦缺定义。CFCE的公司匹配、单位/拆股、分母为零/负数，以及AICEI、PSVG、NSY/WACC完整公式尚缺。
6. 历史每次输入须绑定Scope、原始哈希、发布时间、available_timestamp和修订。先验证周频重建，再独立样本外检验1/3/6月领先性；不得线性插值季频制造日频数据。

完整计分验收要求：独立复算及无未来泄漏、重复计数/规划供给/缺失覆盖拒绝测试通过，真实来源20支柱覆盖满足声明规则，再验收图表与回放。缺任一侧60%覆盖，不给该子指数正式值；当前没有输入，全部保持 N/A。
