"""Settings, costs/budgets, scheduling, security, SEO, voice timing and subtitle logic."""

from datetime import datetime, timedelta, timezone

import pytest

from engine import costs, settings_store
from engine.agents.seo import clean_title, hashtags_for
from engine.agents.voiceover import estimate_timings, map_timings, respell
from engine.enums import Category
from engine.logs import redact
from engine.models import Publication
from engine.publishing.manager import next_slot
from engine.security import CredentialVault, RateLimiter, resolve_role
from engine.video.subtitles import build_ass, chunk_words, hex_to_ass, validate_cues
from tests.conftest import ADMIN, VIEWER

# ------------------------------------------------------------------ settings


def test_defaults_start_safe(session):
    assert settings_store.get(session, "mode") == "test"
    assert settings_store.get(session, "publishing_paused") is True


def test_mode_is_protected(session):
    with pytest.raises(PermissionError):
        settings_store.put(session, "mode", "autonomous")


def test_settings_validation(session):
    with pytest.raises(ValueError):
        settings_store.put(session, "budgets", {"daily_usd": -1})
    with pytest.raises(ValueError):
        settings_store.put(session, "processing", {"fact_confidence_threshold": 0.2})
    with pytest.raises(ValueError):
        settings_store.put(session, "schedule", {"publishing_windows": ["25:00-26:00"]})
    with pytest.raises(KeyError):
        settings_store.put(session, "nope", 1)
    merged = settings_store.put(session, "budgets", {"daily_usd": 3.0})
    assert merged["daily_usd"] == 3.0 and merged["monthly_usd"] == 200.0  # deep-merged with defaults


# ------------------------------------------------------------------ costs


def test_budget_guard_pauses_processing(session):
    settings_store.put(session, "budgets", {"daily_usd": 1.0, "monthly_usd": 10.0, "per_video_usd": 5.0})
    costs.record(session, category="language_model", provider="x", units=1, unit="call", estimated_usd=0.9,
                 actual_usd=0.95)
    costs.check_budget(session, 0.01)
    with pytest.raises(costs.BudgetExceeded):
        costs.check_budget(session, 0.2)
    assert settings_store.get(session, "processing_paused") is True
    assert "daily" in settings_store.get(session, "processing_pause_reason")


def test_per_video_budget_does_not_pause_everything(session):
    settings_store.put(session, "budgets", {"daily_usd": 100.0, "monthly_usd": 1000.0, "per_video_usd": 0.5})
    costs.record(session, category="text_to_speech", provider="x", units=1, unit="c", estimated_usd=0.45,
                 actual_usd=None, job_id=7)
    with pytest.raises(costs.BudgetExceeded):
        costs.check_budget(session, 0.1, job_id=7)
    assert settings_store.get(session, "processing_paused") is False


def test_estimated_and_actual_reported_separately(session):
    costs.record(session, category="language_model", provider="x", units=1, unit="call", estimated_usd=0.5,
                 actual_usd=0.3)
    costs.record(session, category="language_model", provider="x", units=1, unit="call", estimated_usd=0.2,
                 actual_usd=None)
    summary = costs.spend_summary(session)
    assert summary["today"] == {"estimated_usd": 0.7, "actual_usd": 0.3}


# ------------------------------------------------------------------ scheduling


def test_next_slot_respects_windows_and_daily_limits(session):
    settings_store.put(session, "schedule", {"videos_per_day": 2, "publishing_windows": ["12:00-14:00"],
                                             "platforms": {"youtube": {"enabled": True, "max_per_day": 2}}})
    base = datetime(2030, 1, 1, 9, 0, tzinfo=timezone.utc)
    first = next_slot(session, "youtube", base)
    assert first.date() == base.date() and 12 <= first.hour < 14
    from engine.models import ContentJob

    job = ContentJob(category="B_news")
    session.add(job)
    session.flush()
    for i in range(2):
        session.add(Publication(job_id=job.id, platform="youtube", idempotency_key=f"k{i}", status="queued",
                                scheduled_for=first + timedelta(minutes=i)))
    session.flush()
    nxt = next_slot(session, "youtube", base)
    assert nxt.date() == (base + timedelta(days=1)).date()


# ------------------------------------------------------------------ security


def test_vault_roundtrip_and_tamper_detection():
    vault = CredentialVault()
    token = vault.encrypt("secret-access-token")
    assert "secret" not in token and vault.decrypt(token) == "secret-access-token"
    with pytest.raises(RuntimeError):
        vault.decrypt(token[:-4] + "AAAA")


def test_roles():
    assert resolve_role(ADMIN) == "admin"
    assert resolve_role(VIEWER) == "viewer"
    assert resolve_role("wrong") is None and resolve_role(None) is None


def test_rate_limiter():
    rl = RateLimiter(3)
    assert [rl.allow("a") for _ in range(4)] == [True, True, True, False]
    assert rl.allow("b")


def test_log_redaction():
    line = 'Authorization: Bearer abc.def api_key=XYZ123 {"access_token": "tok"} sk-ant-abcdefghijklmnop'
    out = redact(line)
    for secret in ("abc.def", "XYZ123", '"tok"', "sk-ant-abcdefghijklmnop"):
        assert secret not in out


# ------------------------------------------------------------------ SEO


def test_titles_are_not_misleading():
    t = clean_title("GTA 6 map size CONFIRMED officially", "unverified_rumour", 100)
    assert "confirmed" not in t.lower() and "official" not in t.lower() and t.endswith("(Rumour)")
    assert "confirmed" in clean_title("Release date confirmed", "officially_confirmed", 100)
    assert clean_title("THIS IS ALL CAPS SHOUTING", "credibly_reported", 100) != "THIS IS ALL CAPS SHOUTING"


def test_hashtags_are_capped_and_platform_specific():
    yt = hashtags_for("youtube", Category.NEWS, ["leonida", "vice", "trailer", "beach", "lucia", "jason", "map"])
    ig = hashtags_for("instagram", Category.NEWS, ["leonida", "vice", "beach", "lucia", "jason", "map"])
    assert yt[0] == "#Shorts" and len(yt) <= 3
    assert len(ig) <= 5 and "#Shorts" not in ig
    assert len(set(map(str.lower, ig))) == len(ig)


# ------------------------------------------------------------------ voice timing


def test_respell_maps_back_to_displayed_words():
    spoken, groups = respell("GTA 6 is set in Leonida.", {"GTA 6": "G T A six", "Leonida": "Lee-oh-nee-dah"})
    assert spoken == "G T A six is set in Lee-oh-nee-dah."
    spoken_words = spoken.split()
    timings = [{"word": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(spoken_words)]
    mapped = map_timings("GTA 6 is set in Leonida.".split(), groups, timings)
    assert [m["word"] for m in mapped] == ["GTA", "6", "is", "set", "in", "Leonida."]
    assert mapped[0]["start"] == 0 and mapped[1]["end"] == pytest.approx(timings[3]["end"])


def test_estimated_timings_are_monotonic():
    t = estimate_timings("One two three. Four five.".split(), 0.2, 3.0)
    assert all(a["end"] <= b["start"] + 1e-6 for a, b in zip(t, t[1:]))
    assert t[0]["start"] == 0.2 and t[-1]["end"] <= 3.0


# ------------------------------------------------------------------ subtitles


def test_subtitles_and_cue_validation(tmp_path):
    words = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(
        "Did you catch this Vice City detail? It is huge.".split())]
    chunks = chunk_words(words, 3)
    assert all(len(c) <= 3 for c in chunks)
    assert chunks[2][-1]["word"].endswith("?")  # sentence boundary ends a chunk
    cues = build_ass(words, [{"start": 0, "end": 2, "caption": "VICE CITY", "section": "hook"}],
                     tmp_path / "s.ass", brand={"name": "X"}, duration=4.0)
    text = (tmp_path / "s.ass").read_text()
    assert "Style: Sub" in text and hex_to_ass("#FF3D7F") in text
    assert validate_cues(cues, 4.0) == []
    assert validate_cues([{"start": 1, "end": 0.5, "text": "x", "track": "subtitles"}], 4.0)
    assert validate_cues([{"start": 0, "end": 9, "text": "x", "track": "subtitles"}], 4.0)


def test_tightly_timed_chunks_never_overlap(tmp_path):
    # Regression: chunk end padding (+0.08s) used to overlap the next chunk, stacking two lines on screen.
    words = [{"word": f"w{i}", "start": i * 0.30, "end": i * 0.30 + 0.28} for i in range(12)]
    cues = build_ass(words, [], tmp_path / "s.ass", brand={}, duration=4.0)
    assert validate_cues(cues, 4.0) == []
    subs = sorted((c for c in cues if c["track"] == "subtitles"), key=lambda c: c["start"])
    assert all(a["end"] <= b["start"] for a, b in zip(subs, subs[1:]))
    assert "WrapStyle: 0" in (tmp_path / "s.ass").read_text()
