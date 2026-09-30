# Core Research Operations v1

This versioned protocol applies to macro scoring, company/industry research and their mixed slices. It defines execution requirements; it does not certify that every executor is implemented. DATA_CONTRACT.md and IDENTITY.md own the data and role rules. Archived deployment SOPs are not runtime dependencies.

## Active Contract

Before acquisition, freeze the question, workflow, as_of_date, output mode, authorized scope, report sections, required/optional/disabled evidence and stopping conditions. A change to the date, universe or evidence requirements creates a new recorded plan version. Never use model knowledge or user claims as admitted source evidence.

Every admitted fact belongs to one scoped, versioned packet and retains source/file, publication/observation dates, period, unit, fallback status and content hash. Roles reference fact IDs from that same packet; they cannot fetch independently or introduce facts during debate. Missing, stale, unverified or conflicting action-critical evidence yields NEED_EVIDENCE / NO_ACTION.

## Execution protocol

PLAN -> EVIDENCE_GATE -> COMPUTE_OR_SYNTHESIZE -> EVALUATE -> ARCHIVE -> PUBLISH.

1. PLAN: Ping Heng fixes scope and completion criteria. Relevant roles define questions and evidence requirements before selecting plugins. A no-plugin run can deliver this framework and explicit gaps.
2. EVIDENCE_GATE: accept only authorized artifacts that satisfy the frozen contract. Plugin retrieval success is not research completion; raw response text is not a normalized scoring input. Acquisition uses only host-authorized plugin capabilities and registered requirement IDs.
3. COMPUTE_OR_SYNTHESIZE: deterministic scoring uses compiled core policy. Research roles distinguish confirmed facts, interpretations and unknowns, citing packet fact IDs. No role can alter weights, thresholds, evaluator or formal state.
4. EVALUATE: the host checks dates, scope, completeness, references and risk constraints before delivery. Semantic quality judgment cannot override a failed hard gate. Correct denial proves the gate worked, not that the requested research was completed.
5. ARCHIVE: retain the plan, packet/version, source table, role outputs, report and evaluator evidence under scope/run_id. Core JSON and deterministic Markdown reports must both be validated and archived; if either report or archive fails, no official state is promoted.
6. PUBLISH: only the core publisher may update scoped official state after all required gates pass. Research/replay remain NO_ACTION. Model prose and plugin official_output_allowed=false cannot authorize promotion.

## Macro scoring

Ping Heng -> Hong Guan with Jia Zhi when relevant -> Ping Heng. Do not start bull/bear debate for daily scoring. Keep L2 disabled/null, compiled L1/L3 weights, coverage and existing position/risk rules. AI overlay remains a separate workflow; incomplete layers cannot become neutral scores.

The report declares as_of_date/generated_at, source table with known publication dates, fallback, L1/L3/composite, L2 status, main drivers, omitted inputs, position discipline and changes against an authorized prior score. If no comparable scoped prior score exists, report the comparison as unavailable.

A same-day official A-share close score waits for the market close. Cross-market observations retain their own dates. Do not run a legacy fetch pipeline through the Agent or inject the legacy global portfolio file as current evidence.

## Macro outlook and price statistics

Use a separate outlook contract for dated macro releases and explicitly named indices. Freeze the global cutoff, price window/anchor, market calendars and close times, units/adjustments, benchmark, metric definitions and forecast horizon before acquisition. Complete coverage means the frozen declared calendar is satisfied; trusted source/calendar review is still required. Never substitute daily scoring assets for requested indices or label a partial quarter complete.

Core computes price statistics from admitted histories and archives their inputs/derivation. Hong Guan interprets releases and derived metrics, Jia Zhi contributes when explicitly required, and Ping Heng sets falsification/monitoring conditions. A separate semantic reviewer must evaluate the same candidate before delivery. Keep source facts, core-derived statistics and conditional forecasts distinguishable. A forecast is not a new observation, a fabricated probability, a daily score or a trade action. Unsupported strategies remain explicit gaps; local price returns omit FX, dividends and fees.

## Company and industry research

Ping Heng gates and routes. Ge Yan provides a technical baseline when relevant. Jia Zhi identifies value capture, profit pools and bargaining power. Qian Zhan/Shen Du debate only genuine two-sided uncertainty after the common evidence gate; do not force debate for a factual query.

The report includes classification/scope, dated source table, technical baseline where required, value-chain map, evidence-backed bull/bear arguments when debate is required, unresolved conflicts, and Ping Heng's final risk decision, evidence confidence and falsification/monitoring triggers. A role output must identify its packet ID/version and evidence references. No numeric confidence, scenario probability or company allocation is fabricated from missing inputs.

## Mixed and quantitative requests

Mixed keeps macro and research slices independent. A WAIT_FOR_CLOSE or missing macro input must not suppress evidence work that can proceed independently. The final run identifies complete, waiting, blocked and failed slices; it cannot claim the combined task completed while a required slice is incomplete. Children remain non-publishing research/replay runs, even when the parent is official. Only the parent may publish mixed state after every required slice, combined semantic review against the original request, and report/archive gate pass. Preserve evaluated partial report descriptors as staged progress when the combined request fails.

Quant freezes symbols, window, frequency, unit, adjustment, benchmark and metric definitions before calculation. Missing coverage or an unimplemented computation yields an explicit gap, not an invented backtest. A market-history payload alone does not prove a quantitative study is complete.

## Delivery boundary

Final output is rendered by the host from evaluated results, with run_id and unresolved boundaries. Report readers first authorize scope, require a successful terminal run and business evaluators, and verify plan/report/computation identities and artifact hashes. A staged file never proves publication; only the exact scoped official history transaction does. Never announce task completion solely because a model-declared plan is empty or its fetches returned ok. Exceptions, cancellation and budgets become structured terminal statuses and preserve trace evidence. No trade execution, external messaging or automatic policy changes are authorized by this protocol.
