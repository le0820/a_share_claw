# DATA_CONTRACT.md - Data Source and Date Contract

## Core Rule

Every official score or research output must declare:

- `as_of_date`: the market or report date being answered.
- `generated_at`: when the agent generated the file.
- `source`: exact provider, script, URL, or file path.
- `source_timestamp`: the publication, trade, report, or retrieval timestamp.
- `data_period`: trade date, fiscal quarter, monthly release period, or rolling window.
- `fallback_status`: `none`, `stale_fallback`, `static_fallback`, or `unverified`.

If a user asks for July 1 market summary, data dated July 2 or later is forbidden unless the user explicitly asks for a live update.

## Data Capability and Plugin Boundary

Research requirements own data meaning; providers supply evidence. First form the research framework and its dated data requirements, inspect authorized existing artifacts, and then attach only the plugins needed for the remaining gaps. The plugin runtime is a design target in [HARNESS_DESIGN.md](HARNESS_DESIGN.md); current fetch scripts remain the compatibility path.

- Each requirement identifies the capability/metric, universe, period/window, frequency, unit, adjustment method, availability cutoff, coverage threshold and required/optional status. Policy-disabled fields are not data gaps.
- A plugin declares its capabilities, schema/version, supported dates/vintages, permissions, credential references and limits. Core code chooses by capability and contract compatibility, not a fixed provider import.
- Provider replacement is permitted only when units, adjustment, universe, release timing and vintage satisfy the same requirement. Record the selection and any fallback; do not substitute a different economic series or loosen risk limits just because an interface is available.
- Plugin output retains the raw artifact and provenance: provider/plugin version, source URL/file, observation and publication/availability dates, retrieval time, content hash, fallback and truncation. Retrieval time never proves historical availability.
- Missing provenance, invalid schema, insufficient coverage and source disagreement remain explicit failures/gaps. An unconfigured plugin, backend error and `no_results` are different outcomes; none permits fabricated evidence.
- Required gaps block official scoring/action output. Optional omission follows the existing compiled observed-weight rules; L2 stays `disabled/null` until a separately reviewed rule change.
- Hot registration/removal applies to subsequent runs. Each active run pins its plugin/config versions and evidence snapshot; removal cannot silently switch providers or promote a partial result. Historical replay uses archived inputs, not the currently installed provider.

Model access alone is sufficient to develop the research framework, but it does not establish market or macro evidence. Local files and archived datasets may implement the same capability contract without an external network source.

## Current Provider Mappings (Compatibility Defaults)

The table describes existing implementations and reference sources, not mandatory dependencies of the Harness core. Future plugins must preserve the semantic and date constraints in the Notes column; alternatives require an explicit eligible source mapping. Existing scoring keeps its current provider behavior until the plugin migration is implemented.

| Data Type | Primary Source | Backup / Verification | Notes |
|:---|:---|:---|:---|
| A-share/ETF daily K-line and quotes | `easy-tdx==1.20.4` | /caidazi | Current official-scoring adapter. Preserve QFQ semantics; this adapter uses context-managed `MacClient`/`MacExClient`. Other plugins must validate equivalent adjustment and date coverage. |
| HK/US market K-line used by scoring | `easy-tdx MacExClient` | web/FMP/ | Must record last trade date used. |
| CN macro monthly data | `akshare` script output or /cn_financial_pro | official publisher label | Record release period, not just fetch date. |
| US Treasury yields | FRED | FMP/web source cross-check | Current-date runs may use current-vintage FRED graph CSV. Historical runs require an archived FRED/ALFRED artifact; current revisions cannot backfill history. |
| VIX/SPX/Brent | FRED | web/FMP second source when action-relevant | Do not use embedded values for official reports. |
| Dollar / put-call | FRED `DTWEXBGS` broad dollar; SSE official daily option statistics | exact DXY from a separately archived verified source | `DTWEXBGS` is broader than DXY and is never relabelled DXY. SSE growth-option PCR is calculated only from products whose code begins `588`. |
| Hyperscaler capex / OCF / revenue / operating income | SEC Company Facts XBRL | company 10-Q/10-K | Only facts filed on or before `as_of_date` are eligible; company-wide revenue/profit are AI monetization proxies, not pure AI segment disclosure. |
| A-share financing sentiment | SSE + SZSE official margin reports | SSE-only official proxy when SZSE history is incomplete | Same-day close signals use the latest margin report already published by that close; no same-day unpublished balance is used. An SSE-only replay is `unverified`, uses one consistent full-window scope, and must never be spliced onto earlier SSE+SZSE rows. |
| AI tactical US macro | FRED (`DGS2/10/30`, `DFII5/10`, `VIXCLS`, `DTWEXBGS`, `BAA10Y`, `DCOILBRENTEU`, `NFCI`, CPI/PPI/PCE) | U.S. Treasury Daily Rates XML for newer nominal/TIPS observations; archived FRED/ALFRED vintage for history | ADD requires all 2Y/10Y/30Y/10Y TIPS moves over the same five-session window to be no higher; Brent >=5% over that window or >=$105/bbl vetoes ADD. Treasury rows may only append dates newer than the eligible FRED row and retain their own provenance. Historical current-vintage replay is allowed only by explicit flag and is always `unverified`. |
| China CPI/PPI for AI macro | National Bureau of Statistics series via AkShare | NBS release page/archive | Use a conservative 45-day availability lag when an exact release timestamp is unavailable. Historical current-vintage replay remains `unverified`. |
| GPU instance and token list price | Azure Retail Prices API; OpenRouter model catalog | provider official price pages | Snapshot-only. Do not score until a fixed GPU-quality/availability map or fixed token basket plus usage-volume series exists. |
| ETF fundamentals / constituents | issuer/index provider filings acquired by the Agent host | Tavily/QVeris evidence | Optional research artifact, not an L2 scoring prerequisite. The deterministic adapter requires at least 80% observed holdings weight and never estimates residual constituents. |
| Industry/company research | company filings, earnings calls, official docs | industry data/web search | Media and broker reports support, but do not replace primary documents. |

TickFlow is deprecated in this workspace. Do not load `skills/tickflow` or use TickFlow examples as current operating guidance.

## Date Alignment

The owner/scheduler timezone and the A-share market-date timezone are separate:

- `ASCLAW_TIMEZONE` is the owner's local timezone for host display and scheduled tasks; it is independent of the input adapter.
- `ASCLAW_MARKET_TIMEZONE` defaults to `Asia/Shanghai` and defines the A-share trading date used by `as_of_date` future-date checks.
- A request for the current China trading date is therefore valid even when the owner is still on the previous calendar date in `America/Los_Angeles`.
- A same-day official close score is not valid before the A-share close gate. Pre-market and intraday requests return `WAIT_FOR_CLOSE`; after close, missing dated outputs require an upstream full pipeline run rather than treating the audit as proof that the pipeline is unavailable.
- Intraday/provisional scoring is not implemented. If added later, it must use a separate output mode and explicit `fallback_status`/provisional label; it cannot overwrite an official close score.
- Cross-market inputs keep their own exact `observation_date`, `last_trade_date_used`, and `release_date`; the A-share market timezone must never relabel a US or HK observation.

The current compatibility pipeline uses one explicit `--date YYYYMMDD`; the target plugin path plans requirements first and fetches only unresolved inputs:

```bash
cd src/pipeline
uv run python fetch_etf_data.py --days 500
uv run python fetch_macro.py --date YYYYMMDD
uv run python fetch_us_macro.py --date YYYYMMDD
uv run python p1_upgrade.py --date YYYYMMDD
uv run python run_scoring.py --date YYYYMMDD
```

easy-tdx market histories live under `data/raw/market/`. `p1_upgrade.py`, RSI and every other technical calculation must filter them to `trade_date <= as_of_date`. P1 retains price momentum, historical VaR/CVaR, drawdown and correlation diagnostics only.

L2 factor scoring is disabled because the workspace cannot currently obtain official, reproducible FF5 monthly files, especially an official China factor series. L2 is emitted as `null`/`disabled`, has Composite weight `0`, and must not be silently replaced by momentum, valuation snapshots, search results, index proxies, or hard-coded constituent weights. Composite preserves the former L1:L3 ratio and uses `L1 × 4/7 + L3 × 3/7`.

`fetch_macro.py` and `fetch_us_macro.py` may fetch live only for the current A-share market date. A historical run must reuse an exact archived artifact. Dated runtime files live under `data/raw/`, `data/analysis/`, `data/scores/`, `data/reports/`, and `data/state/`. `run_scoring.py` must not use future files and a historical score must not overwrite a newer `system_state.json`.

Missing optional factors inside an active layer are represented as `null`, listed in `data_audit.omitted_factors`, and excluded by renormalizing observed compiled weights. Disabled layers are listed in `data_audit.omitted_layers`. They are never assigned a neutral score. L3 sentiment requires at least 50% of its compiled factor weight to be observed.

## AI Allocation Overlay

The AI strategy is independent of the disabled L2 layer:

- Growth determines the structural band. `expansion` has a 55%-60% base band and a 57.5% center; tactical additions may raise the allocation to 70%.
- Tactical scores have one direction only: momentum `1=overheated`, sentiment `1=euphoric`, liquidity `1=tightening`. High VIX and high put/call therefore reduce the sentiment-euphoria score.
- `ADD` requires all of: positive momentum turn confirmed for two closes, fearful sentiment, falling liquidity-tightening score, and at least two easing liquidity components.
- `REDUCE_TO_BASE` requires all of: high momentum, negative momentum turn, euphoric sentiment, and high/rising liquidity tightening.
- Missing layer coverage returns `NO_ACTION`; it is never converted to a neutral score. Growth contraction plus extreme tightening is the only hard-risk override of the normal growth floor.
- A non-trading requested date must record both `requested_date` and the last `effective_trade_date`. All inputs for a replay are cut off at the effective date, not at a later fetch date.

GPU and token snapshots are P1 observability inputs, not active growth factors. Analyst consensus revision data and a point-in-time free-float leverage denominator remain unavailable from a reliable free automated source; these fields stay `null` and are disclosed rather than estimated.

## Fallback Policy

| Situation | Allowed Action |
|:---|:---|
| Exact file exists | Use it and record `fallback_status: none`. |
| Exact file missing, older file exists | Stop by default. With explicit stale fallback, use newest file with date `<= as_of_date` and record it. |
| Only newer file exists | Stop. Future leakage is never allowed. |
| Required US/macro fields missing | Stop for official reports. Static fallback is only for dry runs and must be labelled. Optional compiled factors may be omitted only under the explicit observed-weight rule above. |
| Historical AI macro archive missing | Stop for official output. Explicit `--allow-current-vintage-backtest` may create a research-only `unverified` replay that cannot update `system_state.json`. |
| Cross-source disagreement exceeds tolerance | Report both values and do not produce a trade/action conclusion until reconciled. |

## Output Audit

Structured outputs should include a `data_audit` block with source file paths and fallback metadata. Markdown reports should include a short "数据口径" section with the same facts in human-readable form.

## Optional Fundamental Evidence Contract

The Agent host may materialize `data/raw/fundamental_inputs_<as_of_date>.json` after Tavily/QVeris evidence collection. Each holding requires `symbol`, 0..1 `weight`, `effective_date`, `publication_date`, `source`, and `source_url`. Each fundamental row requires `symbol`, `period_end`, `filing_date`, `publication_date`, `source`, `source_url`, plus at least one supported metric. `fetch_fundamental.py` validates and aggregates this into `fundamental_<as_of_date>.json`; neither file participates in the currently disabled L2 layer.
