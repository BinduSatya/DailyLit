from __future__ import annotations

import os
import re

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

Telegram response style:
- Write in plain, readable text for a mobile chat.
- Do not use Markdown headings like ###, bold markers like **text**, bullet stars, or horizontal separators.
- Do not use LaTeX or math delimiters like $S$, \(a, b\), or \text{limit}.
- Prefer simple forms like S, (a, b), nums[i], and limit.
- Use short paragraphs and simple numbered lists only when they genuinely help.
- Avoid decorative formatting. Keep symbols only when they are needed for code, variables, ranges, or formulas.
- Keep most replies under 2500 characters. If more detail is needed, ask the user if they want a deeper explanation.
"""

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def _clean_for_telegram(text: str) -> str:
    text = text.strip()

    # Remove LaTeX wrappers
    text = re.sub(r"\\\((.*?)\\\)", r"\1", text, flags=re.S)
    text = re.sub(r"\\\[(.*?)\\\]", r"\1", text, flags=re.S)
    text = re.sub(r"\$(.*?)\$", r"\1", text, flags=re.S)

    # Basic LaTeX cleanup
    replacements = {
        r"\\text\{([^{}]*)\}": r"\1",
        r"\\leq": "<=",
        r"\\geq": ">=",
        r"\\times": "x",
        r"\\neq": "!=",
        r"\\rightarrow": "->",
    }

    for pattern, repl in replacements.items():
        text = re.sub(pattern, repl, text)

    # Remove markdown headings/separators
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.M)

    # Remove bold/italic markers
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)", r"\1", text)

    # Normalize bullets
    text = re.sub(r"^\s*[*]\s+", "- ", text, flags=re.M)

    # Collapse excessive newlines
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


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

    raw_text = getattr(response, "text", None) or getattr(response, "output_text", "") or ""
    return _clean_for_telegram(raw_text)


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
Keep the reply under 2500 characters.
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
- Use plain Telegram text. No Markdown, no LaTeX, no decorative separators.
- Keep the reply under 1200 characters.
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
- Use plain Telegram text. No Markdown, no LaTeX, no decorative separators.
- Keep the reply under 2500 characters.
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
- Use plain Telegram text. No Markdown, no LaTeX, no decorative separators.
- Keep the reply under 2500 characters.
""",
    )


def answer_general_question(user_text: str) -> str:
    return _ai_text(
        input_text=user_text,
        instructions="""
You are a strictly limited LeetCode POTD assistant for Telegram.

You must ONLY respond to:
1. Questions directly related to the current LeetCode POTD.
2. Greetings and simple conversational messages like:
   - hi
   - hello
   - hey
   - bye
   - thank you

Strict Rules:
- Do NOT answer general knowledge questions.
- Do NOT answer coding questions unrelated to the current POTD.
- Do NOT answer math, science, history, politics, personal advice, or any unrelated topic.
- Do NOT generate essays, stories, opinions, or creative content.
- Do NOT roleplay or engage in unrestricted conversation.
- Do NOT answer questions about other LeetCode problems unless explicitly tied to the current POTD.
- If the user asks anything unrelated to the POTD, politely refuse.

For unrelated questions, reply EXACTLY:
"Solve this Problem first."

For greetings:
- Respond briefly and politely.
- Keep responses under 1 sentence.

For POTD-related questions:
- Be concise, clear, and technical.
- Give hints before full solutions when possible.
- Do not hallucinate problem details.
- If context is unclear, ask the user to reference the POTD explicitly.
- Use plain Telegram text. No Markdown, no LaTeX, no decorative separators.
- Try to keep the reply under 2000 characters.
""",
    )
