from __future__ import annotations

import asyncio
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from ai_helpers import (
    MAX_HINTS,
    answer_user_question,
    explain_problem_text,
    hint_text,
    review_user_approach,
)
from db import (
    get_hint_level,
    get_subscribed_users,
    reset_hint_state_for_new_slug,
    set_hint_level,
    set_subscribed,
    upsert_user,
)
from leetcode_api import get_potd
from logger import logger
from message_formatter import build_problem_message, problem_keyboard


FETCH_ERROR = "I could not fetch today's LeetCode problem right now. Please try again shortly."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

    text = (
        "DailyLittingBot is alive.\n\n"
        "Commands:\n"
        "/subscribe - receive daily POTD at 10:00 AM IST\n"
        "/unsubscribe - stop daily messages\n"
        "/potd - show today's problem\n"
        "/hint - get the next hint\n\n"
        "This bot explains problems, gives progressive hints, and reviews approaches without "
        "dumping the final solution."
    )
    await message.reply_text(text)


async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)
    await asyncio.to_thread(set_subscribed, user.id, True)
    await message.reply_text("Subscribed. Daily POTD will arrive at 10:00 AM IST.")


async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)
    await asyncio.to_thread(set_subscribed, user.id, False)
    await message.reply_text("Unsubscribed. No more daily POTD messages.")


async def potd_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

    try:
        potd = await get_potd()
    except Exception as exc:
        logger.exception("Failed to fetch POTD: %s", exc)
        await message.reply_text(FETCH_ERROR)
        return

    await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])

    hint_level = await asyncio.to_thread(get_hint_level, user.id)
    await message.reply_text(
        build_problem_message(potd),
        parse_mode=ParseMode.HTML,
        reply_markup=problem_keyboard(max(1, hint_level + 1)),
        disable_web_page_preview=True,
    )


async def hint_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

    try:
        potd = await get_potd()
    except Exception as exc:
        logger.exception("Failed to fetch POTD for hint: %s", exc)
        await message.reply_text(FETCH_ERROR)
        return

    await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])
    await send_next_hint(update, context, potd)


async def daily_potd_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        potd = await get_potd()
    except Exception as exc:
        logger.exception("Failed to fetch daily POTD: %s", exc)
        return

    users = await asyncio.to_thread(get_subscribed_users)

    if not users:
        logger.info("No subscribed users found.")
        return

    msg = build_problem_message(potd)
    keyboard = problem_keyboard(1)

    for row in users:
        telegram_id = int(row["telegram_id"])
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
            await asyncio.to_thread(reset_hint_state_for_new_slug, telegram_id, potd["slug"])
        except Forbidden:
            logger.warning("User %s blocked the bot. Unsubscribing.", telegram_id)
            await asyncio.to_thread(set_subscribed, telegram_id, False)
        except RetryAfter as exc:
            await asyncio.sleep(exc.retry_after)
            await context.bot.send_message(
                chat_id=telegram_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except TelegramError as exc:
            logger.exception("Failed to send POTD to %s: %s", telegram_id, exc)


async def send_next_hint(update_or_query: Any, context: ContextTypes.DEFAULT_TYPE, potd: dict[str, Any]) -> None:
    user = getattr(update_or_query, "effective_user", None) or getattr(update_or_query, "from_user", None)
    message = getattr(update_or_query, "effective_message", None) or getattr(update_or_query, "message", None)
    if not user or not message:
        return

    await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])
    current = await asyncio.to_thread(get_hint_level, user.id)
    next_level = min(MAX_HINTS, current + 1)

    text = await asyncio.to_thread(hint_text, potd["content"], potd["title"], next_level)
    await asyncio.to_thread(set_hint_level, user.id, next_level, potd["slug"])

    if next_level < MAX_HINTS:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(f"Next hint ({next_level + 1})", callback_data="next_hint")]])
    else:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Review my approach", callback_data="review_approach")]])

    await message.reply_text(text, reply_markup=keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    if not user or not query.message:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

    try:
        potd = await get_potd()
    except Exception as exc:
        logger.exception("Failed to fetch POTD for button action: %s", exc)
        await query.message.reply_text(FETCH_ERROR)
        return

    await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])

    if query.data == "explain_problem":
        text = await asyncio.to_thread(
            explain_problem_text,
            potd["content"],
            potd["title"],
            potd["difficulty"],
            potd["topics"],
        )
        await query.message.reply_text(text)
        return

    if query.data == "next_hint":
        await send_next_hint(query, context, potd)
        return

    if query.data == "review_approach":
        await query.message.reply_text(
            "Paste your code or approach in the chat, and I will review it without giving the final answer."
        )


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    text = (message.text or "").strip()
    if not text:
        return

    await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

    try:
        potd = await get_potd()
    except Exception as exc:
        logger.exception("Failed to fetch POTD for text answer: %s", exc)
        await message.reply_text(FETCH_ERROR)
        return

    await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])

    text_lower = text.lower()
    looks_like_code = (
        "```" in text
        or "class " in text
        or "def " in text
        or "public " in text
        or "import " in text
        or "#include" in text
        or re.search(r"\bfor\s*\(.*;.*;.*\)", text) is not None
        or re.search(r"\bwhile\s*\(", text) is not None
    )

    asks_for_approach_review = any(
        phrase in text_lower
        for phrase in ["my approach", "approach", "can this work", "is this correct", "check my code", "debug", "bug"]
    )

    asks_for_concept_help = any(
        phrase in text_lower
        for phrase in ["why", "how", "what does", "explain", "concept", "intuition", "edge case", "complexity"]
    )

    try:
        if looks_like_code or asks_for_approach_review:
            reply = await asyncio.to_thread(review_user_approach, potd["content"], text, potd["title"])
        elif asks_for_concept_help:
            reply = await asyncio.to_thread(answer_user_question, potd["content"], text, potd["title"])
        else:
            next_level = min(MAX_HINTS, (await asyncio.to_thread(get_hint_level, user.id)) + 1)
            reply = await asyncio.to_thread(hint_text, potd["content"], potd["title"], next_level)
            await asyncio.to_thread(set_hint_level, user.id, next_level, potd["slug"])

        await message.reply_text(reply)
    except Exception as exc:
        logger.exception("Failed to answer user text: %s", exc)
        await message.reply_text("I hit an error while generating the reply.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Update caused error: %s", context.error)
