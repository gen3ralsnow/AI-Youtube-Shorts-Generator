"""Platform export profiles.

Limits are conservative published values at authoring time; the publishing
clients re-check live limits where the platform exposes them (TikTok
``creator_info.max_video_post_duration_sec``, Instagram publishing limit).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportProfile:
    name: str
    platform: str
    width: int = 1080
    height: int = 1920
    max_fps: int = 60
    min_duration: float = 3.0
    max_duration: float = 60.0
    max_size_bytes: int = 4 * 1024**3
    audio_bitrate: str = "192k"
    audio_rate: int = 48000
    thumbnail_size: tuple[int, int] | None = None


PROFILES: dict[str, ExportProfile] = {
    "youtube_shorts": ExportProfile(
        name="youtube_shorts", platform="youtube", max_duration=180.0,
        max_size_bytes=2 * 1024**3, thumbnail_size=(1280, 720),
    ),
    "tiktok": ExportProfile(
        name="tiktok", platform="tiktok", min_duration=3.0, max_duration=600.0,
        max_size_bytes=4 * 1024**3,
    ),
    "instagram_reels": ExportProfile(
        name="instagram_reels", platform="instagram", min_duration=3.0, max_duration=900.0,
        max_size_bytes=300 * 1024**2, audio_bitrate="128k", thumbnail_size=(1080, 1920),
    ),
}

PLATFORM_PROFILE = {"youtube": "youtube_shorts", "tiktok": "tiktok", "instagram": "instagram_reels"}

# The channel's own creative ceiling, independent of platform maximums.
CHANNEL_MAX_SECONDS = 60.0
