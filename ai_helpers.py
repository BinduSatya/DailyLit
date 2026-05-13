from __future__ import annotations

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

from logger import logger


load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
MAX_HINTS = 10

SYSTEM_RULES = """
You are a DSA mentor inside a Telegram bot.

Hard rules:
- Never provide the full solution.
- Never give complete code.
- Never reveal the final optimal algorithm outright.
- Give only hints, explanations, corrections, and debugging guidance.
- If asked directly for the answer, refuse politely and continue with a hint.
- If the user pasted an approach or code, review it and point out mistakes.
- Keep responses concise, practical, and educational.
- For the LeetCode POTD, provide progressive hints numbered 1 to 10.
- Each hint should reveal a little more, but still not finish the problem.
"""

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def _ai_text(input_text: str, instructions: str) -> str:
    if not client:
        return "GEMINI API key is missing, so I cannot generate AI guidance right now."

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_RULES + "\n" + instructions),
            contents=input_text,
        )
    except Exception as exc:
        logger.exception("Gemini request failed: %s", exc)
        return "I could not generate AI guidance right now. Please try again shortly."

    return (getattr(response, "text", None) or getattr(response, "output_text", "") or "").strip()


def explain_problem_text(problem_text: str, title: str, difficulty: str, topics: list[str]) -> str:
    return _ai_text(
        input_text=problem_text,
        instructions=f"""
Explain this problem in plain English.
Title: {title}
Difficulty: {difficulty}
Topics: {', '.join(topics) if topics else 'N/A'}

Do not solve it. Do not give code. Focus on what the problem is asking, what the inputs mean,
what must be produced, and what constraints or patterns matter.
""",
    )


def hint_text(problem_text: str, title: str, hint_level: int) -> str:
    hint_level = max(1, min(MAX_HINTS, hint_level))
    return _ai_text(
        input_text=problem_text,
        instructions=f"""
Generate Hint #{hint_level} for this LeetCode problem.
Title: {title}

Rules:
- Do not solve the problem.
- Do not give code.
- Be progressive. Hint {hint_level} may be slightly more revealing than hint {hint_level - 1}.
- If hint_level is 10, you may get close to the solution, but still do not provide the final answer.
- Keep it short and clear.
""",
    )


def review_user_approach(problem_text: str, user_text: str, title: str) -> str:
    return _ai_text(
        input_text=f"Problem:\n{problem_text}\n\nUser approach/code:\n{user_text}",
        instructions=f"""
Review the user's approach for the LeetCode problem: {title}.

Rules:
- Do not provide the full solution.
- Do not give complete code.
- Point out what is correct, what is weak, what edge cases are missing, and what to think about next.
- If code is pasted, explain bugs or inefficiencies.
- Offer a better direction, but stop before final answer.
""",
    )


def answer_user_question(problem_text: str, user_text: str, title: str) -> str:
    return _ai_text(
        input_text=f"Problem:\n{problem_text}\n\nUser question:\n{user_text}",
        instructions=f"""
Answer the user's conceptual question about the LeetCode problem: {title}.

Rules:
- Do not solve the whole problem.
- Do not give code.
- Explain only the part they asked about.
- Stay within the problem context.
""",
    )
