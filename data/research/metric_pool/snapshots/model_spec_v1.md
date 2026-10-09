# 2026Q4 AI供需指数（AISDI）计算模型规范

**版本：v1.0**  
**适用期：2026Q4 起**  
**用途：历史回测、日频/周频 nowcast、季度结构复盘、产业供需四象限识别、与 CFCE / AICEI / 资产价格联动分析**

---

## 0. 文档目的

本文档定义一套可直接工程化实现的 **AI供需指数（AI Supply-Demand Imbalance Index，AISDI）**。该指数服务于以下问题：

1. AI产业当前究竟处于需求扩张、供给扩张、供给短缺还是过剩阶段？
2. Training 与 Inference 的真实算力需求是否在加速或减速？
3. AI硬件与模型应用两个市场的需求增速，是否快于其 **Effective Supply（有效供给）** 增速？
4. 当前AI产业是健康扩张、瓶颈扩张、过度建设，还是收缩出清？
5. 日频市场噪声与季度长期趋势是否一致？
6. 供需强弱是否最终转化为供应商 FCF/share，并与买方 Adjusted Capital Burden、CFCE、AICEI、Credit Spread 相互验证？

该指数 **不直接衡量估值，也不直接衡量信用风险**。信用与资本回报使用独立的 CFCE、AICEI、AI Economic Spread 进行验证，避免将“产业供需”和“融资质量”混为一个指标。

---

# 1. 核心定义

## 1.1 总指数

统一总指数定义为：

\[
\boxed{
AISDI_t = 0.60\times HSDI_t + 0.40\times MSDI_t
}
\]

其中：

- **HSDI**：Hardware & Infrastructure Supply-Demand Index，AI硬件/基础设施供需指数；
- **MSDI**：Model & Application Supply-Demand Index，模型/应用供需指数。

默认权重为 **60% 硬件/基础设施 + 40% 模型/应用**。原因是当前AI资本周期的主要瓶颈仍集中于 GPU、HBM、光互连、数据中心、电力与commissioning，而模型应用侧数据的历史长度和可比性更弱。

> 建模时允许做 50/50、70/30 等敏感性测试，但正式主序列建议保持固定 60/40，以保证时间可比性。

---

## 1.2 指数的经济含义

AISDI 的核心思想是：

\[
\boxed{
Supply\text{-}Demand\ Gap_t
= Demand\ Growth_t - Effective\ Supply\ Growth_t
}
\]

AISDI 不直接统计“有多少GPU”“规划了多少GW”“模型数量增加了多少”，而是比较：

- **需求增长速度**；
- **真正可使用、已通电、已commission、已产生收入的有效供给增长速度**。

最终将供需缺口映射至 0–100：

- **50 = 供需大致平衡**；
- **>50 = 需求增长快于有效供给，供需趋紧**；
- **<50 = 有效供给增长快于需求，供给趋松/过剩**。

---

# 2. 最重要的口径：Effective Supply

## 2.1 硬件 Effective Supply

AI硬件与基础设施的有效供给只计算：

\[
\boxed{
Energized + Commissioned + Revenue\text{-}Generating
}
\]

不计入：

- 仅公告但未开工的数据中心 MW；
- 已开工但未通电的数据中心 MW；
- 已采购但未安装、未power-on的GPU；
- 未通过客户qualification的HBM/光模块产能；
- 尚未形成实际shipment的产能规划；
- demo、样机、研发产线；
- 远期多年Capex规划；
- 仅有融资承诺但尚未形成实际运营能力的项目。

因此：

\[
Planned\ Capacity \neq Effective\ Supply
\]

---

## 2.2 模型/Application Effective Supply

模型应用侧的有效供给定义为：

\[
\boxed{
Quality\text{-}Adjusted\ Deployable\ AI\ Capacity
}
\]

需要同时满足：

1. 模型已经部署并可稳定调用；
2. 有足够 inference capacity；
3. API / 产品可真实访问；
4. uptime、rate limit、latency 足以支持生产；
5. 模型能力达到目标任务所需质量；
6. 安全、合规、部署限制没有使模型无法实际投入生产。

因此：

\[
Raw\ Capability \neq Deployable\ Supply
\]

模型benchmark提高，但部署被暂停、限流或无法投入生产时，不应视为等量的有效供给增长。

---

# 3. Training 与 Inference 必须拆分

AISDI 的需求端必须同时包含 Training 与 Inference，但二者独立计算。

## 3.1 Training Demand

Training Demand 只在出现下列硬证据时下调：

- 大型 training run 取消；
- training run 持续延期，且导致算力采购或使用量下降；
- 训练集群利用率下降；
- training compute spend下降；
- Hyperscaler AI Capex削减；
- GPU power-on放缓；
- 800G / 1.6T订单或交期显著松动；
- 上游供应商订单/利用率出现一致性下调。

以下情况 **不得自动视为 Training Compute下降**：

- alignment；
- safety；
- red teaming；
- eval；
- post-training；
- 模型发布日期推迟；
- 发布节奏减慢；
- 单一实验室的短期暂停。

因为这些活动本身可能增加 post-training / eval compute。

---

## 3.2 Inference Demand

Inference Demand 不能只看 Token Price。

最低要求：

\[
\boxed{
Token\ Revenue\ Proxy = Token\ Volume \times Token\ Price
}
\]

更理想指标：

\[
\boxed{
Revenue / Successful\ Task
}
\]

以及：

- enterprise agent seats；
- WAU / MAU；
- API calls；
- successful tasks；
- inference tokens；
- agent workload volume；
- AI cloud revenue；
- AI ARR。

原则：

\[
Token\ Price\downarrow \not\Rightarrow Demand\downarrow
\]

如果价格下降但Volume上升得更快，则总需求仍可上升。

---

# 4. 指数结构

## 4.1 HSDI：硬件/基础设施供需指数

\[
\boxed{
HSDI_t = 50 + 0.5\times(D^H_t-S^H_t)
}
\]

其中：

- \(D^H_t\)：Hardware Demand Score，0–100；
- \(S^H_t\)：Hardware Effective Supply Score，0–100。

最终截断至 0–100。

### 默认需求端权重

| Pillar | 权重 | 说明 |
|---|---:|---|
| Hyperscaler / Cloud AI demand | 25% | AI/Cloud revenue、RPO、AI compute commitments、AI Capex |
| Training Compute demand | 20% | training spend、active runs、cluster utilization、GPU allocation |
| Inference demand | 20% | tokens、API workload、agent activity、AI revenue |
| Memory / Optical / GPU orders | 20% | HBM、GPU、800G/1.6T订单、book-to-bill、backlog |
| DC contracted demand | 15% | leased MW、contracted MW、power reservation、capacity bookings |

### 默认供给端权重

| Pillar | 权重 | 说明 |
|---|---:|---|
| Powered-on GPU capacity | 25% | 已安装、已通电、可用GPU equivalents |
| Energized / commissioned DC MW | 25% | 已通电、已commission并可产生收入的数据中心 |
| HBM / Memory effective supply | 20% | qualified shipments、bit supply、wafer output |
| Optical effective supply | 15% | 800G/1.6T shipments、qualified capacity |
| Power / cooling / commissioning capacity | 15% | grid connection、available MW、cooling commissioned capacity |

---

## 4.2 MSDI：模型/应用供需指数

\[
\boxed{
MSDI_t = 50 + 0.5\times(D^M_t-S^M_t)
}
\]

其中：

- \(D^M_t\)：Model/Application Demand Score；
- \(S^M_t\)：Model/Application Effective Supply Score。

### 默认需求端权重

| Pillar | 权重 | 说明 |
|---|---:|---|
| Token Volume | 25% | OpenRouter/API/平台Token使用量 |
| Token Revenue Proxy | 20% | Token Volume × Price |
| Enterprise / consumer active usage | 15% | WAU、MAU、enterprise seats |
| Agent / successful task activity | 20% | agent task volume、successful tasks |
| AI Revenue / ARR | 20% | model/API/AI cloud revenue、ARR |

### 默认供给端权重

| Pillar | 权重 | 说明 |
|---|---:|---|
| Quality-adjusted model capability | 25% | benchmark-adjusted deployable capability |
| Deployable inference throughput | 25% | tokens/sec、capacity、available endpoints |
| Availability / reliability | 15% | uptime、rate limits、latency |
| Open / accessible model supply | 15% | open-weight模型、可部署模型数量 |
| Serving efficiency | 20% | quality-adjusted cost/throughput，成本下降代表供给扩张 |

> 模型供给数据历史较短时，允许降低其置信度，但不要用“模型发布数量”替代实际Deployable Supply。

---

# 5. 原始字段到分数的标准化方法

## 5.1 推荐转换

根据指标类型使用不同转换：

### 流量/规模型

例如：Token Volume、GPU shipments、energized MW、Cloud AI revenue。

优先计算：

\[
 g_{i,t}=\ln\left(\frac{x_{i,t}}{x_{i,t-h}}\right)
\]

其中 \(h\) 根据频率选择：

- 日频：20交易日或30自然日；
- 周频：13周 / 52周；
- 月频：3个月 / 12个月；
- 季频：QoQ annualized / YoY。

### 比率型

例如 utilization、uptime、book-to-bill：

使用 level + change 两部分：

\[
 y_{i,t}=0.5\times LevelZ+0.5\times ChangeZ
\]

### Lead time / wait time

Lead time 上升通常代表供需趋紧；若作为供给能力指标使用，应反向处理。

### Price / rental price

GPU rental、HBM ASP、DRAM价格等属于 **market-clearing validation layer**，原则上不作为核心供需数量指标的高权重组成项，以避免价格与供需重复计数。

---

## 5.2 Robust Z-score

推荐使用滚动中位数与MAD，而不是均值/标准差：

\[
 z_{i,t}=\frac{x_{i,t}-Median(x_i)}{1.4826\times MAD(x_i)}
\]

然后截断：

\[
 z_{i,t}=clip(z_{i,t},-3,3)
\]

推荐历史窗口：

- 日/周频：2–3年；
- 月频：至少36个月；
- 季频：至少12个季度；
- 历史不足时使用 expanding window，但最少不得低于8个有效观察值。

---

## 5.3 0–100映射

\[
\boxed{
Score_{i,t}=clip(50+16.67\times z_{i,t},0,100)
}
\]

解释：

- z = 0 → 50；
- z = +1 → 66.7；
- z = +2 → 83.3；
- z = +3 → 100；
- z = -1 → 33.3；
- z = -2 → 16.7；
- z = -3 → 0。

对“越低代表供给越强”的指标，例如 inference serving cost，应先乘以 polarity = -1。

---

# 6. 聚合公式

对任一Demand或Supply score：

\[
Composite_t = \frac{\sum_i w_i c_{i,t} Score_{i,t}}{\sum_i w_i c_{i,t}}
\]

其中：

- \(w_i\)：固定结构权重；
- \(c_{i,t}\)：该字段当期置信权重，0–1。

置信权重由数据源质量、数据新鲜度、覆盖率共同决定。

---

# 7. 数据质量与置信度

## 7.1 Source Quality

建议建立 source_quality_score：

| 等级 | 分值 | 数据源 |
|---|---:|---|
| A | 1.00 | SEC、公司财报、官方IR、监管、央行、交易所 |
| B | 0.85 | Reuters、Bloomberg、LSEG、ICE、Markit、官方行业协会 |
| C | 0.65 | 高质量产业研究、供应链数据库、专业定价机构 |
| D | 0.40 | 云GPU聚合网站、厂商报价、行业媒体 |
| E | 0.20 | 匿名爆料、论坛、社交媒体、未经确认的渠道调查 |

E级数据不得单独驱动主指数方向变化，只能作为待验证线索。

---

## 7.2 Freshness Decay

不同频率字段允许不同staleness：

| 频率 | 建议最大有效期 |
|---|---:|
| 日频 | 5天 |
| 周频 | 21天 |
| 月频 | 60天 |
| 季频 | 150天 |

置信度可使用：

\[
Freshness_{i,t}=e^{-AgeDays/HalfLife_i}
\]

---

## 7.3 总体 Confidence Score

\[
\boxed{
Confidence_t =100\times\frac{\sum_i w_i\times SourceQuality_i\times Freshness_i\times Valid_i}{\sum_i w_i}
}
\]

建议解释：

- ≥80：高置信；
- 60–80：中等；
- 40–60：低；
- <40：指数应标记 N/A 或仅作方向参考。

### 强制规则

如果 Demand 或 Supply 任一侧的有效权重覆盖率低于60%，该子指数不得给出正式值，写 **N/A**。

---

# 8. 缺失值处理

1. 不允许用0替代缺失值。
2. 不允许用股票价格替代CDS、订单、利用率或供需指标。
3. 高频数据允许短期forward fill，但必须受staleness限制。
4. 月/季频数据可维持最近有效值，但confidence随时间衰减。
5. 对于不可获得的字段，重新归一剩余有效字段权重。
6. 如果缺失导致指标结构发生明显变化，应在结果中标记“composition changed”。
7. 历史回测必须使用 **当时已发布的数据**，禁止使用后来修订值进行无标记回填。

---

# 9. 防止 Look-ahead Bias

所有数据必须同时保存：

- `observation_date`
- `publication_date`
- `available_timestamp`

历史回测使用：

\[
Data_t = \{x: available\_timestamp \le t\}
\]

不能根据“2026Q2的季度数据”在6月30日回填指数，如果该财报实际7月25日才发布。

任何restatement也必须保留：

- original_value；
- revised_value；
- revision_date。

---

# 10. 四象限 Regime 判定

四象限不由AISDI单独决定，而由需求与供给方向共同决定。

分别计算：

\[
DemandBreadth_t=\frac{\sum_i w_i I(g_{i,t}>0)}{\sum_i w_i}
\]

\[
SupplyBreadth_t=\frac{\sum_i w_i I(g_{i,t}>0)}{\sum_i w_i}
\]

定义：

- ≥0.55：上升；
- ≤0.45：下降；
- 0.45–0.55：平/不确定。

### 四象限

| Quadrant | 条件 | 解释 |
|---|---|---|
| I | Supply↑, Demand↑ | 扩张周期 |
| II | Demand↑, Supply平/↓ | 瓶颈扩张 / 紧缺 |
| III | Supply↑, Demand↓ | 过度建设 / 供给过剩 |
| IV | Supply↓, Demand↓ | 收缩 / 出清 |

对于Quadrant I，还必须进一步比较：

\[
Demand\ Growth - Effective\ Supply\ Growth
\]

如果差值显著为正，则属于“扩张但仍紧缺”；如果为负，则属于“扩张但供给开始跑赢需求”。

---

# 11. AISDI区间解释

| AISDI | 状态 | 解释 |
|---:|---|---|
| 80–100 | 极度紧缺 | 需求显著快于有效供给 |
| 65–80 | 紧缺 | 需求主导、价格/交期压力偏强 |
| 55–65 | 轻度紧张 | 需求略快于供给 |
| 45–55 | 平衡 | 供需大致匹配 |
| 35–45 | 轻度宽松 | 供给略快于需求 |
| 20–35 | 过剩 | 供给扩张明显快于需求 |
| 0–20 | 严重过剩/出清 | 高度供给过剩或需求塌缩 |

必须同时展示Quadrant，避免只看50以上/以下。

例如：

- AISDI = 70 + Quadrant I：健康扩张但供给仍紧；
- AISDI = 70 + Quadrant II：典型瓶颈/短缺；
- AISDI = 30 + Quadrant III：过度建设；
- AISDI = 30 + Quadrant IV：需求与供给共同收缩，但需求更弱。

---

# 12. Market-clearing Validation Layer

以下指标不应高权重进入主指数，而用于验证供需判断：

- B300 / GB300 / B200 / GB200 / H200 / H100租赁价格；
- GPU wait time；
- GPU cloud availability；
- HBM ASP；
- DRAM / NAND价格；
- 800G / 1.6T lead time；
- optical module book-to-bill；
- DC lease rate；
- power PPA price；
- data-center vacancy；
- inference API price；
- token price。

如果AISDI显示紧缺，但价格、等待期、lead time和utilization同步下降，应触发 **Model Conflict Flag**。

---

# 13. CFCE、AICEI 与 AISDI 的关系

## 13.1 CFCE

\[
\boxed{
CFCE=\frac{\Delta Supplier\ FCF/share}{\Delta Buyer\ Adjusted\ Capital\ Burden}
}
\]

其中：

\[
Adjusted\ Capital\ Burden=
Debt+Capitalized\ Lease+SPV/JV+Guarantees+Take\text{-}or\text{-}Pay+Net\ Equity\ Issuance
\]

CFCE **不进入AISDI**。

原因：

- AISDI回答“供需是否紧”；
- CFCE回答“这些需求最终转化成多少股东现金流”。

两者必须保持正交。

典型情景：

\[
AISDI\uparrow,\quad CFCE\downarrow
\]

意味着产业需求很强，但创造每单位供应商现金流需要越来越多买方融资负担，资本周期正在泡沫化。

---

## 13.2 AI Economic Spread

\[
\boxed{
AI\ Economic\ Spread=Incremental\ ROIC-WACC
}
\]

同样不进入AISDI。

AISDI高，只说明供需紧；如果ROIC-WACC已经转负，高需求仍可能对应经济价值毁灭。

---

## 13.3 AICEI

AICEI衡量AI基础设施扩张对信用、SPV、担保、租赁、长期承诺和表外结构的依赖程度。

AISDI与AICEI关系：

| AISDI | AICEI | 解释 |
|---|---|---|
| 高 | 低 | 健康紧缺/高回报资本形成 |
| 高 | 高 | 强需求 + 高信用扩张，最值得监控 |
| 低 | 高 | 最危险：需求转弱但债务仍高 |
| 低 | 低 | 正常出清或低景气 |

真正高风险通常是：

\[
\boxed{
AISDI\downarrow + AICEI\uparrow + CFCE\downarrow
}
\]

---

# 14. 建议的数据字段

下面字段采用长表设计，便于Work模型直接构建ETL与历史数据库。

## 14.1 通用元数据字段

| 字段 | 类型 | 说明 |
|---|---|---|
| observation_date | date | 数据对应的经济观察日期 |
| publication_date | date | 发布日期 |
| available_timestamp | datetime | 历史回测真正可使用时间 |
| entity | string | 公司/指数/市场/国家 |
| geography | string | US / China / Korea / Global等 |
| layer | enum | hardware / model / validation / credit |
| side | enum | demand / supply / validation |
| pillar | string | 所属子模块 |
| metric_name | string | 指标名称 |
| value | float | 数值 |
| unit | string | USD、MW、GPU、tokens、%、weeks等 |
| frequency | enum | daily / weekly / monthly / quarterly |
| polarity | int | +1 / -1 |
| source_name | string | 数据源 |
| source_type | string | SEC/IR/Reuters/ICE等 |
| source_quality | float | 0–1 |
| is_estimate | bool | 是否估算 |
| is_planned | bool | 是否规划值 |
| is_energized | bool | 是否已通电 |
| is_commissioned | bool | 是否已commission |
| is_revenue_generating | bool | 是否已产生收入 |
| training_inference_tag | enum | training / inference / both / none |
| restatement_flag | bool | 是否修订 |
| stale_days | int | 距最新发布天数 |
| confidence | float | 单项置信权重 |

---

# 15. Hardware Demand 字段建议

## Hyperscaler / Cloud

- `ai_capex_usd`
- `total_capex_usd`
- `ai_capex_yoy`
- `cloud_revenue_usd`
- `cloud_revenue_yoy`
- `ai_cloud_revenue_usd`
- `ai_rpo_usd`
- `compute_commitments_usd`
- `dc_lease_commitments_usd`
- `finance_lease_additions_usd`
- `take_or_pay_commitments_usd`

建议实体至少包括：

- Microsoft / Azure
- Amazon / AWS
- Alphabet / GCP
- Meta
- Oracle / OCI

---

## Training

- `training_compute_spend_usd`
- `training_gpu_hours`
- `training_gpu_equivalent_units`
- `training_cluster_utilization_pct`
- `active_frontier_training_runs`
- `cancelled_training_runs`
- `delayed_training_runs`
- `training_run_delay_days`
- `training_power_mw`
- `training_gpu_power_on_units`

事件字段必须只使用官方或高质量可验证来源。

---

## Inference

- `inference_tokens`
- `inference_api_calls`
- `inference_gpu_hours`
- `agent_tasks`
- `successful_agent_tasks`
- `enterprise_ai_seats`
- `ai_wau`
- `ai_mau`
- `token_revenue_proxy_usd`
- `revenue_per_successful_task_usd`

---

## GPU / Memory / Optical Orders

- `gpu_orders_units`
- `gpu_backlog_usd`
- `gpu_allocation_units`
- `hbm_orders_bits`
- `hbm_long_term_agreements_usd`
- `dram_orders_bits`
- `nand_orders_bits`
- `optical_800g_orders_units`
- `optical_16t_orders_units`
- `optical_book_to_bill`
- `optical_backlog_usd`

---

## DC Demand

- `contracted_dc_mw`
- `leased_dc_mw`
- `reserved_power_mw`
- `signed_pfa_mw`
- `power_purchase_commitment_mw`
- `dc_prelease_pct`

注意：这些是 **需求/预定量**，不能直接算作Supply。

---

# 16. Hardware Effective Supply 字段建议

## GPU

- `installed_gpu_units`
- `powered_on_gpu_units`
- `revenue_generating_gpu_units`
- `gpu_equivalent_compute_capacity`
- `gpu_cloud_available_units`
- `gpu_cloud_wait_time_days`
- `gpu_utilization_pct`

建议使用“标准GPU等价算力”进行跨代比较，例如基于FP8/BF16或benchmark throughput构造GPU-equivalent，而不是直接把H100和B300按1:1计数。

---

## Data Center

- `dc_announced_mw`
- `dc_under_construction_mw`
- `dc_energized_mw`
- `dc_commissioned_mw`
- `dc_revenue_generating_mw`
- `dc_vacancy_pct`
- `dc_utilization_pct`

核心Supply只使用后三类中的有效部分。

---

## Power

- `grid_connected_mw`
- `available_power_mw`
- `new_power_capacity_mw`
- `ppa_operational_mw`
- `nuclear_output_mw`
- `gas_power_output_mw`
- `renewable_operational_mw`
- `interconnection_queue_mw`
- `grid_connection_delay_months`

`interconnection_queue_mw`仅作为未来pipeline，不计当前Effective Supply。

---

## Memory

- `hbm_bit_shipments`
- `hbm_qualified_capacity_bits`
- `hbm_wafer_capacity`
- `hbm_utilization_pct`
- `dram_bit_shipments`
- `nand_bit_shipments`
- `memory_yield_pct`

---

## Optical

- `optical_800g_shipments_units`
- `optical_16t_shipments_units`
- `optical_800g_capacity_units`
- `optical_16t_capacity_units`
- `optical_yield_pct`
- `optical_lead_time_weeks`

---

# 17. Model/Application Demand 字段

- `openrouter_tokens`
- `api_tokens_total`
- `input_tokens`
- `output_tokens`
- `token_price_input_usd_per_m`
- `token_price_output_usd_per_m`
- `token_revenue_proxy_usd`
- `model_arr_usd`
- `model_api_revenue_usd`
- `ai_cloud_revenue_usd`
- `consumer_wau`
- `enterprise_wau`
- `enterprise_ai_seats`
- `agent_tasks`
- `successful_tasks`
- `revenue_per_successful_task_usd`
- `api_calls`
- `inference_compute_hours`

---

# 18. Model/Application Effective Supply 字段

- `deployed_frontier_model_count`
- `open_weight_frontier_model_count`
- `quality_adjusted_model_score`
- `benchmark_composite_score`
- `api_available_regions`
- `api_capacity_tps`
- `effective_tokens_per_second`
- `api_uptime_pct`
- `p50_latency_ms`
- `p95_latency_ms`
- `rate_limit_tokens_per_min`
- `serving_cost_usd_per_m_tokens`
- `quality_adjusted_serving_cost`
- `context_window_tokens`

推荐派生：

\[
QualityAdjustedCapacity = Throughput\times Availability\times CapabilityIndex
\]

以及：

\[
ServingEfficiency = \frac{QualityAdjustedCapacity}{ServingCost}
\]

---

# 19. Market-clearing 验证字段

- `h100_rental_usd_per_gpu_hour`
- `h200_rental_usd_per_gpu_hour`
- `b200_rental_usd_per_gpu_hour`
- `gb200_rental_usd_per_gpu_hour`
- `b300_rental_usd_per_gpu_hour`
- `gb300_rental_usd_per_gpu_hour`
- `gpu_wait_time_days`
- `hbm_asp`
- `dram_spot_price`
- `dram_contract_price`
- `nand_spot_price`
- `optical_800g_lead_time_weeks`
- `optical_16t_lead_time_weeks`
- `dc_lease_rate_usd_per_kw_month`
- `power_price_usd_per_mwh`

这些字段建议单独绘图，并作为主指数冲突检查。

---

# 20. CFCE / Capital Burden 所需字段

## Supplier端

- `supplier_revenue_usd`
- `supplier_fcf_usd`
- `supplier_fcf_per_share`
- `supplier_diluted_shares`
- `supplier_eps_diluted`
- `supplier_cash_dividends_usd`
- `supplier_gross_buyback_usd`
- `supplier_sbc_usd`
- `supplier_net_share_change_pct`

## Buyer端

- `buyer_debt_usd`
- `buyer_capitalized_lease_usd`
- `buyer_spv_exposure_usd`
- `buyer_jv_exposure_usd`
- `buyer_guarantees_usd`
- `buyer_take_or_pay_usd`
- `buyer_net_equity_issuance_usd`
- `buyer_fcf_usd`
- `buyer_ai_capex_usd`

派生：

\[
AdjustedCapitalBurden=
Debt+CapitalizedLease+SPV+JV+Guarantees+TakeOrPay+NetEquityIssuance
\]

\[
CFCE=\frac{\Delta SupplierFCF/share}{\Delta AdjustedCapitalBurden}
\]

---

# 21. AICEI所需字段

保持原八项：

1. `financed_demand_ratio`
2. `guarantee_to_liquid_assets`
3. `future_commitments_to_fcf`
4. `dso`
5. `receivables_growth_minus_revenue_growth`
6. `customer_equity_exposure`
7. `gpu_dc_residual_value_ltv`
8. `spv_jv_dscr`
9. `funding_stress_composite`

> 原框架中第4项由DSO与应收增速共同构成，因此数据库可以拆成两个底层字段，但季度AICEI仍保持八个大类。

---

# 22. 输出字段

每个日期至少输出：

| 输出字段 | 说明 |
|---|---|
| `aisdi` | 总AI供需指数 0–100 |
| `hsdi` | Hardware Supply-Demand Index |
| `msdi` | Model/Application Supply-Demand Index |
| `hardware_demand_score` | 硬件需求分数 |
| `hardware_supply_score` | 硬件有效供给分数 |
| `model_demand_score` | 模型需求分数 |
| `model_supply_score` | 模型有效供给分数 |
| `hardware_gap` | HDemand − HSupply |
| `model_gap` | MDemand − MSupply |
| `total_gap` | 加权总Gap |
| `hardware_quadrant` | I/II/III/IV |
| `model_quadrant` | I/II/III/IV |
| `aisdi_regime` | 极度紧缺/紧缺/平衡/过剩等 |
| `confidence_score` | 0–100 |
| `coverage_ratio` | 有效字段权重覆盖率 |
| `model_conflict_flag` | 是否与价格/lead-time验证冲突 |
| `training_demand_score` | Training独立分数 |
| `inference_demand_score` | Inference独立分数 |
| `effective_supply_growth` | 有效供给增长估计 |
| `demand_growth` | 需求增长估计 |

另外并排输出但不混入AISDI：

- `cfce`
- `ai_economic_spread`
- `aicei`
- `psvg`
- `nsy`
- `ai_credit_spread_proxy`

---

# 23. 推荐的历史图表

Work模型至少绘制以下图：

## 图1：AISDI总指数

- AISDI
- HSDI
- MSDI
- 50平衡线
- 65 / 80紧缺阈值
- 35 / 20过剩阈值

## 图2：Demand vs Effective Supply

同图显示：

- Hardware Demand Score
- Hardware Effective Supply Score
- Gap

## 图3：Training vs Inference

- Training Demand Score
- Inference Demand Score

观察两者是否出现结构性分化。

## 图4：四象限 Regime

用背景色标记：

- I Supply↑ Demand↑
- II Demand↑ Supply平/↓
- III Supply↑ Demand↓
- IV Supply↓ Demand↓

## 图5：AISDI vs 市场清算指标

可选：

- GPU rental price
- HBM price
- 800G/1.6T lead time
- DC lease rate

用于验证指数。

## 图6：AISDI vs CFCE / AICEI

重点寻找：

\[
AISDI\uparrow + CFCE\downarrow + AICEI\uparrow
\]

该状态代表“需求仍强，但资本效率与信用质量恶化”。

## 图7：Supplier FCF/share vs Buyer Adjusted Capital Burden

这是Q4最重要的资本传导图。

## 图8：AISDI vs 资产价格（仅用于回测，不参与建模）

可以测试：

- SOX
- NVDA
- AVGO
- COHR
- 中际旭创
- 新易盛
- 创业板50ETF

但 **严禁用这些资产价格反过来参与AISDI计算**，避免循环解释。

---

# 24. 回测目标

Work模型应检验AISDI是否领先或同步解释：

1. GPU rental price；
2. HBM价格；
3. Optical lead time；
4. Supplier revenue growth；
5. Supplier gross margin；
6. Supplier FCF/share；
7. Hyperscaler Capex growth；
8. AI credit spread；
9. Capex cuts；
10. 800G/1.6T订单拐点。

推荐分别计算：

- contemporaneous correlation；
- 1M / 3M / 6M lead-lag correlation；
- Granger causality（仅作统计检验，不作因果证明）；
- regime-conditioned returns；
- turning-point precision/recall。

---

# 25. 建模时最重要的禁区

1. **不能把规划供给当Effective Supply。**
2. **不能把Token降价直接等同需求下降。**
3. **不能把安全/对齐/发布延迟直接等同Training Compute下降。**
4. **不能用股价代替CDS、订单、利用率。**
5. **不能把Debt、Lease、SPV、Guarantee重复计算。**
6. **不能使用后来修订的数据进行无标记回填。**
7. **不能为了填满日频数据而对季频字段做线性插值。**
8. **不能用模型发布数量代替可部署AI供给。**
9. **不能把同一底层需求通过Capex、GPU订单、HBM订单多次高权重重复计入。**
10. **AISDI、CFCE、AICEI、Credit Spread必须保持概念正交。**

---

# 26. 建议的计算频率

## Daily Nowcast

更新：

- token volume；
- GPU rental/wait time；
- market-clearing价格；
- verified order/lead-time事件；
- power-on/availability；
- 重大Capex/order公告。

慢频字段保持最近有效值，同时confidence随staleness衰减。

## Weekly Canonical Index

建议每周固定生成一次正式AISDI，是日常跟踪的主序列。

原因：

- 减少日频新闻噪声；
- 更适合Token、租赁、lead time、订单等数据；
- 保持足够及时性。

## Quarterly Structural Recalculation

季度财报集中期正式更新：

- 权重稳定性；
- CFCE；
- Incremental ROIC − WACC；
- PSVG；
- NSY；
- AICEI；
- 数据质量与历史回测。

---

# 27. 推荐初始数据库实体

## Hyperscaler / Buyer

- Microsoft
- Amazon
- Alphabet
- Meta
- Oracle
- OpenAI
- Anthropic
- CoreWeave
- Nebius

## Supplier

- NVIDIA
- Broadcom
- TSMC
- ASML
- Micron
- SK Hynix
- Samsung Electronics
- Coherent
- Lumentum
- 中际旭创
- 新易盛
- 华工科技
- 光迅科技
- PCB/CCL/ABF代表公司
- 液冷/电力代表公司

## Market / Infrastructure

- SOX
- NDX
- US Treasury
- US IG/HY
- DC MW / power datasets
- OpenRouter
- GPU cloud providers
- DRAM/HBM/NAND pricing sources

---

# 28. 建议的最终结构化输出示例

```json
{
  "date": "2026-10-02",
  "aisdi": 72.4,
  "hsdi": 78.1,
  "msdi": 63.8,
  "hardware_demand_score": 84.5,
  "hardware_supply_score": 28.3,
  "model_demand_score": 71.2,
  "model_supply_score": 43.6,
  "hardware_gap": 56.2,
  "model_gap": 27.6,
  "hardware_quadrant": "II",
  "model_quadrant": "I",
  "aisdi_regime": "tight",
  "training_demand_score": 82.0,
  "inference_demand_score": 79.0,
  "confidence_score": 76.0,
  "coverage_ratio": 0.83,
  "model_conflict_flag": false,
  "cfce": null,
  "aicei": null,
  "ai_economic_spread": null
}
```

> 上述数字仅展示数据结构，不代表真实2026-10-02计算结果。

---

# 29. Q4核心解释框架

AISDI最终需要回答的不是“AI是不是泡沫”，而是：

\[
\boxed{
Demand\ Growth - Effective\ Supply\ Growth
}
\]

是否持续为正。

在此之上，再使用：

\[
\boxed{
CFCE=\frac{\Delta Supplier\ FCF/share}{\Delta Buyer\ Adjusted\ Capital\ Burden}
}
\]

判断资本投入转化为股东现金流的效率；

使用：

\[
\boxed{
Incremental\ ROIC-WACC
}
\]

判断新增资本是否真正创造经济价值；

使用AICEI判断这种扩张是否越来越依赖信用、表外结构、长期承诺和残值担保。

因此Q4的完整判断顺序应为：

\[
AISDI
\rightarrow
CFCE
\rightarrow
Incremental\ ROIC-WACC
\rightarrow
AICEI/Credit\ Spread
\rightarrow
Capex\ Decision
\rightarrow
Upstream\ Orders
\]

真正的AI资本周期恶化，应看到：

\[
\boxed{
AISDI\downarrow
+
CFCE\downarrow
+
(Incremental\ ROIC-WACC)\downarrow
+
Credit\ Spread\uparrow
}
\]

并进一步验证：

\[
Capex\ Cuts
\rightarrow
GPU\ Power\text{-}on\downarrow
\rightarrow
800G/1.6T\ Orders\downarrow
\rightarrow
Supplier\ FCF/share\downarrow.
\]

只有当这条反向传导链成立，才应把AI资本周期定义为从“强需求+高资本形成”进入“去杠杆/收缩阶段”。

---

# 30. 给Work模型的执行要求

1. 先建立长表数据库，不直接写死宽表。
2. 所有数据必须保留 publication / available timestamp。
3. 先构建字段级raw database，再做transform与score。
4. 所有权重放入独立config，不写死在代码逻辑中。
5. 主指数使用固定60/40硬件/模型权重，并做敏感性分析。
6. 每个子指数同时输出score、coverage、confidence。
7. 保存每次计算使用的字段版本与source lineage。
8. 历史回测严格避免look-ahead。
9. 先绘制AISDI、HSDI、MSDI、Demand/Supply、Quadrant，再叠加CFCE/AICEI。
10. 股票价格只用于回测和解释，不进入供需指数本身。
11. 若数据不足，宁可输出N/A，也不要用主观分数补齐。
12. 对2024–2026历史期优先重建周频Canonical Index，再扩展日频Nowcast。

---

**最终目标：把“AI需求强不强”升级成一个可回测、可复现、可解释的供需系统，并进一步回答：这些供需变化究竟能否转化为可归属于股东的折现后现金流。**
