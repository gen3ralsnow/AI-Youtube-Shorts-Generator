"""Animated ASS subtitles: short word chunks, the spoken word highlighted, keywords accented,
plus a per-scene headline caption. Rendered with libass via ffmpeg's ``ass`` filter."""

from __future__ import annotations

import re
from pathlib import Path

STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "it", "this", "that", "for",
        "with", "at", "by", "be", "was", "are", "you", "i", "we", "they", "he", "she", "but", "so"}


def hex_to_ass(color: str, alpha: str = "00") -> str:
    color = color.lstrip("#")
    r, g, b = color[0:2], color[2:4], color[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def is_keyword(word: str) -> bool:
    w = re.sub(r"[^\w']", "", word)
    if not w:
        return False
    return bool(re.search(r"\d", w)) or (w[0].isupper() and w.lower() not in STOP) or w.isupper()


def chunk_words(words: list[dict], per_chunk: int = 3, max_gap: float = 0.45) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current and (len(current) >= per_chunk or w["start"] - current[-1]["end"] > max_gap
                        or re.search(r"[.!?]$", current[-1]["word"])):
            chunks.append(current)
            current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def build_ass(words: list[dict], scenes: list[dict], out_path: Path, *, brand: dict,
              width: int = 1080, height: int = 1920, duration: float | None = None) -> list[dict]:
    """Write the .ass file and return the cue list used for QC timing checks."""
    primary = hex_to_ass("#FFFFFF")
    highlight = hex_to_ass(brand.get("primary_color", "#FF3D7F"))
    accent = hex_to_ass(brand.get("accent_color", "#3DD6FF"))
    font = brand.get("font", "Inter")
    per_chunk = int(brand.get("caption_words_per_chunk", 3))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},86,{primary},{primary},&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,6,2,2,80,80,520,1
Style: Headline,{font},58,{primary},{primary},&H00000000,&HB4000000,-1,0,0,0,100,100,1,0,3,14,0,8,70,70,190,1
Style: Brand,{font},34,{accent},{accent},&H00000000,&H00000000,-1,0,0,0,100,100,2,0,1,2,0,9,60,60,70,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    cues: list[dict] = []
    sentence_start = {0} | {i + 1 for i, w in enumerate(words) if re.search(r"[.!?]$", w["word"])}
    for w_idx, w in enumerate(words):
        w["_kw"] = is_keyword(w["word"]) and (w_idx not in sentence_start or w["word"].isupper()
                                               or bool(re.search(r"\d", w["word"])))
    chunks = chunk_words(words, per_chunk)
    for n, chunk in enumerate(chunks):
        c_start, c_end = chunk[0]["start"], chunk[-1]["end"] + 0.08
        if n + 1 < len(chunks):  # never overlap the next chunk (two lines would stack on screen)
            c_end = min(c_end, chunks[n + 1][0]["start"])
        for i, active in enumerate(chunk):
            start = c_start if i == 0 else active["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else c_end
            if end - start < 0.04:
                continue
            parts = []
            for j, w in enumerate(chunk):
                word = _escape(w["word"]).upper()
                if j == i:
                    parts.append(f"{{\\c{highlight}\\fscx108\\fscy108}}{word}{{\\r}}")
                elif w.get("_kw"):
                    parts.append(f"{{\\c{accent}}}{word}{{\\r}}")
                else:
                    parts.append(word)
            pop = "{\\fscx92\\fscy92\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            events.append(f"Dialogue: 1,{ts(start)},{ts(end)},Sub,,0,0,0,,{pop}{' '.join(parts)}")
        cues.append({"start": round(c_start, 3), "end": round(c_end, 3),
                     "text": " ".join(w["word"] for w in chunk), "track": "subtitles"})

    for sc in scenes:
        caption = (sc.get("caption") or "").strip()[:48]
        if not caption or sc.get("section") == "ending":
            continue
        events.append(
            f"Dialogue: 0,{ts(sc['start'])},{ts(sc['end'])},Headline,,0,0,0,,"
            f"{{\\fad(120,120)}}{_escape(caption)}"
        )
        cues.append({"start": sc["start"], "end": sc["end"], "text": caption, "track": "headline"})

    if duration:
        events.append(f"Dialogue: 0,{ts(0)},{ts(duration)},Brand,,0,0,0,,{_escape(brand.get('name', '')).upper()}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return cues


def validate_cues(cues: list[dict], duration: float) -> list[str]:
    problems = []
    by_track: dict[str, list[dict]] = {}
    for c in cues:
        by_track.setdefault(c["track"], []).append(c)
        if c["end"] <= c["start"]:
            problems.append(f"cue ends before it starts: {c['text'][:30]}")
        if c["start"] < 0 or c["end"] > duration + 0.25:
            problems.append(f"cue outside video ({c['start']}-{c['end']} vs {duration:.2f}s): {c['text'][:30]}")
        if c["track"] == "subtitles" and c["end"] - c["start"] < 0.15:
            problems.append(f"cue too short to read: {c['text'][:30]}")
    for track, items in by_track.items():
        items.sort(key=lambda c: c["start"])
        for a, b in zip(items, items[1:]):
            if b["start"] < a["end"] - 0.005:
                problems.append(f"overlapping {track} cues at {b['start']:.2f}s")
    return problems
