# E0 基础设施与验收

开发基线：`docs/portable-harness-data-plugins`，2026-09-30。核心负责运行、契约、评分和风控；插件只提供事实。本文不表示 Issue #1 的 E1–E5 或全部投研业务已经完成。

## 已实现

- `harness/contracts.py`：七类 FailureCategory、RunStatus、EvalResult、ToolResult、PromptCacheTrace、RunRequest/RunOutcome 与四字段 Scope。
- `harness/trace.py`：带校验和的事务迁移、runs/steps/tools/models/artifacts/evaluations/proposals；旧会话和任务保留。查询/追加必须匹配作用域，终态禁止追加；proposal 表仅预留。
- 每次 Agent/CLI 请求先生成 run_id。异常、超时和取消写入终态；调度器不会把 blocked/failed 通知为完成。逐次模型调用记录哈希、usage 和相邻输入前缀长度；不保存模型输入正文，缺失的缓存和首字延迟为 null。
- 宏观保留既有 L1、L3 风险/情绪、L2 disabled、4/7 与 3/7 权重及仓位区间；AI 复用增长、动量、情绪、流动性与绝对利率/Brent 门禁。
- 核心验证作用域、日期、单位、覆盖、来源字段和事实哈希。缺数据、未来数据、unverified/fallback、政策变化和预算耗尽阻止动作。
- 一个 SQLite 事务更新正式状态和终态；宏观/AI 按 scope + workflow 分别保存，不回退到较早日期。research/replay 永远 NO_ACTION；部分归档不等于正式状态。
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
```

run 默认 replay；显式 `--mode official` 才进入正式门禁。操作人必须提供已经审核的事实和来源，核心不能仅凭 source 字符串证明数据真实或 vintage 正确。CLI 身份参数用于本地个人入口；远程宿主必须根据自身认证映射 Scope。

## 插件下一步填充事实包

FactPacket 顶层恰有 `as_of_date/facts`；每项恰有 `capability/scope_key/data/provenance/fallback_status`。scope_key 从规划结果取得。必需能力由核心计划决定，不接受插件提供的评分、权重或动作。

provenance 必須有 `source/source_file/source_timestamp/publication_date/observation_date/data_period/sha256`。sha256 是规范化事实的 canonical JSON 哈希（排序键、紧凑分隔符、UTF-8、禁止 NaN），原始响应哈希另存。source_timestamp 必须带时区；发布时间、观测日及嵌套行情/披露日期不能晚于请求日。

宏观字段/单位见 `engine.MACRO_UNITS`：NFP 为千人、利率为百分数、信用利差为百分点。行情为固定 universe、daily、159682.SZ QFQ/CNY 和 QQQ.US NONE/USD。AI 使用 `schema_version=ai-inputs-v1` 与既有 ai_strategy 原始输入：增长 yoy/融资比例为小数、融资金额为人民币元、期权 put/call 为比例；宏观历史沿用旧规则单位，不接受预计算 action。

归档在 `data/harness_runs/<scope>/<run_id>/`。replay 检查授权作用域、文件根目录、哈希和政策版本，保留 AI 当前仓位参数，不拉取新数据；政策变化则拒绝回放。

## 验收与未完成边界

测试覆盖迁移/回滚、作用域隔离、宏观与旧流水线比对、AI 原始输入、硬门禁、归档失败、逐模型调用 trace、CLI 回放和失败通知。GitHub Actions 配置 Python 3.11/3.12；本次提交是否通过须以对应运行结果为准。

下一步先补公司/行业业务执行器和证据 evaluator，再验收事实映射。company/industry/mixed/quant 可规划，执行返回 `workflow_execution_pending`；不能把运行骨架具备解释为整个研究团队已跑通。

NBS/PBC 发布文本、TickFlow 原生财务/真实账户样本仍需数值归一化和 PIT 验收。Agent 尚不自动把插件响应提交核心评分。传统 `data/state/system_state.json` 未被覆盖，旧读取方迁移与连续正式日更待完成。完整 ContextManifest、长期记忆、固定评测报告和 proposal 审批继续按 Issue #1 后续阶段建设。
