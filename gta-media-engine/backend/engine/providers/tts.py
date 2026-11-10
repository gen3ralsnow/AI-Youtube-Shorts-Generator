"""Provider-independent text-to-speech.

Providers: ElevenLabs (with character timestamps), OpenAI speech, and
``placeholder``. The placeholder renders a quiet guide tone of the right length
so the pipeline can be exercised end to end; every placeholder voiceover is
flagged and quality control refuses to publish it.

Voice cloning of an identifiable person is not supported: only stock/designed
voice IDs that the administrator has rights to are accepted.
"""

from __future__ import annotations

import base64
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from .http import NotConfigured, request


@dataclass
class TTSResult:
    path: Path
    provider: str
    characters: int
    placeholder: bool = False
    # list of {"word", "start", "end"} when the provider returns alignment
    word_timings: list[dict] = field(default_factory=list)


def apply_pronunciations(text: str, mapping: dict[str, str]) -> str:
    # Longest keys first so "GTA VI" wins over "GTA V".
    for src in sorted(mapping, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(src)}(?!\w)", mapping[src], text)
    return text


def words_from_char_alignment(alignment: dict) -> list[dict]:
    chars = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])
    words, current, w_start, w_end = [], "", None, None
    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            if current:
                words.append({"word": current, "start": w_start, "end": w_end})
            current, w_start = "", None
            continue
        if w_start is None:
            w_start = st
        current += ch
        w_end = en
    if current:
        words.append({"word": current, "start": w_start, "end": w_end})
    return words


class TTSProvider:
    name = "base"

    @property
    def available(self) -> bool:
        return False

    def synthesize(self, text: str, voice: dict, out_path: Path) -> TTSResult:
        raise NotConfigured(f"TTS provider '{self.name}' is not configured")


class ElevenLabsTTS(TTSProvider):
    name = "elevenlabs"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, text: str, voice: dict, out_path: Path) -> TTSResult:
        if not self.api_key:
            raise NotConfigured("ELEVENLABS_API_KEY is not set")
        voice_id = voice.get("provider_voice_id")
        if not voice_id:
            raise NotConfigured("voice.provider_voice_id is not configured for ElevenLabs")
        resp = request(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps",
            headers={"xi-api-key": self.api_key, "content-type": "application/json"},
            params={"output_format": "mp3_44100_128"},
            json={
                "text": text,
                "model_id": self.model,
                "voice_settings": {
                    "stability": voice.get("stability", 0.45),
                    "similarity_boost": voice.get("similarity_boost", 0.8),
                    "style": voice.get("emotional_intensity", 0.35),
                    "speed": voice.get("speed", 1.0),
                    "use_speaker_boost": True,
                },
            },
            timeout=180,
        )
        body = resp.json()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(base64.b64decode(body["audio_base64"]))
        timings = words_from_char_alignment(body.get("alignment") or {})
        return TTSResult(out_path, self.name, len(text), word_timings=timings)


class OpenAITTS(TTSProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def synthesize(self, text: str, voice: dict, out_path: Path) -> TTSResult:
        if not self.api_key:
            raise NotConfigured("OPENAI_API_KEY is not set")
        resp = request(
            "POST",
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "voice": voice.get("provider_voice_id") or "onyx",
                "input": text,
                "speed": voice.get("speed", 1.0),
                "instructions": voice.get("narration_style", ""),
                "response_format": "mp3",
            },
            timeout=180,
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(resp.content)
        return TTSResult(out_path, self.name, len(text))


class PlaceholderTTS(TTSProvider):
    """Offline stand-in: a soft guide tone timed to the script. Never publishable."""

    name = "placeholder"

    @property
    def available(self) -> bool:
        return True

    def synthesize(self, text: str, voice: dict, out_path: Path) -> TTSResult:
        words = text.split()
        wpm = 165 * float(voice.get("speed", 1.0))
        seconds_per_word = 60.0 / wpm
        duration = max(1.0, len(words) * seconds_per_word + 0.4)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        expr = "0.12*sin(2*PI*220*t)*(0.5+0.5*sin(2*PI*2.6*t))"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", f"aevalsrc={expr}:s=44100:d={duration:.2f}",
                "-c:a", "libmp3lame", "-b:a", "128k", str(out_path),
            ],
            check=True,
        )
        timings, t = [], 0.2
        for word in words:
            timings.append({"word": word, "start": round(t, 3), "end": round(t + seconds_per_word * 0.92, 3)})
            t += seconds_per_word
        return TTSResult(out_path, self.name, len(text), placeholder=True, word_timings=timings)


def get_tts() -> TTSProvider:
    s = get_settings()
    if s.tts_provider == "elevenlabs":
        return ElevenLabsTTS(s.elevenlabs_api_key, s.elevenlabs_model)
    if s.tts_provider == "openai":
        return OpenAITTS(s.openai_api_key, s.openai_tts_model)
    return PlaceholderTTS()
