"""Model-free JSON interface for external hosts and reproducible diagnostics."""
from __future__ import annotations

import json
from pathlib import Path

from . import DataRun, default_registry


def add_data_parser(sub):
    parser = sub.add_parser("data", help="Use the five data plugins without a model or Telegram")
    commands = parser.add_subparsers(dest="data_command", required=True)
    commands.add_parser("plugins", help="List plugin capabilities and credential readiness")
    for name in ("plan", "fetch"):
        command = commands.add_parser(name, help="Validate a plan" if name == "plan" else "Fetch only the declared requirements")
        command.add_argument("plan_file", type=Path)
    return parser


async def run_data(args, config) -> int:
    run = DataRun(default_registry().snapshot(), config.data_dir / "plugin_runs",
                  json.dumps([str(config.root_dir.resolve()), "local-cli"]))
    if args.data_command == "plugins":
        print(json.dumps({"plugins": run.list_providers()}, ensure_ascii=False, indent=2))
        return 0
    try:
        run.plan(json.loads(args.plan_file.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        print(json.dumps({"ok": False, "error_code": "invalid_plan"}))
        return 2
    if args.data_command == "fetch":
        for requirement_id in run.requirements:
            await run.fetch(requirement_id)
    summary = run.summary()
    run._save("summary.json", summary)
    print(json.dumps({**summary, "artifact_directory": str(run.directory), "results": run.results}, ensure_ascii=False, indent=2))
    return 0 if args.data_command == "plan" or summary["required_data_complete"] else 2
