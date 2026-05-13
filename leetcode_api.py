import re
import requests
import html
from typing import Any
import asyncio
from db import load_cached_potd, cache_potd

LEETCODE_GRAPHQL_URL = "https://leetcode.com/graphql"
LEETCODE_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (DailyLittingBot; +https://t.me/DailyLittingBot)",
    "Referer": "https://leetcode.com/",
}

def strip_html(text: str) -> str:
    text = html.unescape(text)
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_potd_sync() -> dict[str, Any]:
    query = """
    query questionOfToday {
      activeDailyCodingChallengeQuestion {
        date
        link
        question {
          title
          titleSlug
          difficulty
          content
          topicTags {
            name
          }
        }
      }
    }
    """

    resp = requests.post(
        LEETCODE_GRAPHQL_URL,
        json={"query": query},
        headers=LEETCODE_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()

    if "errors" in payload:
        raise RuntimeError(f"LeetCode GraphQL error: {payload['errors']}")

    data = payload["data"]["activeDailyCodingChallengeQuestion"]
    question = data["question"]

    return {
        "date": data["date"],
        "slug": question["titleSlug"],
        "title": question["title"],
        "difficulty": question["difficulty"],
        "content": strip_html(question["content"]),
        "topics": [t["name"] for t in question.get("topicTags", [])],
        "url": f"https://leetcode.com{data['link']}",
    }


async def get_potd() -> dict[str, Any]:
    cached = load_cached_potd()
    try:
        today = await asyncio.to_thread(fetch_potd_sync)
    except Exception:
        if cached:
            return cached
        raise
    if cached and cached.get("slug") == today.get("slug"):
        return cached
    cache_potd(today)
    return today
