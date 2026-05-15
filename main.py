"""
DailyLittingBot

A Telegram bot that fetches the LeetCode problem of the day, sends daily
notifications, gives progressive hints, and reviews user approaches without
dumping a full solution.
"""

from __future__ import annotations

import os
from datetime import time
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import BotCommand
from telegram.error import TelegramError
from telegram.request import HTTPXRequest
from telegram.ext import Application, ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from db import init_db
from leetcode_api import get_potd
from logger import logger
from telegram_handler import (
    button_handler,
    daily_potd_job,
    error_handler,
    hint_command,
    potd_command,
    start,
    subscribe,
    text_message_handler,
    unsubscribe,
)


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TZ_NAME = os.getenv("APP_TIMEZONE", "Asia/Kolkata").strip()
TIMEZONE = ZoneInfo(TZ_NAME)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it to .env before starting the bot.")


def build_app() -> Application:
    request = HTTPXRequest(
        read_timeout=60,
        write_timeout=60,
        connect_timeout=30,
        pool_timeout=30,
    )
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init).build()
        .request(request)
        .build()
    )
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CommandHandler("potd", potd_command))
    app.add_handler(CommandHandler("hint", hint_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_error_handler(error_handler)

    return app


async def post_init(app: Application) -> None:
    try:
        await app.bot.set_my_commands(
            [
                BotCommand("start", "Show bot help"),
                BotCommand("subscribe", "Receive daily POTD at 10:00 AM IST"),
                BotCommand("unsubscribe", "Stop daily POTD messages"),
                BotCommand("potd", "Show today's LeetCode problem"),
                BotCommand("hint", "Get the next progressive hint"),
            ]
        )
    except TelegramError as exc:
        logger.warning("Could not register Telegram command menu: %s", exc)

    if app.job_queue is None:
        logger.warning("Job queue is unavailable. Install python-telegram-bot[job-queue] for daily notifications.")
    else:
        app.job_queue.run_daily(
            daily_potd_job,
            time=time(hour=10, minute=0, tzinfo=TIMEZONE),
            name="daily_potd_10am_ist",
        )

    try:
        await get_potd()
    except Exception as exc:
        logger.warning("Could not preload POTD: %s", exc)


def main() -> None:
    init_db()
    app = build_app()
    logger.info("Starting DailyLittingBot...")
    app.run_polling()


if __name__ == "__main__":
    main()
