# Guideline - Context Reset Recovery

Use this file only as an index after context reset.

## Minimal Recovery

1. Read `AGENTS.md`.
2. Read `IDENTITY.md`.
3. Read `DATA_CONTRACT.md`.
4. Read `data/state/system_state.json`.
5. Choose one operation manual:
   - Macro daily scoring: `src/pipeline/OPERATIONS.md`
   - Industry chain research: `data/deepresearch/OPERATIONS.md`

Stop there unless the user asks for historical context.

## What Not To Load At Startup

- `MEMORY.md`
- `memory/`
- `journal/`
- `analysis/`
- root `sop/`
- `cockpit/`
- historical research reports

These are archives. Search or open them only when a task needs evidence.

## Active Architecture

```text
Macro scoring:
  data fetch -> dated raw data -> dated factor/risk outputs -> dated scores -> system_state

Industry research:
  gate -> source table -> technical baseline -> value-chain map -> bull/bear debate -> risk decision

AI allocation overlay:
  dated growth evidence -> momentum/sentiment/liquidity factors -> position state machine -> dated signal
```

## Official Macro Run

```bash
cd src/pipeline
uv run python fetch_etf_data.py --days=500
uv run python fetch_macro.py --date YYYYMMDD
uv run python fetch_us_macro.py --date YYYYMMDD
uv run python p1_upgrade.py --date YYYYMMDD
uv run python run_scoring.py --date YYYYMMDD
```

For official outputs, do not use `--allow-static-fallback`.
L2 is currently disabled because no official reproducible FF5/China factor source contract exists. Do not substitute search-derived or hard-coded factor proxies; Composite uses L1 × 4/7 + L3 × 3/7.

Use only two Python environments: root `.venv` for the host and `src/pipeline/.venv` for data/scoring. Do not create version-suffixed pipeline environments.
