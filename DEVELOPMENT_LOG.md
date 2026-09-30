# 跨设备开发交接：五源插件

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

## 下一步顺序

1. 配置 TickFlow，取得去敏真实响应样本，完成三表单位/披露日/修订 vintage 和行情覆盖映射。历史三表请求当前明确拒绝；不要移除门禁来迁就接口。
2. 从 compiled 规则列出 CN 必需宏观指标，再实现 NBS/PBC 发布与附件到数值序列的版本化解析；缺失数据继续保留缺口。
3. 将旧 fetch/compute/report 拆开，计算端只读已验证的插件 artifact；完成契约/覆盖门禁后再恢复 Agent 的官方评分与 state promotion。
4. 独立补全 Issue #1 的 FailureCategory、RunStatus、EvalResult、统一 ToolResult、SQLite 迁移、最小 trace repository 和 CLI 摘要，不关闭 Issue #1。

当前行为变化：Agent 仅有六个插件/规则工具；旧 MCP、网页、自由执行、文件、旧 state/记忆/SDK 工具历史不再进入取证上下文。旧人工 pipeline 仍可维护，但不能作为 Agent 绕过插件的入口。外部宿主若另有浏览器/网络工具，需在宿主侧同步约束。
