"""Automated video quality control. Any failed 'blocker' or 'error' check rejects the export."""

from __future__ import annotations

from pathlib import Path

from . import ffmpeg
from .profiles import CHANNEL_MAX_SECONDS, ExportProfile
from .subtitles import validate_cues


def _check(name: str, passed: bool, detail: str, severity: str = "error") -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail, "severity": severity}


def inspect(path: Path, profile: ExportProfile, *, expected_duration: float | None = None,
            cues: list[dict] | None = None, placeholder_voice: bool = False) -> dict:
    checks: list[dict] = []
    if not path.is_file() or path.stat().st_size == 0:
        return {"passed": False, "checks": [_check("file_exists", False, "render missing or empty", "blocker")]}

    errors = ffmpeg.integrity_errors(path)
    checks.append(_check("file_integrity", not errors, "; ".join(errors[:3]) or "decodes cleanly", "blocker"))
    info = ffmpeg.probe(path)

    checks.append(_check("resolution", (info["width"], info["height"]) == (profile.width, profile.height),
                         f"{info['width']}x{info['height']} (want {profile.width}x{profile.height})"))
    aspect_ok = info["height"] and abs(info["width"] / info["height"] - 9 / 16) < 0.01
    checks.append(_check("aspect_ratio", aspect_ok, f"{info['width']}:{info['height']}"))
    fps_ok = any(abs(info["fps"] - f) < 0.1 for f in (30, 60)) and info["fps"] <= profile.max_fps + 0.1
    checks.append(_check("frame_rate", fps_ok, f"{info['fps']} fps"))
    checks.append(_check("video_codec", info["video_codec"] == "h264" and info["pix_fmt"] == "yuv420p",
                         f"{info['video_codec']} / {info['pix_fmt']}"))
    checks.append(_check("container_faststart", ffmpeg.moov_before_mdat(path), "moov atom before mdat",
                         "warning"))

    checks.append(_check("audio_present", info["has_audio"], info["audio_codec"] or "no audio stream"))
    if info["has_audio"]:
        checks.append(_check("audio_codec", info["audio_codec"] == "aac" and info["audio_rate"] == profile.audio_rate,
                             f"{info['audio_codec']} {info['audio_rate']} Hz"))
        vol = ffmpeg.volume_stats(path)
        peak = vol["max_volume_db"]
        checks.append(_check("audio_clipping", peak is not None and peak < -0.1,
                             f"max volume {peak} dB"))
        checks.append(_check("audio_not_silent", vol["mean_volume_db"] is not None and vol["mean_volume_db"] > -50,
                             f"mean volume {vol['mean_volume_db']} dB"))
        loud = ffmpeg.loudness(path)
        lufs, tp = loud.get("integrated_lufs"), loud.get("true_peak_db")
        checks.append(_check("loudness_target", lufs is not None and -16.5 <= lufs <= -11.5,
                             f"{lufs} LUFS (target -14)", "warning"))
        checks.append(_check("true_peak", tp is not None and tp <= -0.5, f"{tp} dBTP", "warning"))
        gaps = ffmpeg.silence_segments(path, -45, 1.5)
        checks.append(_check("no_dead_air", not gaps, f"{len(gaps)} silent gap(s) >= 1.5s", "warning"))

    blacks = ffmpeg.black_segments(path, 0.3)
    long_black = [b for b in blacks if b["duration"] >= 0.5 or b["start"] < 0.05]
    checks.append(_check("black_frames", not long_black,
                         f"{len(long_black)} black segment(s)" if long_black else "none"))

    dur = info["duration"]
    checks.append(_check("duration_platform", profile.min_duration <= dur <= profile.max_duration,
                         f"{dur:.2f}s (platform {profile.min_duration}-{profile.max_duration}s)"))
    checks.append(_check("duration_channel", dur <= CHANNEL_MAX_SECONDS + 1,
                         f"{dur:.2f}s (channel max {CHANNEL_MAX_SECONDS}s)"))
    if expected_duration:
        drift = abs(dur - expected_duration) / expected_duration
        checks.append(_check("duration_matches_narration", drift <= 0.08,
                             f"{dur:.2f}s vs narration {expected_duration:.2f}s ({drift:.0%})"))
    checks.append(_check("file_size", info["size"] <= profile.max_size_bytes,
                         f"{info['size'] / 1e6:.1f} MB (max {profile.max_size_bytes / 1e6:.0f} MB)"))

    if cues is not None:
        problems = validate_cues(cues, dur)
        checks.append(_check("subtitle_timing", not problems, "; ".join(problems[:3]) or f"{len(cues)} cues OK"))

    checks.append(_check("real_voiceover", not placeholder_voice,
                         "placeholder guide tone - configure a TTS provider" if placeholder_voice
                         else "provider narration", "blocker"))

    passed = all(c["passed"] for c in checks if c["severity"] in ("error", "blocker"))
    publishable = passed
    return {"passed": passed, "publishable": publishable, "probe": info, "checks": checks}


def technical_pass(report: dict) -> bool:
    """True when every check except the placeholder-voice blocker passed (used in TEST mode)."""
    return all(c["passed"] for c in report["checks"]
               if c["severity"] in ("error", "blocker") and c["name"] != "real_voiceover")
