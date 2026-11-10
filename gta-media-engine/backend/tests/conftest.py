"""Shared fixtures: an isolated SQLite database and data directory per test, API client,
and helpers for mocked HTTP (httpx.MockTransport) so no test touches a real platform."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ADMIN = "test-admin-token-0123456789"
VIEWER = "test-viewer-token-0123456789"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("ENGINE_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'engine.db'}")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_API_TOKEN", ADMIN)
    monkeypatch.setenv("VIEWER_API_TOKEN", VIEWER)
    monkeypatch.setenv("ENGINE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "10000")
    for key in ("LLM_PROVIDER", "TTS_PROVIDER", "TRANSCRIPTION_PROVIDER", "YOUTUBE_API_KEY", "REDDIT_CLIENT_ID",
                "REDDIT_CLIENT_SECRET", "RSS_FEEDS", "SERPAPI_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
                "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "META_APP_ID", "META_APP_SECRET", "REDIS_URL",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "FOOTAGE_ROOTS"):
        monkeypatch.delenv(key, raising=False)
    from engine import db
    from engine.api import deps
    from engine.config import get_settings, reset_settings_cache

    reset_settings_cache()
    deps._limiter = None
    get_settings().ensure_dirs()
    db.configure(os.environ["DATABASE_URL"])
    db.create_all()
    monkeypatch.setattr("engine.providers.http.time.sleep", lambda *_: None)
    yield
    reset_settings_cache()


@pytest.fixture
def session():
    from engine.db import session_scope

    with session_scope() as s:
        yield s


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from engine.api.app import create_app

    with TestClient(create_app()) as c:
        c.headers["Authorization"] = f"Bearer {ADMIN}"
        yield c


def mock_http(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def make_video(tmp_path):
    """Create a small owned synthetic clip with ffmpeg."""
    from engine.video import ffmpeg

    def _make(name: str = "clip.mp4", seconds: int = 12, size: str = "1280x720", src: str = "testsrc2",
              root: Path | None = None) -> Path:
        from engine.config import get_settings

        out = (root or get_settings().data_dir / "footage") / name
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run(["-y", "-f", "lavfi", "-i", f"{src}=s={size}:r=30", "-t", str(seconds), "-vf", "format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", str(out)])
        return out

    return _make


@pytest.fixture
def make_audio():
    from engine.video import ffmpeg

    def _make(name: str = "music.mp3", seconds: int = 30, freq: int = 220) -> Path:
        from engine.config import get_settings

        out = get_settings().data_dir / "footage" / name
        out.parent.mkdir(parents=True, exist_ok=True)
        ffmpeg.run(["-y", "-f", "lavfi", "-i", f"sine=f={freq}:d={seconds}", "-c:a", "libmp3lame", str(out)])
        return out

    return _make


def seed_topic(session, *, classification="credibly_reported", category="B_news", credibility="credible",
               domain="ign.com", texts=None):
    from engine.models import SourceItem, Topic

    texts = texts or [
        ("GTA 6 trailer shows Vice City beach in Leonida",
         "The new GTA 6 trailer shows a crowded Vice City beach in the state of Leonida. "
         "Rockstar Games released the trailer on its official channel. "
         "The trailer features Lucia and Jason driving along the coast at sunset."),
    ]
    sources = []
    for i, (title, summary) in enumerate(texts):
        src = SourceItem(connector="rss", external_id=f"seed-{uuid.uuid4().hex}", url=f"https://www.{domain}/a{i}",
                         domain=domain, title=title, summary=summary, credibility=credibility, engagement={})
        session.add(src)
        sources.append(src)
    session.flush()
    t = Topic(title=texts[0][0], explanation=texts[0][1][:200], category=category, classification=classification,
              score=80, score_breakdown={}, angle="", hook="", recommended_length="15-25", urgency="normal",
              status="new", fingerprint="vice city beach leonida trailer", keywords=["vice", "city", "beach", "leonida", "trailer"])
    t.sources = sources
    session.add(t)
    session.flush()
    return t
