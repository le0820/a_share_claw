from __future__ import annotations

import argparse
import asyncio
import json

from .config import AppConfig
from .db import Storage
from .data_plugins.cli import add_data_parser, run_data


def main() -> None:
    parser = argparse.ArgumentParser(prog="a-share-claw")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="Run Telegram polling bot and scheduler")
    sub.add_parser("init-db", help="Initialize SQLite database")
    sub.add_parser("show-config", help="Print resolved non-secret config")
    add_data_parser(sub)
    chat = sub.add_parser("chat", help="Run one local chat turn")
    chat.add_argument("message")
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
    config.ensure_dirs()
    storage = Storage(config.database_path)
    storage.init()

    command = args.command or "run"
    if command == "init-db":
        print(f"Initialized {config.database_path}")
        return
    if command == "show-config":
        print(json.dumps(config.safe_dict(), ensure_ascii=False, indent=2))
        return
    if command == "chat":
        from .agent import InvestmentAgent
        agent = InvestmentAgent(config, storage)
        context = storage.get_or_create_context("local", "local-user", "local-chat", "local")
        print(await agent.run(context, args.message))
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
