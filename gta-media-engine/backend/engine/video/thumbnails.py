"""Thumbnail / cover generator (Pillow).

Thumbnails are composed from a frame of *our own rendered video* (therefore only
rights-cleared footage) plus original typography. Nothing imitates Rockstar's
official branding or presents itself as an official screenshot.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf",
    "/usr/share/fonts/opentype/inter/Inter-Black.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

BANNED_TEXT = re.compile(r"(?i)\b(official|confirmed|leaked footage|rockstar games presents)\b")


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def thumbnail_text(title: str, classification: str, max_words: int = 4) -> str:
    words = re.sub(r"(?i)\bgta\s*(vi|6)\b", "", title).split()
    stop = {"the", "a", "an", "of", "in", "to", "and", "is", "this", "that", "for", "on", "with"}
    keep = [w.strip(":|-,.!?") for w in words if w.lower() not in stop][:max_words]
    text = " ".join(keep).upper() or "GTA 6"
    if classification != "officially_confirmed":
        text = BANNED_TEXT.sub("", text).strip()
    if classification in ("community_theory", "unverified_rumour"):
        text = (text + "?") if not text.endswith("?") else text
    return text


def _cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = size
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = (line + " " + word).strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def compose(frame: Path, text: str, out: Path, size: tuple[int, int], variant: str, brand: dict) -> Path:
    primary, accent = _hex(brand.get("primary_color", "#FF3D7F")), _hex(brand.get("accent_color", "#3DD6FF"))
    base = _cover(Image.open(frame).convert("RGB"), size)
    base = ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.25)).enhance(1.12)
    w, h = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for y in range(h):  # bottom gradient for legibility
        alpha = int(210 * max(0.0, (y / h - 0.35) / 0.65))
        od.line([(0, y), (w, y)], fill=(5, 5, 12, alpha))
    img = Image.alpha_composite(base.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img)
    font_size = int(min(w, h) * (0.16 if variant == "A" else 0.13))
    font = _font(font_size)
    lines = _wrap(draw, text, font, int(w * 0.86))[:3]
    line_h = int(font_size * 1.05)
    block_h = line_h * len(lines)

    if variant == "A":  # bottom-left headline with accent bar
        y = h - block_h - int(h * 0.08)
        draw.rectangle([int(w * 0.06), y - 24, int(w * 0.06) + 140, y - 10], fill=primary)
        for i, line in enumerate(lines):
            draw.text((int(w * 0.06), y + i * line_h), line, font=font, fill="white",
                      stroke_width=max(3, font_size // 14), stroke_fill=(0, 0, 0))
    elif variant == "B":  # centered top headline, accent colour keyword
        y = int(h * 0.07)
        for i, line in enumerate(lines):
            tw = draw.textlength(line, font=font)
            fill = accent if i == 0 else "white"
            draw.text(((w - tw) / 2, y + i * line_h), line, font=font, fill=fill,
                      stroke_width=max(3, font_size // 12), stroke_fill=(0, 0, 0))
    else:  # "C": label panel
        y = h - block_h - int(h * 0.1)
        pad = 22
        widest = max(draw.textlength(line, font=font) for line in lines)
        panel = Image.new("RGBA", (int(widest + pad * 2), block_h + pad * 2), primary + (230,))
        img.alpha_composite(panel, (int(w * 0.05), y - pad))
        for i, line in enumerate(lines):
            draw.text((int(w * 0.05) + pad, y + i * line_h), line, font=font, fill="white")
    img = img.convert("RGB").filter(ImageFilter.UnsharpMask(radius=2, percent=60))
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=90, optimize=True)
    if out.stat().st_size > 2 * 1024 * 1024:  # YouTube custom thumbnail limit is 2 MB
        img.save(out, "JPEG", quality=78, optimize=True)
    return out


def concept(title: str, classification: str, brand: dict) -> dict:
    return {
        "main_subject": "highest-motion frame from the hook scene of the rendered video",
        "supporting_text": thumbnail_text(title, classification),
        "palette": [brand.get("primary_color"), brand.get("accent_color"), "#05050C", "#FFFFFF"],
        "composition": {
            "A": "bottom-left headline with brand accent bar over darkened lower third",
            "B": "centred top headline, first line in accent colour",
            "C": "solid brand-colour label panel, lower left",
        },
        "rules": "no official logos, no fake 'official' labels, speculation marked with '?'",
    }
