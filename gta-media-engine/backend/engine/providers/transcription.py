"""Provider-independent speech-to-text: OpenAI transcription API or local faster-whisper."""

from __future__ import annotations

from pathlib import Path

from ..config import get_settings
from .http import NotConfigured, request


class Transcriber:
    name = "none"

    @property
    def available(self) -> bool:
        return False

    def transcribe(self, path: Path) -> dict:
        """Return {"text": str, "words": [{"word","start","end"}]}."""
        raise NotConfigured("No transcription provider configured (TRANSCRIPTION_PROVIDER)")


class OpenAITranscriber(Transcriber):
    name = "openai"

    def __init__(self, api_key: str):
        self.api_key = api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, path: Path) -> dict:
        if not self.api_key:
            raise NotConfigured("OPENAI_API_KEY is not set")
        with path.open("rb") as fh:
            resp = request(
                "POST",
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                data={
                    "model": "whisper-1",
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                },
                files={"file": (path.name, fh)},
                timeout=300,
            )
        body = resp.json()
        words = [
            {"word": w["word"], "start": w["start"], "end": w["end"]} for w in body.get("words", [])
        ]
        return {"text": body.get("text", ""), "words": words}


class LocalWhisper(Transcriber):
    name = "local"

    @property
    def available(self) -> bool:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
        return True

    def transcribe(self, path: Path) -> dict:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise NotConfigured("faster-whisper is not installed (pip install faster-whisper)") from exc
        model = WhisperModel("base", device="auto", compute_type="int8")
        segments, _ = model.transcribe(str(path), word_timestamps=True)
        words, text = [], []
        for seg in segments:
            text.append(seg.text)
            for w in seg.words or []:
                words.append({"word": w.word.strip(), "start": w.start, "end": w.end})
        return {"text": " ".join(text).strip(), "words": words}


def get_transcriber() -> Transcriber:
    s = get_settings()
    if s.transcription_provider == "openai":
        return OpenAITranscriber(s.openai_api_key)
    if s.transcription_provider == "local":
        return LocalWhisper()
    return Transcriber()
