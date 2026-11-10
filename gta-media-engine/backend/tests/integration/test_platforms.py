"""OAuth and publishing flows for YouTube, TikTok and Instagram against mocked platform APIs.

These prove the request/response handling. They do NOT prove the live integrations work:
that needs real app credentials, approved scopes and a test account.
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from engine import settings_store
from engine.enums import OperatingMode
from engine.models import PlatformConnection
from engine.publishing import manager, oauth
from engine.publishing.base import UploadOptions
from engine.publishing.instagram import InstagramClient
from engine.publishing.tiktok import TikTokClient
from engine.publishing.youtube import YouTubeClient
from engine.security import CredentialVault
from tests.conftest import mock_http
from tests.unit.test_analytics_and_publishing import _ready_job


@pytest.fixture
def apps(monkeypatch):
    for k, v in {"GOOGLE_CLIENT_ID": "gid", "GOOGLE_CLIENT_SECRET": "gsecret", "TIKTOK_CLIENT_KEY": "tkey",
                 "TIKTOK_CLIENT_SECRET": "tsecret", "META_APP_ID": "mid", "META_APP_SECRET": "msecret"}.items():
        monkeypatch.setenv(k, v)
    from engine.config import reset_settings_cache

    reset_settings_cache()


def _connect(session, platform, token="tok", expires_in=3600, account_id="acct"):
    conn = oauth.connection(session, platform)
    vault = CredentialVault()
    conn.access_token_enc, conn.refresh_token_enc = vault.encrypt(token), vault.encrypt("refresh")
    conn.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    conn.status, conn.account_id = "verified", account_id
    session.flush()


# ------------------------------------------------------------------ OAuth


def test_oauth_flow_with_state_and_pkce(session, apps):
    url = oauth.start(session, "youtube")
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    state = q["state"][0]
    with pytest.raises(PermissionError):
        oauth.finish(session, "youtube", "code", "forged-state")

    def handler(req: httpx.Request):
        if req.url.host == "oauth2.googleapis.com":
            body = parse_qs(req.content.decode())
            assert body["code_verifier"][0] and body["grant_type"] == ["authorization_code"]
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
                                             "scope": "a b"})
        return httpx.Response(200, json={"items": [{"id": "UC123", "snippet": {"title": "VI Signal"}, "status": {}}]})

    conn = oauth.finish(session, "youtube", "code", state, http=mock_http(handler))
    assert conn.status == "verified" and conn.account_name == "VI Signal"
    assert conn.access_token_enc != "at" and CredentialVault().decrypt(conn.access_token_enc) == "at"
    assert conn.oauth_state == ""  # single use


def test_unconfigured_app_cannot_start_oauth(session):
    from engine.providers.http import NotConfigured

    with pytest.raises(NotConfigured):
        oauth.start(session, "tiktok")


def test_expired_token_is_refreshed(session, apps):
    _connect(session, "youtube", token="old", expires_in=-10)

    def handler(req):
        return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})

    assert oauth.access_token(session, "youtube", http=mock_http(handler)) == "new"
    conn = session.get(PlatformConnection, "youtube")
    assert CredentialVault().decrypt(conn.refresh_token_enc) == "refresh"  # kept when not rotated


def test_failed_refresh_marks_connection_expired(session, apps):
    from engine.providers.http import NotConfigured

    _connect(session, "tiktok", expires_in=-10)
    with pytest.raises(NotConfigured):
        oauth.access_token(session, "tiktok", http=mock_http(lambda r: httpx.Response(400, json={"error": "invalid_grant"})))
    assert session.get(PlatformConnection, "tiktok").status == "expired"


# ------------------------------------------------------------------ YouTube


def test_youtube_resumable_upload_and_status(tmp_path, apps):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 1000)
    seen = {}

    def handler(req: httpx.Request):
        if req.method == "POST" and req.url.path.endswith("/videos"):
            seen["init"] = req
            return httpx.Response(200, headers={"location": "https://upload.example/session1"})
        if req.method == "PUT":
            seen["put_len"] = len(req.content)
            return httpx.Response(200, json={"id": "vid1"})
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "processed",
                                                                   "privacyStatus": seen.get("privacy", "public")}}]})
        return httpx.Response(404)

    yt = YouTubeClient(mock_http(handler))
    res = yt.upload("tok", video, {"title": "T" * 150, "description": "d", "keywords": ["gta 6"]},
                    UploadOptions(privacy="private"))
    assert res.status == "processing" and res.external_id == "vid1" and seen["put_len"] == 1000
    import json

    body = json.loads(seen["init"].content)
    assert len(body["snippet"]["title"]) == 100 and body["status"]["privacyStatus"] == "private"
    assert body["status"]["selfDeclaredMadeForKids"] is False
    assert yt.status("tok", "vid1", None).status == "published"
    seen["privacy"] = "private"
    assert yt.status("tok", "vid1", None).status == "drafted"  # uploaded, not public


def test_youtube_rejected_video_is_failed(apps):
    yt = YouTubeClient(mock_http(lambda r: httpx.Response(200, json={"items": [{"status": {
        "uploadStatus": "rejected", "rejectionReason": "copyright"}}]})))
    res = yt.status("tok", "vid1", None)
    assert res.status == "failed" and "copyright" in res.detail


# ------------------------------------------------------------------ TikTok


def _tiktok_handler(state):
    def handler(req: httpx.Request):
        path = req.url.path
        if path.endswith("/creator_info/query/"):
            return httpx.Response(200, json={"data": {"privacy_level_options": ["SELF_ONLY", "PUBLIC_TO_EVERYONE"],
                                                      "max_video_post_duration_sec": 600}, "error": {"code": "ok"}})
        if path.endswith("/inbox/video/init/") or path.endswith("/video/init/"):
            state["init_path"] = path
            state["init_body"] = req.content
            return httpx.Response(200, json={"data": {"publish_id": "p1", "upload_url": "https://up.example/u"},
                                             "error": {"code": "ok"}})
        if req.method == "PUT":
            state["range"] = req.headers["content-range"]
            return httpx.Response(201)
        if path.endswith("/status/fetch/"):
            return httpx.Response(200, json={"data": {"status": state.get("remote", "SEND_TO_USER_INBOX"),
                                                      "publicaly_available_post_id": [7300]}, "error": {"code": "ok"}})
        return httpx.Response(404)

    return handler


def test_tiktok_inbox_draft_by_default(tmp_path, apps):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 2048)
    state = {}
    tt = TikTokClient(mock_http(_tiktok_handler(state)))
    res = tt.upload("tok", video, {"description": "caption"}, UploadOptions())
    assert state["init_path"].endswith("/inbox/video/init/") and state["range"] == "bytes 0-2047/2048"
    assert tt.status("tok", None, res.tracking_id).status == "drafted"
    state["remote"] = "PUBLISH_COMPLETE"
    done = tt.status("tok", None, "p1")
    assert done.status == "published" and done.external_id == "7300"


def test_tiktok_direct_post_requires_consent(apps):
    tt = TikTokClient(mock_http(_tiktok_handler({})))
    video = {"duration": 30, "width": 1080, "height": 1920}
    problems = tt.eligibility("tok", video, UploadOptions(tiktok_mode="direct_post"))
    assert any("consent" in p for p in problems)
    ok = UploadOptions(tiktok_mode="direct_post", consent={"privacy_level": "SELF_ONLY", "confirmed_by": "admin",
                                                           "confirmed_at": "now", "music_usage_confirmed": True})
    assert tt.eligibility("tok", video, ok) == []
    assert tt.eligibility("tok", {**video, "duration": 900}, UploadOptions())  # over creator limit


def test_tiktok_api_errors_surface(apps):
    from engine.providers.http import ProviderError

    tt = TikTokClient(mock_http(lambda r: httpx.Response(200, json={"error": {"code": "spam_risk_too_many_posts",
                                                                               "message": "slow down"}})))
    with pytest.raises(ProviderError, match="spam_risk"):
        tt.creator_info("tok")


# ------------------------------------------------------------------ Instagram


def test_instagram_container_then_publish(tmp_path, apps):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 500)
    state = {"status": "IN_PROGRESS"}

    def handler(req: httpx.Request):
        p = req.url.path
        if p.endswith("/media") and req.method == "POST":
            body = parse_qs(req.content.decode())
            assert body["media_type"] == ["REELS"] and body["upload_type"] == ["resumable"]
            return httpx.Response(200, json={"id": "c1"})
        if req.url.host == "rupload.facebook.com":
            assert req.headers["file_size"] == "500" and req.headers["authorization"].startswith("OAuth ")
            return httpx.Response(200, json={"success": True})
        if p.endswith("/c1"):
            return httpx.Response(200, json={"status_code": state["status"]})
        if p.endswith("/media_publish"):
            state["published"] = True
            return httpx.Response(200, json={"id": "m1"})
        if p.endswith("/m1"):
            return httpx.Response(200, json={"permalink": "https://instagram.com/reel/x"})
        return httpx.Response(404)

    ig = InstagramClient(mock_http(handler))
    ig.account_id = "17841"
    res = ig.upload("tok", video, {"description": "cap"}, UploadOptions())
    assert res.status == "processing" and res.tracking_id == "c1"
    assert ig.status("tok", None, "c1").status == "processing" and "published" not in state
    state["status"] = "FINISHED"
    done = ig.status("tok", None, "c1")
    assert done.status == "published" and done.external_id == "m1" and done.url.endswith("/reel/x")


def test_instagram_requires_professional_account(apps):
    from engine.providers.http import ProviderError

    ig = InstagramClient(mock_http(lambda r: httpx.Response(200, json={"user_id": 1, "account_type": "PERSONAL"})))
    with pytest.raises(ProviderError, match="professional"):
        ig.account_info("tok")


# ------------------------------------------------------------------ manager end to end (mocked platform)


def test_manager_publishes_only_after_confirmation_and_retries_transient(session, tmp_path, apps, monkeypatch):
    job = _ready_job(session, tmp_path)
    settings_store.put(session, "mode", OperatingMode.AUTONOMOUS.value, allow_protected=True)
    settings_store.put(session, "publishing_paused", False)
    _connect(session, "youtube")
    monkeypatch.setattr("engine.publishing.manager.ffmpeg.probe",
                        lambda p: {"duration": 30, "width": 1080, "height": 1920})
    pub = next(p for p in manager.enqueue(session, job) if p.platform == "youtube")
    calls = {"n": 0}

    def flaky(req: httpx.Request):
        if req.method == "POST" and req.url.path.endswith("/videos"):
            calls["n"] += 1
            if calls["n"] <= 3:  # exhaust the client's own retries once
                return httpx.Response(503)
            return httpx.Response(200, headers={"location": "https://upload.example/s"})
        if req.method == "PUT":
            return httpx.Response(200, json={"id": "vid9"})
        if req.url.path.endswith("/videos"):
            return httpx.Response(200, json={"items": [{"status": {"uploadStatus": "uploaded"}}]})
        return httpx.Response(200, json={})

    http = mock_http(flaky)
    manager.publish(session, pub, http=http)
    assert pub.status == "queued" and pub.attempts == 1 and pub.next_attempt_at  # transient -> backoff
    pub.next_attempt_at = None
    manager.publish(session, pub, http=http)
    assert pub.status == "processing" and pub.external_id == "vid9"  # uploaded, NOT yet published
    manager.publish(session, pub, http=http)  # idempotent: no second upload
    assert calls["n"] == 4
    manager.refresh_status(session, pub, http=http)
    assert pub.status == "processing"
