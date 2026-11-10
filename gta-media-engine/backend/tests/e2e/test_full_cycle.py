"""End-to-end production cycle with real FFmpeg rendering.

discover -> research -> score -> select -> fact-check -> script -> validate -> narrate ->
select footage -> render -> QC -> thumbnails -> metadata -> queue -> publish -> analytics.

Platform APIs are mocked; AI providers run offline (rule-based script, placeholder voice
in the TEST-mode cycle, a stand-in TTS in the publishing cycle).
"""

from pathlib import Path

import httpx
import pytest

from engine import settings_store
from engine.agents.base import AgentContext
from engine.agents.research import ResearchAgent, TrendAnalystAgent
from engine.agents.strategist import ContentStrategistAgent
from engine.analytics import analyst, collector
from engine.cli import demo_footage
from engine.enums import OperatingMode
from engine.models import (
    ContentJob,
    MediaAsset,
    MetadataPackage,
    MetricSnapshot,
    Publication,
    Render,
    StageRun,
    Thumbnail,
)
from engine.pipeline import orchestrator
from engine.providers.tts import PlaceholderTTS
from engine.publishing import manager
from engine.research.connectors import RSSConnector
from engine.video import ffmpeg, footage
from tests.conftest import mock_http
from tests.integration.test_platforms import _connect

pytestmark = pytest.mark.slow

FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>IGN</title>
<item><title>GTA 6 trailer shows a crowded Vice City beach in Leonida</title>
<link>https://www.ign.com/articles/gta-6-trailer-beach</link><guid>ign-gta6-1</guid>
<description>The new GTA 6 trailer shows a crowded Vice City beach in the state of Leonida. Rockstar Games
released the trailer on its YouTube channel. The trailer features Lucia and Jason driving along the coast at sunset.
Fans counted dozens of vehicles in the beach traffic scene.</description></item>
</channel></rss>"""


def _discover(session):
    feed = RSSConnector(["https://feeds.example.com/ign"], client=mock_http(
        lambda r: httpx.Response(404) if r.url.path == "/robots.txt" else httpx.Response(200, content=FEED)))
    ctx = AgentContext(session)
    research = ResearchAgent([feed]).run(ctx, {})
    trends = TrendAnalystAgent().run(ctx, {})
    settings_store.put(session, "processing", {"min_topic_score": 0})
    plan = ContentStrategistAgent().run(ctx, {"count": 1})
    return research, trends, plan


def _add_audio_assets(session, make_audio, test_only: bool):
    for name, kind, tags, freq in (("bed.mp3", "music", ["bed"], 110), ("impact.mp3", "sfx", ["impact"], 880),
                                   ("swipe.mp3", "sfx", ["swipe"], 1200)):
        p = make_audio(name, seconds=30 if kind == "music" else 1, freq=freq)
        a = footage.register_asset(session, path=str(p), kind=kind, original_source="generated tone",
                                   owner="operator", licence="owned", tags=tags,
                                   usage_permissions="testing only" if test_only else "all uses",
                                   allowed_platforms=["youtube", "tiktok", "instagram"])
        footage.verify_rights(a, "test")


def test_test_mode_cycle_renders_but_never_publishes(session, make_audio):
    research, trends, plan = _discover(session)
    assert research.new_items == 1 and trends.topics_created == 1 and len(plan.selected) == 1
    session.commit()  # demo_footage uses its own session; SQLite allows one writer
    demo_footage(3)
    _add_audio_assets(session, make_audio, test_only=True)
    job = orchestrator.advance(session, plan.selected[0].job_id)

    stages = {r.stage: r for r in session.query(StageRun).filter_by(job_id=job.id) if r.status != "superseded"}
    failed = {k: v.error for k, v in stages.items() if v.status != "succeeded"}
    assert job.status == "ready", (job.error, job.blocked_reason, failed)
    assert len(stages) == 10 and not failed

    renders = session.query(Render).filter_by(job_id=job.id).all()
    assert {r.profile for r in renders} == {"youtube_shorts", "tiktok", "instagram_reels"}
    for r in renders:
        info = ffmpeg.probe(r.path)
        assert (info["width"], info["height"], info["video_codec"], info["audio_codec"]) == (1080, 1920, "h264", "aac")
        assert r.qc_passed is True  # technical QC
        assert r.qc["passed"] is False  # but not publishable: placeholder voice + test-only assets
    ig = next(r for r in renders if r.profile == "instagram_reels")
    assert ffmpeg.probe(ig.path)["audio_bitrate"] <= 135_000  # Instagram profile audio cap
    assert session.query(Thumbnail).filter_by(job_id=job.id).count() == 6
    assert all(Path(t.path).stat().st_size < 2 * 1024 * 1024 for t in session.query(Thumbnail))
    metas = {m.platform: m for m in session.query(MetadataPackage).filter_by(job_id=job.id)}
    assert set(metas) == {"youtube", "tiktok", "instagram"}
    assert "#Shorts" in metas["youtube"].hashtags and len(metas["youtube"].title_variations) == 5
    assert "Sources:" in metas["youtube"].description and "ign.com" in metas["youtube"].description

    pubs = session.query(Publication).filter_by(job_id=job.id).all()
    assert len(pubs) == 3
    for p in pubs:
        p.scheduled_for = None
    out = manager.process_due(session)
    assert out["dry_run"] == 3 and out["sent"] == 0
    assert {p.status for p in pubs} == {"dry_run"}


def test_autonomous_cycle_publishes_after_confirmation_and_collects_metrics(session, make_audio, make_video,
                                                                           monkeypatch):
    class StandInTTS(PlaceholderTTS):
        """Provider double: same audio as the placeholder but reported as real narration."""

        name = "stand-in"

        def synthesize(self, text, voice, out_path):
            res = super().synthesize(text, voice, out_path)
            res.placeholder, res.provider = False, self.name
            return res

    monkeypatch.setattr("engine.agents.voiceover.get_tts", lambda: StandInTTS())
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gs")
    from engine.config import reset_settings_cache

    reset_settings_cache()
    settings_store.put(session, "schedule", {"platforms": {"tiktok": {"enabled": False},
                                                           "instagram": {"enabled": False}}})
    for i, src in enumerate(("testsrc2", "mandelbrot", "smptehdbars")):
        p = make_video(f"owned_{i}.mp4", seconds=15, size="1920x1080", src=src)
        a = footage.register_asset(session, path=str(p), kind="footage", original_source="own gameplay capture",
                                   owner="operator", licence="owned", allowed_platforms=["youtube"],
                                   tags=["vice", "city", "beach", "leonida", "trailer"], monetization_allowed=True)
        footage.verify_rights(a, "test")
    _add_audio_assets(session, make_audio, test_only=False)
    _, _, plan = _discover(session)
    job = orchestrator.advance(session, plan.selected[0].job_id)
    assert job.status == "ready", (job.error, job.blocked_reason)
    render = session.query(Render).filter_by(job_id=job.id, profile="youtube_shorts").one()
    assert render.qc["passed"] is True, render.qc.get("content_blockers")

    settings_store.put(session, "mode", OperatingMode.AUTONOMOUS.value, allow_protected=True)
    settings_store.put(session, "publishing_paused", False)
    _connect(session, "youtube")
    remote = {"status": "uploaded", "uploads": 0}

    def youtube(req: httpx.Request):
        if req.method == "POST" and req.url.path.endswith("/videos"):
            return httpx.Response(200, headers={"location": "https://upload.example/s"})
        if req.method == "PUT":
            remote["uploads"] += 1
            return httpx.Response(200, json={"id": "short1"})
        if "thumbnails" in req.url.path:
            return httpx.Response(403, json={"error": "channel not verified"})
        if req.url.path.endswith("/videos") and "statistics" in req.url.params.get("part", ""):
            return httpx.Response(200, json={"items": [{"statistics": {"viewCount": "1500", "likeCount": "120",
                                                                       "commentCount": "14"}}]})
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": remote["status"],
                                                                   "privacyStatus": "public"}}]})
        if "youtubeanalytics" in req.url.host:
            return httpx.Response(200, json={"columnHeaders": [{"name": "shares"}, {"name": "averageViewDuration"},
                                                               {"name": "averageViewPercentage"},
                                                               {"name": "subscribersGained"}],
                                             "rows": [[9, 14.2, 71.5, 3]]})
        return httpx.Response(404)

    http = mock_http(youtube)
    pub = session.query(Publication).filter_by(job_id=job.id, platform="youtube").one()
    pub.scheduled_for = None
    manager.process_due(session, http=http)
    assert pub.status == "processing" and pub.published_at is None  # not claimed before confirmation
    remote["status"] = "processed"
    manager.process_due(session, http=http)
    assert pub.status == "published" and pub.url.endswith("short1") and remote["uploads"] == 1
    assert session.get(ContentJob, job.id).status == "published"

    out = collector.collect(session, http=http)
    snap = session.query(MetricSnapshot).one()
    assert out["snapshots"] == 1 and snap.views == 1500 and snap.followers_gained == 3
    assert snap.saves is None  # YouTube does not report saves: stays missing
    summary = analyst.analyse(session)
    assert summary["videos_with_metrics"] == 1
    assert summary["answers"]["best_hooks"]["answer"] is None  # one video is not a sample
    assert session.query(MediaAsset).filter(MediaAsset.times_used > 0).count() >= 1
