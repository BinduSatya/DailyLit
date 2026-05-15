from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import Forbidden, RetryAfter, TelegramError
from telegram.ext import ContextTypes

from ai_text_helpers import (
    MAX_HINTS,
    answer_general_question,
    answer_user_question,
    explain_problem_text,
    hint_text,
    review_user_approach,
)
from ai_voice_helpers import text_to_voice_file
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
BUSY_CALLBACK = "busy"
MAX_TELEGRAM_MESSAGE_LENGTH = 3900
VOICE_ERROR = "I could not generate the voice response right now. Please try again shortly."


def _user_busy(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("busy"))


def _set_user_busy(context: ContextTypes.DEFAULT_TYPE, value: bool) -> None:
    context.user_data["busy"] = value


async def _show_button_loading(update: Update, text: str = "Working on it...") -> None:
    query = update.callback_query
    if not query or not query.message:
        return

    await query.answer(text)
    try:
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text, callback_data=BUSY_CALLBACK)]])
        )
    except TelegramError as exc:
        logger.debug("Could not update inline keyboard loading state: %s", exc)


def _split_telegram_text(text: str, limit: int = MAX_TELEGRAM_MESSAGE_LENGTH) -> list[str]:
    text = text.strip()
    if not text:
        return [""]

    chunks: list[str] = []
    remaining = text

    while len(remaining) > limit:
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


async def _reply_text_safely(
    message: Any,
    text: str,
    *,
    parse_mode: str | None = None,
    reply_markup: InlineKeyboardMarkup | None = None,
    disable_web_page_preview: bool | None = None,
) -> None:
    chunks = _split_telegram_text(text)
    for index, chunk in enumerate(chunks):
        await message.reply_text(
            chunk,
            parse_mode=parse_mode,
            reply_markup=reply_markup if index == 0 else None,
            disable_web_page_preview=disable_web_page_preview,
        )


async def _reply_voice_safely(message: Any, text: str) -> None:
    voice_path: str | None = None

    try:
        bot = message.get_bot() if hasattr(message, "get_bot") else None
        if bot:
            await bot.send_chat_action(
                chat_id=message.chat_id,
                action=ChatAction.RECORD_VOICE
            )

        voice_path = await asyncio.to_thread(
            text_to_voice_file,
            text
        )

        with open(voice_path, "rb") as voice:
            await message.reply_voice(
                voice=voice,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30,
                pool_timeout=30,
            )

    except Exception as exc:
        logger.exception(
            "Voice reply failed: %s",
            exc
        )

        await message.reply_text(
            "Voice generation/upload failed, sending text instead.\n\n"
            + text[:3500]
        )

    finally:
        if voice_path:
            try:
                os.remove(voice_path)

            except OSError as exc:
                logger.debug(
                    "Could not remove temporary voice file %s: %s",
                    voice_path,
                    exc,
                )


def _hint_choice_keyboard(next_hint_level: int) -> InlineKeyboardMarkup:
    next_hint_level = max(1, min(MAX_HINTS, next_hint_level))
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"Next hint ({next_hint_level})", callback_data="next_hint")],
            [InlineKeyboardButton(f"Voice hint ({next_hint_level})", callback_data="next_hint_voice")],
        ]
    )


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
    await _reply_text_safely(
        message,
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

    await _reply_text_safely(message, text, reply_markup=keyboard)


async def send_next_hint_voice(update_or_query: Any, context: ContextTypes.DEFAULT_TYPE, potd: dict[str, Any]) -> None:
    user = getattr(update_or_query, "effective_user", None) or getattr(update_or_query, "from_user", None)
    message = getattr(update_or_query, "effective_message", None) or getattr(update_or_query, "message", None)
    if not user or not message:
        return

    await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])
    current = await asyncio.to_thread(get_hint_level, user.id)
    next_level = min(MAX_HINTS, current + 1)

    text = await asyncio.to_thread(hint_text, potd["content"], potd["title"], next_level)
    await asyncio.to_thread(set_hint_level, user.id, next_level, potd["slug"])
    await _reply_voice_safely(message, text, f"{potd['title']} hint {next_level}")

    if next_level < MAX_HINTS:
        keyboard = _hint_choice_keyboard(next_level + 1)
    else:
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Review my approach", callback_data="review_approach")]])

    await message.reply_text("Want another hint?", reply_markup=keyboard)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return

    if query.data == BUSY_CALLBACK:
        await query.answer("Still working on your previous request.", show_alert=False)
        return

    if _user_busy(context):
        await query.answer("Please wait for the current request to finish.", show_alert=False)
        return

    _set_user_busy(context, True)
    await _show_button_loading(update)

    user = query.from_user
    if not user or not query.message:
        _set_user_busy(context, False)
        return

    try:
        await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

        potd = await get_potd()
        await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])

        if query.data == "explain_problem":
            text = await asyncio.to_thread(
                explain_problem_text,
                potd["content"],
                potd["title"],
                potd["difficulty"],
                potd["topics"],
            )
            await _reply_text_safely(query.message, text)
            hint_level = await asyncio.to_thread(get_hint_level, user.id)
            await query.message.reply_text("Ready for a hint?", reply_markup=_hint_choice_keyboard(hint_level + 1))
            return

        if query.data == "explain_problem_voice":
            text = await asyncio.to_thread(
                explain_problem_text,
                potd["content"],
                potd["title"],
                potd["difficulty"],
                potd["topics"],
            )
            await _reply_voice_safely(query.message, text, f"{potd['title']} explanation")
            hint_level = await asyncio.to_thread(get_hint_level, user.id)
            await query.message.reply_text("Ready for a hint?", reply_markup=_hint_choice_keyboard(hint_level + 1))
            return

        if query.data == "next_hint":
            await send_next_hint(query, context, potd)
            return

        if query.data == "next_hint_voice":
            await send_next_hint_voice(query, context, potd)
            return

        if query.data == "review_approach":
            await _reply_text_safely(
                query.message,
                "Paste your code or approach in the chat, and I will review it without giving the final answer."
            )
            return

        await _reply_text_safely(query.message, "I did not recognize that action.")
    except Exception as exc:
        logger.exception("Failed to handle button action: %s", exc)
        if query.data in {"explain_problem_voice", "next_hint_voice"}:
            await query.message.reply_text(VOICE_ERROR)
        else:
            await query.message.reply_text(
                FETCH_ERROR if query.data in {"explain_problem", "next_hint"} else "I hit an error."
            )
    finally:
        _set_user_busy(context, False)


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    text = (message.text or "").strip()
    if not text:
        return

    if _user_busy(context):
        await message.reply_text("Please wait for the current request to finish.")
        return

    _set_user_busy(context, True)

    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.TYPING)
        await asyncio.to_thread(upsert_user, user.id, user.username, user.first_name)

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

        asks_for_problem_help = any(
            phrase in text_lower
            for phrase in [
                "potd",
                "leetcode",
                "problem",
                "hint",
                "test case",
                "edge case",
                "complexity",
                "complexities",
                "constraint",
                "input",
                "output",
                "approach",
            ]
        )

        asks_for_next_hint = any(
            phrase in text_lower
            for phrase in ["hint", "next hint", "give me clue", "clue", "stuck", "how", "clues"]
        )

        if not (looks_like_code or asks_for_approach_review or asks_for_problem_help):
            reply = await asyncio.to_thread(answer_general_question, text)
            await _reply_text_safely(message, reply)
            return

        try:
            potd = await get_potd()
        except Exception as exc:
            logger.exception("Failed to fetch POTD for text answer: %s", exc)
            await message.reply_text(FETCH_ERROR)
            return

        await asyncio.to_thread(reset_hint_state_for_new_slug, user.id, potd["slug"])

        if looks_like_code or asks_for_approach_review:
            reply = await asyncio.to_thread(review_user_approach, potd["content"], text, potd["title"])
        elif asks_for_next_hint:
            next_level = min(MAX_HINTS, (await asyncio.to_thread(get_hint_level, user.id)) + 1)
            reply = await asyncio.to_thread(hint_text, potd["content"], potd["title"], next_level)
            await asyncio.to_thread(set_hint_level, user.id, next_level, potd["slug"])
        else:
            reply = await asyncio.to_thread(answer_user_question, potd["content"], text, potd["title"])

        await _reply_text_safely(message, reply)
    except Exception as exc:
        logger.exception("Failed to answer user text: %s", exc)
        await message.reply_text("I hit an error while generating the reply.")
    finally:
        _set_user_busy(context, False)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Update caused error: %s", context.error)
