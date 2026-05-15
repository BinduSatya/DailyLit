from __future__ import annotations

import os
import tempfile
import wave

from dotenv import load_dotenv
from google import genai
from google.genai import types


load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_VOICE_MODEL = os.getenv("GEMINI_VOICE_MODEL", "gemini-3-flash-preview").strip()
GEMINI_VOICE_NAME = os.getenv("GEMINI_VOICE_NAME", "Kore").strip()

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def _write_wave_file(filename: str, pcm: bytes, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> None:
    with wave.open(filename, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(rate)
        wf.writeframes(pcm)


def text_to_voice_file(text: str) -> str:
    """Generate a spoken WAV file from text and return the temporary file path."""
    if client is None:
        raise RuntimeError("GEMINI_API_KEY is required to generate voice responses.")

    response = client.models.generate_content(
        model=GEMINI_VOICE_MODEL,
        contents=text.strip(),
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=GEMINI_VOICE_NAME,
                    )
                )
            ),
        ),
    )

    data = response.candidates[0].content.parts[0].inline_data.data
    temp = tempfile.NamedTemporaryFile(prefix="dailylit_voice_", suffix=".wav", delete=False)
    temp.close()
    _write_wave_file(temp.name, data)
    return temp.name
