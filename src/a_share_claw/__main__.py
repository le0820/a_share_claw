from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .aisdi import add_aisdi_parser, run_aisdi
from .config import AppConfig
from .db import Storage
from .data_plugins.cli import add_data_parser, run_data
from .harness.cli import add_harness_parser, run_harness


def main() -> None:
    parser = argparse.ArgumentParser(prog="a-share-claw")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Run Telegram polling bot and scheduler")
    sub.add_parser("init-db", help="Initialize SQLite database")
    sub.add_parser("show-config", help="Print resolved non-secret config")
    add_aisdi_parser(sub)
    add_data_parser(sub)
    add_harness_parser(sub)
    chat = sub.add_parser("chat", help="Run one local chat turn")
    chat.add_argument("message")
    chat.add_argument("--host-contract", type=Path, help="Explicit trusted-chat-host-v1 profile scoped to this exact conversation")
    chat.add_argument("--date", help="Explicit as-of date; must match the trusted host profile")
    chat.add_argument("--five-chart-contract",type=Path,help="Explicit scoped five-chart-host-v1 public source plan")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    try:
        asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("Stopped.")


async def async_main(args: argparse.Namespace) -> None:
    config = AppConfig.from_env()
    if args.command == "data":
        raise SystemExit(await run_data(args, config))
    if args.command == "aisdi":
        raise SystemExit(run_aisdi(args, config))
    config.ensure_dirs()
    storage = Storage(config.database_path)
    storage.init()

    if args.command in {"harness", "trace"}:
        try:
            raise SystemExit(run_harness(args, config, storage))
        finally:
            storage.close()

    command = args.command or "run"
    if command == "init-db":
        print(f"Initialized {config.database_path}")
        return
    if command == "show-config":
        print(json.dumps(config.safe_dict(), ensure_ascii=False, indent=2))
        return
    if command == "chat":
        from .agent import InvestmentAgent
        profile = None
        if getattr(args,"host_contract",None) is not None:
            from .chat_host import TrustedChatProfile
            try:
                profile = TrustedChatProfile.parse(json.loads(args.host_contract.read_text()))
            except (ValueError,OSError,KeyError,TypeError):
                print(json.dumps({"ok":False,"error_code":"invalid_chat_host_profile"}))
                storage.close()
                raise SystemExit(2)
        context = storage.get_or_create_context("local", "local-user", "local-chat", "local")
        chart_adapter=None
        if getattr(args,"five_chart_contract",None):
            from .data_plugins.five_chart_host import load_host_contract
            from .harness.contracts import Scope
            try:chart_adapter=load_host_contract(args.five_chart_contract,scope_key=Scope.from_context(config.root_dir,context).key,as_of_date=args.date,archive_root=config.data_dir/"five_chart_sources")
            except (ValueError,OSError,TypeError,KeyError):
                storage.close();raise SystemExit("invalid_five_chart_host_contract")
        agent = InvestmentAgent(config,storage,trusted_chat=profile,five_chart_adapter=chart_adapter,run_budget={"wall_clock_seconds":600,"max_tool_calls":200} if chart_adapter else None)
        try:
            outcome = await agent.run_result(context,args.message,as_of_date=getattr(args,"date",None))
            print(outcome.output)
            if profile is not None and outcome.status.value != "succeeded":
                raise SystemExit(2)
        finally:
            storage.close()
        return
    if command == "run":
        from .agent import InvestmentAgent
        from .scheduler import Scheduler
        from .telegram import TelegramBot
        agent = InvestmentAgent(config, storage)
        if not config.telegram_bot_token:
            raise SystemExit("TELEGRAM_BOT_TOKEN is required for run mode.")
        bot = TelegramBot(config.telegram_bot_token, storage, agent, config.telegram_allowed_user_ids)
        scheduler = Scheduler(config, storage, agent, bot.send_message)
        await asyncio.gather(bot.run(), scheduler.run())
        return
    raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
