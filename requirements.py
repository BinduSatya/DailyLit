"""Compatibility shim.

The installable dependency list lives in requirements.txt. This file remains so
older notes that mention requirements.py do not point at invalid Python.
"""

REQUIREMENTS = [
    "tzdata",
    "requests",
    "python-dotenv",
    "google-genai",
    "python-telegram-bot[job-queue]",
]
