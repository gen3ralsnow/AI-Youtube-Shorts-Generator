"""Edit assembly and rendering with a single FFmpeg filter graph.

Video: per-scene trim -> 9:16 cover crop with a slow pan (subtle motion) ->
xfade transitions -> animated ASS subtitles.
Audio: processed narration + rights-cleared music (ducked under the voice with a
sidechain compressor) + rights-cleared SFX at scene boundaries -> EBU R128
loudness normalisation to -14 LUFS / -1.5 dBTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import ffmpeg
from .footage import Pick
from .profiles import PROFILES, ExportProfile

TRANSITIONS = {"fade": ("fade", 0.25), "swipe": ("slideleft", 0.22), "cut": ("fade", 0.04)}


def retime_scenes(scenes: list[dict], words: list[dict], total: float) -> list[dict]:
    """Assign each scene its real time span from narration word timings."""
    out, cursor = [], 0
    for i, sc in enumerate(scenes):
        n = len(sc["narration"].split())
        span = words[cursor:cursor + n]
        cursor += n
        start = 0.0 if i == 0 else (span[0]["start"] if span else out[-1]["end"])
        out.append({**sc, "start": round(start, 3), "end": None})
    for i in range(len(out)):
        out[i]["end"] = round(out[i + 1]["start"] if i + 1 < len(out) else total, 3)
        if out[i]["end"] - out[i]["start"] < 0.6:  # keep every shot readable
            out[i]["end"] = round(out[i]["start"] + 0.6, 3)
    for i in range(1, len(out)):
        out[i]["start"] = out[i - 1]["end"]
    out[-1]["end"] = max(out[-1]["end"], total)
    return out


@dataclass
class RenderPlan:
    picks: list[Pick]
    scenes: list[dict]
    narration: Path
    subtitles: Path
    total: float
    fps: int
    music: Path | None = None
    sfx: dict[str, Path] = field(default_factory=dict)
    music_volume: float = 0.22


def choose_fps(source_fps: list[float]) -> int:
    return 60 if source_fps and min(source_fps) >= 50 else 30


def build_command(plan: RenderPlan, out_path: Path) -> list[str]:
    args: list[str] = ["-y"]
    n = len(plan.picks)
    # xds[i] = overlap between clip i and clip i+1 (the incoming scene's transition)
    xds = [TRANSITIONS.get(plan.scenes[i + 1]["transition"], TRANSITIONS["fade"])[1] if i + 1 < n else 0.0
           for i in range(n)]
    clip_lens = []
    for i, pick in enumerate(plan.picks):
        length = (plan.scenes[i]["end"] - plan.scenes[i]["start"]) + xds[i]
        clip_lens.append(length)
        args += ["-ss", f"{pick.start:.3f}", "-t", f"{length + 0.1:.3f}", "-i", pick.path]
    args += ["-i", str(plan.narration)]
    narr_idx = n
    music_idx = None
    if plan.music:
        args += ["-stream_loop", "-1", "-i", str(plan.music)]
        music_idx = n + 1
    sfx_inputs: list[tuple[int, float]] = []
    next_idx = n + (2 if plan.music else 1)
    for sc in plan.scenes:
        name = sc.get("sfx")
        if name and name in plan.sfx:
            args += ["-i", str(plan.sfx[name])]
            sfx_inputs.append((next_idx, sc["start"]))
            next_idx += 1

    W, H = 1080, 1920
    filters: list[str] = []
    for i, length in enumerate(clip_lens):
        direction = "(iw-ow)*t/{d}" if i % 2 == 0 else "(iw-ow)*(1-t/{d})"
        x = direction.format(d=f"{max(length, 0.1):.3f}")
        filters.append(
            f"[{i}:v]scale={int(W * 1.08)}:{int(H * 1.08)}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H}:x='{x}':y='(ih-oh)/2',fps={plan.fps},setsar=1,format=yuv420p,"
            f"trim=duration={length:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
    last = "v0"
    offset = 0.0
    for i in range(1, n):
        kind, xd = TRANSITIONS.get(plan.scenes[i]["transition"], TRANSITIONS["fade"])
        offset += clip_lens[i - 1] - xds[i - 1]
        filters.append(f"[{last}][v{i}]xfade=transition={kind}:duration={xd}:offset={offset:.3f}[x{i}]")
        last = f"x{i}"
    subs = str(plan.subtitles).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    filters.append(f"[{last}]ass='{subs}',trim=duration={plan.total:.3f},setpts=PTS-STARTPTS[vout]")

    filters.append(f"[{narr_idx}:a]aresample=48000,aformat=channel_layouts=stereo,apad[narr]")
    mix_inputs = ["[narr_main]"]
    if music_idx is not None:
        filters.append("[narr]asplit=2[narr_main][narr_sc]")
        filters.append(
            f"[{music_idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={plan.music_volume},"
            f"afade=t=in:d=0.6[mus]"
        )
        filters.append("[mus][narr_sc]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=350[ducked]")
        mix_inputs.append("[ducked]")
    else:
        filters.append("[narr]anull[narr_main]")
    for k, (idx, at) in enumerate(sfx_inputs):
        ms = int(max(0.0, at - 0.05) * 1000)
        filters.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume=0.5,adelay={ms}|{ms}[sfx{k}]")
        mix_inputs.append(f"[sfx{k}]")
    filters.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:normalize=0,"
        f"atrim=duration={plan.total:.3f},afade=t=out:st={max(0.0, plan.total - 0.4):.3f}:d=0.4,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]"
    )
    args += [
        "-filter_complex", ";".join(filters), "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high",
        "-maxrate", "10M", "-bufsize", "20M", "-pix_fmt", "yuv420p", "-r", str(plan.fps),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{plan.total:.3f}",
        "-movflags", "+faststart", str(out_path),
    ]
    return args


def render(plan: RenderPlan, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = max(300, plan.total * 40)
    ffmpeg.run(build_command(plan, out_path), timeout=timeout)
    return out_path


def export_profile(master: Path, profile: ExportProfile | str, out_path: Path) -> Path:
    """Platform export: video stream copied from the master, audio re-encoded to the profile."""
    profile = PROFILES[profile] if isinstance(profile, str) else profile
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        "-y", "-i", str(master), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy",
        "-c:a", "aac", "-b:a", profile.audio_bitrate, "-ar", str(profile.audio_rate),
        "-movflags", "+faststart", str(out_path),
    ])
    return out_path


def process_narration(raw: Path, out: Path) -> Path:
    """Trim long pauses, clean low rumble, normalise speech level."""
    out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run([
        "-y", "-i", str(raw), "-af",
        "highpass=f=70,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05:"
        "stop_periods=-1:stop_duration=0.45:stop_threshold=-45dB,loudnorm=I=-16:TP=-2:LRA=9",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out),
    ])
    return out
