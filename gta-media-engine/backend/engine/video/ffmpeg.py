"""Thin, testable wrappers around ffmpeg/ffprobe."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


class FFmpegError(RuntimeError):
    pass


def available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run(args: list[str], timeout: float = 1800) -> subprocess.CompletedProcess:
    cmd = [FFMPEG, "-hide_banner", "-nostdin", *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"ffmpeg timed out after {timeout}s") from exc
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-1500:]}")
    return proc


def probe(path: Path | str) -> dict:
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {proc.stderr[-500:]}")
    data = json.loads(proc.stdout or "{}")
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})
    fps = 0.0
    if video and video.get("avg_frame_rate", "0/0") != "0/0":
        num, den = video["avg_frame_rate"].split("/")
        fps = float(num) / float(den) if float(den) else 0.0
    return {
        "duration": float(fmt.get("duration") or (video or audio or {}).get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "format": fmt.get("format_name", ""),
        "width": int(video["width"]) if video else 0,
        "height": int(video["height"]) if video else 0,
        "fps": round(fps, 3),
        "video_codec": video.get("codec_name") if video else None,
        "pix_fmt": video.get("pix_fmt") if video else None,
        "has_audio": audio is not None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "audio_rate": int(audio.get("sample_rate", 0)) if audio else 0,
        "audio_bitrate": int(audio.get("bit_rate", 0) or 0) if audio else 0,
    }


def detect_scenes(path: Path | str, threshold: float = 0.3, duration: float | None = None) -> list[dict]:
    proc = run(["-i", str(path), "-an", "-vf", f"select='gt(scene,{threshold})',metadata=print",
                "-f", "null", "-"], timeout=1200)
    cuts: list[tuple[float, float]] = []
    pts = None
    for line in proc.stderr.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            pts = float(m.group(1))
        m = re.search(r"lavfi\.scene_score=([\d.]+)", line)
        if m and pts is not None:
            cuts.append((pts, float(m.group(1))))
            pts = None
    duration = duration if duration is not None else probe(path)["duration"]
    bounds = [0.0] + [c[0] for c in cuts] + [duration]
    scores = [0.0] + [c[1] for c in cuts]
    scenes = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        if end - start >= 0.5:
            scenes.append({"start": round(start, 3), "end": round(end, 3), "cut_score": round(scores[i], 3)})
    return scenes


def motion_score(path: Path | str, start: float, end: float) -> float:
    """Mean inter-frame luma difference (YDIF) over a segment, normalised to ~0..1."""
    proc = run(["-ss", f"{start:.3f}", "-t", f"{max(0.5, end - start):.3f}", "-i", str(path), "-an",
                "-vf", "fps=4,scale=320:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF",
                "-f", "null", "-"], timeout=300)
    vals = [float(v) for v in re.findall(r"lavfi\.signalstats\.YDIF=([\d.]+)", proc.stderr)]
    if not vals:
        return 0.0
    return round(min(1.0, (sum(vals) / len(vals)) / 25.0), 3)


def volume_stats(path: Path | str) -> dict:
    proc = run(["-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"], timeout=600)
    mean = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)
    peak = re.search(r"max_volume:\s*(-?[\d.]+|-inf) dB", proc.stderr)

    def _f(m):
        if not m or m.group(1) == "-inf":
            return None
        return float(m.group(1))

    return {"mean_volume_db": _f(mean), "max_volume_db": _f(peak)}


def loudness(path: Path | str) -> dict:
    proc = run(["-i", str(path), "-vn", "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                "-f", "null", "-"], timeout=600)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", proc.stderr, re.S)
    if not m:
        return {}
    data = json.loads(m.group(0))

    def _f(key):
        try:
            return float(data[key])
        except (KeyError, ValueError):
            return None

    return {"integrated_lufs": _f("input_i"), "true_peak_db": _f("input_tp"), "lra": _f("input_lra")}


def black_segments(path: Path | str, min_duration: float = 0.3) -> list[dict]:
    proc = run(["-i", str(path), "-an", "-vf", f"blackdetect=d={min_duration}:pix_th=0.10",
                "-f", "null", "-"], timeout=900)
    return [
        {"start": float(a), "end": float(b), "duration": float(c)}
        for a, b, c in re.findall(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", proc.stderr)
    ]


def silence_segments(path: Path | str, noise_db: float = -45, min_duration: float = 1.0) -> list[dict]:
    proc = run(["-i", str(path), "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
                "-f", "null", "-"], timeout=600)
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", proc.stderr)]
    return [{"start": s, "end": e} for s, e in zip(starts, ends)]


def integrity_errors(path: Path | str) -> list[str]:
    proc = subprocess.run([FFMPEG, "-v", "error", "-nostdin", "-i", str(path), "-f", "null", "-"],
                          capture_output=True, text=True, timeout=900)
    lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
    if proc.returncode != 0 and not lines:
        lines = [f"ffmpeg exited {proc.returncode}"]
    return lines[:20]


def moov_before_mdat(path: Path | str) -> bool:
    """True when the MP4 is 'fast start' (moov atom precedes mdat)."""
    with open(path, "rb") as fh:
        pos = 0
        size_total = Path(path).stat().st_size
        while pos < size_total:
            fh.seek(pos)
            header = fh.read(16)
            if len(header) < 8:
                return False
            size = int.from_bytes(header[:4], "big")
            kind = header[4:8]
            if size == 1:
                size = int.from_bytes(header[8:16], "big")
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size < 8:
                return False
            pos += size
    return False


def extract_frame(path: Path | str, at: float, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["-y", "-ss", f"{max(0.0, at):.3f}", "-i", str(path), "-frames:v", "1", "-q:v", "2", str(out)])
    return out
