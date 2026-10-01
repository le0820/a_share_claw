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

Research requirements own data meaning; providers supply evidence. First form the research framework and its dated data requirements, inspect authorized existing artifacts, and then attach only the plugins needed for the remaining gaps. The five-source runtime is implemented in [DATA_PLUGINS.md](DATA_PLUGINS.md). Legacy fetch scripts remain manual maintenance tools and are not available to the Agent.

- Each requirement identifies the capability/metric, universe, period/window, frequency, unit, adjustment method, availability cutoff, coverage threshold and required/optional status. Policy-disabled fields are not data gaps.
- A plugin declares its capabilities, schema/version, supported dates/vintages, permissions, credential references and limits. Core code chooses by capability and contract compatibility, not a fixed provider import.
- Provider replacement is permitted only when units, adjustment, universe, release timing and vintage satisfy the same requirement. Record the selection and any fallback; do not substitute a different economic series or loosen risk limits just because an interface is available.
- Plugin output retains the raw artifact and provenance: provider/plugin version, source URL/file, observation and publication/availability dates, retrieval time, content hash, fallback and truncation. Retrieval time never proves historical availability.
- Missing provenance, invalid schema, insufficient coverage and source disagreement remain explicit failures/gaps. An unconfigured plugin, backend error and `no_results` are different outcomes; none permits fabricated evidence.
- Required gaps block official scoring/action output. Optional omission follows the existing compiled observed-weight rules; L2 stays `disabled/null` until a separately reviewed rule change.
- Hot registration/removal applies to subsequent runs. Each active run pins its plugin/config versions and evidence snapshot; removal cannot silently switch providers or promote a partial result. Historical replay uses archived inputs, not the currently installed provider.

Model access alone is sufficient to develop the research framework, but it does not establish market or macro evidence. Local files and archived datasets may implement the same capability contract without an external network source.

## Current Agent Sources

| Capability | Selected source | Current eligibility |
| --- | --- | --- |
| China macro publications | NBS official website; PBC official website | Fixed native prose mapping; explicit current captures can hand off research facts; historical page revisions/attachments remain unverified |
| Requested index daily levels | easy-tdx, pinned SDK 1.20.4 | Explicit current index snapshots with native identity; core checks frozen calendar/coverage and computes statistics; historical publication/revision vintage is not certified |
| Auxiliary quote/bars/three statements | TickFlow, disabled by default | Current raw interfaces remain unverified; not the primary market source or core price handoff |
| US PCE headline/core release rates | BEA official Personal Income and Outlays release | Native BEA-reported monthly/year-on-year percentages retain separate *_reported metric identities; never relabel them as raw index levels or core-computed rates. Current capture does not certify historical revision vintage. |
| Optional US macro observations | explicitly enabled FRED with explicit realtime vintage | Historical selection stays date-level; separately planned current series/metadata captures can supply native PCE index levels; original observation release dates unknown |
| US corporate facts | SEC Company Facts + exact recent filing metadata | Current paired captures can hand off native CIK/concept/unit/duration/accession facts; filed/acceptance are separate from capture/public availability; historical selection stays unadmitted, missing concepts remain gaps |

The latest owner AGENTS instruction makes easy-tdx the primary market source and forbids TickFlow as primary. The default five active sources are NBS/PBC/easy-tdx/BEA/SEC; the retained TickFlow adapter requires explicit host enablement and stays auxiliary. No AkShare, Tavily, QVeris or generic web fallback is available to the Agent. Legacy scripts below describe the retained manual pipeline, not the plugin-only Agent path. All five-source runs are research-only until validated inputs are integrated into the deterministic scoring/evaluation gate.

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

The legacy manually supervised fundamental workflow may materialize `data/raw/fundamental_inputs_<as_of_date>.json` after Tavily/QVeris evidence collection. Each holding requires `symbol`, 0..1 `weight`, `effective_date`, `publication_date`, `source`, and `source_url`. Each fundamental row requires `symbol`, `period_end`, `filing_date`, `publication_date`, `source`, `source_url`, plus at least one supported metric. `fetch_fundamental.py` validates and aggregates this into `fundamental_<as_of_date>.json`; neither file participates in the currently disabled L2 layer.


## Monthly Release and Historical Fact Packs

Every monthly update must freeze the new observation and explicit comparable history together using research_spec.monthly_history. Admit source-bound historical facts before judging changes; missing history blocks a trend conclusion. Match entity, metric, native unit, monthly or cumulative period and source-version limits. Prefer a common published version and disclose separate releases or unresolved revisions. A historical observation captured today is not a certified historical information vintage.

The core calculates percentage-point differences between reported rates/levels. A change in YoY rates is not MoM growth; year-to-date rate comparisons do not reconstruct standalone months. Two observations show adjacent change only. Sparse official historical comparisons must not be interpolated into a continuous series. Legacy single-period research specifications remain observation-only and do not satisfy monthly-update acceptance. Cross-run and cross-day archive reuse still requires an explicit scoped re-admission design; no automatic cross-Scope injection is authorized.
