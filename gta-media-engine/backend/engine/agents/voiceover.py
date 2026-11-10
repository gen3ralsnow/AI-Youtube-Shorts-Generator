"""Agent 6: Voiceover - narration generation, processing, timing and validation."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import func, select

from .. import costs, settings_store
from ..config import get_settings
from ..models import ContentJob, Voiceover
from ..providers.transcription import get_transcriber
from ..providers.tts import get_tts
from ..video import ffmpeg
from ..video.assemble import process_narration
from ..video.profiles import CHANNEL_MAX_SECONDS
from .base import Agent, AgentBlocked, AgentContext
from .scriptwriter import BUCKETS, latest_script


class VoiceInput(BaseModel):
    job_id: int


class VoiceOutput(BaseModel):
    voiceover_id: int
    provider: str
    placeholder: bool
    raw_duration: float
    duration: float
    within_target: bool
    timing_source: str


def respell(text: str, mapping: dict[str, str]) -> tuple[str, list[tuple[list[int], int]]]:
    """Apply pronunciation fixes word-group by word-group.

    Returns the spoken text and groups of (original word indices, spoken word count)
    so provider word timings can be mapped back onto the *displayed* words.
    """
    words = text.split()
    keys = sorted(((k.split(), v.split()) for k, v in mapping.items()), key=lambda kv: -len(kv[0]))
    spoken: list[str] = []
    groups: list[tuple[list[int], int]] = []
    i = 0
    while i < len(words):
        matched = False
        for kwords, vwords in keys:
            n = len(kwords)
            window = [re.sub(r"[^\w']", "", w).lower() for w in words[i:i + n]]
            if window == [k.lower() for k in kwords]:
                trail = re.sub(r"^[\w']+", "", words[i + n - 1])  # keep punctuation after the term
                group_spoken = vwords[:-1] + [vwords[-1] + trail]
                spoken += group_spoken
                groups.append((list(range(i, i + n)), len(group_spoken)))
                i += n
                matched = True
                break
        if not matched:
            spoken.append(words[i])
            groups.append(([i], 1))
            i += 1
    return " ".join(spoken), groups


def map_timings(display_words: list[str], groups: list[tuple[list[int], int]], spoken_timings: list[dict]) -> list[dict]:
    out: list[dict] = []
    cursor = 0
    for orig_idx, n_spoken in groups:
        span = spoken_timings[cursor:cursor + n_spoken]
        cursor += n_spoken
        if not span:
            break
        start, end = span[0]["start"], span[-1]["end"]
        step = (end - start) / len(orig_idx)
        for k, idx in enumerate(orig_idx):
            out.append({"word": display_words[idx], "start": round(start + k * step, 3),
                        "end": round(start + (k + 1) * step, 3)})
    return out


def estimate_timings(display_words: list[str], start: float, end: float) -> list[dict]:
    """Character-weighted spread with sentence pauses, when no provider alignment exists."""
    weights = [len(w) + 2 + (6 if re.search(r"[.!?]$", w) else 0) for w in display_words]
    total = sum(weights) or 1
    t, out = start, []
    for w, wt in zip(display_words, weights):
        d = (end - start) * wt / total
        out.append({"word": w, "start": round(t, 3), "end": round(t + d * 0.9, 3)})
        t += d
    return out


class VoiceoverAgent(Agent[VoiceInput, VoiceOutput]):
    name = "voiceover"
    responsibility = "Generate consistent, correctly pronounced narration and validate it against the timeline."
    input_model, output_model = VoiceInput, VoiceOutput
    max_attempts = 2

    def execute(self, ctx: AgentContext, payload: VoiceInput) -> VoiceOutput:
        s = ctx.session
        job = s.get(ContentJob, payload.job_id)
        script = latest_script(s, payload.job_id)
        if job is None or script is None:
            raise AgentBlocked("no script to voice")
        voice = settings_store.get(s, "voice")
        settings = get_settings()
        tts = get_tts()
        display = script.voiceover_text
        spoken_text, groups = respell(display, voice.get("pronunciations", {}))

        version = (s.scalar(select(func.count()).select_from(Voiceover).where(Voiceover.job_id == job.id)) or 0) + 1
        raw_path = settings.data_dir / "audio" / "raw" / f"job{job.id}_v{version}.mp3"
        processed_path = settings.data_dir / "audio" / "processed" / f"job{job.id}_v{version}.wav"

        est = 0.0 if tts.name == "placeholder" else costs.estimate_tts(len(spoken_text))
        if est:
            costs.check_budget(s, est, job.id)
        result = tts.synthesize(spoken_text, voice, raw_path)
        if est:
            costs.record(s, category="text_to_speech", provider=tts.name, units=len(spoken_text),
                         unit="characters", estimated_usd=est,
                         actual_usd=costs.estimate_tts(result.characters), job_id=job.id)

        raw_dur = ffmpeg.probe(result.path)["duration"]
        process_narration(result.path, processed_path)
        dur = ffmpeg.probe(processed_path)["duration"]
        ratio = dur / raw_dur if raw_dur else 1.0
        display_words = display.split()

        timing_source = "estimated"
        timings: list[dict] = []
        if result.word_timings:
            spoken_timings = [{**w, "start": w["start"] * ratio, "end": w["end"] * ratio} for w in result.word_timings]
            if len(spoken_timings) == sum(n for _, n in groups):
                timings = map_timings(display_words, groups, spoken_timings)
                timing_source = f"{tts.name}_alignment"
        if not timings:
            transcriber = get_transcriber()
            span_start, span_end = 0.15, max(0.5, dur - 0.2)
            if transcriber.available:
                tr = transcriber.transcribe(processed_path)
                if tr["words"]:
                    span_start, span_end = tr["words"][0]["start"], tr["words"][-1]["end"]
                    timing_source = f"{transcriber.name}_transcription_span"
                    costs.record(s, category="transcription", provider=transcriber.name, units=dur,
                                 unit="seconds", estimated_usd=costs.estimate_transcription(dur),
                                 actual_usd=costs.estimate_transcription(dur), job_id=job.id)
            timings = estimate_timings(display_words, span_start, span_end)

        lo, hi = BUCKETS.get(job.duration_bucket, (15, 60))
        if dur > CHANNEL_MAX_SECONDS + 1:
            raise AgentBlocked(f"narration is {dur:.1f}s, above the {CHANNEL_MAX_SECONDS:.0f}s channel limit")
        within = dur <= hi * 1.1 and abs(dur - script.estimated_seconds) / max(1, script.estimated_seconds) < 0.35

        row = Voiceover(
            job_id=job.id, provider=result.provider, is_placeholder=result.placeholder,
            voice_settings={k: voice.get(k) for k in ("profile", "speed", "stability", "emotional_intensity",
                                                      "narration_style", "provider_voice_id")},
            raw_path=str(result.path), processed_path=str(Path(processed_path)),
            raw_duration=round(raw_dur, 3), duration=round(dur, 3), word_timings=timings,
        )
        s.add(row)
        s.flush()
        return VoiceOutput(voiceover_id=row.id, provider=result.provider, placeholder=result.placeholder,
                           raw_duration=round(raw_dur, 3), duration=round(dur, 3), within_target=within,
                           timing_source=timing_source)


def latest_voiceover(session, job_id: int) -> Voiceover | None:
    return session.scalar(select(Voiceover).where(Voiceover.job_id == job_id).order_by(Voiceover.id.desc()))
