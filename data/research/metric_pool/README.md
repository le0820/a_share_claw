# AISDI 指标池维护入口

本指标池服务行业链研究，保留完整原规格111个候选字段。2026-10-08复核、AISDI截止2026-10-05：**40已获取／33可间接估计／38未获取**。取得样本不等于正式连续输入。

- `registry.json`：逐指标分类、来源家族、披露频率、历史描述、估计限制、更新方式及下一步复核要求。
- `sources.json`：7类已实现的公开快照抓取配置。源与指标的关联只表示候选证据，不能自动补齐原量。
- `evidence_index.json`、`snapshots/manifest.json`：39份已取得AISDI标准化事实／审计的索引及原始、打包后哈希；另冻结分类、时间轴、20支柱、35指数与原规格。完整原始网页／PDF保留在原本的本地`data/`，未上传GitHub。
- `STATUS.md`：完整目标的受阻状态及解除条件。

所有命令从仓库根目录运行。工具是**人工维护脚本**，没有接入核心工具allowlist、数据插件或正式评分。需要Python3.11+、curl和POSIX文件锁（当前验证macOS）；未安装定时任务。

## 校验与更新

```sh
python3 src/research_maintenance/metric_pool.py validate
python3 src/research_maintenance/public_snapshots.py --source ramp_tsm --fetch
python3 src/research_maintenance/public_snapshots.py --source all --fetch
python3 src/research_maintenance/metric_pool.py status --as-of 2026-10-08
```

抓取当前证据写入被Git忽略的`data/research/metric_pool/runtime/`：按SHA256保存不可覆盖原件，日志追加抓取时间、URL、验证结果；错误响应也保存，但隔离。进程退出码非0表示有来源失败。**不会使用旧快照假装本次成功**。Ramp另比较旧日期修订、新增与删除；日标签滚动7日量不可逐日累加。其他源仅验证各自既定schema，跨版本语义仍需复核。

`status --as-of`按UTC日期检查抓取时点，排除晚于截止日的版本；未知发布日期保持null。来源数据日期与抓取日期分别报告，快照成功不代表已观察到持续数值更新。Ramp日频5天／周频21天规则只诊断其样本时效，不提升为正式输入。其他未核定数据日期的来源不凭抓取时间宣称“新鲜”。

已有本地原件可离线导入，保留原始抓取时间：

```sh
python3 src/research_maintenance/public_snapshots.py --source all \
  --evidence-dir data/harness_acceptance/aisdi_full_support_20261005
```

此离线命令需要该原始目录；**干净Git checkout不包含这些原件**。干净checkout可校验冻结指标池、读取打包的标准化事实，并用`--fetch`获取新公开快照。

## 披露型指标的持续维护

其余指标按`registry.json`记录的月／季／事件披露频率检查同一机构的原始报告或合同。取得原件后，在授权的本地目录准备如下候选包；`series_id`固定一家公司／样本与口径，不能为凑历史合并主体、币种或季度／累计数据：

```json
{
  "schema_version": 1,
  "metric": "ai_cloud_revenue_usd",
  "series_id": "issuer-quarterly-revenue-v1",
  "unit": "USD",
  "population": "明确发行人和业务分部",
  "definition": "原生会计口径、合并范围及季报确认政策",
  "frequency": "quarterly",
  "source_url": "https://发行人原始报告地址",
  "captured_at": "带时区的真实ISO抓取时间",
  "source_payload_sha256": "真实原始文件SHA256",
  "records": [
    {"observation_date": "2026-06-30", "release_date": null,
     "value": 1, "qualifier": "exact"}
  ]
}
```

示例值仅说明schema，不是研究数据。`qualifier`必须保留exact／approximate／lower_bound／upper_bound／conditional。缺失值不导入、不变0；有界或条件值不变成精确值。取得数据后：

```sh
python3 src/research_maintenance/metric_pool.py import-series PACKET.json --raw ORIGINAL_FILE
```

导入校验原件哈希、指标身份、单位／样本／定义、三类日期、重复日期和有限数值；同series ID改变口径会拒绝。旧版永久保留，同一包重复导入不重复记日志。新日期、删除、修订均登记。导入仅为`unverified`候选；原始发布时间、币种、分母、有效供给状态、历史长度及原规格等价性仍须人工复核。

复核或分类变更须同时更新冻结快照、manifest哈希与registry，并运行`validate`；在PR中说明变化。既有日期无法可靠提取时明确保留未知，不能从文字或捕获时间猜测。每支柱篮子／权重、每侧60%有效覆盖及正式历史门禁独立维护，指标池不能改这些规则。
