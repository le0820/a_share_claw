# AGENTS.md - Investment Research Harness Startup

This workspace is a host-independent investment research Harness. Its core is the research contracts, templates, workflows, evaluation and risk controls. Telegram is an optional input/output adapter. Codex, Claude Code, Meta Muse and WorkBuddy are target hosts; portable integrations are design targets until individually implemented and verified. See `HARNESS_DESIGN.md` for architecture and migration status.

## Startup Load Order

Load only these files at the start of a new task:

1. `README.md` - system map and active directory layout
2. `IDENTITY.md` - five-role research team and routing rules
3. `DATA_CONTRACT.md` - data source, date, and fallback rules
4. The authorized scoped state, if present; `data/state/system_state.json` is only a personal-mode state path, not an unconditional startup load for every host/user
5. `HARNESS_DESIGN.md` and `DATA_PLUGINS.md` for architecture, host adaptation or plugin work
6. `src/a_share_claw/RESEARCH_OPERATIONS.md` only when the task needs core execution detail; `src/pipeline/OPERATIONS.md` is the manual compatibility runbook. `data/deepresearch/OPERATIONS.md` is an archive, not a runtime dependency

Do not load `MEMORY.md`, `memory/YYYY-MM-DD.md`, `journal/`, `analysis/`, or full SOP prose during startup. Those are archives and evidence stores; open them only when a specific question requires them.

## Active Scope

The system has two core workflows:

- Macro daily scoring: L1/L2/L3/composite score, data audit, position band, daily report.
- Industry chain research: technical baseline, value-chain value capture, bull/bear debate, risk decision.

The system has one team model:

- Ping Heng routes and gates requests.
- Hong Guan interprets macro.
- Jia Zhi anchors fundamentals and value-chain economics.
- Ge Yan validates technical claims.
- Qian Zhan and Shen Du debate only when the question has genuine two-sided uncertainty.

## Hard Rules

- Form the research framework, output template and dated data requirements before choosing providers. Check authorized existing evidence, then acquire only the missing required data and explicitly selected optional evidence.
- Host identity and available tools do not automatically authorize state access or network calls. Map each request to `workspace + principal + session + agent_key`; adapters map legacy platform/user/chat identifiers without widening access.
- Keep data providers outside core research rules. A plugin may supply evidence but cannot change metric definitions, weights, risk gates, evaluators or official-state promotion rules.
- With no eligible provider, return a framework and gap report. Do not fabricate required data or publish an official action conclusion. Disabled factors such as L2 are policy states, not gaps to auto-fill.
- Every official output must state `as_of_date`, source files, release dates when known, and whether fallback data was used.
- Never answer a date-specific market question with data later than the requested date unless the user explicitly asks for a live update.
- No silent fallback. If exact data is missing, either stop or label a stale fallback explicitly.
- Primary fact acquisition follows the owner's latest source rule: NBS, PBC, easy-tdx, BEA and SEC; FRED is retained only as an explicitly enabled optional adapter. TickFlow is disabled by default and cannot be the primary market source; its retained auxiliary adapter has no core price promotion path. Legacy fetch/MCP/Bash/file tools must not be re-exposed as a shortcut. See `DATA_PLUGINS.md` for capability limitations.
- Do not revive creative writing scripts or identities.
- Treat cross-session and cross-user context leakage as a bug.

## Editing Policy

Keep this workspace small. New docs should be short, indexed, and connected to the two active workflows. Put dated pipeline outputs under `data/`, research reports under `data/research/output/`, and one-off archives under existing archive folders without adding them to startup context.

Clearly label proposed interfaces and missing implementations. E0 contracts, SQLite trace, macro/AI policy, core company/industry execution, independent price statistics, outlook templates and mixed slice orchestration are implemented, with verified JSON/Markdown delivery and constrained model-assisted framework compilation and cancellation-safe async/chat core entry; see E0_INFRA.md for acceptance and remaining real model-quality and data bindings. The full Issue #1 evaluation/proposal framework remains unfinished. Do not mark unverified publication/native financial payloads as official scoring inputs. Complete core policy and business gates before expanding data-source integrations.
