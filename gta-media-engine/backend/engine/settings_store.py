"""Runtime operating settings stored in the database and editable from the dashboard.

The system boots in TEST mode with publishing paused. AUTONOMOUS mode can only be
entered through ``activate_autonomous`` after the launch checklist passes.
"""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from .enums import Category, OperatingMode
from .models import Setting

DEFAULTS: dict[str, Any] = {
    "mode": OperatingMode.TEST.value,
    "publishing_paused": True,  # global emergency pause, on until explicitly lifted
    "processing_paused": False,  # set automatically when a budget is exhausted
    "processing_pause_reason": "",
    "autonomous_activated_by": "",
    "autonomous_activated_at": None,
    "schedule": {
        "videos_per_day": 3,
        "timezone": "UTC",
        "publishing_windows": ["12:00-14:00", "17:00-20:00"],
        "platforms": {
            "youtube": {"enabled": True, "max_per_day": 3, "privacy_when_autonomous": "public"},
            "tiktok": {"enabled": True, "max_per_day": 3, "mode": "inbox_draft"},
            "instagram": {"enabled": True, "max_per_day": 3},
        },
        "research_interval_minutes": 60,
        "analytics_interval_minutes": 180,
    },
    "categories": {
        "enabled": [c.value for c in Category],
        "target_mix": {
            Category.DISCOVERIES.value: 0.30,
            Category.NEWS.value: 0.25,
            Category.THEORIES.value: 0.15,
            Category.ENTERTAINMENT.value: 0.15,
            Category.VERSUS.value: 0.15,
        },
        "priority": [Category.NEWS.value, Category.DISCOVERIES.value],
    },
    "processing": {
        "max_concurrent_jobs": 2,
        "stage_retry_limit": 2,
        "publish_retry_limit": 4,
        "min_topic_score": 55,
        "fact_confidence_threshold": 0.8,
        "duplicate_similarity_threshold": 0.6,
        "words_per_minute": 165,
    },
    "budgets": {
        "daily_usd": 10.0,
        "monthly_usd": 200.0,
        "per_video_usd": 1.5,
        "alert_ratio": 0.8,
    },
    "voice": {
        "profile": "narrator_main",
        "provider_voice_id": "",
        "speed": 1.05,
        "stability": 0.45,
        "similarity_boost": 0.8,
        "emotional_intensity": 0.35,
        "narration_style": "energetic, clear, conversational gaming-news narrator",
        "pronunciations": {
            "GTA VI": "G T A six",
            "GTA 6": "G T A six",
            "GTA V": "G T A five",
            "Leonida": "Lee-oh-nee-dah",
            "Vice City": "Vice City",
            "Jason": "Jason",
            "Lucia": "Loo-SEE-ah",
        },
    },
    "brand": {
        "name": "VI Signal",
        "primary_color": "#FF3D7F",
        "accent_color": "#3DD6FF",
        "font": "Inter",
        "caption_words_per_chunk": 3,
    },
    "analytics": {"min_sample_size": 5},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def get(session: Session, key: str) -> Any:
    row = session.get(Setting, key)
    default = DEFAULTS.get(key)
    if row is None:
        return copy.deepcopy(default)
    if isinstance(default, dict) and isinstance(row.value, dict):
        return _deep_merge(default, row.value)
    return row.value


def get_all(session: Session) -> dict[str, Any]:
    return {key: get(session, key) for key in DEFAULTS}


PROTECTED_KEYS = {"mode", "autonomous_activated_by", "autonomous_activated_at"}


def put(session: Session, key: str, value: Any, *, allow_protected: bool = False) -> Any:
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting: {key}")
    if key in PROTECTED_KEYS and not allow_protected:
        raise PermissionError(f"'{key}' can only change through the mode activation flow")
    _validate(key, value)
    row = session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    session.flush()
    return get(session, key)


def _validate(key: str, value: Any) -> None:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        return
    if isinstance(default, dict) and not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    if key == "budgets":
        for k, v in value.items():
            if not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"budgets.{k} must be a non-negative number")
    if key == "processing":
        conf = value.get("fact_confidence_threshold")
        if conf is not None and not 0.5 <= float(conf) <= 1.0:
            raise ValueError("fact_confidence_threshold must be between 0.5 and 1.0")
        jobs = value.get("max_concurrent_jobs")
        if jobs is not None and not 1 <= int(jobs) <= 32:
            raise ValueError("max_concurrent_jobs must be between 1 and 32")
    if key == "schedule":
        vpd = value.get("videos_per_day")
        if vpd is not None and not 0 <= int(vpd) <= 50:
            raise ValueError("videos_per_day must be between 0 and 50")
        for window in value.get("publishing_windows", []):
            _parse_window(window)


def _parse_window(window: str) -> tuple[int, int]:
    try:
        start, end = window.split("-")
        sh, sm = (int(x) for x in start.split(":"))
        eh, em = (int(x) for x in end.split(":"))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Bad publishing window '{window}', expected HH:MM-HH:MM") from exc
    a, b = sh * 60 + sm, eh * 60 + em
    if not (0 <= a < 1440 and 0 < b <= 1440 and a < b):
        raise ValueError(f"Bad publishing window '{window}'")
    return a, b


def parse_window(window: str) -> tuple[int, int]:
    return _parse_window(window)


def mode(session: Session) -> OperatingMode:
    return OperatingMode(get(session, "mode"))
