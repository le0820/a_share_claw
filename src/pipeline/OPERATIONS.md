# Macro Scoring Operations

This is the active runbook for macro daily scoring. It intentionally replaces older broad SOP-loading behavior.

## Required Context

Before running or interpreting scores, read:

1. `../../DATA_CONTRACT.md`
2. `../../data/state/system_state.json`
3. `../compiled/rules_L1.json`
4. `../compiled/rules_L2.json`
5. `../compiled/rules_L3.json`
6. `../compiled/weight_matrix.json`
7. `../compiled/data_source_map.json`

Do not load full SOP prose unless a compiled rule is ambiguous.

## Official Run

Set the report date explicitly:

First-time dependency sync:

```bash
uv sync --project src/pipeline
```

```bash
AS_OF_DATE=YYYYMMDD
cd src/pipeline
uv run python fetch_etf_data.py --days 500
uv run python fetch_macro.py --date "$AS_OF_DATE"
uv run python fetch_us_macro.py --date "$AS_OF_DATE"
uv run python p1_upgrade.py --date "$AS_OF_DATE"
uv run python run_scoring.py --date "$AS_OF_DATE"
uv run python generate_daily_report.py --date "$AS_OF_DATE"
```

Rules:

- `fetch_etf_data.py` uses `easy-tdx==1.20.4` context-managed clients and writes configured symbols to `data/raw/market/`; downstream scripts must filter to `trade_date <= AS_OF_DATE`.
- Historical CN/US macro fetches never call live current-vintage endpoints. They require exact archived artifacts.
- `p1_upgrade.py` writes dated easy-tdx momentum/risk diagnostics to `../../data/analysis/`.
- FF5 and China style-factor regression are disabled. Do not substitute unofficial proxies or hard-coded series.
- `run_scoring.py` refuses missing exact-date files by default and never injects a neutral score for a missing factor.
- `--allow-stale-fallback` may use newest file with date `<= AS_OF_DATE`; it must be labelled in `data_audit`.
- `--allow-static-fallback` is dry-run only and not acceptable for an official daily report.

### Market-session gate

For the current A-share market date, call `get_market_session_status` before an official run:

- `pre_market` / `trading`: do not run or publish the same-day official close score; return `WAIT_FOR_CLOSE`.
- `post_close`: if `inspect_data_audit` reports missing/incomplete exact outputs, run `run_macro_pipeline(stage="full")`.
- `historical`: an exact dated run is allowed regardless of the current intraday clock.

`inspect_data_audit` is preflight only. Missing files mean “not generated or not yet run”, not “the pipeline is unavailable”. A mixed macro + deepresearch request must continue its independent evidence-collection slice while the macro slice waits for close.

## Outputs

| Output | Path |
|:---|:---|
| CN raw macro | `../../data/raw/raw_macro_${AS_OF_DATE}.json` |
| US/web macro | `../../data/raw/raw_macro_us_${AS_OF_DATE}.json` |
| P1 momentum/risk diagnostics | `../../data/analysis/p1_upgrade_results_${AS_OF_DATE}.json` |
| L1/L2/L3/composite scores | `../../data/scores/scores_*_${AS_OF_DATE}.json` |
| Current state | `../../data/state/system_state.json` |
| **Structured report** | `../../data/reports/daily_report_${AS_OF_DATE}.md` (YAML frontmatter + 表格, 前端可解析) |

## Scoring Rules Module

L1 阈值逻辑已抽离为独立纯函数模块:

- `src/pipeline/rules_L1.py` 保存可执行阈值；所有权重从 `src/compiled/weight_matrix.json` 加载，避免双重事实来源。
- `src/pipeline/pipeline_universe.json` 是评分标的的唯一执行清单。
- 可选 tech-capex 输入为 `data/raw/tech_capex_${AS_OF_DATE}.json`，必须包含同日契约、`publication_date`、`source`、`quarter` 和 `capex_pct_ocf`。缺失时该因子不评分并重归一权重，不填中性 3。
- L2 当前为 `disabled`/`null`，Composite 权重为 0；L1/L3 按原 0.40:0.30 比例重归一为 4/7:3/7。
- `fundamental_${AS_OF_DATE}.json` 是可选研究证据，不参与当前每日评分。

## Daily Report Generation

`run_scoring.py` 只产出 JSON。**报告由 `generate_daily_report.py` 单独生成**:

```bash
cd src/pipeline
uv run python generate_daily_report.py --date "$AS_OF_DATE"
```

输出 `../../data/reports/daily_report_${AS_OF_DATE}.md`:
- **YAML frontmatter**: composite/l1/l2/l3/position_band/position_range/fallback_used 等元数据 → 前端直接解析。
- **数据派生表格**: L1 US/CN 因子、L2 禁用状态、L3 情绪/风险（全部来自 JSON，不伪造）。
- **五角色研判**: 自动串接各层 `assessment` 为"驱动摘要"；定性研判/多空辩论/最终风控动作由 LLM 在交付时填充 `<!-- TEAM_ANALYSIS -->` 标记位。

Each structured output should include `as_of_date` and `data_audit`.

## AI Sector Allocation Pipeline (P0/P1)

The AI pipeline is a separate growth-regime plus tactical-overlay workflow; it does not populate or replace L2.

```bash
AS_OF_DATE=YYYYMMDD
cd src/pipeline
uv run python fetch_ai_market.py --date "$AS_OF_DATE" --days 800
uv run python fetch_ai_growth.py --date "$AS_OF_DATE"
uv run python fetch_ai_macro.py --date "$AS_OF_DATE"
uv run python fetch_cn_sentiment.py --date "$AS_OF_DATE"
uv run python compute_ai_factors.py --date "$AS_OF_DATE"
uv run python run_ai_position.py --date "$AS_OF_DATE" --current-ai-pct 57.5
```

For a research-only historical replay without complete archived inputs, add `--allow-current-vintage-backtest` to `fetch_ai_macro.py`, `--allow-unverified-inputs` to `compute_ai_factors.py`, and `--allow-unverified` to `run_ai_position.py`. Every downstream artifact remains `unverified`; it must not update current state.

The `run_ai_strategy` agent tool applies the same flags and close gate. `stage=prices` archives current Azure GPU-instance and OpenRouter token-list-price catalogs; historical price catalogs are never reconstructed. These snapshots remain unscored until normalization and usage/availability coverage are sufficient.

| Output | Path |
|:---|:---|
| SEC growth input | `../../data/raw/ai/ai_growth_${AS_OF_DATE}.json` |
| AI ETF / benchmark manifest | `../../data/raw/ai/ai_market_${AS_OF_DATE}.json` |
| US/CN macro histories | `../../data/raw/ai/ai_macro_${AS_OF_DATE}.json` |
| Margin / option sentiment | `../../data/raw/ai/cn_sentiment_${AS_OF_DATE}.json` |
| Growth, momentum, sentiment, liquidity factors | `../../data/factors/ai/ai_factors_${AS_OF_DATE}.json` |
| Position signal | `../../data/signals/ai/ai_signal_${AS_OF_DATE}.json` |
| Event replay | `../../data/backtests/ai/ai_replay_*.json` |

Rules are compiled in `../compiled/ai_strategy_rules.json`; source capability and gaps are in `../compiled/ai_data_source_map.json`. A replay over a weekend request must map it to the last actual trading close and record that mapping in the replay audit.

## Interpretation Flow

1. Hong Guan: L1 macro drivers, data freshness, rate/inflation/liquidity interpretation.
2. Jia Zhi: optional fundamental evidence and the implications of the disabled L2 layer.
3. Ping Heng: final risk posture, position band, action/no-action decision.

Do not run Qian Zhan/Shen Du debate for daily scoring.

## Failure Handling

| Failure | Required Response |
|:---|:---|
| Missing exact CN macro/factor files | Re-run upstream with `--date`; do not use future files. |
| Current market date is pre-market or trading | Wait for the A-share close gate; continue any independent deepresearch slice. |
| Post-close audit reports missing exact files | Run the full pipeline; do not stop merely because the audit is incomplete. |
| Missing US/web fields | Populate `raw_macro_us_${AS_OF_DATE}.json` with dated source values. |
| Historical macro artifact missing | Restore an exact archived FRED/ALFRED or publisher artifact; never live-backfill with current revisions. |
| Optional factor missing | Record it in `omitted_factors` and use the compiled observed-weight rule; never impute 3. |
| Stale fallback needed | Use `--allow-stale-fallback`, record it, and downgrade confidence. |
| Static fallback needed | Dry run only. Label output as non-official. |
| Cross-source mismatch | Report both values and stop before action recommendation. |

## Report Template

```markdown
# Daily Score - YYYY-MM-DD

## 数据口径
- as_of_date:
- generated_at:
- source files:
- fallback_status:

## Scores
- L1:
- L2: disabled/null
- L3:
- Composite:

## Drivers
- Macro:
- Optional fundamentals / L2 disabled status:
- Sentiment/risk:

## Position Decision
- Band:
- Central:
- Action:
- Confidence:

## Watch Items
- Trigger:
- Next release:
```
