from __future__ import annotations

import html
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from ai_helpers import MAX_HINTS


def build_problem_message(potd: dict[str, Any]) -> str:
    topics = ", ".join(potd.get("topics") or []) or "N/A"
    safe_content = html.escape(potd.get("content", ""))[:3500]
    return (
        "<b>LeetCode POTD</b>\n\n"
        f"<b>Title:</b> {html.escape(potd['title'])}\n"
        f"<b>Difficulty:</b> {html.escape(potd['difficulty'])}\n"
        f"<b>Topics:</b> {html.escape(topics)}\n\n"
        f"<b>Problem statement:</b>\n{safe_content}\n\n"
        f"<b>Link:</b> {html.escape(potd['url'])}"
    )


def problem_keyboard(next_hint_level: int) -> InlineKeyboardMarkup:
    next_hint_level = max(1, min(MAX_HINTS, next_hint_level))
    rows = [
        [InlineKeyboardButton("Explain problem", callback_data="explain_problem")],
        [InlineKeyboardButton(f"Next hint ({next_hint_level})", callback_data="next_hint")],
        [InlineKeyboardButton("Review my approach", callback_data="review_approach")],
    ]
    return InlineKeyboardMarkup(rows)
