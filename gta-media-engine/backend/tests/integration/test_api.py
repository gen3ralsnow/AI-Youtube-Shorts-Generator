"""HTTP API: auth, validation, controls, activation gate and content management."""

from engine.models import Topic
from tests.conftest import ADMIN, VIEWER


def test_auth_required_and_roles(client):
    assert client.get("/health").status_code == 200
    anon = client.get("/api/overview", headers={"Authorization": ""})
    assert anon.status_code == 401
    viewer = {"Authorization": f"Bearer {VIEWER}"}
    assert client.get("/api/overview", headers=viewer).status_code == 200
    assert client.post("/api/control/pause-publishing", headers=viewer).status_code == 403
    assert client.get("/api/audit", headers=viewer).status_code == 403


def test_security_headers(client):
    r = client.get("/api/overview")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_overview_reports_missing_metrics_as_null(client):
    data = client.get("/api/overview").json()
    assert data["mode"] == "test" and data["publishing_paused"] is True
    assert data["total_views"] is None and data["engagement_rate"] is None


def test_settings_update_validation_and_audit(client):
    assert client.put("/api/settings/budgets", json={"daily_usd": 5}).json()["daily_usd"] == 5
    assert client.put("/api/settings/budgets", json={"daily_usd": -5}).status_code == 422
    assert client.put("/api/settings/mode", json="autonomous").status_code == 403
    assert client.put("/api/settings/unknown", json=1).status_code == 404
    actions = [a["action"] for a in client.get("/api/audit").json()]
    assert "settings.update" in actions


def test_controls_and_activation_gate(client):
    assert client.post("/api/control/resume-publishing").status_code == 409  # only in AUTONOMOUS
    assert client.post("/api/control/pause-processing").json()["processing_paused"] is True
    assert client.post("/api/control/resume-processing").json()["processing_paused"] is False
    check = client.get("/api/control/launch-checklist").json()
    assert check["ready"] is False
    keys = {i["key"]: i for i in check["items"]}
    assert not keys["tts"]["passed"] and not keys["footage"]["passed"] and not keys["platform_youtube"]["passed"]
    bad = client.post("/api/control/activate-autonomous", json={"confirm": "yes"})
    assert bad.status_code == 422
    refused = client.post("/api/control/activate-autonomous", json={"confirm": "ACTIVATE AUTONOMOUS MODE"})
    assert refused.status_code == 409 and refused.json()["detail"]["failing"]
    assert client.get("/api/control/mode").json()["mode"] == "test"


def test_manual_topic_classification_cannot_be_overridden(client, session):
    body = {"title": "Rumoured GTA 6 map size", "category": "B_news",
            "sources": [{"url": "https://www.reddit.com/r/GTA6/x", "title": "Leaked insider says map is huge"}]}
    t = client.post("/api/topics", json=body).json()
    assert t["classification"] == "unverified_rumour" and t["category"] == "C_theories"
    job = client.post(f"/api/topics/{t['id']}/produce").json()
    assert job["status"] == "queued"
    assert client.post(f"/api/topics/{t['id']}/produce").status_code == 200  # explicit second job allowed
    assert client.post("/api/topics/999/produce").status_code == 404


def test_rejected_topic_cannot_be_produced(client, session):
    from tests.conftest import seed_topic

    t = seed_topic(session)
    session.commit()
    assert client.post(f"/api/topics/{t.id}/reject").json()["status"] == "rejected"
    assert client.post(f"/api/topics/{t.id}/produce").status_code == 409
    assert session.get(Topic, t.id) is not None


def test_asset_register_requires_rights_fields_and_root(client, make_video, tmp_path):
    path = make_video("a.mp4", seconds=2)
    missing = client.post("/api/assets", json={"path": str(path), "original_source": "x", "owner": "", "licence": "y"})
    assert missing.status_code == 422
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    r = client.post("/api/assets", json={"path": str(outside), "original_source": "src", "owner": "me", "licence": "own"})
    assert r.status_code == 422 and "outside" in r.json()["detail"]
    ok = client.post("/api/assets", json={"path": str(path), "original_source": "my capture", "owner": "me",
                                          "licence": "owned", "tags": ["city"]}).json()
    assert ok["rights_verified"] is False
    assert client.post(f"/api/assets/{ok['id']}/verify").status_code == 422  # no platforms yet
    client.patch(f"/api/assets/{ok['id']}", json={"allowed_platforms": ["youtube"]})
    v = client.post(f"/api/assets/{ok['id']}/verify").json()
    assert v["rights_verified"] is True
    changed = client.patch(f"/api/assets/{ok['id']}", json={"licence": "changed"}).json()
    assert changed["rights_verified"] is False  # rights edits force re-verification


def test_upload_rejects_bad_types(client):
    r = client.post("/api/assets/upload", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415


def test_oauth_callback_escapes_input(client):
    r = client.get("/api/platforms/youtube/callback", params={"error": "<script>alert(1)</script>"})
    assert r.status_code == 400 and "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_platforms_list_reports_missing_app_credentials(client):
    rows = {p["platform"]: p for p in client.get("/api/platforms").json()}
    assert rows["tiktok"]["app_credentials_missing"] == ["TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"]
    assert client.post("/api/platforms/tiktok/connect").status_code == 412


def test_system_health_flags_placeholder_voice(client):
    h = client.get("/api/system/health").json()
    assert h["components"]["database"]["ok"] and h["components"]["ffmpeg"]["ok"]
    assert h["providers"]["tts"]["placeholder"] is True


def test_rate_limit(client, monkeypatch):
    from engine.api import deps
    from engine.security import RateLimiter

    deps._limiter = RateLimiter(2)
    codes = [client.get("/api/control/mode").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert ADMIN


def test_streaming_files_do_not_hold_db_connections(client, session, tmp_path):
    """Regression: file endpoints held a pooled DB connection for the whole stream,
    so a page of <video> elements exhausted the pool and hung the API."""
    from engine.config import get_settings
    from engine.db import get_engine
    from engine.models import ContentJob, Render

    path = get_settings().data_dir / "renders" / "r.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * 4096)
    job = ContentJob(category="B_news")
    session.add(job)
    session.flush()
    r = Render(job_id=job.id, profile="tiktok", path=str(path))
    session.add(r)
    session.commit()
    pool = get_engine().pool
    before = pool.checkedout()
    with client.stream("GET", f"/api/files/render/{r.id}") as resp:
        assert resp.status_code == 200
        next(resp.iter_bytes(1024))
        assert pool.checkedout() == before  # nothing held while the body streams
    assert client.get("/api/files/render/9999").status_code == 404
