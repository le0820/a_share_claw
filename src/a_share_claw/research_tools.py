from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import AppConfig
from .context import ConversationContext
from .research_context import resolve_system_state_path
from .utils import json_dumps, truncate


RULE_FILES = {
    "l1": "rules_L1.json",
    "l2": "rules_L2.json",
    "l3": "rules_L3.json",
    "weights": "weight_matrix.json",
    "sources": "data_source_map.json",
    "ai": "ai_strategy_rules.json",
    "ai_sources": "ai_data_source_map.json",
}

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(15, 0)


class ResearchRuntime:
    def __init__(self, config: AppConfig, context: ConversationContext):
        self.config = config
        self.context = context

    async def get_system_state(self) -> str:
        path, scope = resolve_system_state_path(self.config, self.context)
        if path is None:
            return json_dumps(
                {
                    "available": False,
                    "scope": scope,
                    "reason": "global portfolio state is withheld in multi-user contexts",
                }
            )
        data = _load_json(path)
        if data is None:
            return json_dumps({"available": False, "scope": scope, "path": str(path), "reason": "state file missing or invalid"})
        return truncate(
            json_dumps(
                {
                    "available": True,
                    "scope": scope,
                    "path": str(path),
                    "state_source_audit": _audit_state_source_paths(data, self.config.root_dir),
                    "state": data,
                }
            ),
            self.config.max_tool_output_chars,
        )

    async def get_compiled_rule(self, layer: str, section: str | None = None) -> str:
        key = layer.strip().casefold()
        filename = RULE_FILES.get(key)
        if filename is None:
            return json_dumps({"error": "unknown layer", "allowed": sorted(RULE_FILES)})
        path = self.config.compiled_dir / filename
        data = _load_json(path)
        if data is None:
            return json_dumps({"error": "rule file missing or invalid", "path": str(path)})

        selected: Any = data
        if section:
            selected, found = _select_json_section(data, section)
            if not found:
                return json_dumps(
                    {
                        "error": "section not found",
                        "layer": key,
                        "section": section,
                        "available_sections": list(data),
                    }
                )

        payload = {"layer": key, "path": str(path), "section": section, "rule": selected}
        rendered = json_dumps(payload)
        if len(rendered) <= self.config.max_tool_output_chars:
            return rendered
        return json_dumps(
            {
                "layer": key,
                "path": str(path),
                "section": section,
                "description": data.get("_description"),
                "version": data.get("_version"),
                "available_sections": list(data),
                "note": "Rule is larger than the tool output budget; request one section by name.",
            }
        )

    async def get_operation_manual(self, workflow: str, section: str | None = None) -> str:
        normalized = workflow.strip().casefold()
        if normalized in {"macro", "ai"}:
            path = self.config.pipeline_dir / "OPERATIONS.md"
        elif normalized == "industry":
            path = self.config.research_operations_path
        else:
            return json_dumps({"error": "unknown workflow", "allowed": ["macro", "ai", "industry"]})

        if not path.is_file():
            return json_dumps({"error": "operation manual missing", "path": str(path)})
        content = path.read_text(encoding="utf-8", errors="replace")
        if section:
            selected = _select_markdown_section(content, section)
            if selected is None:
                return json_dumps(
                    {
                        "error": "section not found",
                        "workflow": normalized,
                        "section": section,
                        "available_sections": _markdown_headings(content),
                    }
                )
            content = selected
        elif normalized == "industry":
            content = "# Industry operation index\n\n" + "\n".join(_markdown_headings(content))
        return truncate(content, self.config.max_tool_output_chars)

    async def run_macro_pipeline(
        self,
        as_of_date: str,
        stage: str = "full",
        allow_stale_fallback: bool = False,
        timeout_seconds: int = 900,
        *,
        now_utc: datetime | None = None,
    ) -> str:
        date = _validate_as_of_date(as_of_date, self.config.market_timezone, now_utc)
        normalized_stage = stage.strip().casefold()
        if normalized_stage not in {"full", "score"}:
            return json_dumps({"error": "stage must be full or score"})
        session = market_session_status(date, self.config.market_timezone, now_utc)
        if not session["official_run_allowed"]:
            return json_dumps(
                {
                    "ok": False,
                    "as_of_date": date,
                    "stage": normalized_stage,
                    "status": "blocked_by_market_session",
                    "reason": session["reason"],
                    "market_session": session,
                    "next_action": session["next_action"],
                    "note": "Missing audit files are not proof that the pipeline is unavailable.",
                }
            )

        date_args = ["--date", date]
        commands: list[list[str]] = []
        if normalized_stage == "full":
            commands.extend(
                [
                    ["fetch_etf_data.py", "--days", "500"],
                    ["fetch_macro.py", *date_args],
                    ["fetch_us_macro.py", *date_args],
                    ["p1_upgrade.py", *date_args],
                ]
            )

        score_command = ["run_scoring.py", *date_args]
        if allow_stale_fallback:
            score_command.append("--allow-stale-fallback")
        commands.append(score_command)

        if normalized_stage == "full":
            commands.append(["generate_daily_report.py", *date_args])

        run_result = await self._run_pipeline_commands(commands, timeout_seconds)
        audit = self._build_data_audit(date)
        audit["execution"] = pipeline_execution_plan(
            date,
            self.config.market_timezone,
            audit,
            now_utc,
        )
        run_result["as_of_date"] = date
        run_result["stage"] = normalized_stage
        run_result["allow_stale_fallback"] = allow_stale_fallback
        run_result["allow_static_fallback"] = False
        run_result["data_audit"] = audit
        return truncate(json_dumps(run_result), self.config.max_tool_output_chars)

    async def generate_daily_report(
        self,
        as_of_date: str,
        timeout_seconds: int = 120,
        *,
        now_utc: datetime | None = None,
    ) -> str:
        date = _validate_as_of_date(as_of_date, self.config.market_timezone, now_utc)
        session = market_session_status(date, self.config.market_timezone, now_utc)
        if not session["official_run_allowed"]:
            return json_dumps(
                {
                    "ok": False,
                    "as_of_date": date,
                    "status": "blocked_by_market_session",
                    "reason": session["reason"],
                    "market_session": session,
                    "next_action": session["next_action"],
                }
            )
        audit = self._build_data_audit(date)
        audit["execution"] = pipeline_execution_plan(
            date,
            self.config.market_timezone,
            audit,
            now_utc,
        )
        if not audit["score_inputs_ready"]:
            return json_dumps(
                {
                    "ok": False,
                    "as_of_date": date,
                    "reason": "exact dated score inputs are incomplete",
                    "data_audit": audit,
                }
            )
        result = await self._run_pipeline_commands([["generate_daily_report.py", "--date", date]], timeout_seconds)
        result["as_of_date"] = date
        result["report_path"] = str(self.config.reports_dir / f"daily_report_{date}.md")
        result["data_audit"] = audit
        return truncate(json_dumps(result), self.config.max_tool_output_chars)

    async def run_ai_strategy(
        self,
        as_of_date: str,
        stage: str = "full",
        current_ai_pct: float = 57.5,
        allow_current_vintage_backtest: bool = False,
        timeout_seconds: int = 900,
        *,
        now_utc: datetime | None = None,
    ) -> str:
        """Run the deterministic AI growth/tactical pipeline for one dated close.

        Historical FRED current-vintage data is deliberately opt-in and remains
        non-official all the way through the factor and position artifacts.
        """

        date = _validate_as_of_date(as_of_date, self.config.market_timezone, now_utc)
        normalized_stage = stage.strip().casefold()
        if normalized_stage not in {"full", "score", "prices"}:
            return json_dumps({"error": "stage must be full, score, or prices"})
        if not 0 <= current_ai_pct <= 100:
            return json_dumps({"error": "current_ai_pct must be between 0 and 100"})

        if normalized_stage != "prices":
            session = market_session_status(date, self.config.market_timezone, now_utc)
            if not session["official_run_allowed"]:
                return json_dumps(
                    {
                        "ok": False,
                        "as_of_date": date,
                        "stage": normalized_stage,
                        "status": "blocked_by_market_session",
                        "reason": session["reason"],
                        "market_session": session,
                        "next_action": session["next_action"],
                    }
                )

        commands: list[list[str]] = []
        if normalized_stage == "prices":
            commands.append(["fetch_ai_prices.py", "--date", date])
        else:
            if normalized_stage == "full":
                macro_command = ["fetch_ai_macro.py", "--date", date]
                if allow_current_vintage_backtest:
                    macro_command.append("--allow-current-vintage-backtest")
                commands.extend(
                    [
                        ["fetch_ai_market.py", "--date", date, "--days", "800"],
                        ["fetch_ai_growth.py", "--date", date],
                        macro_command,
                        ["fetch_cn_sentiment.py", "--date", date],
                    ]
                )
            factor_command = ["compute_ai_factors.py", "--date", date]
            position_command = [
                "run_ai_position.py",
                "--date",
                date,
                "--current-ai-pct",
                str(current_ai_pct),
            ]
            if allow_current_vintage_backtest:
                factor_command.append("--allow-unverified-inputs")
                position_command.append("--allow-unverified")
            commands.extend([factor_command, position_command])

        run_result = await self._run_pipeline_commands(commands, timeout_seconds)
        run_result.update(
            {
                "as_of_date": date,
                "stage": normalized_stage,
                "current_ai_pct": current_ai_pct,
                "allow_current_vintage_backtest": allow_current_vintage_backtest,
                "data_audit": self._build_ai_data_audit(date),
            }
        )
        return truncate(json_dumps(run_result), self.config.max_tool_output_chars)

    async def get_market_session_status(
        self,
        as_of_date: str,
        *,
        now_utc: datetime | None = None,
    ) -> str:
        date = _validate_as_of_date(as_of_date, self.config.market_timezone, now_utc)
        return json_dumps(market_session_status(date, self.config.market_timezone, now_utc))

    async def inspect_data_audit(
        self,
        as_of_date: str,
        *,
        now_utc: datetime | None = None,
    ) -> str:
        date = _validate_as_of_date(as_of_date, self.config.market_timezone, now_utc)
        audit = self._build_data_audit(date)
        audit["execution"] = pipeline_execution_plan(
            date,
            self.config.market_timezone,
            audit,
            now_utc,
        )
        return truncate(json_dumps(audit), self.config.max_tool_output_chars)

    async def _run_pipeline_commands(self, commands: list[list[str]], timeout_seconds: int) -> dict[str, Any]:
        python = _pipeline_python(self.config)
        timeout = max(1, min(timeout_seconds, 1800))
        results: list[dict[str, Any]] = []
        for command in commands:
            proc = await asyncio.create_subprocess_exec(
                python,
                *command,
                cwd=str(self.config.pipeline_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                results.append({"command": command, "returncode": None, "error": f"timed out after {timeout}s"})
                return {"ok": False, "commands": results}
            item = {
                "command": command,
                "returncode": proc.returncode,
                "stdout": _tail(stdout.decode("utf-8", errors="replace"), 600 if proc.returncode == 0 else 2000),
                "stderr": _tail(stderr.decode("utf-8", errors="replace"), 600 if proc.returncode == 0 else 2000),
            }
            results.append(item)
            if proc.returncode != 0:
                return {"ok": False, "commands": results}
        return {"ok": True, "commands": results}

    def _build_data_audit(self, date: str) -> dict[str, Any]:
        expected = {
            "cn_macro": self.config.raw_data_dir / f"raw_macro_{date}.json",
            "us_macro": self.config.raw_data_dir / f"raw_macro_us_{date}.json",
            "p1_analysis": self.config.analysis_dir / f"p1_upgrade_results_{date}.json",
            "score_l1": self.config.scores_dir / f"scores_L1_{date}.json",
            "score_l2": self.config.scores_dir / f"scores_L2_{date}.json",
            "score_l3": self.config.scores_dir / f"scores_L3_{date}.json",
            "score_composite": self.config.scores_dir / f"scores_composite_{date}.json",
        }
        files: dict[str, Any] = {}
        missing: list[str] = []
        fallbacks: list[str] = []
        future_sources: list[str] = []
        missing_audits: list[str] = []
        missing_source_metadata: list[str] = []
        date_mismatches: list[str] = []

        for label, path in expected.items():
            data = _load_json(path)
            if data is None:
                missing.append(label)
                continue
            meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
            embedded_date = data.get("as_of_date") or data.get("data_date") or data.get("_date") or meta.get("as_of_date")
            source = data.get("source") or meta.get("source")
            files[label] = {
                "path": str(path),
                "as_of_date": embedded_date,
                "source": source,
                "fallback_status": meta.get("fallback_status", "none"),
            }
            if embedded_date != date:
                date_mismatches.append(f"{label}={embedded_date}")
            fallbacks.extend(_find_fallback_markers(data, label))
            future_sources.extend(_find_future_observations(data, date, label))
            audit = data.get("data_audit")
            if not isinstance(audit, dict):
                audit = meta.get("data_audit")
            if (label.startswith("score_") or label.endswith("_analysis")) and not isinstance(audit, dict):
                missing_audits.append(label)
            if label in {"cn_macro", "us_macro"} and not source:
                missing_source_metadata.append(label)

        score_labels = {"score_l1", "score_l2", "score_l3", "score_composite"}
        score_inputs_ready = not (score_labels & set(missing))
        official_ready = not (
            missing
            or fallbacks
            or future_sources
            or missing_audits
            or missing_source_metadata
            or date_mismatches
        )
        return {
            "as_of_date": date,
            "files": files,
            "missing": missing,
            "fallbacks": sorted(set(fallbacks)),
            "future_sources": sorted(set(future_sources)),
            "missing_data_audit": missing_audits,
            "missing_source_metadata": missing_source_metadata,
            "date_mismatches": date_mismatches,
            "score_inputs_ready": score_inputs_ready,
            "official_ready": official_ready,
        }

    def _build_ai_data_audit(self, date: str) -> dict[str, Any]:
        expected = {
            "growth": self.config.raw_data_dir / "ai" / f"ai_growth_{date}.json",
            "market": self.config.raw_data_dir / "ai" / f"ai_market_{date}.json",
            "macro": self.config.raw_data_dir / "ai" / f"ai_macro_{date}.json",
            "sentiment": self.config.raw_data_dir / "ai" / f"cn_sentiment_{date}.json",
            "factors": self.config.data_dir / "factors" / "ai" / f"ai_factors_{date}.json",
            "signal": self.config.data_dir / "signals" / "ai" / f"ai_signal_{date}.json",
        }
        files: dict[str, Any] = {}
        missing: list[str] = []
        fallbacks: list[str] = []
        future_sources: list[str] = []
        date_mismatches: list[str] = []
        for label, path in expected.items():
            data = _load_json(path)
            if data is None:
                missing.append(label)
                continue
            embedded_date = data.get("as_of_date")
            status = data.get("fallback_status")
            if status is None and isinstance(data.get("data_audit"), dict):
                status = data["data_audit"].get("fallback_status")
            files[label] = {
                "path": str(path),
                "as_of_date": embedded_date,
                "effective_trade_date": data.get("effective_trade_date"),
                "source": data.get("source"),
                "fallback_status": status or "none",
                "official": data.get("official"),
            }
            if embedded_date != date:
                date_mismatches.append(f"{label}={embedded_date}")
            fallbacks.extend(_find_fallback_markers(data, label))
            future_sources.extend(_find_future_observations(data, date, label))
        official_ready = not (missing or fallbacks or future_sources or date_mismatches)
        return {
            "as_of_date": date,
            "files": files,
            "missing": missing,
            "fallbacks": sorted(set(fallbacks)),
            "future_sources": sorted(set(future_sources)),
            "date_mismatches": date_mismatches,
            "official_ready": official_ready,
            "optional_price_snapshot": str(self.config.raw_data_dir / "ai" / f"ai_prices_{date}.json"),
        }


def _validate_as_of_date(value: str, timezone: str, now_utc: datetime | None = None) -> str:
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("as_of_date must use YYYYMMDD")
    parsed = datetime.strptime(value, "%Y%m%d").date()
    reference = now_utc or datetime.now(ZoneInfo("UTC"))
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=ZoneInfo("UTC"))
    today = reference.astimezone(ZoneInfo(timezone)).date()
    if parsed > today:
        raise ValueError(f"as_of_date {value} is in the future relative to {today.isoformat()}")
    return value


def market_session_status(
    as_of_date: str,
    timezone: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    date = _validate_as_of_date(as_of_date, timezone, now_utc)
    reference = now_utc or datetime.now(ZoneInfo("UTC"))
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=ZoneInfo("UTC"))
    market_now = reference.astimezone(ZoneInfo(timezone))
    target_date = datetime.strptime(date, "%Y%m%d").date()

    retry_after: str | None = None
    if target_date < market_now.date():
        phase = "historical"
        official_run_allowed = True
        next_action = "RUN_OR_USE_EXISTING"
        reason = "historical dates are eligible for an exact dated official run"
    elif market_now.weekday() >= 5:
        phase = "non_trading_day"
        official_run_allowed = False
        next_action = "WAIT_FOR_TRADING_DAY"
        reason = "the current A-share market date is a weekend; no same-day close exists"
    elif market_now.time() < MARKET_OPEN:
        phase = "pre_market"
        official_run_allowed = False
        next_action = "WAIT_FOR_CLOSE"
        reason = "the current A-share market date has not opened and has no official close"
        retry_after = market_now.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0).isoformat()
    elif market_now.time() < MARKET_CLOSE:
        phase = "trading"
        official_run_allowed = False
        next_action = "WAIT_FOR_CLOSE"
        reason = "the current A-share market date is still trading and has no official close"
        retry_after = market_now.replace(hour=MARKET_CLOSE.hour, minute=MARKET_CLOSE.minute, second=0, microsecond=0).isoformat()
    else:
        phase = "post_close"
        official_run_allowed = True
        next_action = "RUN_OR_USE_EXISTING"
        reason = "the current A-share market date has reached the official close gate"

    return {
        "as_of_date": date,
        "market_timezone": timezone,
        "market_now": market_now.isoformat(),
        "phase": phase,
        "official_run_allowed": official_run_allowed,
        "next_action": next_action,
        "retry_after": retry_after,
        "reason": reason,
    }


def pipeline_execution_plan(
    as_of_date: str,
    timezone: str,
    audit: dict[str, Any],
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    session = market_session_status(as_of_date, timezone, now_utc)
    if not session["official_run_allowed"]:
        action = session["next_action"]
        should_run = False
        reason = session["reason"]
    elif audit.get("official_ready"):
        action = "USE_EXISTING_OUTPUTS"
        should_run = False
        reason = "exact dated official outputs already pass the data audit"
    else:
        action = "RUN_MACRO_PIPELINE"
        should_run = True
        reason = "the market-session gate is open and exact official outputs are missing or incomplete"
    return {
        "action": action,
        "pipeline_should_run": should_run,
        "missing_files_mean": "not_generated_or_not_yet_run",
        "reason": reason,
        "market_session": session,
    }


def _audit_state_source_paths(data: dict[str, Any], root: Path) -> dict[str, Any]:
    audit = data.get("data_audit")
    source_files = audit.get("source_files") if isinstance(audit, dict) else None
    if not isinstance(source_files, list):
        return {
            "ok": False,
            "status": "missing_source_files",
            "external_paths": [],
            "missing_paths": [],
        }

    resolved_root = root.resolve()
    external_paths: list[str] = []
    missing_paths: list[str] = []
    for item in source_files:
        raw_path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(raw_path, str) or not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            external_paths.append(raw_path)
        if not resolved.is_file():
            missing_paths.append(raw_path)

    ok = not external_paths and not missing_paths
    return {
        "ok": ok,
        "status": "current_repo" if ok else "legacy_external_or_missing",
        "external_paths": external_paths,
        "missing_paths": missing_paths,
        "instruction": (
            "Use state as verified evidence only after the current repository rebuilds its dated sources."
            if not ok
            else "Source paths are inside the current repository and exist."
        ),
    }


def _pipeline_python(config: AppConfig) -> str:
    configured = os.environ.get("ASCLAW_PIPELINE_PYTHON")
    if configured:
        return configured
    bundled = config.pipeline_dir / ".venv" / "bin" / "python"
    if bundled.is_file():
        return str(bundled)
    return sys.executable


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _select_json_section(data: dict[str, Any], selector: str) -> tuple[Any, bool]:
    current: Any = data
    for part in selector.split("."):
        if not isinstance(current, dict):
            return None, False
        exact = next((key for key in current if key.casefold() == part.strip().casefold()), None)
        if exact is None:
            return None, False
        current = current[exact]
    return current, True


def _markdown_headings(content: str) -> list[str]:
    return [match.group(0).strip() for match in re.finditer(r"(?m)^#{2,4}\s+.+$", content)]


def _select_markdown_section(content: str, selector: str) -> str | None:
    headings = list(re.finditer(r"(?m)^(#{2,4})\s+(.+)$", content))
    normalized = selector.casefold()
    for index, match in enumerate(headings):
        if normalized not in match.group(2).casefold():
            continue
        level = len(match.group(1))
        end = len(content)
        for later in headings[index + 1 :]:
            if len(later.group(1)) <= level:
                end = later.start()
                break
        return content[match.start() : end].strip()
    return None


def _find_fallback_markers(value: Any, path: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key == "fallback_status" and item not in (None, "none"):
                found.append(f"{child}={item}")
            elif key == "source" and isinstance(item, str) and item.strip().casefold().startswith(
                ("fallback", "static", "estimated", "hardcoded")
            ):
                found.append(f"{child}={item}")
            elif key in {"fallbacks", "static_fallback_fields"} and item:
                found.append(f"{child}=nonempty")
            found.extend(_find_fallback_markers(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_fallback_markers(item, f"{path}[{index}]"))
    return found


def _find_future_observations(value: Any, as_of_date: str, path: str) -> list[str]:
    found: list[str] = []
    as_of = datetime.strptime(as_of_date, "%Y%m%d").date()
    observed_keys = {
        "effective_date",
        "filing_date",
        "last_trade_date_used",
        "observation_date",
        "publication_date",
        "release_date",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in observed_keys and isinstance(item, str):
                parsed = _parse_loose_date(item)
                if parsed is not None and parsed > as_of:
                    found.append(f"{child}={item}")
            found.extend(_find_future_observations(item, as_of_date, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_future_observations(item, as_of_date, f"{path}[{index}]"))
    return found


def _parse_loose_date(value: str):
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(value[:10] if fmt == "%Y-%m-%d" else value, fmt).date()
        except ValueError:
            continue
    return None


def _tail(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return "[earlier output omitted]\n" + value[-max_chars:]
