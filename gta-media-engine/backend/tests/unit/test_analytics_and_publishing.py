from datetime import datetime, timedelta, timezone
from pathlib import Path

from engine import settings_store
from engine.agents.base import AgentContext
from engine.agents.strategist import ContentStrategistAgent, build_calendar
from engine.analytics import analyst
from engine.enums import OperatingMode
from engine.models import ContentJob, MetadataPackage, MetricSnapshot, Publication, Render, Script, Topic
from engine.publishing import manager
from tests.conftest import seed_topic

# ------------------------------------------------------------------ analytics


def _published(session, n, category, views, likes, comments=None, hook="Did you see this?"):
    for i in range(n):
        job = ContentJob(category=category, duration_bucket="15-25", status="published")
        session.add(job)
        session.flush()
        session.add(Script(job_id=job.id, title=f"t{i}", category=category, chosen_hook=hook, voiceover_text="x"))
        pub = Publication(job_id=job.id, platform="youtube", idempotency_key=f"j{job.id}:youtube",
                          status="published", external_id=f"v{job.id}",
                          published_at=datetime.now(timezone.utc) - timedelta(days=1))
        session.add(pub)
        session.flush()
        session.add(MetricSnapshot(publication_id=pub.id, views=views, likes=likes + i, comments=comments))
    session.flush()


def test_engagement_rate_ignores_missing_metrics():
    assert analyst.engagement_rate(MetricSnapshot(views=1000, likes=50, comments=None)) == 0.05
    assert analyst.engagement_rate(MetricSnapshot(views=None, likes=50)) is None
    assert analyst.engagement_rate(MetricSnapshot(views=1000)) is None  # no interaction data != zero


def test_small_samples_give_no_conclusions(session):
    _published(session, 2, "A_discoveries", 1000, 100)
    _published(session, 2, "B_news", 1000, 10)
    result = analyst.analyse(session)
    assert result["answers"]["best_hooks"]["answer"] is None
    assert all(not g["sufficient_sample"] for g in result["by_category"])
    assert "Not enough" in result["answers"]["next_focus"]


def test_clear_winner_with_sufficient_samples(session):
    _published(session, 6, "A_discoveries", 1000, 100, comments=20)
    _published(session, 6, "B_news", 1000, 10, comments=1)
    result = analyst.analyse(session)
    cats = {g["group"]: g for g in result["by_category"]}
    assert cats["A_discoveries"]["sufficient_sample"] and cats["A_discoveries"]["relative_engagement"] > 1
    assert "A_discoveries" in result["answers"]["next_focus"]
    assert result["missing_metric_counts"]["shares"] == 12  # recorded as missing, not zero


def test_weekly_report_and_strategy_feedback(session):
    _published(session, 6, "A_discoveries", 1000, 100)
    _published(session, 6, "B_news", 1000, 10)
    report = analyst.weekly_report(session)
    assert report.body["recommendations"]
    from engine.agents.strategist import category_multipliers

    mult = category_multipliers(session)
    assert mult["A_discoveries"] > 1 > mult["B_news"]
    assert 0.7 <= min(mult.values()) and max(mult.values()) <= 1.3


# ------------------------------------------------------------------ strategy


def test_strategist_balances_categories(session):
    for cat in ("A_discoveries", "A_discoveries", "B_news"):
        t = seed_topic(session, category=cat)
        t.score = 90 if cat == "A_discoveries" else 70
    session.flush()
    out = ContentStrategistAgent().run(AgentContext(session), {"count": 2})
    cats = [s.category for s in out.selected]
    assert sorted(cats) == ["A_discoveries", "B_news"]  # diversity beats a second A
    assert all(session.get(Topic, s.topic_id).status == "selected" for s in out.selected)
    assert len(build_calendar(session, 2)) == 2 * settings_store.get(session, "schedule")["videos_per_day"]


def test_strategist_skips_low_scores_and_sourceless_topics(session):
    t = seed_topic(session)
    t.score = 10
    session.add(Topic(title="no sources", category="A_discoveries", classification="community_theory", score=99))
    session.flush()
    out = ContentStrategistAgent().run(AgentContext(session), {"count": 3})
    assert out.selected == []


# ------------------------------------------------------------------ publishing controls


def _ready_job(session, tmp: Path, qc_passed=True):
    topic = seed_topic(session)
    job = ContentJob(topic_id=topic.id, category=topic.category, status="ready")
    session.add(job)
    session.flush()
    for platform, profile in (("youtube", "youtube_shorts"), ("tiktok", "tiktok"), ("instagram", "instagram_reels")):
        path = tmp / f"{job.id}_{profile}.mp4"
        path.write_bytes(b"video-bytes-" + profile.encode())
        session.add(Render(job_id=job.id, profile=profile, path=str(path), qc={"passed": qc_passed}, qc_passed=True))
        session.add(MetadataPackage(job_id=job.id, platform=platform, title="t", description="d"))
    session.flush()
    return job


def test_enqueue_is_idempotent(session, tmp_path):
    job = _ready_job(session, tmp_path)
    first = manager.enqueue(session, job)
    second = manager.enqueue(session, job)
    assert len(first) == 3 and [p.id for p in first] == [p.id for p in second]


def test_test_mode_never_publishes(session, tmp_path):
    job = _ready_job(session, tmp_path)
    pubs = manager.enqueue(session, job)

    def boom(*a, **k):
        raise AssertionError("platform must not be contacted in TEST mode")

    import engine.publishing.oauth as oauth

    orig = oauth.client_for
    oauth.client_for = boom
    try:
        for p in pubs:
            manager.publish(session, p)
            assert p.status == "dry_run" and "TEST mode" in p.last_error
    finally:
        oauth.client_for = orig


def _autonomous(session):
    settings_store.put(session, "mode", OperatingMode.AUTONOMOUS.value, allow_protected=True)
    settings_store.put(session, "publishing_paused", False)


def test_pause_keeps_content_queued(session, tmp_path):
    job = _ready_job(session, tmp_path)
    _autonomous(session)
    settings_store.put(session, "publishing_paused", True)
    pub = manager.enqueue(session, job)[0]
    manager.publish(session, pub)
    assert pub.status == "queued" and "paused" in pub.last_error and pub.next_attempt_at is not None


def test_qc_failure_and_unconnected_platforms_block(session, tmp_path):
    job = _ready_job(session, tmp_path, qc_passed=False)
    _autonomous(session)
    pub = manager.enqueue(session, job)[0]
    manager.publish(session, pub)
    assert pub.status == "blocked" and "QC" in pub.last_error

    job2 = _ready_job(session, tmp_path)
    pub2 = manager.enqueue(session, job2)[0]
    manager.publish(session, pub2)
    assert pub2.status == "blocked" and "not connected" in pub2.last_error


def test_duplicate_content_is_refused(session, tmp_path):
    job = _ready_job(session, tmp_path)
    _autonomous(session)
    pub = manager.enqueue(session, job)[0]
    session.add(Publication(job_id=job.id, platform=pub.platform, idempotency_key="other",
                            content_hash=pub.content_hash, status="published"))
    session.flush()
    manager.publish(session, pub)
    assert pub.status == "blocked" and "identical video" in pub.last_error


def test_requeue_rules(session, tmp_path):
    job = _ready_job(session, tmp_path)
    pub = manager.enqueue(session, job)[0]
    pub.status = "failed"
    manager.requeue(session, pub)
    assert pub.status == "queued"
    pub.status = "published"
    import pytest

    with pytest.raises(ValueError):
        manager.requeue(session, pub)
