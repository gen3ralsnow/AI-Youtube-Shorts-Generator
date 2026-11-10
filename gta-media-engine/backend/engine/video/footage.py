"""Footage rights register, analysis and script-aware clip selection.

Hard rules:
* An asset is only selectable when ``rights_verified`` is true, it has not
  expired, and every target platform is in ``allowed_platforms``.
* Imports are restricted to configured footage roots (no arbitrary paths, no
  downloading of third-party videos).
* Footage audio is never used in renders (game audio may contain licensed music).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..enums import AssetKind
from ..models import MediaAsset
from ..research.text import keyset
from . import ffmpeg

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


class RightsError(ValueError):
    pass


def allowed_roots() -> list[Path]:
    s = get_settings()
    roots = [Path(r).resolve() for r in s.footage_roots]
    roots.append((s.data_dir / "footage").resolve())
    return roots


def check_path(path: str | Path) -> Path:
    p = Path(path).resolve()
    if not any(p == r or r in p.parents for r in allowed_roots()):
        raise RightsError(f"{p} is outside the configured footage roots")
    if not p.is_file():
        raise RightsError(f"{p} does not exist")
    return p


def register_asset(session: Session, *, path: str, kind: str, original_source: str, owner: str,
                   licence: str, usage_permissions: str = "", attribution_required: str = "",
                   allowed_platforms: list[str] | None = None, monetization_allowed: bool = False,
                   is_official_material: bool = False, expires_at: datetime | None = None,
                   restrictions: str = "", tags: list[str] | None = None, description: str = "",
                   analyse: bool = True) -> MediaAsset:
    p = check_path(path)
    kind = AssetKind(kind).value
    ext = p.suffix.lower()
    expected = {"footage": VIDEO_EXTS, "music": AUDIO_EXTS, "sfx": AUDIO_EXTS, "image": IMAGE_EXTS}[kind]
    if ext not in expected:
        raise RightsError(f"{ext} is not a valid {kind} file type")
    for field_name, value in (("original_source", original_source), ("owner", owner), ("licence", licence)):
        if not value or not value.strip():
            raise RightsError(f"rights register requires '{field_name}'")
    if session.scalar(select(MediaAsset).where(MediaAsset.path == str(p))):
        raise RightsError("asset already registered")
    info = ffmpeg.probe(p) if kind != "image" else {"duration": 0, "width": 0, "height": 0, "fps": 0, "has_audio": False}
    if kind == "footage" and not info["width"]:
        raise RightsError("file has no video stream")
    if kind in ("music", "sfx") and not info["has_audio"]:
        raise RightsError("file has no audio stream")
    asset = MediaAsset(
        kind=kind, path=str(p), original_source=original_source.strip(), owner=owner.strip(),
        licence=licence.strip(), usage_permissions=usage_permissions, attribution_required=attribution_required,
        allowed_platforms=sorted(set(allowed_platforms or [])), monetization_allowed=monetization_allowed,
        is_official_material=is_official_material, expires_at=expires_at, restrictions=restrictions,
        rights_verified=False, tags=[t.lower() for t in (tags or [])], description=description,
        duration=info["duration"], width=info["width"], height=info["height"], fps=info["fps"],
        has_audio=info["has_audio"],
    )
    session.add(asset)
    session.flush()
    if analyse and kind == "footage":
        analyse_asset(asset)
    return asset


def verify_rights(asset: MediaAsset, actor: str) -> None:
    if not (asset.owner and asset.licence and asset.original_source):
        raise RightsError("cannot verify an incomplete rights record")
    if not asset.allowed_platforms:
        raise RightsError("set allowed_platforms before verifying rights")
    asset.rights_verified = True
    asset.verified_by = actor


def analyse_asset(asset: MediaAsset, transcriber=None) -> None:
    scenes = ffmpeg.detect_scenes(asset.path, 0.3, asset.duration)
    if len(scenes) <= 1 and asset.duration > 8:
        # Single long shot: window it so selection can still pick sections.
        step = 4.0
        scenes = [{"start": round(t, 3), "end": round(min(asset.duration, t + step), 3), "cut_score": 0.0}
                  for t in _frange(0, asset.duration - 1.0, step)]
    for sc in scenes[:60]:
        try:
            sc["motion"] = ffmpeg.motion_score(asset.path, sc["start"], sc["end"])
        except ffmpeg.FFmpegError:
            sc["motion"] = 0.0
    asset.scenes = scenes
    if transcriber is not None and getattr(transcriber, "available", False) and asset.has_audio:
        asset.transcript = transcriber.transcribe(Path(asset.path)).get("text", "")[:5000]


def _frange(start: float, stop: float, step: float):
    t = start
    while t < stop:
        yield t
        t += step


def usable(asset: MediaAsset, platforms: list[str], now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    if not asset.rights_verified:
        return False, "rights not verified"
    if asset.expires_at:
        exp = asset.expires_at if asset.expires_at.tzinfo else asset.expires_at.replace(tzinfo=timezone.utc)
        if exp <= now:
            return False, "licence expired"
    missing = set(platforms) - set(asset.allowed_platforms or [])
    if missing:
        return False, f"not licensed for {sorted(missing)}"
    if not Path(asset.path).is_file():
        return False, "file missing"
    return True, ""


def is_test_only(asset: MediaAsset) -> bool:
    """Assets marked for testing (e.g. synthetic demo footage) can render but never publish."""
    return "testing only" in (asset.usage_permissions or "").lower()


def usable_assets(session: Session, kind: str, platforms: list[str]) -> list[MediaAsset]:
    rows = session.scalars(select(MediaAsset).where(MediaAsset.kind == kind))
    return [a for a in rows if usable(a, platforms)[0]]


@dataclass
class Pick:
    scene_index: int
    asset_id: int
    path: str
    start: float
    duration: float
    score: float
    reason: str


def select_segments(scenes: list[dict], durations: list[float], assets: list[MediaAsset],
                    prefer_official: bool = False) -> list[Pick]:
    """Rank every (asset, shot) against each script scene and choose without reuse."""
    if not assets:
        raise RightsError("no rights-verified footage available for the target platforms")
    used: set[tuple[int, float]] = set()
    picks: list[Pick] = []
    for idx, (scene, need) in enumerate(zip(scenes, durations)):
        want = set(scene.get("visual", {}).get("keywords", [])) | keyset(scene.get("narration", ""))
        best: tuple[float, MediaAsset, dict, str] | None = None
        for asset in assets:
            vocab = set(asset.tags or []) | keyset(asset.description or "") | keyset(asset.transcript or "")
            overlap = len(want & vocab) / max(1, min(len(want), 6)) if want else 0.0
            for shot in asset.scenes or [{"start": 0.0, "end": asset.duration, "motion": 0.0}]:
                key = (asset.id, shot["start"])
                if key in used or asset.duration < need:
                    continue
                motion = shot.get("motion", 0.0)
                novelty = 1.0 / (1 + asset.times_used)
                official = 1.0 if (asset.is_official_material and prefer_official) else 0.0
                fits = 1.0 if shot["end"] - shot["start"] >= need else 0.6
                score = 0.5 * min(1.0, overlap) + 0.2 * motion + 0.1 * novelty + 0.1 * official + 0.1 * fits
                if best is None or score > best[0]:
                    reason = (f"keyword overlap {overlap:.2f}, motion {motion:.2f}, "
                              f"{'fits shot' if fits == 1 else 'extends past shot'}")
                    best = (score, asset, shot, reason)
        if best is None:
            # all shots used: allow reuse of the best asset at a different offset
            asset = max(assets, key=lambda a: a.duration)
            start = (idx * 3.7) % max(0.1, asset.duration - need)
            picks.append(Pick(idx, asset.id, asset.path, round(start, 3), need, 0.0, "reuse: footage pool exhausted"))
            continue
        score, asset, shot, reason = best
        start = shot["start"]
        if start + need > asset.duration:
            start = max(0.0, asset.duration - need)
        used.add((asset.id, shot["start"]))
        picks.append(Pick(idx, asset.id, asset.path, round(start, 3), round(need, 3), round(score, 3), reason))
    return picks
