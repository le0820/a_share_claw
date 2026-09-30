# 跨设备开发交接：五源插件

## E0 核心续开发（2026-09-30）

继续使用 `docs/portable-harness-data-plugins`。先补核心运行/策略/门禁，再接事实插件。统一契约、事务迁移、作用域 trace、逐模型请求观测、宏观/AI 计算、CLI 摘要/回放与失败通知已落地，详见 [E0_INFRA.md](E0_INFRA.md)。原 main 的本地未提交文件未改动。

本地完整套件按锁文件分别验证 Python 3.11/3.12：均为 **127 passed, 40 subtests passed**。包括宏观与旧流水线结果比对、AI 输入与覆盖门禁、迁移失败回滚、归档失败撤销发布、作用域及正式状态隔离。下文的 90 项及旧 CI 结果属于先前提交，不能代表本次提交。

尚未完成公司/行业/mixed/quant 业务执行器、供应商事实映射与连续正式日更。暂不合并 main、不关闭 Issue #1。后续按 README 先验收核心业务门禁，再接插件。

日期：2026-09-30。相关 Issue：[#1](https://github.com/le0820/a_share_claw/issues/1)。代码分支：`docs/portable-harness-data-plugins`，PR：[#2](https://github.com/le0820/a_share_claw/pull/2)。

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

## 下一步顺序（2026-09-30 校正）

以 [E0_INFRA.md 的验收与未完成边界](E0_INFRA.md#验收与未完成边界) 为准。旧记录中“先配置 TickFlow、最后补 E0”的顺序已失效。

1. 先闭环核心：版本化运行协议与实际上下文记录 → 框架/业务执行 → 报告/发布及状态读取 → 逐项验收证据。已有 CI 结果不代替业务验收。
2. 核心验收通过后，接入 NBS/PBC/TickFlow/FRED/SEC 事实映射；按当前数据/主源授权、单位、披露日、vintage 和覆盖限制准入，不允许原生响应直接提升正式状态。
3. 五源接线后，再执行 8 月 PCE、8 月中国国民经济和三指数三季度 → 四季度展望 case。此前直接下载的原始材料未验收，不计作接入或 case 完成。
4. 连续正式日更、完整上下文/记忆、调度恢复与 E3–E5 留在对应后续工作包；PR #2 和 Issue #1 的关闭条件分别记录，不因一个 case 通过而整体宣告完成。

当前行为变化：Agent 仅有六个插件/规则工具；旧 MCP、网页、自由执行、文件、旧 state/记忆/SDK 工具历史不再进入取证上下文。旧人工 pipeline 仍可维护，但不能作为 Agent 绕过插件的入口。外部宿主若另有浏览器/网络工具，需在宿主侧同步约束。
